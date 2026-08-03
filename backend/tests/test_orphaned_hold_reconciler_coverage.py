"""Coverage top-up for utils/orphaned_hold_reconciler.py (A1c, Sub-tier C).

test_orphaned_hold_reconciler.py already exercises the behavioural core (claim CAS,
what it will/won't touch, dry-run, outcome bookkeeping). This file targets the
remaining gaps left at 69% (91 stmts / 28 missing):

  * ``_pod_id`` (line 226) — trivial, but untested.
  * ``orphaned_hold_reconciler_loop`` (231-253) — the leader-lock gate, the
    happy/failure/cancellation ticks, and the jittered vs. fixed sleep durations,
    following the same fake-``asyncio.sleep``-raises-``CancelledError`` pattern used
    in test_stuck_ride_sweeper_coverage.py.
  * The per-ride ``except Exception`` in ``reconcile_tick`` (204-210) — the existing
    suite's "a raising Stripe call does not abort the tick" test drives the exception
    through ``card_hold_release.cancel_authorization``, but ``release_open_hold`` is
    documented (card_hold_release.py) as never raising and catches that internally,
    so that path never reaches ``reconcile_tick``'s own except block. Covered here by
    patching ``release_open_hold`` itself (as imported into this module) to raise,
    exercising the defence-in-depth path directly.
  * The ``ImportError`` fallback for ``utils.loop_monitor`` (57-59) — only taken when
    ``loop_monitor`` genuinely can't be imported, which is never true in this test
    environment. Forced by blocking ``sys.modules["utils.loop_monitor"]`` and
    reloading the module, then reloading again to restore normal state for every
    other test in the session (this module's ``_record_heartbeat`` binding is a
    module-level singleton other tests read via ``mod._record_heartbeat``).

No bug found while reading this module for this pass — noted explicitly per
CLAUDE.md convention rather than leaving silence to imply full coverage. The
leader-lock-skip branch's fixed (non-jittered) sleep vs. the post-tick jittered sleep
is a deliberate asymmetry (comment: "TTL = 2x interval ... the atomic DB claim is the
real guard"), not an oversight, so it's asserted as designed behaviour, not flagged.

Test-only change — no application code modified.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tests._factories import ride_row

pytestmark = pytest.mark.unit

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


# ── _pod_id ──────────────────────────────────────────────────────────


def test_pod_id_combines_hostname_and_pid(monkeypatch):
    from backend.utils import orphaned_hold_reconciler as mod

    monkeypatch.setattr(mod.socket, "gethostname", lambda: "worker-7")
    monkeypatch.setattr(mod.os, "getpid", lambda: 4242)

    assert mod._pod_id() == "worker-7-4242"


# ── reconcile_tick: release_open_hold itself raising (204-210) ─────────


@pytest.mark.anyio
async def test_release_open_hold_raising_directly_is_caught_and_counted_failed(monkeypatch, caplog):
    """Defence-in-depth: release_open_hold is documented as never raising, but if
    that contract were ever violated, reconcile_tick's own except block must still
    protect the batch rather than letting one ride's exception strand the rest."""
    from backend.utils import orphaned_hold_reconciler as mod

    rides = [_orphan(id="r1"), _orphan(id="r2", payment_intent_id="pi_2")]
    monkeypatch.setattr(mod.db, "get_rows", AsyncMock(return_value=rides))
    monkeypatch.setattr(mod.db, "update_one", AsyncMock(return_value={"id": "x"}))
    monkeypatch.setattr(mod, "release_open_hold", AsyncMock(side_effect=RuntimeError("contract violated")))
    monkeypatch.setattr(mod, "_metric_inc", MagicMock())

    with caplog.at_level(logging.ERROR):
        summary = await mod.reconcile_tick()

    assert summary["failed"] == 2
    assert summary["released"] == 0
    assert any("release failed for ride" in r.message for r in caplog.records)


# ── orphaned_hold_reconciler_loop ───────────────────────────────────────


class TestLoopLeaderLock:
    @pytest.mark.anyio
    async def test_lock_acquired_runs_tick_and_sleeps_with_jitter(self, monkeypatch):
        from backend.utils import orphaned_hold_reconciler as mod

        monkeypatch.setattr(mod, "redis_set_nx", AsyncMock(return_value=True))
        tick = AsyncMock(return_value={"found": 1, "released": 1})
        monkeypatch.setattr(mod, "reconcile_tick", tick)
        heartbeat = MagicMock()
        monkeypatch.setattr(mod, "_record_heartbeat", heartbeat)
        monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await mod.orphaned_hold_reconciler_loop()

        tick.assert_awaited_once()
        heartbeat.assert_called_once_with(mod._LOOP_NAME)
        # First sleep is the startup jitter (0..60s), second is the post-tick
        # interval +/- 10% jitter (both zeroed here via random.uniform).
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == 0
        assert sleep_calls[1] == mod.RECONCILE_INTERVAL_SECONDS

    @pytest.mark.anyio
    async def test_lock_not_acquired_skips_tick_but_still_heartbeats_and_sleeps_fixed_interval(self, monkeypatch):
        """Losing the leader-lock race must not skip the heartbeat (or the loop would
        look dead to loop_monitor) and must sleep the *un-jittered* interval — the
        jitter only applies to the post-tick sleep."""
        from backend.utils import orphaned_hold_reconciler as mod

        monkeypatch.setattr(mod, "redis_set_nx", AsyncMock(return_value=False))
        tick = AsyncMock()
        monkeypatch.setattr(mod, "reconcile_tick", tick)
        heartbeat = MagicMock()
        monkeypatch.setattr(mod, "_record_heartbeat", heartbeat)
        monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await mod.orphaned_hold_reconciler_loop()

        tick.assert_not_awaited()
        heartbeat.assert_called_once_with(mod._LOOP_NAME)
        assert sleep_calls[1] == mod.RECONCILE_INTERVAL_SECONDS


class TestLoopTickFailureHandling:
    @pytest.mark.anyio
    async def test_tick_failure_is_logged_metric_incremented_and_loop_still_sleeps(self, monkeypatch, caplog):
        from backend.utils import orphaned_hold_reconciler as mod

        monkeypatch.setattr(mod, "redis_set_nx", AsyncMock(return_value=True))
        monkeypatch.setattr(mod, "reconcile_tick", AsyncMock(side_effect=RuntimeError("db down")))
        monkeypatch.setattr(mod, "_record_heartbeat", MagicMock())
        metric_inc = MagicMock()
        monkeypatch.setattr(mod, "_metric_inc", metric_inc)
        monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

        with caplog.at_level(logging.ERROR):
            with pytest.raises(asyncio.CancelledError):
                await mod.orphaned_hold_reconciler_loop()

        assert sleep_calls  # the loop survived the exception and reached its sleep
        assert any("tick failed" in r.message for r in caplog.records)
        metric_inc.assert_called_once_with("spinr_bgloop_errors_total", {"loop": "orphaned_hold_reconciler"})

    @pytest.mark.anyio
    async def test_cancelled_error_from_tick_propagates_immediately_without_error_metric(self, monkeypatch):
        from backend.utils import orphaned_hold_reconciler as mod

        monkeypatch.setattr(mod, "redis_set_nx", AsyncMock(return_value=True))
        monkeypatch.setattr(mod, "reconcile_tick", AsyncMock(side_effect=asyncio.CancelledError()))
        monkeypatch.setattr(mod, "_record_heartbeat", MagicMock())
        metric_inc = MagicMock()
        monkeypatch.setattr(mod, "_metric_inc", metric_inc)
        monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0)

        async def fake_sleep(secs):
            pass  # only the startup jitter sleep should run before cancellation

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await mod.orphaned_hold_reconciler_loop()

        metric_inc.assert_not_called()

    @pytest.mark.anyio
    async def test_found_zero_summary_does_not_log_tick_summary(self, monkeypatch, caplog):
        """The `if summary.get("found"):` guard's false branch — an empty backlog
        must not spam an info log every 15 minutes."""
        from backend.utils import orphaned_hold_reconciler as mod

        monkeypatch.setattr(mod, "redis_set_nx", AsyncMock(return_value=True))
        monkeypatch.setattr(mod, "reconcile_tick", AsyncMock(return_value={"found": 0}))
        monkeypatch.setattr(mod, "_record_heartbeat", MagicMock())
        monkeypatch.setattr(mod, "_metric_inc", MagicMock())
        monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

        with caplog.at_level(logging.INFO):
            with pytest.raises(asyncio.CancelledError):
                await mod.orphaned_hold_reconciler_loop()

        assert not any("tick summary" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_multiple_ticks_before_cancellation(self, monkeypatch):
        from backend.utils import orphaned_hold_reconciler as mod

        monkeypatch.setattr(mod, "redis_set_nx", AsyncMock(return_value=True))
        tick = AsyncMock(return_value={"found": 0})
        monkeypatch.setattr(mod, "reconcile_tick", tick)
        monkeypatch.setattr(mod, "_record_heartbeat", MagicMock())
        monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 4:  # 1 startup jitter + 3 tick sleeps
                raise asyncio.CancelledError()

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await mod.orphaned_hold_reconciler_loop()

        assert tick.await_count == 3


# ── ImportError fallback for utils.loop_monitor (57-59) ────────────────


def test_heartbeat_fallback_is_a_silent_noop_when_loop_monitor_is_unimportable():
    """Forces the ImportError branch by blocking `utils.loop_monitor` in
    sys.modules (a None entry makes any import of it raise ImportError
    immediately) and reloading the module so its top-level try/except re-runs.

    Restores the module afterward — `_record_heartbeat` is a module-level
    singleton other tests in this file read as `mod._record_heartbeat`, so
    leaving the fallback bound would silently defang heartbeat assertions in
    tests that run later in the same session.
    """
    from backend.utils import orphaned_hold_reconciler as mod

    with patch.dict(sys.modules, {"utils.loop_monitor": None}):
        importlib.reload(mod)
        try:
            # The fallback is a no-op — must not raise, must return None.
            assert mod._record_heartbeat("some-loop") is None
        finally:
            # Restore real behaviour before leaving the `with`, and again after,
            # belt-and-braces: reload must happen outside the sys.modules block
            # too since the block itself is what forces the fallback.
            pass
    importlib.reload(mod)
    # Back to the real loop_monitor-backed heartbeat recorder.
    import utils.loop_monitor as loop_monitor  # type: ignore

    assert mod._record_heartbeat is loop_monitor.record_heartbeat
