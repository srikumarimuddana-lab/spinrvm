"""Additional coverage for backend/utils/period1_distance_finalizer.py.

Complements test_period1_distance_finalizer.py, which already covers
_driver_left_period1's happy paths and _tick's "which drivers get finalized"
logic by mocking _finalize_one/_pending_accumulators/db_supabase.run_sync
wholesale. This file targets what that mocking style structurally cannot
reach: the *bodies* of the closures passed to db_supabase.run_sync (an
AsyncMock(return_value=...) never calls the function it's given, so the real
_claim()/_q() closures were never executed), the exception branches, and the
outer while-True loop.

Written by reading backend/utils/period1_distance_finalizer.py only — pytest
was NOT run against this file (or any file) while writing it, per task
instructions. Test bodies are deliberately simple / structurally mirrored to
the source to minimize the chance of a typo producing a false pass.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils import period1_distance_finalizer as mod

pytestmark = pytest.mark.unit


def _run(coro):
    return asyncio.run(coro)


async def _run_sync_inline(func, retry_policy="read"):
    """Stand-in for db_supabase.run_sync that actually invokes the closure it's
    given (in-process, synchronously) instead of swallowing it like a plain
    AsyncMock(return_value=...) would. This is what lets the real _claim()/_q()
    closure bodies execute under test.
    """
    return func()


def _chain_mock(execute_result):
    """A MagicMock standing in for the supabase client, returning itself from
    every chained builder call except .execute()."""
    m = MagicMock()
    m.table.return_value = m
    m.select.return_value = m
    m.update.return_value = m
    m.eq.return_value = m
    m.gt.return_value = m
    m.limit.return_value = m
    m.execute.return_value = execute_result
    return m


# --- _driver_left_period1: active-ride check raises (lines 55-58) ----------


def test_left_period1_returns_false_when_active_ride_check_raises():
    with patch.object(mod, "resolve_active_ride", AsyncMock(side_effect=RuntimeError("db down"))):
        result = _run(mod._driver_left_period1({"id": "d1", "is_online": True}))
    # Can't confirm the driver left Period 1 -> don't finalize yet (fail-safe).
    assert result is False


# --- _finalize_one: supabase-None guard (line 71) ---------------------------


def test_finalize_one_returns_false_when_supabase_none():
    db = SimpleNamespace(supabase=None, run_sync=AsyncMock(), insert_one=AsyncMock())
    row = {"id": "d1", "period1_accum_km": 5.0}
    with patch.object(mod, "db_supabase", db):
        assert _run(mod._finalize_one(row, "t9")) is False
    db.run_sync.assert_not_awaited()
    db.insert_one.assert_not_awaited()


# --- _finalize_one: real _claim() closure body (lines 76-83) ---------------


def test_finalize_one_claim_closure_builds_expected_chain_and_writes():
    execute_result = SimpleNamespace(data=[{"id": "d1"}])  # 1 row claimed
    chain = _chain_mock(execute_result)
    db = SimpleNamespace(
        supabase=chain,
        run_sync=_run_sync_inline,
        insert_one=AsyncMock(return_value={"id": "row"}),
    )
    row = {"id": "d1", "period1_accum_km": 2.5, "period1_accum_since": "t0"}
    with patch.object(mod, "db_supabase", db):
        assert _run(mod._finalize_one(row, "t9")) is True

    chain.table.assert_called_with("drivers")
    chain.update.assert_called_with({"period1_accum_km": 0, "period1_accum_since": None})
    chain.eq.assert_any_call("id", "d1")
    chain.eq.assert_any_call("period1_accum_km", 2.5)
    assert chain.eq.call_count == 2
    db.insert_one.assert_awaited_once()


def test_finalize_one_claim_closure_zero_rows_skips_insert():
    execute_result = SimpleNamespace(data=[])  # a concurrent finalizer/replica already claimed it
    chain = _chain_mock(execute_result)
    db = SimpleNamespace(supabase=chain, run_sync=_run_sync_inline, insert_one=AsyncMock())
    row = {"id": "d1", "period1_accum_km": 2.5}
    with patch.object(mod, "db_supabase", db):
        assert _run(mod._finalize_one(row, "t9")) is False
    db.insert_one.assert_not_awaited()


def test_finalize_one_claim_closure_data_none_treated_as_zero_rows():
    # getattr(res, "data", None) or [] -> exercise the "data is explicitly None" leg.
    execute_result = SimpleNamespace(data=None)
    chain = _chain_mock(execute_result)
    db = SimpleNamespace(supabase=chain, run_sync=_run_sync_inline, insert_one=AsyncMock())
    row = {"id": "d1", "period1_accum_km": 2.5}
    with patch.object(mod, "db_supabase", db):
        assert _run(mod._finalize_one(row, "t9")) is False
    db.insert_one.assert_not_awaited()


# --- _pending_accumulators (lines 106-120) ----------------------------------


def test_pending_accumulators_returns_empty_when_supabase_none():
    db = SimpleNamespace(supabase=None, run_sync=AsyncMock())
    with patch.object(mod, "db_supabase", db):
        assert _run(mod._pending_accumulators()) == []
    db.run_sync.assert_not_awaited()


def test_pending_accumulators_queries_expected_chain_and_returns_rows():
    rows = [{"id": "d1", "is_online": True, "period1_accum_km": 1.2, "period1_accum_since": "t0"}]
    execute_result = SimpleNamespace(data=rows)
    chain = _chain_mock(execute_result)
    db = SimpleNamespace(supabase=chain, run_sync=_run_sync_inline)
    with patch.object(mod, "db_supabase", db):
        result = _run(mod._pending_accumulators())
    assert result == rows
    chain.table.assert_called_with("drivers")
    chain.select.assert_called_with("id,is_online,period1_accum_km,period1_accum_since")
    chain.gt.assert_called_with("period1_accum_km", 0)
    chain.limit.assert_called_with(mod.BATCH_LIMIT)


def test_pending_accumulators_data_none_returns_empty_list():
    execute_result = SimpleNamespace(data=None)
    chain = _chain_mock(execute_result)
    db = SimpleNamespace(supabase=chain, run_sync=_run_sync_inline)
    with patch.object(mod, "db_supabase", db):
        assert _run(mod._pending_accumulators()) == []


# --- _tick: per-driver exception is caught and skipped (lines 137-138) -----


def test_tick_continues_after_per_driver_exception():
    rows = [
        {"id": "boom", "is_online": False, "period1_accum_km": 1.0},
        {"id": "ok", "is_online": False, "period1_accum_km": 2.0},
    ]
    with (
        patch.object(mod, "get_app_settings", AsyncMock(return_value={"period1_distance_tracking_enabled": True})),
        patch.object(mod, "_pending_accumulators", AsyncMock(return_value=rows)),
        patch.object(
            mod,
            "_finalize_one",
            AsyncMock(side_effect=[RuntimeError("write failed"), True]),
        ) as fin,
    ):
        recorded = _run(mod._tick())
    # "boom" raised and was skipped (not counted); "ok" recorded normally.
    assert recorded == 1
    assert fin.await_count == 2


# --- period1_distance_finalizer_loop (lines 148-155) ------------------------
#
# The loop is `while True`, so every test below breaks out of it deterministically
# by making the trailing `await asyncio.sleep(INTERVAL_SECONDS)` raise
# CancelledError on its first call, then asserts on what happened *before* that
# point (lock acquisition / tick invocation / exception handling).


def test_loop_ticks_once_when_lock_acquired_then_stops():
    with (
        patch.object(mod, "redis_set_nx", AsyncMock(return_value=True)),
        patch.object(mod, "_tick", AsyncMock(return_value=3)) as tick,
        patch.object(mod.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)) as sleep,
    ):
        with pytest.raises(asyncio.CancelledError):
            _run(mod.period1_distance_finalizer_loop())
    tick.assert_awaited_once()
    sleep.assert_awaited_once_with(mod.INTERVAL_SECONDS)


def test_loop_skips_tick_when_lock_not_acquired():
    with (
        patch.object(mod, "redis_set_nx", AsyncMock(return_value=False)),
        patch.object(mod, "_tick", AsyncMock()) as tick,
        patch.object(mod.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)),
    ):
        with pytest.raises(asyncio.CancelledError):
            _run(mod.period1_distance_finalizer_loop())
    tick.assert_not_awaited()


def test_loop_swallows_lock_exception_and_still_sleeps():
    with (
        patch.object(mod, "redis_set_nx", AsyncMock(side_effect=RuntimeError("redis down"))),
        patch.object(mod, "_tick", AsyncMock()) as tick,
        patch.object(mod.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)) as sleep,
    ):
        with pytest.raises(asyncio.CancelledError):
            _run(mod.period1_distance_finalizer_loop())
    tick.assert_not_awaited()
    sleep.assert_awaited_once_with(mod.INTERVAL_SECONDS)


def test_loop_swallows_tick_exception_and_still_sleeps():
    with (
        patch.object(mod, "redis_set_nx", AsyncMock(return_value=True)),
        patch.object(mod, "_tick", AsyncMock(side_effect=RuntimeError("tick blew up"))) as tick,
        patch.object(mod.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)) as sleep,
    ):
        with pytest.raises(asyncio.CancelledError):
            _run(mod.period1_distance_finalizer_loop())
    tick.assert_awaited_once()
    sleep.assert_awaited_once_with(mod.INTERVAL_SECONDS)
