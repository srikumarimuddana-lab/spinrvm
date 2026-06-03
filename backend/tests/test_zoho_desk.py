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
        _connected_config(access_token="AT", access_token_expires_at="2999-01-01T00:00:00+00:00"),
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


@pytest.mark.anyio
async def test_ticket_count_omits_status_param(monkeypatch):
    # Codex P2: /ticketsCount does not support a `status` filter. Ensure we
    # only ever send departmentId there (status breakdown is sampled instead).
    _patch_db(
        monkeypatch,
        _connected_config(access_token="AT", access_token_expires_at="2999-01-01T00:00:00+00:00"),
    )
    seen = {}

    def handler(method, url, kwargs):
        seen["url"] = url
        seen["params"] = kwargs.get("params") or {}
        return _FakeResponse(200, {"count": 42})

    _patch_http(monkeypatch, handler)
    out = await zoho.ticket_count(department_id="dep1")
    assert out == 42
    assert "ticketsCount" in seen["url"]
    assert "status" not in seen["params"]
    assert seen["params"].get("departmentId") == "dep1"


@pytest.mark.anyio
async def test_list_tickets_uses_departmentids_and_created_sort(monkeypatch):
    # Codex P2: List Tickets filters by `departmentIds` (plural); singular is
    # ignored. Default sort must be a supported field (`-createdTime`).
    _patch_db(
        monkeypatch,
        _connected_config(access_token="AT", access_token_expires_at="2999-01-01T00:00:00+00:00"),
    )
    seen = {}

    def handler(method, url, kwargs):
        seen["params"] = kwargs.get("params") or {}
        return _FakeResponse(200, {"data": []})

    _patch_http(monkeypatch, handler)
    await zoho.list_tickets(department_id="dep1")
    assert seen["params"].get("departmentIds") == "dep1"
    assert "departmentId" not in seen["params"]
    assert seen["params"].get("sortBy") == "-createdTime"


@pytest.mark.anyio
async def test_send_reply_includes_from_email(monkeypatch):
    # Codex P2: EMAIL replies must carry `fromEmailAddress` or Zoho rejects them.
    _patch_db(
        monkeypatch,
        _connected_config(access_token="AT", access_token_expires_at="2999-01-01T00:00:00+00:00"),
    )
    seen = {}

    def handler(method, url, kwargs):
        seen["body"] = kwargs.get("json") or {}
        return _FakeResponse(200, {"id": "r1"})

    _patch_http(monkeypatch, handler)
    await zoho.send_reply("t1", content="hi", to="x@y.ca", from_email="support@spinr.ca")
    assert seen["body"]["fromEmailAddress"] == "support@spinr.ca"
    assert seen["body"]["to"] == "x@y.ca"


@pytest.mark.anyio
async def test_lost_and_found_autocreate_happy(monkeypatch):
    import services.zoho_desk_integration as integ

    db = MagicMock()
    db.find_one = AsyncMock(
        side_effect=[
            {"id": "default", "enabled": True},  # config
            {"id": "u1", "name": "Jane Doe", "email": "j@x.ca", "phone": "+1"},  # rider
        ]
    )
    db.update_one = AsyncMock()
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt1"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_ticket_for_lost_and_found(
        {
            "id": "c1",
            "rider_user_id": "u1",
            "item_description": "Wallet",
            "item_category": "bag",
            "ride_id": "r1",
            "reporter_type": "driver",
        },
        {"ride_code": "SPN-1"},
    )
    created.assert_awaited_once()
    kwargs = created.call_args.kwargs
    assert kwargs["category"] == "Lost & Found"
    assert kwargs["email"] == "j@x.ca" and kwargs["first_name"] == "Jane"
    # linked the ticket id back onto the case
    db.update_one.assert_awaited_once()
    assert db.update_one.call_args.args[2] == {"zoho_ticket_id": "zt1"}


@pytest.mark.anyio
async def test_lost_and_found_autocreate_skips(monkeypatch):
    import services.zoho_desk_integration as integ

    db = MagicMock()
    db.find_one = AsyncMock(return_value={"id": "default", "enabled": False})
    db.update_one = AsyncMock()
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock()
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    # disabled integration -> no ticket
    await integ.create_ticket_for_lost_and_found({"id": "c1"}, None)
    created.assert_not_awaited()

    # already linked -> no ticket (and never even checks config)
    await integ.create_ticket_for_lost_and_found({"id": "c1", "zoho_ticket_id": "zt9"}, None)
    created.assert_not_awaited()


@pytest.mark.anyio
async def test_dispute_autocreate_happy(monkeypatch):
    import services.zoho_desk_integration as integ

    db = MagicMock()
    db.find_one = AsyncMock(
        side_effect=[
            {"id": "default", "enabled": True},  # config
            {"id": "u1", "name": "Sam Rider", "email": "s@x.ca"},  # user
        ]
    )
    db.update_one = AsyncMock()
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt7"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_ticket_for_dispute(
        {
            "id": "d1",
            "user_id": "u1",
            "reason": "Overcharged",
            "description": "x",
            "requested_amount": 12,
            "original_fare": 20,
            "ride_id": "r1",
        },
        {"ride_code": "SPN-9"},
    )
    created.assert_awaited_once()
    assert created.call_args.kwargs["category"] == "Payment"
    assert created.call_args.kwargs["priority"] == "High"
    assert db.update_one.call_args.args[2] == {"zoho_ticket_id": "zt7"}


@pytest.mark.anyio
async def test_support_escalation(monkeypatch):
    import services.zoho_desk_integration as integ
    from services.zoho_desk_service import ZohoDeskError as ZErr

    db = MagicMock()
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt8", "ticketNumber": "555"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    # disabled -> raises (route turns this into the fallback reply)
    db.find_one = AsyncMock(return_value={"id": "default", "enabled": False})
    with pytest.raises(ZErr):
        await integ.create_support_ticket(user={"id": "u1", "email": "a@b.ca"}, message="help")
    created.assert_not_awaited()

    # enabled -> creates a Chat-channel ticket
    db.find_one = AsyncMock(return_value={"id": "default", "enabled": True})
    res = await integ.create_support_ticket(
        user={"id": "u1", "email": "a@b.ca", "name": "Al B"}, message="Card declined\nplease help"
    )
    assert res["ticketNumber"] == "555"
    assert created.call_args.kwargs["channel"] == "Chat"
    assert created.call_args.kwargs["subject"].startswith("Support — Card declined")


@pytest.mark.anyio
async def test_complaint_and_safety_autocreate(monkeypatch):
    import services.zoho_desk_integration as integ

    db = MagicMock()
    db.find_one = AsyncMock(return_value={"id": "default", "enabled": True})
    db.update_one = AsyncMock()
    monkeypatch.setattr(integ, "db_supabase", db)
    created = AsyncMock(return_value={"id": "zt1"})
    monkeypatch.setattr(integ.zoho, "create_ticket", created)

    await integ.create_ticket_for_safety(
        {"id": "s1", "category": "assault", "role": "rider", "reported_by_user_id": None}
    )
    assert created.call_args.kwargs["category"] == "Safety"
    assert created.call_args.kwargs["priority"] == "Urgent"
    db.update_one.assert_awaited()  # linked back to safety_incidents


@pytest.mark.anyio
async def test_reverse_close_links(monkeypatch):
    import services.zoho_desk_integration as integ

    db = MagicMock()
    # complaints has one open row linked to a closed ticket; flags one active row
    async def _get_rows(table, *a, **k):
        if table == "complaints":
            return [{"id": "c1", "status": "open", "zoho_ticket_id": "zt1"}]
        if table == "flags":
            return [{"id": "f1", "is_active": True, "zoho_ticket_id": "zt1"}]
        return []

    db.get_rows = AsyncMock(side_effect=_get_rows)
    db.update_one = AsyncMock()
    monkeypatch.setattr(integ, "db_supabase", db)

    await integ.close_linked_records(["zt1"])
    calls = {c.args[0]: c.args[2] for c in db.update_one.call_args_list}
    assert calls["complaints"]["status"] == "resolved"
    assert calls["flags"] == {"is_active": False}


def test_parse_zoho_time_window():
    # Codex P2: the trends `days` selector must actually filter by created
    # time. The route helper parses Zoho timestamps; unparseable -> None.
    from routes.admin.support_tickets import _parse_zoho_time

    dt = _parse_zoho_time("2026-06-01T12:00:00.000Z")
    assert dt is not None and dt.tzinfo is not None
    assert _parse_zoho_time("") is None
    assert _parse_zoho_time("not-a-date") is None
