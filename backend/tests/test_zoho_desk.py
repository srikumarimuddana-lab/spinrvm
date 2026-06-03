"""
Unit tests for the Zoho Desk integration (services/zoho_desk_service.py).

Covers the contract that matters for production safety:
  - not-configured / disabled -> ZohoDeskError(503), never a silent fallback
  - unknown data center -> 503
  - on-demand token refresh + persistence of the new access token
  - 401 mid-flight -> refresh once and retry
  - upstream error body surfaced as ZohoDeskError(502)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import services.zoho_desk_service as zoho
from services.zoho_desk_service import ZohoDeskError


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeClient:
    """Async-context-manager stand-in for httpx.AsyncClient. ``handler`` is a
    callable (method, url, kwargs) -> _FakeResponse."""

    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        return self._handler("POST", url, kwargs)

    async def request(self, method, url, **kwargs):
        return self._handler(method, url, kwargs)


def _connected_config(**overrides):
    cfg = {
        "id": "default",
        "enabled": True,
        "data_center": "ca",
        "org_id": "700123",
        "client_id": "cid",
        "client_secret": "secret",
        "refresh_token": "rtok",
        "access_token": "",
        "access_token_expires_at": None,
    }
    cfg.update(overrides)
    return cfg


def _patch_db(monkeypatch, row):
    fake_db = MagicMock()
    fake_db.find_one = AsyncMock(return_value=row)
    fake_db.update_one = AsyncMock(return_value=None)
    monkeypatch.setattr(zoho, "db_supabase", fake_db)
    return fake_db


def _patch_http(monkeypatch, handler):
    monkeypatch.setattr(zoho.httpx, "AsyncClient", lambda *a, **k: _FakeClient(handler))


@pytest.mark.anyio
async def test_not_configured_raises_503(monkeypatch):
    _patch_db(monkeypatch, None)
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.list_tickets()
    assert ei.value.status == 503


@pytest.mark.anyio
async def test_disabled_raises_503(monkeypatch):
    _patch_db(monkeypatch, _connected_config(enabled=False))
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.list_tickets()
    assert ei.value.status == 503


@pytest.mark.anyio
async def test_unknown_data_center_raises_503(monkeypatch):
    _patch_db(monkeypatch, _connected_config(data_center="mars"))
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.list_tickets()
    assert ei.value.status == 503


@pytest.mark.anyio
async def test_refresh_and_request_persists_token(monkeypatch):
    db = _patch_db(monkeypatch, _connected_config())
    calls = []

    def handler(method, url, kwargs):
        calls.append((method, url))
        if "/oauth/v2/token" in url:
            return _FakeResponse(200, {"access_token": "AT1", "expires_in": 3600})
        # Must use Canada DC domain and the bearer header scheme.
        assert "desk.zohocloud.ca" in url
        assert kwargs["headers"]["Authorization"] == "Zoho-oauthtoken AT1"
        assert kwargs["headers"]["orgId"] == "700123"
        return _FakeResponse(200, {"data": [{"id": "1", "subject": "hi"}]})

    _patch_http(monkeypatch, handler)
    out = await zoho.list_tickets(limit=10)
    assert out["data"][0]["id"] == "1"
    # token refresh happened and was persisted
    assert any("/oauth/v2/token" in u for _, u in calls)
    db.update_one.assert_awaited()
    persisted = db.update_one.call_args.args[2]
    assert persisted["access_token"] == "AT1"
    assert persisted["access_token_expires_at"] is not None


@pytest.mark.anyio
async def test_401_triggers_single_refresh_retry(monkeypatch):
    # Start with a "fresh" token so the first call uses it and gets a 401.
    _patch_db(
        monkeypatch,
        _connected_config(
            access_token="STALE",
            access_token_expires_at="2999-01-01T00:00:00+00:00",
        ),
    )
    seq = {"n": 0}

    def handler(method, url, kwargs):
        if "/oauth/v2/token" in url:
            return _FakeResponse(200, {"access_token": "FRESH", "expires_in": 3600})
        seq["n"] += 1
        if seq["n"] == 1:
            return _FakeResponse(401, {"error": "expired"})
        assert kwargs["headers"]["Authorization"] == "Zoho-oauthtoken FRESH"
        return _FakeResponse(200, {"data": []})

    _patch_http(monkeypatch, handler)
    out = await zoho.list_tickets()
    assert out == {"data": []}
    assert seq["n"] == 2  # original + one retry


@pytest.mark.anyio
async def test_refresh_failure_raises_502(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        if "/oauth/v2/token" in url:
            # Zoho returns 200 with an error body for bad grants.
            return _FakeResponse(200, {"error": "invalid_code"})
        return _FakeResponse(200, {"data": []})

    _patch_http(monkeypatch, handler)
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.list_tickets()
    assert ei.value.status == 502


@pytest.mark.anyio
async def test_upstream_error_surfaced_502(monkeypatch):
    _patch_db(
        monkeypatch,
        _connected_config(
            access_token="AT", access_token_expires_at="2999-01-01T00:00:00+00:00"
        ),
    )

    def handler(method, url, kwargs):
        return _FakeResponse(500, {"errorCode": "INTERNAL_ERROR"}, text="boom")

    _patch_http(monkeypatch, handler)
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.get_ticket("123")
    assert ei.value.status == 502


@pytest.mark.anyio
async def test_update_ticket_rejects_empty(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.update_ticket("1", {"unknown_field": "x"})
    assert ei.value.status == 400
