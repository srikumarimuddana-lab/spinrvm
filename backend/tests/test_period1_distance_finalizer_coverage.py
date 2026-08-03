"""
A1c Sub-tier C coverage: backend/utils/period1_distance_finalizer.py (64% -> target 90%+).

`test_period1_distance_finalizer.py` covers `_driver_left_period1`'s
offline/on-ride/still-online branches, `_finalize_one`'s claim-won/
claim-lost/zero-accum branches, and `_tick`'s flag-off/happy-path
branches. This file closes:

- `_driver_left_period1`: the active-ride-check exception branch (can't
  confirm they left -> conservatively NOT finalized, `False`).
- `_finalize_one` / `_pending_accumulators`: the `db_supabase.supabase is
  None` early-return branches.
- `_tick`: one driver's processing exception not aborting the batch
  (`logger.error`, skip, continue to the next driver).
- `period1_distance_finalizer_loop`: lock-not-acquired skips the tick
  (still sleeps), lock-acquired runs the tick, and a tick exception is
  caught/logged (loop survives).

Patch target follows the established pattern in
`test_period1_distance_finalizer.py`: `utils.period1_distance_finalizer.<name>`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio

P = "utils.period1_distance_finalizer."


async def test_driver_left_period1_active_ride_check_exception_is_conservative():
    from utils.period1_distance_finalizer import _driver_left_period1

    driver_row = {"id": "d1", "is_online": True}
    with patch(P + "resolve_active_ride", AsyncMock(side_effect=RuntimeError("db down"))):
        result = await _driver_left_period1(driver_row)
    assert result is False


async def test_finalize_one_no_supabase_returns_false():
    from utils.period1_distance_finalizer import _finalize_one

    driver_row = {"id": "d1", "period1_accum_km": 5.0}
    with patch(P + "db_supabase.supabase", None):
        result = await _finalize_one(driver_row, "2026-01-01T00:00:00+00:00")
    assert result is False


async def test_pending_accumulators_no_supabase_returns_empty():
    from utils.period1_distance_finalizer import _pending_accumulators

    with patch(P + "db_supabase.supabase", None):
        result = await _pending_accumulators()
    assert result == []


async def test_tick_one_driver_exception_does_not_abort_batch():
    from utils.period1_distance_finalizer import _tick

    drivers = [
        {"id": "bad", "is_online": False, "period1_accum_km": 3.0},
        {"id": "good", "is_online": False, "period1_accum_km": 4.0},
    ]

    async def fake_left(driver_row):
        if driver_row["id"] == "bad":
            raise RuntimeError("boom")
        return True

    with (
        patch(P + "get_app_settings", AsyncMock(return_value={"period1_distance_tracking_enabled": True})),
        patch(P + "_pending_accumulators", AsyncMock(return_value=drivers)),
        patch(P + "_driver_left_period1", fake_left),
        patch(P + "_finalize_one", AsyncMock(return_value=True)),
    ):
        recorded = await _tick()
    # Only "good" is recorded; "bad" is skipped without aborting the tick.
    assert recorded == 1


# ---------------------------------------------------------------------------
# period1_distance_finalizer_loop
# ---------------------------------------------------------------------------


async def test_loop_lock_not_acquired_skips_tick_but_still_sleeps():
    tick = AsyncMock()

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "redis_set_nx", AsyncMock(return_value=False)),
        patch(P + "_tick", tick),
        patch(P + "asyncio.sleep", fake_sleep),
    ):
        from utils.period1_distance_finalizer import period1_distance_finalizer_loop

        with pytest.raises(asyncio.CancelledError):
            await period1_distance_finalizer_loop()
    tick.assert_not_awaited()


async def test_loop_lock_acquired_runs_tick():
    tick = AsyncMock()

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "redis_set_nx", AsyncMock(return_value=True)),
        patch(P + "_tick", tick),
        patch(P + "asyncio.sleep", fake_sleep),
    ):
        from utils.period1_distance_finalizer import period1_distance_finalizer_loop

        with pytest.raises(asyncio.CancelledError):
            await period1_distance_finalizer_loop()
    tick.assert_awaited_once()


async def test_loop_survives_lock_or_tick_exception():
    async def failing_lock(*a, **kw):
        raise RuntimeError("redis down")

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "redis_set_nx", failing_lock),
        patch(P + "asyncio.sleep", fake_sleep),
    ):
        from utils.period1_distance_finalizer import period1_distance_finalizer_loop

        with pytest.raises(asyncio.CancelledError):
            await period1_distance_finalizer_loop()
