"""Additional coverage for utils/orphaned_hold_reconciler.py (A1c Sub-tier C
batch 3).

test_orphaned_hold_reconciler.py already covers the reconcile_tick behavioural
contract in depth (claim races, terminal-state filtering, dry-run, failure
counting, RELEASED_UNMARKED semantics). This file closes the remaining gaps:

  - release_open_hold() itself raising (not just returning a failure outcome)
    is still caught per-ride and counted as `failed` — one rider's crash must
    not strand the rest of the batch (module docstring's replay-safety
    contract: "One rider's failure must not strand the next rider's funds")
  - _pod_id()
  - orphaned_hold_reconciler_loop()'s own control flow: the initial stagger
    sleep, the lock-not-acquired skip path, the happy-path tick + summary
    log, generic-exception handling (+ error metric), and CancelledError
    propagating out rather than being swallowed
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tests._factories import ride_row

_OLD = "2026-07-01T12:00:00+00:00"


def _orphan(**over):
    base = dict(
        id="ride-orphan-1",
        rider_id="rider-1",
        status="cancelled",
        auth_status="authorized",
        payment_intent_id="pi_orphan_1",
        authorized_amount="27.50",
        grand_total="22.50",
        updated_at=_OLD,
    )
    base.update(over)
    return ride_row(**base)


# ── release_open_hold raising is caught per-ride, not just its outcome ──


@pytest.mark.anyio
@pytest.mark.unit
async def test_release_open_hold_raising_is_caught_and_counted_as_failed():
    """The existing suite covers release_open_hold *returning* FAILED/ERROR.
    This covers it *raising* — e.g. an unexpected exception inside
    card_hold_release rather than a handled Stripe failure. The reconciler's
    per-ride try/except must still catch it and keep going."""
    stack = ExitStack()
    stack.enter_context(patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_orphan()])))
    stack.enter_context(patch("backend.db_supabase.update_one", AsyncMock(return_value={"id": "x"})))
    stack.enter_context(
        patch(
            "backend.utils.orphaned_hold_reconciler.release_open_hold",
            AsyncMock(side_effect=RuntimeError("unexpected crash")),
        )
    )
    stack.enter_context(patch("backend.utils.orphaned_hold_reconciler._metric_inc", MagicMock()))
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        summary = await reconcile_tick()

    assert summary["failed"] == 1
    assert summary["released"] == 0


@pytest.mark.anyio
@pytest.mark.unit
async def test_release_open_hold_raising_does_not_stop_the_rest_of_the_batch():
    rides = [_orphan(id="r1", payment_intent_id="pi_1"), _orphan(id="r2", payment_intent_id="pi_2")]
    seen: list[str] = []

    async def _release(ride, *, source):
        seen.append(ride["id"])
        if ride["id"] == "r1":
            raise RuntimeError("boom")
        from backend.utils.card_hold_release import RELEASED

        return RELEASED

    stack = ExitStack()
    stack.enter_context(patch("backend.db_supabase.get_rows", AsyncMock(return_value=rides)))
    stack.enter_context(patch("backend.db_supabase.update_one", AsyncMock(return_value={"id": "x"})))
    stack.enter_context(patch("backend.utils.orphaned_hold_reconciler.release_open_hold", _release))
    stack.enter_context(patch("backend.utils.orphaned_hold_reconciler._metric_inc", MagicMock()))
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        summary = await reconcile_tick()

    assert seen == ["r1", "r2"], f"batch stopped early: {seen}"
    assert summary["failed"] == 1
    assert summary["released"] == 1


# ── _pod_id ────────────────────────────────────────────────────────


def test_pod_id_format():
    import os
    import socket

    from backend.utils.orphaned_hold_reconciler import _pod_id

    assert _pod_id() == f"{socket.gethostname()}-{os.getpid()}"


# ── orphaned_hold_reconciler_loop control flow ───────────────────────


class _StopLoop(BaseException):
    """Sentinel used to break out of the loop's `while True` after we've
    observed the behaviour under test, without relying on CancelledError
    (which the loop treats specially)."""


@pytest.mark.anyio
async def test_loop_initial_stagger_sleep_then_skips_when_lock_not_acquired():
    sleep_calls: list[float] = []
    heartbeats: list[str] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        # Let the startup stagger (#1) and the first not-acquired skip sleep
        # (#2) return normally so the `continue` after it actually executes
        # and the while loop comes back around for a second not-acquired
        # pass; stop on the second skip sleep (#3).
        if len(sleep_calls) == 3:
            raise _StopLoop()

    reconcile_mock = AsyncMock()

    with (
        patch("asyncio.sleep", fake_sleep),
        patch("backend.utils.orphaned_hold_reconciler.redis_set_nx", AsyncMock(return_value=False)),
        patch("backend.utils.orphaned_hold_reconciler.reconcile_tick", reconcile_mock),
        patch("backend.utils.orphaned_hold_reconciler._record_heartbeat", lambda name: heartbeats.append(name)),
    ):
        from backend.utils.orphaned_hold_reconciler import RECONCILE_INTERVAL_SECONDS, orphaned_hold_reconciler_loop

        with pytest.raises(_StopLoop):
            await orphaned_hold_reconciler_loop()

    # sleep #1 is the 0-60s startup stagger; #2 and #3 are the not-acquired
    # skip sleeps from two successive loop iterations (proving `continue`
    # actually loops back to redis_set_nx rather than falling through).
    assert len(sleep_calls) == 3
    assert 0 <= sleep_calls[0] <= 60
    assert sleep_calls[1] == sleep_calls[2] == RECONCILE_INTERVAL_SECONDS
    reconcile_mock.assert_not_awaited()
    assert heartbeats  # heartbeat still recorded even when skipping


@pytest.mark.anyio
async def test_loop_survives_a_redis_lock_error_and_still_runs_the_tick():
    """2026-08-11 P1 fix: redis_set_nx now raises on a real Redis error
    instead of silently falling back per-replica. Previously this call sat
    directly in `while True:` with no surrounding try/except -- an
    unhandled exception here would have killed the loop task permanently."""
    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) == 2:
            raise _StopLoop()

    reconcile_mock = AsyncMock(return_value={"found": 0})

    with (
        patch("asyncio.sleep", fake_sleep),
        patch(
            "backend.utils.orphaned_hold_reconciler.redis_set_nx",
            AsyncMock(side_effect=ConnectionError("redis down")),
        ),
        patch("backend.utils.orphaned_hold_reconciler.reconcile_tick", reconcile_mock),
        patch("backend.utils.orphaned_hold_reconciler._record_heartbeat", MagicMock()),
    ):
        from backend.utils.orphaned_hold_reconciler import orphaned_hold_reconciler_loop

        with pytest.raises(_StopLoop):
            await orphaned_hold_reconciler_loop()

    reconcile_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_loop_runs_tick_and_logs_summary_when_orphans_found(caplog):
    sleep_calls: list[float] = []
    heartbeats: list[str] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) == 2:
            raise _StopLoop()

    reconcile_mock = AsyncMock(return_value={"found": 2, "released": 2})

    with (
        patch("asyncio.sleep", fake_sleep),
        patch("backend.utils.orphaned_hold_reconciler.redis_set_nx", AsyncMock(return_value=True)),
        patch("backend.utils.orphaned_hold_reconciler.reconcile_tick", reconcile_mock),
        patch("backend.utils.orphaned_hold_reconciler._record_heartbeat", lambda name: heartbeats.append(name)),
        caplog.at_level(logging.INFO),
    ):
        from backend.utils.orphaned_hold_reconciler import orphaned_hold_reconciler_loop

        with pytest.raises(_StopLoop):
            await orphaned_hold_reconciler_loop()

    reconcile_mock.assert_awaited_once()
    assert "tick summary" in caplog.text
    assert heartbeats


@pytest.mark.anyio
async def test_loop_does_not_log_summary_when_nothing_found(caplog):
    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) == 2:
            raise _StopLoop()

    reconcile_mock = AsyncMock(return_value={"found": 0})

    with (
        patch("asyncio.sleep", fake_sleep),
        patch("backend.utils.orphaned_hold_reconciler.redis_set_nx", AsyncMock(return_value=True)),
        patch("backend.utils.orphaned_hold_reconciler.reconcile_tick", reconcile_mock),
        patch("backend.utils.orphaned_hold_reconciler._record_heartbeat", MagicMock()),
    ):
        from backend.utils.orphaned_hold_reconciler import orphaned_hold_reconciler_loop

        with pytest.raises(_StopLoop), caplog.at_level(logging.INFO):
            await orphaned_hold_reconciler_loop()

    assert "tick summary" not in caplog.text


@pytest.mark.anyio
async def test_loop_swallows_generic_exception_and_increments_error_metric():
    sleep_calls: list[float] = []
    metric_calls: list[tuple] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) == 2:
            raise _StopLoop()

    with (
        patch("asyncio.sleep", fake_sleep),
        patch("backend.utils.orphaned_hold_reconciler.redis_set_nx", AsyncMock(return_value=True)),
        patch(
            "backend.utils.orphaned_hold_reconciler.reconcile_tick",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
        patch("backend.utils.orphaned_hold_reconciler._record_heartbeat", MagicMock()),
        patch(
            "backend.utils.orphaned_hold_reconciler._metric_inc",
            lambda name, labels=None, **kw: metric_calls.append((name, labels)),
        ),
    ):
        from backend.utils.orphaned_hold_reconciler import orphaned_hold_reconciler_loop

        # Must not propagate — the loop must survive a tick failure and keep going.
        with pytest.raises(_StopLoop):
            await orphaned_hold_reconciler_loop()

    assert ("spinr_bgloop_errors_total", {"loop": "orphaned_hold_reconciler"}) in metric_calls


@pytest.mark.anyio
async def test_loop_reraises_cancelled_error_instead_of_swallowing_it():
    """asyncio.CancelledError must propagate for clean task shutdown — the
    loop's `except asyncio.CancelledError: raise` exists specifically so a
    graceful shutdown isn't mistaken for a tick failure (which would emit a
    spurious error metric and keep looping)."""

    async def fake_initial_sleep(_seconds):
        return None

    with (
        patch("asyncio.sleep", fake_initial_sleep),
        patch("backend.utils.orphaned_hold_reconciler.redis_set_nx", AsyncMock(return_value=True)),
        patch(
            "backend.utils.orphaned_hold_reconciler.reconcile_tick",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        patch("backend.utils.orphaned_hold_reconciler._record_heartbeat", MagicMock()) as heartbeat_mock,
    ):
        from backend.utils.orphaned_hold_reconciler import orphaned_hold_reconciler_loop

        with pytest.raises(asyncio.CancelledError):
            await orphaned_hold_reconciler_loop()

    # The loop must exit via the CancelledError branch, not fall through to
    # the generic-exception branch's heartbeat + continue.
    heartbeat_mock.assert_not_called()
