"""Coverage top-up for utils/distance_reconciliation.py (A1c Sub-tier C).

Test-only addition — no application code changed. Complements
tests/test_distance_reconciliation.py (which already covers
evaluate_reconciliation() and the happy/empty paths of
_run_reconciliation_tick()) by exercising the previously-uncovered lines:

  - _pod_id()                                  (line 64)
  - _seconds_until() both branches             (lines 68-72)
  - the aggregate-bias ERROR log path in the
    tick (logger.error at line 144)
  - distance_reconciliation_loop() entirely    (lines 171-183): initial
    sleep, lock-acquired/tick-runs, lock-held-elsewhere/tick-skipped, and
    tick-raises/exception-swallowed-and-logged branches.

Lines 30-32 (the `try: from .. import db_supabase ...` relative-import
branch of the dual-import block) are not covered here. Which branch of that
try/except runs is fixed at module-import time by how the test process's
sys.path is set up, not by anything a test body can control post-import,
and this repo's test suite imports the module via the top-level
`from utils.distance_reconciliation import ...` spelling (see
test_distance_reconciliation.py), which takes the `except ImportError`
path. Exercising the `try` path would require a second, differently-rooted
import of the same module (e.g. via `backend.utils.distance_reconciliation`)
in a state where `backend` is a real package on sys.path — orthogonal to
this module's own logic and consistent with the existing coverage gap
being isolated to exactly those three lines.
"""

from __future__ import annotations

import asyncio
import os
import socket
from datetime import datetime as real_datetime
from datetime import timedelta
from datetime import timezone as real_timezone
from unittest.mock import AsyncMock, patch

from utils.distance_reconciliation import (
    AGG_MIN_SAMPLES,
    BIAS_THRESHOLD,
    _pod_id,
    _run_reconciliation_tick,
    _seconds_until,
    distance_reconciliation_loop,
)


def _run(coro):
    return asyncio.run(coro)


def _ride(rid, quoted, measured):
    return {"id": rid, "planned_distance_km": quoted, "actual_distance_km": measured}


class _StopLoop(Exception):
    """Sentinel used to break distance_reconciliation_loop's `while True`."""


class FakeDateTime(real_datetime):
    """Fixed-clock stand-in for datetime.now(timezone.utc)."""

    _fixed = real_datetime(2026, 8, 2, 10, 30, tzinfo=real_timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


class TestPodId:
    def test_pod_id_format(self):
        pod_id = _pod_id()
        assert pod_id == f"{socket.gethostname()}:{os.getpid()}"
        assert ":" in pod_id


class TestSecondsUntil:
    def test_target_later_today_no_rollover(self):
        # now=10:30 UTC, target=14:00 UTC -> later today, no +1 day.
        with patch("utils.distance_reconciliation.datetime", FakeDateTime):
            seconds = _seconds_until(14)
        expected = real_datetime(2026, 8, 2, 14, 0, tzinfo=real_timezone.utc) - FakeDateTime._fixed
        assert seconds == expected.total_seconds()
        assert seconds > 0

    def test_target_already_passed_rolls_to_tomorrow(self):
        # now=10:30 UTC, target=04:00 UTC -> already passed today, rolls +1 day.
        with patch("utils.distance_reconciliation.datetime", FakeDateTime):
            seconds = _seconds_until(4)
        expected = real_datetime(2026, 8, 2, 4, 0, tzinfo=real_timezone.utc) + timedelta(days=1) - FakeDateTime._fixed
        assert seconds == expected.total_seconds()
        assert seconds > 0

    def test_target_equal_to_now_rolls_to_tomorrow(self):
        # target == now hits the `<=` branch (not just `<`): a FakeDateTime
        # whose .now() is exactly on an hour boundary equal to the requested
        # target hour, forcing target == now.
        class FixedAtHour(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime(2026, 8, 2, 4, 0, 0, 0, tzinfo=real_timezone.utc)

        with patch("utils.distance_reconciliation.datetime", FixedAtHour):
            seconds = _seconds_until(4)
        assert seconds == timedelta(days=1).total_seconds()


class TestTickAggregateBias:
    def test_tick_logs_error_on_systematic_bias(self):
        # AGG_MIN_SAMPLES rides all with ratio 2.5 (way past BIAS_THRESHOLD)
        # so aggregate["biased"] is True and the ERROR/Sentry path (line 144)
        # is exercised.
        rides = [_ride(f"r{i}", 1.0, 2.5) for i in range(AGG_MIN_SAMPLES)]
        assert abs(2.5 - 1.0) > BIAS_THRESHOLD  # sanity: this really is "biased"

        with (
            patch("utils.distance_reconciliation.db_supabase.get_rows", AsyncMock(return_value=rides)),
            patch("utils.distance_reconciliation.db_supabase.update_one", AsyncMock(return_value={})) as update,
            patch("utils.distance_reconciliation.record_integrity_event", AsyncMock(return_value=True)),
            patch("utils.distance_reconciliation.logger") as mock_logger,
        ):
            _run(_run_reconciliation_tick())

        assert mock_logger.error.called
        error_args = mock_logger.error.call_args[0]
        assert "SYSTEMATIC" in error_args[0]
        assert error_args[1] == 2.5
        assert error_args[2] == AGG_MIN_SAMPLES
        # All rides still get claimed regardless of the bias finding.
        assert len(update.await_args[0][1]["id"]["$in"]) == AGG_MIN_SAMPLES

    def test_tick_no_error_log_when_not_biased(self):
        rides = [_ride(f"r{i}", 1.0, 1.0) for i in range(AGG_MIN_SAMPLES)]
        with (
            patch("utils.distance_reconciliation.db_supabase.get_rows", AsyncMock(return_value=rides)),
            patch("utils.distance_reconciliation.db_supabase.update_one", AsyncMock(return_value={})),
            patch("utils.distance_reconciliation.record_integrity_event", AsyncMock(return_value=True)),
            patch("utils.distance_reconciliation.logger") as mock_logger,
        ):
            _run(_run_reconciliation_tick())
        assert not mock_logger.error.called
        assert mock_logger.info.called


class TestClaimUnevaluatedRides:
    def test_ride_with_missing_measured_distance_is_left_unclaimed(self):
        """Fixed: _run_reconciliation_tick now only claims rides that were
        actually evaluated (produced a ratio via _has_usable_distance), not
        every ride merely fetched in the batch.

        evaluate_reconciliation() `continue`s past any ride whose
        actual_distance_km isn't usable yet (e.g. the deferred route
        finalizer hasn't backfilled it), so that ride contributes no ratio
        and no divergence -- it is never actually reconciled. The claiming
        step now shares the same `_has_usable_distance` eligibility check,
        so that never-actually-checked ride is left unclaimed
        (`distance_reconciled_at` stays None) and will be re-fetched and
        evaluated on a future tick once its data lands, instead of being
        silently excluded from reconciliation forever.
        """
        rides = [
            _ride("evaluated", 1.0, 1.0),  # produces a ratio, legitimately claimed
            _ride("never_measured", 1.0, None),  # skipped by evaluate_reconciliation
        ]
        with (
            patch("utils.distance_reconciliation.db_supabase.get_rows", AsyncMock(return_value=rides)),
            patch("utils.distance_reconciliation.db_supabase.update_one", AsyncMock(return_value={})) as update,
            patch("utils.distance_reconciliation.record_integrity_event", AsyncMock(return_value=True)),
        ):
            _run(_run_reconciliation_tick())

        claimed_ids = set(update.await_args[0][1]["id"]["$in"])
        # Fixed behavior: only the ride that was actually evaluated is claimed.
        assert claimed_ids == {"evaluated"}


class TestDistanceReconciliationLoop:
    def test_loop_sleeps_then_acquires_lock_and_runs_tick(self):
        sleep_mock = AsyncMock(side_effect=[None, _StopLoop()])
        with (
            patch("utils.distance_reconciliation.asyncio.sleep", sleep_mock),
            patch("utils.distance_reconciliation._seconds_until", return_value=0.0) as seconds_until,
            patch("utils.distance_reconciliation.redis_set_nx", AsyncMock(return_value=True)) as lock,
            patch("utils.distance_reconciliation._run_reconciliation_tick", AsyncMock()) as tick,
            patch("utils.distance_reconciliation._pod_id", return_value="host:123"),
        ):
            try:
                _run(distance_reconciliation_loop(4))
                raised = False
            except _StopLoop:
                raised = True
        assert raised
        seconds_until.assert_called_once_with(4)
        lock.assert_awaited_once()
        tick.assert_awaited_once()
        assert sleep_mock.await_count == 2

    def test_loop_skips_tick_when_lock_held_elsewhere(self):
        sleep_mock = AsyncMock(side_effect=[None, _StopLoop()])
        with (
            patch("utils.distance_reconciliation.asyncio.sleep", sleep_mock),
            patch("utils.distance_reconciliation._seconds_until", return_value=0.0),
            patch("utils.distance_reconciliation.redis_set_nx", AsyncMock(return_value=False)),
            patch("utils.distance_reconciliation._run_reconciliation_tick", AsyncMock()) as tick,
        ):
            try:
                _run(distance_reconciliation_loop(4))
                raised = False
            except _StopLoop:
                raised = True
        assert raised
        tick.assert_not_awaited()

    def test_loop_swallows_and_logs_tick_exception(self):
        sleep_mock = AsyncMock(side_effect=[None, _StopLoop()])
        with (
            patch("utils.distance_reconciliation.asyncio.sleep", sleep_mock),
            patch("utils.distance_reconciliation._seconds_until", return_value=0.0),
            patch("utils.distance_reconciliation.redis_set_nx", AsyncMock(return_value=True)),
            patch(
                "utils.distance_reconciliation._run_reconciliation_tick",
                AsyncMock(side_effect=RuntimeError("db exploded")),
            ),
            patch("utils.distance_reconciliation.logger") as mock_logger,
        ):
            try:
                _run(distance_reconciliation_loop(4))
                raised = False
            except _StopLoop:
                raised = True
        # The tick's RuntimeError must NOT propagate out of the loop -- it's
        # caught by `except Exception` and logged, then the loop sleeps again
        # (which is what raises our _StopLoop sentinel next).
        assert raised
        assert mock_logger.error.called
        assert mock_logger.error.call_args.kwargs.get("exc_info") is True
