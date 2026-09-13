"""An outer cancellation of run_sync must not leak the half-open circuit probe.

`repositories/_base.run_sync` releases the breaker's single half-open probe on
every exit path it knows about — success, failure, the deadline TimeoutError,
the ValueError early-exit. It did NOT release it on cancellation, and
`asyncio.CancelledError` is BaseException-only, so neither `except TimeoutError`
nor `except Exception` ever saw it.

That path is reached in production on a schedule: `server.py::_db_ready` wraps
its health ping in `asyncio.wait_for(..., timeout=_HEALTH_PING_TIMEOUT)` (3s) and
that bound fired 111 times in six days. If the cancelled call happened to be the
probe, `_probe_in_flight` stayed True forever, `should_allow()` returned False for
every subsequent call, and the whole API 503'd until the process restarted —
exactly what `release_probe()` exists to prevent.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.anyio


def _arm_half_open(breaker) -> None:
    """Half-open with the probe still UNCLAIMED.

    run_sync calls should_allow() itself, so the probe must be available for it
    to grant to its own call — claiming it here first would make run_sync the
    refused second caller and raise before it ever reaches the executor.
    """
    breaker._state = "half_open"
    breaker._probe_in_flight = False


class TestCancellationReleasesTheProbe:
    async def test_outer_wait_for_cancellation_does_not_leak_the_probe(self):
        from backend.repositories import _base

        _arm_half_open(_base._breaker)
        try:
            started = asyncio.Event()

            def _slow():
                started.set()
                # Longer than the outer bound below, so the outer wait_for wins.
                import time as _t

                _t.sleep(2.0)
                return "unreachable"

            # An OUTER deadline, exactly like server.py::_db_ready's 3s bound.
            with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                await asyncio.wait_for(_base.run_sync(_slow, "read"), timeout=0.15)

            assert _base._breaker._probe_in_flight is False, (
                "the half-open probe leaked: should_allow() will now refuse every "
                "future call and the whole API 503s until restart"
            )
            # Released back to OPEN with a fresh window, matching the
            # deadline-abort path's documented semantics.
            assert _base._breaker._state == "open"
            # And the breaker is genuinely usable again: force the open window
            # to have elapsed and confirm a fresh probe can be granted. Before
            # the fix this stayed False forever.
            _base._breaker._opened_at = 0.0
            assert _base._breaker.should_allow() is True
        finally:
            # Never leave a module-level breaker dirty for the rest of the suite.
            _base._breaker.record_success()

    async def test_a_healthy_call_still_closes_the_breaker(self):
        """Guard against over-correcting: the success path is untouched."""
        from backend.repositories import _base

        _arm_half_open(_base._breaker)
        try:
            assert await _base.run_sync(lambda: "ok", "read") == "ok"
            assert _base._breaker._state == "closed"
            assert _base._breaker._probe_in_flight is False
        finally:
            _base._breaker.record_success()

    async def test_cancellation_propagates_rather_than_being_swallowed(self):
        """CLAUDE.md: never silently swallow. The caller must still see cancel."""
        from backend.repositories import _base

        _base._breaker.record_success()  # closed: no probe involved
        cancelled_seen = False

        def _slow():
            import time as _t

            _t.sleep(2.0)
            return "unreachable"

        task = asyncio.create_task(_base.run_sync(_slow, "read"))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            cancelled_seen = True
        assert cancelled_seen, "run_sync must re-raise CancelledError, not absorb it"
