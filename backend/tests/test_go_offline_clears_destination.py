"""C136: going offline clears destination ("heading home") mode.

The go-offline write on the legacy PUT /drivers/{driver_id}/status path must
clear destination_mode + address/coords + set_at/expires_at in the SAME
update payload as went_offline_at (one write, not a second call). Going
online must never touch destination fields.

Harness mirrors tests/test_go_online_location_captured_at.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from backend.routes.drivers import status as status_mod
except ImportError:
    from routes.drivers import status as status_mod  # type: ignore

USER = {"id": "u1"}

_DEST_COLS = (
    "destination_address",
    "destination_lat",
    "destination_lng",
    "destination_set_at",
    "destination_expires_at",
)


def _driver(is_online: bool) -> dict:
    return {
        "id": "drv-1",
        "user_id": "u1",
        "status": "active",
        "is_verified": True,
        "is_online": is_online,
        "service_area_id": None,
        "destination_mode": True,
        "destination_lat": 52.5,
        "destination_lng": -106.0,
    }


def _patches(*, current_online: bool, requested_online: bool, update_one: AsyncMock):
    async def _get_rows(table, filters=None, **kw):
        if table in (
            "rides",
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
async def test_go_offline_clears_destination_mode_in_same_write():
    update_one = AsyncMock(return_value={"id": "drv-1"})
    p = _patches(current_online=True, requested_online=False, update_one=update_one)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
        await status_mod.update_driver_status(driver_id="drv-1", is_online=False, lat=None, lng=None, current_user=USER)

    written = update_one.await_args_list[0].args[2]
    # Same payload as the offline intent timestamp — one write.
    assert "went_offline_at" in written
    assert written["destination_mode"] is False
    for col in _DEST_COLS:
        assert col in written and written[col] is None, col
    # No second drivers write carrying the destination clear.
    dest_writes = [c for c in update_one.await_args_list if "destination_mode" in c.args[2]]
    assert len(dest_writes) == 1


@pytest.mark.anyio
async def test_go_online_does_not_touch_destination_fields():
    update_one = AsyncMock(return_value={"id": "drv-1"})
    p = _patches(current_online=False, requested_online=True, update_one=update_one)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
        await status_mod.update_driver_status(
            driver_id="drv-1", is_online=True, lat=52.1332, lng=-106.6700, current_user=USER
        )

    for call in update_one.await_args_list:
        payload = call.args[2]
        assert "destination_mode" not in payload
        for col in _DEST_COLS:
            assert col not in payload


@pytest.mark.anyio
async def test_go_offline_pre_migration_465_retry_still_goes_offline():
    """If migration 465 isn't applied, the combined payload 400s with PGRST204
    on destination_set_at/expires_at. The existing minimal-retry path must
    still land the offline flip (is_online=False) rather than raising."""
    err = RuntimeError("PGRST204: Could not find the 'destination_expires_at' column of 'drivers'")
    update_one = AsyncMock(side_effect=[err, {"id": "drv-1"}])
    p = _patches(current_online=True, requested_online=False, update_one=update_one)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
        await status_mod.update_driver_status(driver_id="drv-1", is_online=False, lat=None, lng=None, current_user=USER)

    assert update_one.await_count == 2
    retried = update_one.await_args_list[1].args[2]
    assert retried["is_online"] is False
    assert "destination_expires_at" not in retried
