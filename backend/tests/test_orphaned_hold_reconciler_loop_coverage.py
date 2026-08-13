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


# ---------------------------------------------------------------------------
# ACTION_ITEMS B21: throttle-lock TTL must expire before the loop's own next
# wake, or the pod that ran the last tick fails its own SET NX and skips a
# full interval — see utils/ledger_projection.py's _LOCK_TTL_SECONDS for the
# sibling fix this mirrors.
# ---------------------------------------------------------------------------


def test_lock_ttl_expires_before_the_earliest_next_wake():
    """Stated as an invariant so a future tuning change to the interval or the
    jitter fraction can't silently re-break the cadence."""
    from utils import orphaned_hold_reconciler as m

    jitter_fraction = 0.1  # matches the loop's `delta = interval * 0.1`
    min_sleep = m.RECONCILE_INTERVAL_SECONDS * (1 - jitter_fraction)
    lock_ttl = int(m.RECONCILE_INTERVAL_SECONDS * 0.85)
    assert lock_ttl < min_sleep, (
        f"lock TTL {lock_ttl}s must expire before the shortest possible sleep "
        f"({min_sleep}s), or the loop skips its own next tick"
    )


async def test_loop_reacquires_its_own_lock_on_the_next_wake():
    """REGRESSION: with the old TTL = 2x interval against a 1x interval sleep,
    the pod that ran the last tick woke to find its OWN key still alive,
    failed SET NX, and slept another full interval — so a loop documented as
    "15min" actually ticked every ~30min.

    Simulated against a virtual clock with real SET NX EX semantics, jitter
    pinned to its most adverse value (the SHORTEST sleep) — the case the TTL
    has to survive. Mirrors ledger_projection.py's loop-cadence regression
    test. random.uniform is pinned to its `lo` argument for BOTH the startup
    jitter (`uniform(0, 60)` -> 0, keeping the virtual clock's start at t=0)
    and the per-tick jitter (`uniform(-delta, delta)` -> -delta, the shortest
    sleep) — the same trick both calls need.
    """
    from utils import orphaned_hold_reconciler as m

    clock = {"t": 0.0}
    expiries: dict[str, float] = {}
    wakes = {"n": 0}

    async def fake_set_nx(key, _value, ttl):
        exp = expiries.get(key)
        if exp is not None and exp > clock["t"]:
            return False
        expiries[key] = clock["t"] + ttl
        return True

    async def fake_sleep(secs):
        clock["t"] += secs
        wakes["n"] += 1
        # Call 1 is the startup jitter (absorbed, no-op at t=0). Calls 2 and 3
        # are the two tick-interval sleeps; cancel on the 3rd so the loop ran
        # exactly twice.
        if wakes["n"] >= 3:
            raise asyncio.CancelledError

    with (
        patch.object(m, "redis_set_nx", side_effect=fake_set_nx),
        patch.object(m, "reconcile_tick", AsyncMock(return_value={"found": 0})) as tick,
        patch.object(m, "_record_heartbeat"),
        patch.object(m.asyncio, "sleep", side_effect=fake_sleep),
        patch.object(m.random, "uniform", side_effect=lambda lo, _hi: lo),
    ):
        try:
            await m.orphaned_hold_reconciler_loop()
        except asyncio.CancelledError:
            pass

    assert tick.await_count == 2, (
        "the single replica must tick once per interval; a TTL longer than the "
        "minimum sleep makes it skip its own next wake and halves the cadence"
    )
