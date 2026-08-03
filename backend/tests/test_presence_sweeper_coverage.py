"""Coverage for backend/utils/presence_sweeper.py (A1c, Sub-tier C).

Test-only change; no application code modified.

Module context (see its own docstring): the presence sweeper was retired
after a production incident where a Redis-partitioned replica mass-flipped
`drivers.is_online` to False for drivers that were genuinely online (see
CLAUDE.md's `is_available ⇒ is_online` invariant -- this file is exactly the
kind of code that invariant exists to protect against). `_sweep_once` is now
a permanent no-op that always returns 0, and `presence_sweeper_loop` is kept
around only so the loop-jitter/metrics test suite
(`test_p3_loop_jitter_metrics.py`) still has stable symbols to import; the
loop is no longer scheduled from `core/lifespan.py`. Because the body is
inert, there is no `is_online`/`is_available` write path left in this file
to blast-radius-check -- the entire risk this module used to carry was
removed when it became a no-op, not by anything in this test file.

This file targets the coverage gap left by the existing tests
(`test_p3_loop_jitter_metrics.py` covers the happy-path shape: two
`asyncio.sleep` calls per cycle and the duration gauge on success). What was
still missing:
  - `_sweep_once` itself (line 62): never awaited directly.
  - The `presence_sweeper_loop` error branch (lines 78-82, 85): a raising
    tick's `logger.error` call, the `_had_error` flag driving
    `spinr_bgloop_errors_total`, and that `CancelledError` re-raises
    immediately instead of being logged as a tick failure.
  - The `ImportError` fallback for `_record_heartbeat` (lines 42-44): only
    reachable if `utils.loop_monitor` fails to import at module load time,
    which requires forcing the import to fail and reloading the module.

No bug found in this file: the no-op behavior is the *intended* fix for the
original incident (see module docstring), not a regression to flag.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

import backend.utils.presence_sweeper as sweeper_mod

# ---------------------------------------------------------------------------
# _sweep_once — pure no-op (line 62)
# ---------------------------------------------------------------------------


class TestSweepOnce:
    @pytest.mark.anyio
    async def test_sweep_once_is_a_pure_noop_returning_zero(self):
        """`_sweep_once` never touches the DB or Redis -- it is a permanent
        no-op documented in the module docstring as the fix for the
        Redis-partition mass-flip incident. Calling it must be side-effect
        free and always return 0."""
        result = await sweeper_mod._sweep_once()
        assert result == 0


# ---------------------------------------------------------------------------
# presence_sweeper_loop — error branch (lines 78-82, 85)
# ---------------------------------------------------------------------------


async def _run_loop_one_iteration(extra_patches: dict):
    """Run presence_sweeper_loop for exactly one full tick (initial jitter +
    one loop-body pass), then let CancelledError on the second sleep end it.

    Must be awaited directly from within an already-running event loop (the
    caller's `@pytest.mark.anyio` test) -- do NOT wrap this in
    `asyncio.get_event_loop().run_until_complete(...)`, which would try to
    start a second, nested event loop and raise `RuntimeError: This event
    loop is already running`.
    """
    sleep_calls: list[float] = []

    async def capture_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", capture_sleep),
        patch.object(sweeper_mod, "_sweep_once", extra_patches["_sweep_once"]),
        patch.object(sweeper_mod, "_metric_gauge", extra_patches["_metric_gauge"]),
        patch.object(sweeper_mod, "_metric_inc", extra_patches["_metric_inc"]),
        patch.object(sweeper_mod, "_record_heartbeat", extra_patches["_record_heartbeat"]),
    ):
        try:
            await sweeper_mod.presence_sweeper_loop()
        except asyncio.CancelledError:
            pass

    return sleep_calls


class TestPresenceSweeperLoopErrorBranch:
    @pytest.mark.anyio
    async def test_tick_exception_is_logged_error_and_increments_error_metric(self, caplog):
        """A raising `_sweep_once` must be caught (not propagated), logged
        via `logger.error` (not `.warning` -- CLAUDE.md's "do not silently
        swallow errors" rule), and drive the `_had_error` flag so
        `spinr_bgloop_errors_total` is incremented. The loop must still
        proceed to emit the duration gauge and heartbeat, and still sleep
        (i.e. a tick failure does not kill the loop)."""
        gauge_calls: list[tuple] = []
        inc_calls: list[tuple] = []
        heartbeat_calls: list[str] = []

        async def failing_sweep():
            raise RuntimeError("simulated presence sweep failure")

        def fake_gauge(name, value, labels):
            gauge_calls.append((name, value, labels))

        def fake_inc(name, labels=None):
            inc_calls.append((name, labels))

        def fake_heartbeat(name):
            heartbeat_calls.append(name)

        with caplog.at_level(logging.ERROR):
            sleep_calls = await _run_loop_one_iteration(
                {
                    "_sweep_once": AsyncMock(side_effect=failing_sweep),
                    "_metric_gauge": fake_gauge,
                    "_metric_inc": fake_inc,
                    "_record_heartbeat": fake_heartbeat,
                }
            )

        # Loop survived the exception and reached both sleeps.
        assert len(sleep_calls) == 2

        # logger.error (not .warning) with the underlying exception surfaced.
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("tick failed" in r.message for r in error_records)
        assert any("simulated presence sweep failure" in r.message for r in error_records)

        # Duration gauge still emitted even on failure.
        assert len(gauge_calls) == 1
        name, value, labels = gauge_calls[0]
        assert name == "spinr_bgloop_duration_ms"
        assert labels == {"loop": "presence_sweeper"}
        assert value >= 0

        # Error counter incremented exactly once, with the right label.
        assert len(inc_calls) == 1
        inc_name, inc_labels = inc_calls[0]
        assert inc_name == "spinr_bgloop_errors_total"
        assert inc_labels == {"loop": "presence_sweeper"}

        # Heartbeat still recorded despite the failure.
        assert heartbeat_calls == ["presence_sweeper (60s)"]

    @pytest.mark.anyio
    async def test_cancelled_error_from_tick_propagates_without_error_metric(self):
        """`asyncio.CancelledError` must re-raise immediately (line 78-79)
        rather than being treated as a tick failure -- it is not logged as
        an error and must not increment `spinr_bgloop_errors_total`, since
        that would misreport a normal shutdown/cancellation as a fault."""
        inc_calls: list[tuple] = []
        gauge_calls: list[tuple] = []

        async def cancelled_sweep():
            raise asyncio.CancelledError()

        async def passthrough_sleep(seconds):
            # Only the initial startup jitter sleep should run; the
            # CancelledError from the tick should propagate before any
            # loop-body sleep is reached.
            pass

        with (
            patch("asyncio.sleep", passthrough_sleep),
            patch.object(sweeper_mod, "_sweep_once", AsyncMock(side_effect=cancelled_sweep)),
            patch.object(sweeper_mod, "_metric_gauge", lambda *a, **k: gauge_calls.append((a, k))),
            patch.object(sweeper_mod, "_metric_inc", lambda *a, **k: inc_calls.append((a, k))),
            patch.object(sweeper_mod, "_record_heartbeat", MagicMock()),
        ):
            with pytest.raises(asyncio.CancelledError):
                await sweeper_mod.presence_sweeper_loop()

        assert inc_calls == []
        # The gauge/heartbeat lines are after the try/except in the loop body;
        # a re-raised CancelledError skips over them entirely for this tick.
        assert gauge_calls == []

    @pytest.mark.anyio
    async def test_no_error_metric_on_successful_tick_after_a_failing_one(self):
        """Sanity check that `_had_error` is tick-local, not sticky: a
        second, successful tick after a failed one must not also increment
        the error counter."""
        inc_calls: list[tuple] = []
        call_count = 0

        async def flaky_sweep():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first tick fails")
            return 0

        sleep_count = 0

        async def capture_sleep(seconds):
            nonlocal sleep_count
            sleep_count += 1
            # 1 startup jitter + 2 loop-body sleeps = 3 total for two ticks.
            if sleep_count >= 3:
                raise asyncio.CancelledError()

        with (
            patch("asyncio.sleep", capture_sleep),
            patch.object(sweeper_mod, "_sweep_once", flaky_sweep),
            patch.object(sweeper_mod, "_metric_gauge", MagicMock()),
            patch.object(sweeper_mod, "_metric_inc", lambda *a, **k: inc_calls.append((a, k))),
            patch.object(sweeper_mod, "_record_heartbeat", MagicMock()),
        ):
            try:
                await sweeper_mod.presence_sweeper_loop()
            except asyncio.CancelledError:
                pass

        # Exactly one error increment, from the first tick only.
        assert len(inc_calls) == 1


# ---------------------------------------------------------------------------
# Module-import fallback for _record_heartbeat (lines 42-44)
# ---------------------------------------------------------------------------


class TestRecordHeartbeatImportFallback:
    def test_fallback_record_heartbeat_used_when_loop_monitor_import_fails(self):
        """If `utils.loop_monitor` cannot be imported (e.g. partial deploy,
        circular import during a refactor), the module must fall back to a
        local no-op `_record_heartbeat` rather than failing to import
        entirely -- `presence_sweeper_loop` unconditionally calls
        `_record_heartbeat` every tick, so a hard ImportError here would
        take down the whole module (and, historically, its callers) instead
        of just losing heartbeat visibility for this one loop.

        Forces the fallback branch by making `utils.loop_monitor` appear
        unimportable (`sys.modules[name] = None` is the standard trick to
        make Python's import machinery raise ImportError for a name) and
        reloading the module under that condition.
        """
        mod_name = sweeper_mod.__name__  # "backend.utils.presence_sweeper"

        try:
            with patch.dict(sys.modules, {"utils.loop_monitor": None}):
                reloaded = importlib.reload(sys.modules[mod_name])
                # The fallback is a plain local function; calling it must be
                # a total no-op (no exception, no return value of note).
                assert reloaded._record_heartbeat("presence_sweeper (60s)") is None
        finally:
            # `patch.dict` has restored the real "utils.loop_monitor" entry
            # in sys.modules by this point (context exited); reload again,
            # now outside the patch, so the module goes back to its normal
            # loop_monitor-backed `_record_heartbeat` and later tests in the
            # same process aren't affected by this reload.
            importlib.reload(sweeper_mod)

        # After restoring, the module should be back to using the real
        # loop_monitor-backed heartbeat (import succeeds normally again).
        import backend.utils.loop_monitor as real_loop_monitor

        assert sweeper_mod._record_heartbeat is real_loop_monitor.record_heartbeat
