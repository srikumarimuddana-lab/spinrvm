"""Insurance-period correctness of the driver online/offline toggle.

Pins the 2026-08-19 insurance-audit blocker fixes in routes/drivers/status.py:

  1. Go-offline is refused (409) for EVERY active ride status including
     driver_assigned — the driver is already obligated at assignment
     (Period 2 starts there per CLAUDE.md), so allowing offline would drop
     them to Period 0 personal-auto coverage mid-obligation.
  2. A go-online flip while a busy ride exists opens the ride-derived
     period (3 for in_progress with ride_id, 2 for assigned/accepted/
     arrived) — never a blanket Period 1 while a passenger or assignment
     is in play.
  3. A rideless flip still records 1 (online) / 0 (offline) unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

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


def _patches(*, current_online: bool, requested_online: bool, busy_rides: list):
    """Common mock stack. get_driver_by_id serves the pre-write row first,
    then the post-write verify row already flipped to the requested state."""
    period_mock = AsyncMock()

    async def _get_rows(table, filters=None, **kw):
        if table == "rides":
            return busy_rides
        if table in ("driver_documents", "ride_offers", "service_areas", "settings", "app_settings"):
            return []
        raise AssertionError(f"unexpected table {table}")

    return (
        period_mock,
        (
            patch.object(
                status_mod.db_supabase,
                "get_driver_by_id",
                AsyncMock(side_effect=[_driver(current_online), _driver(requested_online)]),
            ),
            patch.object(status_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(status_mod.db_supabase, "update_one", AsyncMock(return_value={"id": "drv-1"})),
            patch.object(status_mod._deps, "record_period_transition", period_mock),
            patch.object(status_mod._deps, "mark_present", AsyncMock()),
            patch.object(status_mod._deps, "clear_presence", AsyncMock()),
            patch.object(status_mod, "reset_miss_streak", AsyncMock()),
            patch("utils.dual_run_monitor.record_go_online_flip", AsyncMock()),
        ),
    )


async def _toggle(is_online: bool):
    return await status_mod.update_driver_status(
        driver_id="drv-1", is_online=is_online, lat=None, lng=None, current_user=USER
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "active_status",
    ["driver_assigned", "driver_accepted", "driver_arrived", "in_progress"],
)
async def test_go_offline_409s_for_every_active_ride_status(active_status):
    """driver_assigned was previously missing from the guard — a driver
    could go offline (Period 0) while already obligated to a ride."""
    period_mock, patches = _patches(
        current_online=True,
        requested_online=False,
        busy_rides=[{"id": "ride-1", "status": active_status}],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        with pytest.raises(HTTPException) as exc:
            await _toggle(False)

    assert exc.value.status_code == 409
    period_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_go_online_with_in_progress_ride_opens_period_3():
    period_mock, patches = _patches(
        current_online=False,
        requested_online=True,
        busy_rides=[{"id": "ride-1", "status": "in_progress"}],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        result = await _toggle(True)

    assert result["success"] is True
    period_mock.assert_awaited_once_with("drv-1", 3, ride_id="ride-1")


@pytest.mark.anyio
async def test_go_online_with_accepted_ride_opens_period_2():
    period_mock, patches = _patches(
        current_online=False,
        requested_online=True,
        busy_rides=[{"id": "ride-1", "status": "driver_accepted"}],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        await _toggle(True)

    period_mock.assert_awaited_once_with("drv-1", 2, ride_id="ride-1")


@pytest.mark.anyio
async def test_rideless_flips_still_record_1_and_0():
    period_mock, patches = _patches(current_online=False, requested_online=True, busy_rides=[])
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        await _toggle(True)
    period_mock.assert_awaited_once_with("drv-1", 1)

    period_mock, patches = _patches(current_online=True, requested_online=False, busy_rides=[])
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        await _toggle(False)
    period_mock.assert_awaited_once_with("drv-1", 0)
