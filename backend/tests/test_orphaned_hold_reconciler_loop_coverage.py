"""
A1c Sub-tier C coverage: backend/utils/orphaned_hold_reconciler.py (69% -> target 90%+).

`test_orphaned_hold_reconciler.py` covers `find_orphaned_holds`, `_claim`,
and `reconcile_tick` extensively (17 tests). It only asserts that
`orphaned_hold_reconciler_loop` is *registered* in lifespan
(`test_the_loop_is_registered_in_lifespan`), never exercises the loop
function's own body. This file closes:

- `orphaned_hold_reconciler_loop`: lock-not-acquired skips the tick and
  re-loops, lock-acquired runs `reconcile_tick` and logs a summary only
  when `found` is truthy, `asyncio.CancelledError` re-raises (never
  swallowed -- graceful shutdown must propagate), a generic tick exception
  is caught/logged/counted via `spinr_bgloop_errors_total` (loop survives),
  and the heartbeat/jitter-sleep call on every iteration.
- `_pod_id`'s hostname-pid shape (note the `-` separator here, unlike
  `offer_expiry_reaper._pod_id`'s `:` separator).

The loop does an unconditional startup jitter
``await asyncio.sleep(random.uniform(0, 60))`` before entering its `while
True`, so every ``asyncio.sleep`` patch below must let the FIRST call
through as a no-op and only break out of the loop on a later call --
otherwise the test would either really sleep up to 60s or never reach the
lock-check/tick at all.

Patch target: `utils.orphaned_hold_reconciler.*` (module-bound names via
its own dual-import block), matching the established pattern in
`test_orphaned_hold_reconciler.py`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _sleep_then_cancel_after(n: int):
    """Return a fake asyncio.sleep: no-ops for the first `n` calls (absorbing
    the startup jitter sleep + any lock-not-acquired retries), then raises
    CancelledError to break out of the `while True` loop deterministically."""
    calls: list[float] = []

    async def _fake_sleep(secs):
        calls.append(secs)
        if len(calls) > n:
            raise asyncio.CancelledError()

    return _fake_sleep, calls


async def test_loop_lock_not_acquired_skips_tick_and_sleeps():
    from utils import orphaned_hold_reconciler as m

    tick = AsyncMock()
    # 1 no-op absorbs the startup jitter; the 2nd call (the lock-not-acquired
    # retry sleep) raises to end the test.
    fake_sleep, _ = _sleep_then_cancel_after(1)

    with (
        patch.object(m, "redis_set_nx", AsyncMock(return_value=False)),
        patch.object(m, "reconcile_tick", tick),
        patch.object(m.asyncio, "sleep", fake_sleep),
        patch.object(m, "_record_heartbeat"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await m.orphaned_hold_reconciler_loop()
    tick.assert_not_awaited()


async def test_loop_lock_acquired_runs_tick_and_logs_summary_when_found():
    from utils import orphaned_hold_reconciler as m

    tick = AsyncMock(return_value={"found": 2, "released": 2})
    fake_sleep, _ = _sleep_then_cancel_after(1)

    with (
        patch.object(m, "redis_set_nx", AsyncMock(return_value=True)),
        patch.object(m, "reconcile_tick", tick),
        patch.object(m.asyncio, "sleep", fake_sleep),
        patch.object(m, "_record_heartbeat") as mock_hb,
    ):
        with pytest.raises(asyncio.CancelledError):
            await m.orphaned_hold_reconciler_loop()
    tick.assert_awaited_once()
    mock_hb.assert_called_once_with(m._LOOP_NAME)


async def test_loop_skips_summary_log_when_nothing_found():
    from utils import orphaned_hold_reconciler as m

    tick = AsyncMock(return_value={"found": 0})
    fake_sleep, _ = _sleep_then_cancel_after(1)

    with (
        patch.object(m, "redis_set_nx", AsyncMock(return_value=True)),
        patch.object(m, "reconcile_tick", tick),
        patch.object(m.asyncio, "sleep", fake_sleep),
        patch.object(m, "_record_heartbeat"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await m.orphaned_hold_reconciler_loop()
    tick.assert_awaited_once()


async def test_loop_cancelled_error_from_tick_propagates_not_swallowed():
    from utils import orphaned_hold_reconciler as m

    async def cancelling_tick():
        raise asyncio.CancelledError()

    # No lock-loop sleep is ever reached (the exception fires inside the
    # try/except before the trailing sleep), so only the startup jitter
    # needs absorbing.
    fake_sleep, _ = _sleep_then_cancel_after(999)

    with (
        patch.object(m, "redis_set_nx", AsyncMock(return_value=True)),
        patch.object(m, "reconcile_tick", cancelling_tick),
        patch.object(m.asyncio, "sleep", fake_sleep),
        patch.object(m, "_record_heartbeat"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await m.orphaned_hold_reconciler_loop()


async def test_loop_generic_tick_exception_is_caught_logged_and_counted():
    from utils import orphaned_hold_reconciler as m

    async def failing_tick():
        raise RuntimeError("boom")

    fake_sleep, _ = _sleep_then_cancel_after(1)

    with (
        patch.object(m, "redis_set_nx", AsyncMock(return_value=True)),
        patch.object(m, "reconcile_tick", failing_tick),
        patch.object(m.asyncio, "sleep", fake_sleep),
        patch.object(m, "_record_heartbeat"),
        patch.object(m, "_metric_inc") as mock_inc,
    ):
        with pytest.raises(asyncio.CancelledError):
            await m.orphaned_hold_reconciler_loop()
    mock_inc.assert_called_once_with("spinr_bgloop_errors_total", {"loop": "orphaned_hold_reconciler"})


def test_pod_id_shape():
    from utils.orphaned_hold_reconciler import _pod_id

    pod_id = _pod_id()
    assert "-" in pod_id
    host, _, pid = pod_id.rpartition("-")
    assert host
    assert pid.isdigit()
