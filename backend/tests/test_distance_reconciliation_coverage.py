"""
Coverage-gap tests for utils/distance_reconciliation.py (A1c Sub-tier C,
Batch 10 pick).

test_distance_reconciliation.py already covers the pure
`evaluate_reconciliation()` branches and the happy/no-op paths of
`_run_reconciliation_tick`. This file closes the remaining gaps found via
`--cov-report=term-missing`:

  - `_pod_id()` (hostname:pid identity used for the Redis leader lock owner)
  - `_seconds_until()`'s same-day-vs-wrap-to-tomorrow branches
  - `_run_reconciliation_tick`'s `aggregate["biased"]` ERROR-log branch (the
    whole point of this module per its docstring: "a platform-wide bias...
    trips an alert within a day")
  - `distance_reconciliation_loop`'s three branches: lock acquired -> tick
    runs, lock held by another replica -> skip, and tick-raises -> caught
    and logged so the daily loop survives to the next day
"""

from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import utils.distance_reconciliation as dr


def _ride(rid, quoted, measured):
    return {"id": rid, "planned_distance_km": quoted, "actual_distance_km": measured}


# ---------------------------------------------------------------------------
# _pod_id
# ---------------------------------------------------------------------------


def test_pod_id_combines_hostname_and_pid():
    pod = dr._pod_id()
    assert pod.startswith(socket.gethostname() + ":")
    assert pod.split(":")[-1].isdigit()


# ---------------------------------------------------------------------------
# _seconds_until
# ---------------------------------------------------------------------------


class _FixedDatetime(datetime):
    """Stand-in for the module's `datetime` so `.now(tz)` returns a fixed
    instant, letting us pin both sides of the same-day/next-day branch."""

    _fixed = datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed if tz is None else cls._fixed.astimezone(tz)


def test_seconds_until_target_later_today():
    with patch.object(dr, "datetime", _FixedDatetime):
        # target 16:00 UTC, now fixed at 10:00 UTC -> same day, 6h away.
        secs = dr._seconds_until(16)
    assert secs == pytest.approx(6 * 3600, abs=1)


def test_seconds_until_target_already_passed_wraps_to_tomorrow():
    with patch.object(dr, "datetime", _FixedDatetime):
        # target 04:00 UTC, now fixed at 10:00 UTC -> already passed -> +1 day.
        secs = dr._seconds_until(4)
    expected = (
        _FixedDatetime._fixed.replace(hour=4, minute=0, second=0, microsecond=0)
        + timedelta(days=1)
        - _FixedDatetime._fixed
    ).total_seconds()
    assert secs == pytest.approx(expected, abs=1)
    # 04:00 tomorrow minus 10:00 today == 18h -- confirms it wrapped forward
    # a day rather than (impossibly) landing on a negative same-day delta.
    assert secs == pytest.approx(18 * 3600, abs=1)


def test_seconds_until_target_equal_to_now_wraps_to_tomorrow():
    with patch.object(dr, "datetime", _FixedDatetime):
        # target == now exactly -> `target <= now` -> wraps (not a same-day 0s).
        secs = dr._seconds_until(10)
    assert secs == pytest.approx(24 * 3600, abs=1)


# ---------------------------------------------------------------------------
# _run_reconciliation_tick: the systematic-bias ERROR log branch
# ---------------------------------------------------------------------------


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_tick_logs_error_on_systematic_bias(caplog):
    # AGG_MIN_SAMPLES rides, all consistently 2.5x over quote -> biased=True.
    rides = [_ride(f"r{i}", 1.0, 2.5) for i in range(dr.AGG_MIN_SAMPLES)]
    with (
        patch.object(dr.db_supabase, "get_rows", AsyncMock(return_value=rides)),
        patch.object(dr.db_supabase, "update_one", AsyncMock(return_value={"id": "x"})),
        patch.object(dr, "record_integrity_event", AsyncMock(return_value=True)),
        caplog.at_level("ERROR", logger="utils.distance_reconciliation"),
    ):
        _run(dr._run_reconciliation_tick())

    assert any("SYSTEMATIC quote-vs-measured bias" in rec.message for rec in caplog.records)


def test_tick_no_error_log_when_not_biased(caplog):
    rides = [_ride(f"r{i}", 1.0, 1.0) for i in range(dr.AGG_MIN_SAMPLES)]
    with (
        patch.object(dr.db_supabase, "get_rows", AsyncMock(return_value=rides)),
        patch.object(dr.db_supabase, "update_one", AsyncMock(return_value={"id": "x"})),
        patch.object(dr, "record_integrity_event", AsyncMock(return_value=True)),
        caplog.at_level("ERROR", logger="utils.distance_reconciliation"),
    ):
        _run(dr._run_reconciliation_tick())

    assert not any("SYSTEMATIC" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# distance_reconciliation_loop
# ---------------------------------------------------------------------------


class _StopLoop(Exception):
    """Sentinel used to escape the infinite `while True` after one pass."""


@pytest.mark.anyio
async def test_loop_runs_tick_when_lock_acquired():
    tick = AsyncMock()
    sleeps: list = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise _StopLoop()

    with (
        patch.object(dr, "_seconds_until", return_value=0.0),
        patch.object(dr.asyncio, "sleep", _fake_sleep),
        patch.object(dr, "redis_set_nx", AsyncMock(return_value=True)),
        patch.object(dr, "_run_reconciliation_tick", tick),
    ):
        with pytest.raises(_StopLoop):
            await dr.distance_reconciliation_loop()

    tick.assert_awaited_once()
    # first sleep is the "wait until target hour", second is the 24h cadence.
    assert sleeps[0] == 0.0
    assert sleeps[1] == 86400


@pytest.mark.anyio
async def test_loop_skips_tick_when_lock_held_elsewhere(caplog):
    tick = AsyncMock()

    async def _fake_sleep(seconds):
        raise _StopLoop()

    with (
        patch.object(dr, "_seconds_until", return_value=0.0),
        patch.object(dr.asyncio, "sleep", AsyncMock(side_effect=[None, _StopLoop()])),
        patch.object(dr, "redis_set_nx", AsyncMock(return_value=False)),
        patch.object(dr, "_run_reconciliation_tick", tick),
        caplog.at_level("INFO", logger="utils.distance_reconciliation"),
    ):
        with pytest.raises(_StopLoop):
            await dr.distance_reconciliation_loop()

    tick.assert_not_awaited()
    assert any("another replica holds the lock" in rec.message for rec in caplog.records)


@pytest.mark.anyio
async def test_loop_survives_tick_exception_and_logs_error(caplog):
    tick = AsyncMock(side_effect=RuntimeError("db unreachable"))

    with (
        patch.object(dr, "_seconds_until", return_value=0.0),
        patch.object(dr.asyncio, "sleep", AsyncMock(side_effect=[None, _StopLoop()])),
        patch.object(dr, "redis_set_nx", AsyncMock(return_value=True)),
        patch.object(dr, "_run_reconciliation_tick", tick),
        caplog.at_level("ERROR", logger="utils.distance_reconciliation"),
    ):
        # The loop itself must NOT propagate the tick's exception -- only the
        # sleep-injected _StopLoop sentinel should escape.
        with pytest.raises(_StopLoop):
            await dr.distance_reconciliation_loop()

    tick.assert_awaited_once()
    assert any("tick raised" in rec.message for rec in caplog.records)
