"""Tests for the LMS training integration.

Covers the admin endpoint GET /api/admin/drivers/{driver_id}/training and the
lms_service phone normalization / upstream error mapping.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from routes.admin import drivers as admin_drivers
from services import lms_service

LMS_DATA = {
    "driver": {"id": "rec-1", "full_name": "Alex Driver"},
    "training": {
        "status": "in_progress",
        "registered": True,
        "completion_percentage": 60,
        "courses": [],
    },
    "certificates": [],
    "history": {"quiz_attempts": [], "communications": []},
}


# ---------- lms_service.normalize_phone ----------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+13065551234", "3065551234"),
        ("13065551234", "3065551234"),
        ("306-555-1234", "3065551234"),
        ("(306) 555 1234", "3065551234"),
        ("3065551234", "3065551234"),
        ("555-1234", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_phone(raw, expected):
    assert lms_service.normalize_phone(raw) == expected


# ---------- admin endpoint ----------


def _driver(**overrides):
    return {"id": "driver-1", "user_id": "user-1", "phone": None, **overrides}


async def test_training_endpoint_returns_lms_payload():
    payload = {"success": True, "matched": True, "data": LMS_DATA}

    with (
        patch.object(
            admin_drivers.db_supabase,
            "get_driver_by_id",
            new=AsyncMock(return_value=_driver()),
        ),
        patch.object(
            admin_drivers.db_supabase,
            "get_user_by_id",
            new=AsyncMock(return_value={"id": "user-1", "phone": "+13065551234"}),
        ),
        patch.object(
            admin_drivers.lms_service,
            "get_training_by_phone",
            new=AsyncMock(return_value=payload),
        ) as fetch,
    ):
        result = await admin_drivers.admin_get_driver_training("driver-1", refresh=False)

    assert result["matched"] is True
    assert result["reason"] is None
    assert result["phone_last4"] == "1234"
    assert result["lms"] == LMS_DATA
    fetch.assert_awaited_once_with("+13065551234", force_refresh=False)


async def test_training_endpoint_404_for_unknown_driver():
    with patch.object(admin_drivers.db_supabase, "get_driver_by_id", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await admin_drivers.admin_get_driver_training("nope", refresh=False)
    assert exc.value.status_code == 404


async def test_training_endpoint_no_phone_returns_unmatched():
    with (
        patch.object(
            admin_drivers.db_supabase,
            "get_driver_by_id",
            new=AsyncMock(return_value=_driver()),
        ),
        patch.object(
            admin_drivers.db_supabase,
            "get_user_by_id",
            new=AsyncMock(return_value={"id": "user-1", "phone": None}),
        ),
        patch.object(admin_drivers.lms_service, "get_training_by_phone", new=AsyncMock()) as fetch,
    ):
        result = await admin_drivers.admin_get_driver_training("driver-1", refresh=False)

    assert result == {"matched": False, "reason": "no_phone", "phone_last4": None, "lms": None}
    fetch.assert_not_awaited()


async def test_training_endpoint_falls_back_to_driver_phone():
    """users.phone is the source of truth, but drivers.phone is the fallback."""
    with (
        patch.object(
            admin_drivers.db_supabase,
            "get_driver_by_id",
            new=AsyncMock(return_value=_driver(phone="3065559999")),
        ),
        patch.object(
            admin_drivers.db_supabase,
            "get_user_by_id",
            new=AsyncMock(return_value={"id": "user-1", "phone": None}),
        ),
        patch.object(
            admin_drivers.lms_service,
            "get_training_by_phone",
            new=AsyncMock(return_value={"success": True, "matched": False, "data": None}),
        ) as fetch,
    ):
        result = await admin_drivers.admin_get_driver_training("driver-1", refresh=False)

    assert result["matched"] is False
    assert result["reason"] == "not_found_in_lms"
    assert result["phone_last4"] == "9999"
    fetch.assert_awaited_once_with("3065559999", force_refresh=False)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (lms_service.LMSNotConfiguredError("no creds"), 503),
        (lms_service.LMSUpstreamError("LMS API error (HTTP 500)"), 502),
    ],
)
async def test_training_endpoint_maps_lms_errors(error, status):
    with (
        patch.object(
            admin_drivers.db_supabase,
            "get_driver_by_id",
            new=AsyncMock(return_value=_driver()),
        ),
        patch.object(
            admin_drivers.db_supabase,
            "get_user_by_id",
            new=AsyncMock(return_value={"id": "user-1", "phone": "+13065551234"}),
        ),
        patch.object(
            admin_drivers.lms_service,
            "get_training_by_phone",
            new=AsyncMock(side_effect=error),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await admin_drivers.admin_get_driver_training("driver-1", refresh=False)
    assert exc.value.status_code == status


# ---------- lms_service.get_training_by_phone ----------


def _mock_transport(handler):
    return httpx.MockTransport(handler)


async def test_get_training_by_phone_fetches_and_caches():
    payload = {"success": True, "matched": True, "data": LMS_DATA}
    cache: dict[str, str] = {}

    async def fake_redis_get(key):
        return cache.get(key)

    async def fake_redis_set(key, value, ttl=None):
        cache[key] = value

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json=payload)

    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_client(**kwargs)

    with (
        patch.object(
            lms_service,
            "get_app_settings",
            new=AsyncMock(
                return_value={
                    "lms_api_base_url": "https://training.spinr.ca/",
                    "lms_api_key": "secret-key",
                }
            ),
        ),
        patch.object(lms_service, "redis_get", new=fake_redis_get),
        patch.object(lms_service, "redis_set", new=fake_redis_set),
        patch.object(lms_service.httpx, "AsyncClient", new=client_factory),
    ):
        result = await lms_service.get_training_by_phone("+13065551234")

    assert result == payload
    # Trailing slash stripped; phone sent as normalized 10 digits.
    assert captured["url"] == ("https://training.spinr.ca/api/integration/driver-training?phone=3065551234")
    assert captured["api_key"] == "secret-key"
    # Cached under a hashed key — the raw phone never appears in the key.
    assert len(cache) == 1
    cache_key = next(iter(cache))
    assert "3065551234" not in cache_key
    assert json.loads(cache[cache_key]) == payload


async def test_get_training_by_phone_uses_cache():
    payload = {"success": True, "matched": False, "data": None}
    key = lms_service._cache_key("3065551234")

    with (
        patch.object(lms_service, "redis_get", new=AsyncMock(return_value=json.dumps(payload))) as rget,
        patch.object(lms_service, "get_app_settings", new=AsyncMock()) as settings,
    ):
        result = await lms_service.get_training_by_phone("+13065551234")

    assert result == payload
    rget.assert_awaited_once_with(key)
    settings.assert_not_awaited()  # cache hit → no upstream call


async def test_get_training_by_phone_not_configured():
    with (
        patch.object(lms_service, "redis_get", new=AsyncMock(return_value=None)),
        patch.object(lms_service, "get_app_settings", new=AsyncMock(return_value={})),
    ):
        with pytest.raises(lms_service.LMSNotConfiguredError):
            await lms_service.get_training_by_phone("+13065551234")


async def test_get_training_by_phone_rejects_unpinned_lms_host(monkeypatch):
    """Do not send the LMS API key to a host controlled via app_settings."""
    monkeypatch.delenv("LMS_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("ENV", "production")

    async def fail_if_called(*args, **kwargs):  # pragma: no cover - should never run
        raise AssertionError("HTTP client must not be opened for an unpinned host")

    with (
        patch.object(lms_service, "redis_get", new=AsyncMock(return_value=None)),
        patch.object(
            lms_service,
            "get_app_settings",
            new=AsyncMock(
                return_value={
                    "lms_api_base_url": "https://evil.example.com",
                    "lms_api_key": "secret-key",
                }
            ),
        ),
        patch.object(lms_service.httpx, "AsyncClient", new=fail_if_called),
    ):
        with pytest.raises(lms_service.LMSNotConfiguredError):
            await lms_service.get_training_by_phone("+13065551234")


def test_lms_allowed_hosts_can_be_pinned_from_environment(monkeypatch):
    monkeypatch.setenv("LMS_ALLOWED_HOSTS", "training2.spinr.ca, staging-training.spinr.ca")

    assert lms_service._validate_lms_base_url("https://training2.spinr.ca/") == "https://training2.spinr.ca"


async def test_get_training_by_phone_upstream_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_client(**kwargs)

    with (
        patch.object(lms_service, "redis_get", new=AsyncMock(return_value=None)),
        patch.object(
            lms_service,
            "get_app_settings",
            new=AsyncMock(
                return_value={
                    "lms_api_base_url": "https://training.spinr.ca",
                    "lms_api_key": "secret-key",
                }
            ),
        ),
        patch.object(lms_service.httpx, "AsyncClient", new=client_factory),
    ):
        with pytest.raises(lms_service.LMSUpstreamError):
            await lms_service.get_training_by_phone("+13065551234")


async def test_get_training_by_phone_rejects_short_phone():
    with pytest.raises(ValueError):
        await lms_service.get_training_by_phone("555-1234")


def _patched_lms_call(handler):
    """Common patch stack for a live (cache-miss) lms_service call."""
    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_client(**kwargs)

    return (
        patch.object(lms_service, "redis_get", new=AsyncMock(return_value=None)),
        patch.object(lms_service, "redis_set", new=AsyncMock()),
        patch.object(
            lms_service,
            "get_app_settings",
            new=AsyncMock(
                return_value={
                    "lms_api_base_url": "https://training.spinr.ca",
                    "lms_api_key": "secret-key",
                }
            ),
        ),
        patch.object(lms_service.httpx, "AsyncClient", new=client_factory),
    )


async def test_get_training_by_phone_non_json_200_is_upstream_error():
    """A 200 with a non-JSON body must map to 502, not crash as a 500."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>proxy error page</html>")

    p1, p2, p3, p4 = _patched_lms_call(handler)
    with p1, p2, p3, p4:
        with pytest.raises(lms_service.LMSUpstreamError):
            await lms_service.get_training_by_phone("+13065551234")


async def test_get_training_by_phone_success_false_is_upstream_error():
    """An application-level LMS failure must not read as not_found_in_lms."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error": "boom"})

    p1, p2, p3, p4 = _patched_lms_call(handler)
    with p1, p2, p3, p4:
        with pytest.raises(lms_service.LMSUpstreamError):
            await lms_service.get_training_by_phone("+13065551234")


async def test_upstream_error_logs_never_contain_phone(caplog):
    """PIPEDA: logs must not carry the phone (query URL / echoed body)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="error for phone 3065551234")

    p1, p2, p3, p4 = _patched_lms_call(handler)
    with p1, p2, p3, p4, caplog.at_level("ERROR"):
        with pytest.raises(lms_service.LMSUpstreamError):
            await lms_service.get_training_by_phone("+13065551234")

    assert "3065551234" not in caplog.text
