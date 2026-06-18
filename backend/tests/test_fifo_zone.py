"""Airport FIFO queue zone detection (utils/fifo_zone.py, ADR-008).

Contract:
  * Entering a feeder-lot's radius stamps the driver into the TERMINAL queue;
    leaving all of a terminal's lots clears it.
  * Two lots feeding one terminal share one queue (entering either → same id).
  * Repeated pings inside the lot do NOT re-write (no back-of-line reset).
  * Exit uses a hysteresis band so boundary jitter doesn't flap the queue.
  * With no FIFO venue configured and an untracked driver, it never touches the DB.
  * A DB failure in the queue write never propagates into location ingestion.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import fifo_zone as fz

# A terminal venue (fifo_enabled) and two feeder lots ~300 m away pointing at it.
TERMINAL = "term-yxe"
_LOT_A = {
    "queue_for_venue_id": TERMINAL,
    "center_lat": 52.1700,
    "center_lng": -106.7000,
    "radius_m": 150,
    "is_active": True,
}
_LOT_B = {
    "queue_for_venue_id": TERMINAL,
    "center_lat": 52.1900,
    "center_lng": -106.7000,
    "radius_m": 150,
    "is_active": True,
}
# The terminal venue itself (fifo_enabled, no queue_for_venue_id), ~1.3 km south
# of the lots — this is the pickup-detection geofence dispatch resolves against.
_TERMINAL_VENUE = {
    "id": TERMINAL,
    "fifo_enabled": True,
    "center_lat": 52.1580,
    "center_lng": -106.7000,
    "radius_m": 250,
    "is_active": True,
}


@pytest.fixture(autouse=True)
def _clean_state():
    fz._reset_for_tests()
    yield
    fz._reset_for_tests()


def _patch_lots(*lots):
    return patch.object(fz.db_supabase, "get_rows", AsyncMock(return_value=list(lots)))


@pytest.mark.anyio
async def test_entering_lot_stamps_terminal_queue():
    set_zone = AsyncMock(return_value=True)
    with _patch_lots(_LOT_A, _LOT_B), patch.object(fz.db_supabase, "set_driver_zone", set_zone):
        out = await fz.update_driver_zone("drv-1", 52.1700, -106.7000)  # dead-center of lot A
    assert out == TERMINAL
    set_zone.assert_awaited_once_with("drv-1", TERMINAL)


@pytest.mark.anyio
async def test_both_lots_feed_one_shared_queue():
    set_zone = AsyncMock(return_value=True)
    with _patch_lots(_LOT_A, _LOT_B), patch.object(fz.db_supabase, "set_driver_zone", set_zone):
        a = await fz.update_driver_zone("drv-a", 52.1700, -106.7000)  # lot A
        b = await fz.update_driver_zone("drv-b", 52.1900, -106.7000)  # lot B
    assert a == b == TERMINAL  # same terminal id from different lots


@pytest.mark.anyio
async def test_repeated_pings_inside_lot_do_not_rewrite():
    set_zone = AsyncMock(return_value=True)
    with _patch_lots(_LOT_A), patch.object(fz.db_supabase, "set_driver_zone", set_zone):
        await fz.update_driver_zone("drv-1", 52.1700, -106.7000)
        await fz.update_driver_zone("drv-1", 52.1701, -106.7001)  # still inside
        await fz.update_driver_zone("drv-1", 52.1700, -106.7000)
    # Only the first ping is a transition; the rest are skipped before any write.
    set_zone.assert_awaited_once()


@pytest.mark.anyio
async def test_leaving_all_lots_clears_queue():
    set_zone = AsyncMock(return_value=True)
    with _patch_lots(_LOT_A), patch.object(fz.db_supabase, "set_driver_zone", set_zone):
        await fz.update_driver_zone("drv-1", 52.1700, -106.7000)  # enter
        out = await fz.update_driver_zone("drv-1", 52.5000, -106.7000)  # ~3.7 km away
    assert out is None
    assert set_zone.await_args_list[-1].args == ("drv-1", None)


@pytest.mark.anyio
async def test_hysteresis_keeps_driver_queued_just_outside_radius():
    # ~165 m north of lot A center: outside the 150 m radius but inside the
    # 150 * 1.15 = 172.5 m hysteresis band, so a queued driver stays queued.
    set_zone = AsyncMock(return_value=True)
    just_outside = (52.1700 + 165 / 111_320.0, -106.7000)
    with _patch_lots(_LOT_A), patch.object(fz.db_supabase, "set_driver_zone", set_zone):
        await fz.update_driver_zone("drv-1", 52.1700, -106.7000)  # enter
        out = await fz.update_driver_zone("drv-1", *just_outside)
    assert out == TERMINAL
    set_zone.assert_awaited_once()  # no exit write — hysteresis held


@pytest.mark.anyio
async def test_no_fifo_configured_is_a_no_op():
    set_zone = AsyncMock(return_value=True)
    # venues exist but none are feeder lots (no queue_for_venue_id).
    plain_venue = {
        "queue_for_venue_id": None,
        "center_lat": 52.1,
        "center_lng": -106.7,
        "radius_m": 150,
        "is_active": True,
    }
    with _patch_lots(plain_venue), patch.object(fz.db_supabase, "set_driver_zone", set_zone):
        out = await fz.update_driver_zone("drv-1", 52.1700, -106.7000)
    assert out is None
    set_zone.assert_not_awaited()


@pytest.mark.anyio
async def test_authoritative_current_zone_drives_exit_across_replicas():
    # REST path on a cold replica: in-process cache is empty, but the caller
    # passes the real zone from the driver row, so a driver who left is cleared.
    set_zone = AsyncMock(return_value=True)
    with _patch_lots(_LOT_A), patch.object(fz.db_supabase, "set_driver_zone", set_zone):
        out = await fz.update_driver_zone("drv-1", 52.5000, -106.7000, current_zone_id=TERMINAL)
    assert out is None
    set_zone.assert_awaited_once_with("drv-1", None)


@pytest.mark.anyio
async def test_db_failure_never_propagates():
    set_zone = AsyncMock(side_effect=RuntimeError("supabase down"))
    with _patch_lots(_LOT_A), patch.object(fz.db_supabase, "set_driver_zone", set_zone):
        out = await fz.update_driver_zone("drv-1", 52.1700, -106.7000)  # would enter
    # Swallowed (best-effort): returns prior state (None), does not raise.
    assert out is None


# ── fifo_terminal_for_point (dispatch-side pickup detection) ──────────────────


@pytest.mark.anyio
async def test_pickup_inside_terminal_resolves_terminal_id():
    with _patch_lots(_TERMINAL_VENUE, _LOT_A):
        out = await fz.fifo_terminal_for_point(52.1580, -106.7000)  # terminal center
    assert out == TERMINAL


@pytest.mark.anyio
async def test_pickup_outside_any_terminal_returns_none():
    with _patch_lots(_TERMINAL_VENUE):
        out = await fz.fifo_terminal_for_point(52.5000, -106.7000)  # ~4.7 km away
    assert out is None


@pytest.mark.anyio
async def test_feeder_lot_is_not_a_pickup_terminal():
    # A lot (queue_for_venue_id set, not fifo_enabled) must NOT match pickup
    # detection — only the terminal does, even if the pin is dead-center of a lot.
    with _patch_lots(_LOT_A):
        out = await fz.fifo_terminal_for_point(52.1700, -106.7000)
    assert out is None
