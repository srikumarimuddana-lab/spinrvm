"""Unit + integration tests for ACTION_ITEMS.md B15(b): real on-call paging
for rider/driver SOS.

Pins:
  utils/safety_paging.py (unit):
    - No HTTP call, no exception, returns False when
      sos_paging_webhook_url is not configured (safely disabled default).
    - No HTTP call when app_settings itself fails to load (treated as
      disabled, never raised).
    - POSTs a PagerDuty Events API v2 shaped payload when configured, using
      only IDs + a geohashed area (never raw lat/lng, name, email, phone).
    - Returns True on a 2xx response.
    - Returns False (never raises) on a non-2xx response.
    - Returns False (never raises) on a transport exception (timeout, DNS,
      connection refused, ...).

  routes/rides/safety.py::trigger_emergency (integration):
    - page_on_call is invoked with the persisted incident when SOS fires.
    - A paging failure (exception raised by page_on_call) does not prevent
      the SOS response from succeeding — mirrors the existing best-effort
      posture of notify_safety_team / the admin WS broadcast.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

RIDER_ID = "rider_b15b"
DRIVER_USER_ID = "driver_user_b15b"
DRIVER_ID = "driver_row_b15b"
RIDE_ID = "ride_b15b_001"

_WEBHOOK_URL = "https://events.pagerduty.com/v2/enqueue"
# Deliberately NOT a realistic-looking secret (no hex/high-entropy shape) --
# a prior version of this fixture used a 32-char hex string that gitleaks'
# generic-api-key rule flagged as a possible real credential. This is just
# as effective for the tests below (routing key is an opaque string field,
# no format validation) without looking like real key material.
_ROUTING_KEY = "fake-test-routing-key-not-a-real-secret"


def _incident(**overrides) -> dict:
    incident = {
        "id": "incident-b15b-1",
        "ride_id": RIDE_ID,
        "reported_by_user_id": RIDER_ID,
        "role": "rider",
        "category": "sos_button",
        "description": "Emergency assistance requested",
        "status": "open",
        "latitude": 50.4452,
        "longitude": -104.6189,
        "reported_at": "2026-08-01T00:00:00+00:00",
        "created_at": "2026-08-01T00:00:00+00:00",
    }
    incident.update(overrides)
    return incident


def _make_http_mocks(post_side_effect=None, status_code: int = 202):
    """Mirrors test_loop_alert.py's httpx.AsyncClient context-manager mock."""
    inner = AsyncMock()
    if post_side_effect is not None:
        inner.post.side_effect = post_side_effect
    else:
        resp = MagicMock()
        resp.status_code = status_code
        resp.is_success = 200 <= status_code < 300
        resp.text = "" if resp.is_success else "rejected"
        inner.post.return_value = resp

    cm_mock = AsyncMock()
    cm_mock.__aenter__.return_value = inner
    return cm_mock, inner


# ─────────────────────────────────────────────────────────────────────────
# utils/safety_paging.py — unit
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_disabled_when_webhook_url_not_configured():
    import backend.utils.safety_paging as mod

    with (
        patch.object(mod, "get_app_settings", AsyncMock(return_value={})),
        patch("httpx.AsyncClient") as mock_cls,
    ):
        result = await mod.page_on_call(_incident())

    assert result is False
    mock_cls.assert_not_called()


@pytest.mark.anyio
async def test_disabled_when_app_settings_load_fails():
    """A settings-read failure is treated as disabled, never raised —
    mirrors utils/meta_capi.py's get_config."""
    import backend.utils.safety_paging as mod

    with (
        patch.object(mod, "get_app_settings", AsyncMock(side_effect=RuntimeError("settings service down"))),
        patch("httpx.AsyncClient") as mock_cls,
    ):
        result = await mod.page_on_call(_incident())

    assert result is False
    mock_cls.assert_not_called()


@pytest.mark.anyio
async def test_posts_pagerduty_shaped_payload_when_configured():
    import backend.utils.safety_paging as mod

    cm_mock, inner = _make_http_mocks(status_code=202)
    settings = {
        "sos_paging_webhook_url": _WEBHOOK_URL,
        "sos_paging_routing_key": _ROUTING_KEY,
    }

    with (
        patch.object(mod, "get_app_settings", AsyncMock(return_value=settings)),
        patch("httpx.AsyncClient", return_value=cm_mock),
    ):
        result = await mod.page_on_call(_incident())

    assert result is True
    inner.post.assert_called_once()
    args, kwargs = inner.post.call_args
    assert args[0] == _WEBHOOK_URL
    payload = kwargs["json"]

    # PagerDuty Events API v2 shape.
    assert payload["routing_key"] == _ROUTING_KEY
    assert payload["event_action"] == "trigger"
    assert "payload" in payload
    assert payload["payload"]["severity"] == "critical"

    # PIPEDA: only IDs + geohashed area, never raw lat/lng or PII.
    details = payload["payload"]["custom_details"]
    assert details["incident_id"] == "incident-b15b-1"
    assert details["ride_id"] == RIDE_ID
    assert details["reported_by_user_id"] == RIDER_ID
    assert "area_geohash" in details
    assert details["area_geohash"] != "?"  # incident has valid lat/lng
    payload_str = str(payload)
    assert "50.4452" not in payload_str
    assert "-104.6189" not in payload_str


@pytest.mark.anyio
async def test_geohash_is_placeholder_when_no_coordinates():
    import backend.utils.safety_paging as mod

    cm_mock, inner = _make_http_mocks(status_code=202)
    settings = {"sos_paging_webhook_url": _WEBHOOK_URL, "sos_paging_routing_key": _ROUTING_KEY}

    with (
        patch.object(mod, "get_app_settings", AsyncMock(return_value=settings)),
        patch("httpx.AsyncClient", return_value=cm_mock),
    ):
        await mod.page_on_call(_incident(latitude=None, longitude=None))

    _, kwargs = inner.post.call_args
    assert kwargs["json"]["payload"]["custom_details"]["area_geohash"] == "?"


@pytest.mark.anyio
async def test_returns_false_on_non_2xx_response():
    import backend.utils.safety_paging as mod

    cm_mock, _ = _make_http_mocks(status_code=400)
    settings = {"sos_paging_webhook_url": _WEBHOOK_URL, "sos_paging_routing_key": _ROUTING_KEY}

    with (
        patch.object(mod, "get_app_settings", AsyncMock(return_value=settings)),
        patch("httpx.AsyncClient", return_value=cm_mock),
    ):
        result = await mod.page_on_call(_incident())

    assert result is False


@pytest.mark.anyio
async def test_returns_false_never_raises_on_transport_exception():
    import backend.utils.safety_paging as mod

    cm_mock, _ = _make_http_mocks(post_side_effect=Exception("connection refused"))
    settings = {"sos_paging_webhook_url": _WEBHOOK_URL, "sos_paging_routing_key": _ROUTING_KEY}

    with (
        patch.object(mod, "get_app_settings", AsyncMock(return_value=settings)),
        patch("httpx.AsyncClient", return_value=cm_mock),
    ):
        result = await mod.page_on_call(_incident())  # must not raise

    assert result is False


@pytest.mark.anyio
async def test_logs_error_on_transport_exception(caplog):
    import logging

    import backend.utils.safety_paging as mod

    cm_mock, _ = _make_http_mocks(post_side_effect=Exception("connection refused"))
    settings = {"sos_paging_webhook_url": _WEBHOOK_URL, "sos_paging_routing_key": _ROUTING_KEY}

    with (
        patch.object(mod, "get_app_settings", AsyncMock(return_value=settings)),
        patch("httpx.AsyncClient", return_value=cm_mock),
        caplog.at_level(logging.ERROR, logger="backend.utils.safety_paging"),
    ):
        await mod.page_on_call(_incident())

    assert any("page request failed" in r.message for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────
# routes/rides/safety.py::trigger_emergency — integration
# ─────────────────────────────────────────────────────────────────────────


def _ride(status: str = "in_progress") -> dict:
    return {"id": RIDE_ID, "rider_id": RIDER_ID, "driver_id": DRIVER_ID, "status": status}


class _Req:
    message = "Emergency!"
    latitude = 50.4452
    longitude = -104.6189
    # Mirrors EmergencyRequest's default (migration 315). None = no dedup,
    # which is the always-insert path these paging tests exercise.
    idempotency_key = None


async def _trigger_emergency(page_on_call_mock):
    from backend.routes import rides as rides_mod

    async def _get_rows(table, query, **kwargs):
        if table == "emergency_contacts":
            return []
        return []

    with (
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
        patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.routes.rides._deps.db_supabase.insert_one", AsyncMock(return_value=None)),
        patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides._deps.db_supabase.get_user_by_id",
            AsyncMock(return_value={"first_name": "Test", "last_name": "User"}),
        ),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides._deps.send_sms", AsyncMock(return_value={"success": True})),
        patch("backend.routes.rides.safety.notify_safety_team", AsyncMock(return_value={})),
        patch("backend.routes.rides.safety.page_sos_on_call", page_on_call_mock),
    ):
        return await rides_mod.trigger_emergency(
            ride_id=RIDE_ID,
            body=_Req(),
            current_user={"id": RIDER_ID},
        )


@pytest.mark.anyio
async def test_trigger_emergency_calls_page_on_call_with_incident():
    page_mock = AsyncMock(return_value=True)

    result = await _trigger_emergency(page_mock)

    assert result["success"] is True
    page_mock.assert_called_once()
    (incident_arg,), _ = page_mock.call_args
    assert incident_arg["ride_id"] == RIDE_ID
    assert incident_arg["reported_by_user_id"] == RIDER_ID
    assert incident_arg["id"] == result["incident_id"]


@pytest.mark.anyio
async def test_paging_failure_does_not_block_sos_response():
    """A paging exception must not prevent the SOS response from
    succeeding — best-effort, non-blocking, matching notify_safety_team's
    posture in the same function."""
    page_mock = AsyncMock(side_effect=RuntimeError("PagerDuty webhook exploded"))

    result = await _trigger_emergency(page_mock)

    assert result["success"] is True
    assert "incident_id" in result
    page_mock.assert_called_once()


@pytest.mark.anyio
async def test_paging_returning_false_does_not_block_sos_response():
    """The default no-op (unconfigured) path returns False rather than
    raising; that must also leave the SOS response unaffected."""
    page_mock = AsyncMock(return_value=False)

    result = await _trigger_emergency(page_mock)

    assert result["success"] is True
