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


# ── v2 availability path (driver_availability_v2_enabled) ──────────────────
# Found in Ravi's review of Surya's T1: the v2 go_offline goes through
# driver_availability_service and returned before the legacy write, so it
# never cleared destination mode. Default-off flag, but "going offline clears
# it" must hold on both paths.


def _v2_patches(*, update_one: AsyncMock, change: AsyncMock):
    base = _patches(current_online=True, requested_online=False, update_one=update_one)
    return (
        *base,
        patch.object(status_mod, "_availability_v2_enabled", AsyncMock(return_value=True)),
        patch.object(status_mod, "_change_availability_status", change),
        patch.object(status_mod, "_finish_v2_status", AsyncMock(return_value={"ok": True})),
    )


async def _call_v2(action: str):
    return await status_mod.update_driver_status(
        driver_id="drv-1",
        is_online=False,
        lat=None,
        lng=None,
        online_epoch="ep-1",
        request_id="req-1",
        availability_action=action,
        current_user=USER,
    )


@pytest.mark.anyio
async def test_v2_go_offline_clears_destination_mode():
    update_one = AsyncMock(return_value={"id": "drv-1"})
    change = AsyncMock(return_value={"code": "OK"})
    ps = _v2_patches(update_one=update_one, change=change)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8], ps[9], ps[10]:
        assert await _call_v2("go_offline") == {"ok": True}

    change.assert_awaited_once()
    dest_writes = [c for c in update_one.await_args_list if "destination_mode" in c.args[2]]
    assert len(dest_writes) == 1
    table, filt, payload = dest_writes[0].args
    assert table == "drivers" and filt == {"id": "drv-1"}
    assert payload["destination_mode"] is False
    for col in _DEST_COLS:
        assert payload[col] is None, col


@pytest.mark.anyio
async def test_v2_stop_requests_keeps_destination_mode():
    update_one = AsyncMock(return_value={"id": "drv-1"})
    change = AsyncMock(return_value={"code": "OK"})
    ps = _v2_patches(update_one=update_one, change=change)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8], ps[9], ps[10]:
        await _call_v2("stop_requests")
    assert not [c for c in update_one.await_args_list if "destination_mode" in c.args[2]]


@pytest.mark.anyio
async def test_v2_go_offline_destination_clear_failure_does_not_fail_offline(caplog):
    update_one = AsyncMock(side_effect=RuntimeError("db down"))
    change = AsyncMock(return_value={"code": "OK"})
    ps = _v2_patches(update_one=update_one, change=change)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8], ps[9], ps[10]:
        assert await _call_v2("go_offline") == {"ok": True}
    assert any("failed to clear destination mode" in r.getMessage() for r in caplog.records)


# ── #5781 finding 3: fence the v2 destination-clear write on online_epoch ──
# A driver who goes offline then immediately back online before this trailing
# write lands must not have their fresh destination wiped by the stale
# go-offline transition's clear.


def _v2_patches_with_transition(*, update_one: AsyncMock, change: AsyncMock, transition_epoch):
    base = _patches(current_online=True, requested_online=False, update_one=update_one)
    finish_result = {"ok": True, "transition": {"online_epoch": transition_epoch}}
    return (
        *base,
        patch.object(status_mod, "_availability_v2_enabled", AsyncMock(return_value=True)),
        patch.object(status_mod, "_change_availability_status", change),
        patch.object(status_mod, "_finish_v2_status", AsyncMock(return_value=finish_result)),
    )


@pytest.mark.anyio
async def test_v2_go_offline_clears_destination_when_epoch_still_current():
    """The common case: no race. The transition's own epoch is passed as the
    write filter and the clear lands normally."""
    update_one = AsyncMock(return_value={"id": "drv-1"})
    change = AsyncMock(return_value={"code": "OK"})
    ps = _v2_patches_with_transition(update_one=update_one, change=change, transition_epoch="7")
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8], ps[9], ps[10]:
        await _call_v2("go_offline")

    dest_writes = [c for c in update_one.await_args_list if "destination_mode" in c.args[2]]
    assert len(dest_writes) == 1
    table, filt, payload = dest_writes[0].args
    assert table == "drivers" and filt == {"id": "drv-1", "online_epoch": 7}
    assert payload["destination_mode"] is False


@pytest.mark.anyio
async def test_v2_go_offline_destination_clear_skipped_when_epoch_already_advanced(caplog):
    """The race: online_epoch on the driver row no longer matches the
    go-offline transition's epoch because the driver already went back online.
    The filtered write matches zero rows (update_one returns None) and the
    fresh destination set by the new session is left untouched -- logged at
    INFO, not treated as a failure."""
    update_one = AsyncMock(return_value=None)
    change = AsyncMock(return_value={"code": "OK"})
    ps = _v2_patches_with_transition(update_one=update_one, change=change, transition_epoch="7")
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8], ps[9], ps[10]:
        with caplog.at_level("INFO"):
            assert await _call_v2("go_offline") == {"ok": True, "transition": {"online_epoch": "7"}}

    dest_writes = [c for c in update_one.await_args_list if "destination_mode" in c.args[2]]
    assert len(dest_writes) == 1
    assert dest_writes[0].args[1] == {"id": "drv-1", "online_epoch": 7}
    assert any("skipped clearing destination mode" in r.getMessage() for r in caplog.records)
    assert not any("failed to clear destination mode" in r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_v2_go_offline_clear_falls_back_unfenced_when_epoch_unparsable():
    """A missing/garbage transition epoch falls back to the pre-fix
    unconditional write rather than silently never clearing."""
    update_one = AsyncMock(return_value={"id": "drv-1"})
    change = AsyncMock(return_value={"code": "OK"})
    ps = _v2_patches_with_transition(update_one=update_one, change=change, transition_epoch="not-a-number")
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8], ps[9], ps[10]:
        await _call_v2("go_offline")

    dest_writes = [c for c in update_one.await_args_list if "destination_mode" in c.args[2]]
    assert len(dest_writes) == 1
    assert dest_writes[0].args[1] == {"id": "drv-1"}
