"""#5357: go-online must stamp location_captured_at alongside lat/lng.

drivers.updated_at is bumped on every go-online/go-offline call regardless
of whether a fresh GPS fix came with it. Before this fix, a go-online call
that DID supply lat/lng only wrote lat/lng + updated_at — never
location_captured_at, the sensor-timestamp column
utils/breadcrumbs.py's GPS-plausibility chain seeds from
(routes/drivers/location.py's driver_last_known dict). That let a stale
lat/lng pair with a just-now updated_at, understating elapsed_seconds
enough to falsely reject the next trip's first real breadcrumb as a
teleport. See docs/change-log/... and issue #5357.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from backend.routes.drivers import status as status_mod
except ImportError:
    from routes.drivers import status as status_mod  # type: ignore

USER = {"id": "u1"}


def _driver(is_online: bool) -> dict:
    return {
        "id": "drv-1",
        "user_id": "u1",
        "status": "active",
        "is_verified": True,
        "is_online": is_online,
        "service_area_id": None,
    }


def _patches(*, current_online: bool, requested_online: bool, update_one: AsyncMock):
    async def _get_rows(table, filters=None, **kw):
        if table == "rides":
            return []
        if table in (
            "driver_documents",
            "ride_offers",
            "service_areas",
            "settings",
            "app_settings",
            "legal_documents",
        ):
            return []
        raise AssertionError(f"unexpected table {table}")

    return (
        patch.object(
            status_mod.db_supabase,
            "get_driver_by_id",
            AsyncMock(side_effect=[_driver(current_online), _driver(requested_online)]),
        ),
        patch.object(status_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(status_mod.db_supabase, "update_one", update_one),
        patch.object(status_mod._deps, "record_period_transition", AsyncMock()),
        patch.object(status_mod._deps, "mark_present", AsyncMock()),
        patch.object(status_mod._deps, "clear_presence", AsyncMock()),
        patch.object(status_mod, "reset_miss_streak", AsyncMock()),
        patch("utils.dual_run_monitor.record_go_online_flip", AsyncMock()),
    )


@pytest.mark.anyio
async def test_go_online_with_fresh_fix_stamps_location_captured_at():
    update_one = AsyncMock(return_value={"id": "drv-1"})
    patches = _patches(current_online=False, requested_online=True, update_one=update_one)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        await status_mod.update_driver_status(
            driver_id="drv-1", is_online=True, lat=52.1332, lng=-106.6700, current_user=USER
        )

    written = update_one.await_args_list[0].args[2]
    assert written["lat"] == 52.1332
    assert written["lng"] == -106.6700
    assert "location_captured_at" in written
    assert written["location_captured_at"] == written["updated_at"]


@pytest.mark.anyio
async def test_go_online_without_a_fix_does_not_touch_location_captured_at():
    """Cold start / permission dialog not yet granted: no lat/lng supplied,
    so the previous position must not be re-stamped as if it were fresh."""
    update_one = AsyncMock(return_value={"id": "drv-1"})
    patches = _patches(current_online=False, requested_online=True, update_one=update_one)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        await status_mod.update_driver_status(driver_id="drv-1", is_online=True, lat=None, lng=None, current_user=USER)

    written = update_one.await_args_list[0].args[2]
    assert "lat" not in written
    assert "lng" not in written
    assert "location_captured_at" not in written


@pytest.mark.anyio
async def test_go_offline_does_not_touch_location_captured_at():
    update_one = AsyncMock(return_value={"id": "drv-1"})
    patches = _patches(current_online=True, requested_online=False, update_one=update_one)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        await status_mod.update_driver_status(
            driver_id="drv-1", is_online=False, lat=52.1332, lng=-106.6700, current_user=USER
        )

    written = update_one.await_args_list[0].args[2]
    assert "lat" not in written
    assert "lng" not in written
    assert "location_captured_at" not in written


@pytest.mark.anyio
async def test_pgrst204_retry_drops_location_captured_at_too():
    """If location_captured_at (not just the intent-timestamp columns the
    existing PGRST204 fallback was written for) is itself the missing
    column on some environment's schema, the retry payload must not still
    carry it — otherwise the retry hits the identical PGRST204 and raises
    unhandled instead of degrading gracefully."""
    first_call_error = RuntimeError("PGRST204: column location_captured_at does not exist")
    update_one = AsyncMock(side_effect=[first_call_error, {"id": "drv-1"}])
    patches = _patches(current_online=False, requested_online=True, update_one=update_one)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        await status_mod.update_driver_status(
            driver_id="drv-1", is_online=True, lat=52.1332, lng=-106.6700, current_user=USER
        )

    assert update_one.await_count == 2
    retried = update_one.await_args_list[1].args[2]
    assert "location_captured_at" not in retried
    assert retried["lat"] == 52.1332
