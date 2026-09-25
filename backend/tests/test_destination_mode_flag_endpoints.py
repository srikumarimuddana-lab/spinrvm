"""C136 / migration 482 — driver destination endpoints behind
``settings.destination_mode_enabled`` (default off), plus usage audit rows.

Flag-ON behaviour (TTL stamping, ``active`` computation) is pinned by
``TestDestinationMode`` in test_drivers_extended.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.anyio

USER = {"id": "user-dest-1", "role": "driver"}
DRIVER_ID = "drv-dest-1"


def _driver(**extra) -> dict:
    d = {"id": DRIVER_ID, "user_id": USER["id"], "destination_mode": False}
    d.update(extra)
    return d


def _active_driver() -> dict:
    now = datetime.now(timezone.utc)
    return _driver(
        destination_mode=True,
        destination_address="123 Example St",
        destination_lat=52.15,
        destination_lng=-106.65,
        destination_set_at=now.isoformat(),
        destination_expires_at=(now + timedelta(hours=1)).isoformat(),
    )


def _patches(settings: dict | Exception, driver: dict | None):
    settings_mock = (
        AsyncMock(side_effect=settings) if isinstance(settings, Exception) else AsyncMock(return_value=settings)
    )
    return (
        patch("backend.routes.drivers.profile.get_app_settings", settings_mock),
        patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=driver)),
        patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=driver)),
        patch("backend.routes.drivers.profile.log_user_action", AsyncMock()),
    )


async def _call(fn_name: str, settings, driver, **kwargs):
    from backend.routes.drivers import profile

    p_settings, p_find, p_update, p_audit = _patches(settings, driver)
    with p_settings, p_find, p_update as update, p_audit as audit:
        try:
            result = await getattr(profile, fn_name)(current_user=USER, **kwargs)
        except HTTPException as e:
            result = e
    return result, update, audit


def _req():
    from backend.routes.drivers.profile import SetDestinationRequest

    return SetDestinationRequest(address="123 Example St", lat=52.15, lng=-106.65)


# ── flag OFF (default) ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "settings",
    [{}, {"destination_mode_enabled": False}, {"destination_mode_enabled": None}, {"destination_mode_enabled": "true"}],
)
async def test_set_refused_when_switch_off(settings):
    result, update, audit = await _call("set_destination_mode", settings, _driver(), req=_req())
    assert isinstance(result, HTTPException)
    assert result.status_code == 409
    assert result.detail == "Destination mode is not available."
    update.assert_not_awaited()
    audit.assert_not_awaited()


async def test_get_reports_disabled_and_inactive_even_with_live_row():
    result, _, _ = await _call("get_destination_mode", {}, _active_driver())
    assert result["enabled"] is False
    assert result["active"] is False
    # Raw stored flag still reported so the app can offer a clear.
    assert result["destination_mode"] is True


async def test_clear_still_works_when_switch_off():
    result, update, audit = await _call("clear_destination_mode", {}, _active_driver())
    assert result == {"success": True, "destination_mode": False}
    payload = update.await_args.args[2]
    assert payload["destination_mode"] is False
    assert payload["destination_lat"] is None and payload["destination_lng"] is None
    audit.assert_awaited_once()


async def test_settings_read_failure_is_503_not_silently_off():
    result, update, _ = await _call("set_destination_mode", RuntimeError("db down"), _driver(), req=_req())
    assert isinstance(result, HTTPException)
    assert result.status_code == 503
    update.assert_not_awaited()


# ── flag ON ───────────────────────────────────────────────────────────

ON = {"destination_mode_enabled": True}


async def test_get_reports_enabled_when_switch_on():
    result, _, _ = await _call("get_destination_mode", ON, _active_driver())
    assert result["enabled"] is True
    assert result["active"] is True


async def test_set_writes_audit_row_with_ids_only():
    result, update, audit = await _call("set_destination_mode", ON, _driver(), req=_req())
    assert result["success"] is True
    update.assert_awaited_once()
    audit.assert_awaited_once()
    user, action, resource, resource_id, details = audit.await_args.args
    assert user is USER
    assert (action, resource, resource_id) == ("driver_destination_mode_set", "drivers", DRIVER_ID)
    assert set(details) == {"destination_expires_at"}
    flat = repr(audit.await_args)
    for leaked in ("123 Example St", "52.15", "-106.65"):
        assert leaked not in flat


async def test_clear_writes_audit_row_with_ids_only():
    _, _, audit = await _call("clear_destination_mode", ON, _active_driver())
    audit.assert_awaited_once()
    user, action, resource, resource_id, details = audit.await_args.args
    assert (action, resource, resource_id) == ("driver_destination_mode_cleared", "drivers", DRIVER_ID)
    assert details == {"was_destination_mode": True, "was_active": True}
    flat = repr(audit.await_args)
    for leaked in ("123 Example St", "52.15", "-106.65"):
        assert leaked not in flat
