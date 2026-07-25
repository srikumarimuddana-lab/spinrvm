"""The location-batch endpoint accumulates Period-1 deadhead distance only when
enabled, online, and with no active ride — and stores a scalar, never coords."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

USER_ID = "user_p1"


def _driver(**over):
    d = {"id": "drv_p1", "user_id": USER_ID, "is_online": True, "period1_accum_km": 0, "period1_accum_since": None}
    d.update(over)
    return d


def _moving_batch():
    return [
        {"latitude": 50.4400, "longitude": -104.6200, "accuracy": 8},
        {"latitude": 50.4410, "longitude": -104.6200, "accuracy": 8},
        {"latitude": 50.4420, "longitude": -104.6200, "accuracy": 8},
    ]


def _call(batch, *, flag, active_ride, driver):
    from backend.routes import drivers as drv

    captured = {}

    async def _update_one(table, filt, data, *a, **k):
        if table == "drivers":
            captured.update(data)
        return driver

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
        patch("backend.routes.drivers._deps.mark_present", AsyncMock()),
        patch("backend.utils.location_integrity.check_location_integrity", AsyncMock(return_value=(True, None))),
        # Imported inline inside the handler, so patch the source modules.
        patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(return_value={"period1_distance_tracking_enabled": flag}),
        ),
        patch("backend.utils.breadcrumbs.resolve_active_ride", AsyncMock(return_value=active_ride)),
    ):
        asyncio.run(drv.update_location_batch(batch=batch, current_user={"id": USER_ID}))
    return captured


def test_accumulates_when_enabled_online_no_ride():
    captured = _call(_moving_batch(), flag=True, active_ride=None, driver=_driver())
    assert captured.get("period1_accum_km", 0) > 0.1
    assert captured.get("period1_accum_since") is not None


def test_since_not_reset_when_already_set():
    captured = _call(
        _moving_batch(), flag=True, active_ride=None, driver=_driver(period1_accum_km=2.0, period1_accum_since="t0")
    )
    # Adds to the existing total and leaves the span start untouched.
    assert captured["period1_accum_km"] > 2.0
    assert "period1_accum_since" not in captured


def test_skips_when_flag_off():
    captured = _call(_moving_batch(), flag=False, active_ride=None, driver=_driver())
    assert "period1_accum_km" not in captured


def test_skips_when_on_active_ride():
    captured = _call(_moving_batch(), flag=True, active_ride={"id": "ride_1"}, driver=_driver())
    assert "period1_accum_km" not in captured


def test_skips_when_offline():
    captured = _call(_moving_batch(), flag=True, active_ride=None, driver=_driver(is_online=False))
    assert "period1_accum_km" not in captured
