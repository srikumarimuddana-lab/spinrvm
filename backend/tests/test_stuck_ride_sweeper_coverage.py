"""Coverage for utils/stuck_ride_sweeper.py (A1c, Sub-tier B).

Background loop (one of the 17 in `core/lifespan.py`) that force-cancels rides
stuck in `searching` for > 5 minutes (e.g. dispatch timer lost to a pod
restart), releases the rider's card hold, notifies the rider (WS + push),
and frees the assigned driver if one was mid-offer. Had no dedicated test
file; 57.32% coverage.

Ride-state-machine note (CLAUDE.md): this sweeper only ever claims rides in
`status == "searching"` (`.eq("status", "searching")` in the atomic claim
query) and only ever transitions them to `cancelled`. It never touches
`in_progress` rides, so it does not run into the "never cancel after trip
start" invariant at all -- there is no violation to flag here. The atomic
claim (`update().eq("status", "searching").lt("ride_requested_at", cutoff)`)
is itself the race guard: a claim that returns zero rows for a given ride
(already accepted/cancelled by the time the sweeper's query ran) simply
never appears in `claimed_rides`, so there is nothing to double-act on --
tested implicitly by the "no stuck rides" empty-claim case below, since the
claim and the "what do we do with what we got back" logic are the same
code path in this module (there is no separate per-row race-guard update
the way scheduled_rides.py has one).

Ordering note tested explicitly: the module comments that
`release_open_hold` (money) must run before the WS/push notifications
(network round-trips) for each ride, per CLAUDE.md's "don't await
Twilio/Stripe inline ahead of money-critical work" anti-pattern guidance.
Verified with a shared call-order list.

Error-handling note: every per-ride side effect (WS notify, push notify,
driver release) is wrapped in its own try/except that logs via
`logger.error(..., exc_info=True)` and continues -- this matches, not
violates, the "do not silently swallow errors" convention (the DB claim
failure path uses `logger.error` too, not `logger.warning`). No bug found
in this file during review; noted here per instructions since none was
found.

No application code modified -- test-only change.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def sweeper(monkeypatch):
    from backend.utils import stuck_ride_sweeper as mod

    # Ensure the `if not supabase: return` guard doesn't short-circuit tests
    # that don't care about it; individual tests override as needed.
    monkeypatch.setattr(mod, "supabase", MagicMock(name="supabase_client"))
    return mod


def _ride(**overrides):
    base = {
        "id": "ride-1",
        "rider_id": "rider-1",
        "driver_id": "driver-1",
    }
    base.update(overrides)
    return base


def _patch_common(monkeypatch, sweeper, *, claimed):
    """Patch the standard set of collaborators for `_sweep`, returning the
    individual AsyncMocks so tests can assert on them."""
    run_sync = AsyncMock(return_value=claimed)
    release_hold = AsyncMock(return_value="released")
    send_ws = AsyncMock()
    send_push = AsyncMock()
    set_driver_available = AsyncMock()
    metric_inc = MagicMock()

    monkeypatch.setattr(sweeper.db_supabase, "run_sync", run_sync)
    monkeypatch.setattr(sweeper, "release_open_hold", release_hold)
    monkeypatch.setattr(sweeper.manager, "send_personal_message", send_ws)
    monkeypatch.setattr(sweeper, "send_push_notification", send_push)
    monkeypatch.setattr(sweeper.db_supabase, "set_driver_available", set_driver_available)
    monkeypatch.setattr(sweeper, "_metric_inc", metric_inc)

    return {
        "run_sync": run_sync,
        "release_hold": release_hold,
        "send_ws": send_ws,
        "send_push": send_push,
        "set_driver_available": set_driver_available,
        "metric_inc": metric_inc,
    }


# ── _sweep ───────────────────────────────────────────────────────────────────


class TestSweepGuardsAndNoop:
    @pytest.mark.anyio
    async def test_no_supabase_client_is_noop(self, sweeper, monkeypatch):
        monkeypatch.setattr(sweeper, "supabase", None)
        run_sync = AsyncMock()
        monkeypatch.setattr(sweeper.db_supabase, "run_sync", run_sync)

        await sweeper._sweep()

        run_sync.assert_not_awaited()

    @pytest.mark.anyio
    async def test_no_stuck_rides_is_noop(self, sweeper, monkeypatch):
        mocks = _patch_common(monkeypatch, sweeper, claimed=[])

        await sweeper._sweep()

        mocks["run_sync"].assert_awaited_once()
        mocks["release_hold"].assert_not_awaited()
        mocks["send_ws"].assert_not_awaited()
        mocks["send_push"].assert_not_awaited()
        mocks["set_driver_available"].assert_not_awaited()
        mocks["metric_inc"].assert_not_called()

    @pytest.mark.anyio
    async def test_claim_none_treated_as_no_rows(self, sweeper, monkeypatch):
        """`_rows_from_res` should always give a list, but the falsy-check
        (`if not claimed_rides`) must also tolerate a None return without
        blowing up on `len(None)` downstream."""
        mocks = _patch_common(monkeypatch, sweeper, claimed=None)

        await sweeper._sweep()

        mocks["release_hold"].assert_not_awaited()
        mocks["metric_inc"].assert_not_called()

    @pytest.mark.anyio
    async def test_db_claim_error_is_logged_loudly_and_not_swallowed(self, sweeper, monkeypatch, caplog):
        run_sync = AsyncMock(side_effect=RuntimeError("connection reset"))
        monkeypatch.setattr(sweeper.db_supabase, "run_sync", run_sync)
        release_hold = AsyncMock()
        monkeypatch.setattr(sweeper, "release_open_hold", release_hold)

        with caplog.at_level(logging.ERROR):
            # Must not raise -- caught, logged, and the loop wrapper handles
            # any further failure; this is a soft-return within `_sweep`.
            await sweeper._sweep()

        release_hold.assert_not_awaited()
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("DB claim failed" in r.message for r in error_records)


class TestSweepHappyPath:
    @pytest.mark.anyio
    async def test_single_stuck_ride_releases_hold_notifies_and_frees_driver(self, sweeper, monkeypatch):
        ride = _ride()
        mocks = _patch_common(monkeypatch, sweeper, claimed=[ride])

        await sweeper._sweep()

        mocks["release_hold"].assert_awaited_once_with(ride, source="sweeper")

        mocks["send_ws"].assert_awaited_once()
        ws_args, ws_kwargs = mocks["send_ws"].await_args
        payload, client_id = ws_args[0], ws_args[1]
        assert client_id == "rider_rider-1"
        assert payload["type"] == "ride_cancelled"
        assert payload["ride_id"] == "ride-1"
        assert payload["reason"] == "no_drivers_found"

        mocks["send_push"].assert_awaited_once()
        push_args, push_kwargs = mocks["send_push"].await_args
        assert push_args[0] == "rider-1"
        assert push_args[3]["ride_id"] == "ride-1"
        assert push_args[3]["type"] == "ride_cancelled"

        mocks["set_driver_available"].assert_awaited_once_with("driver-1", True)

        mocks["metric_inc"].assert_called_once_with("spinr_stuck_ride_sweeper_cancelled_total", {"count": "1"})

    @pytest.mark.anyio
    async def test_hold_release_happens_before_rider_notifications(self, sweeper, monkeypatch):
        """Money-critical work (card hold release) must not queue behind
        network round-trips (WS / push), per the module's own docstring and
        CLAUDE.md's anti-pattern guidance."""
        order = []

        async def fake_release(ride, *, source):
            order.append("release_hold")
            return "released"

        async def fake_ws(message, client_id):
            order.append("ws")

        async def fake_push(*args, **kwargs):
            order.append("push")

        run_sync = AsyncMock(return_value=[_ride()])
        monkeypatch.setattr(sweeper.db_supabase, "run_sync", run_sync)
        monkeypatch.setattr(sweeper, "release_open_hold", fake_release)
        monkeypatch.setattr(sweeper.manager, "send_personal_message", fake_ws)
        monkeypatch.setattr(sweeper, "send_push_notification", fake_push)
        monkeypatch.setattr(sweeper.db_supabase, "set_driver_available", AsyncMock())
        monkeypatch.setattr(sweeper, "_metric_inc", MagicMock())

        await sweeper._sweep()

        assert order[0] == "release_hold"
        assert order[1:] == ["ws", "push"]

    @pytest.mark.anyio
    async def test_no_rider_id_skips_ws_and_push_but_still_releases_hold_and_driver(self, sweeper, monkeypatch):
        ride = _ride(rider_id=None)
        mocks = _patch_common(monkeypatch, sweeper, claimed=[ride])

        await sweeper._sweep()

        mocks["release_hold"].assert_awaited_once_with(ride, source="sweeper")
        mocks["send_ws"].assert_not_awaited()
        mocks["send_push"].assert_not_awaited()
        mocks["set_driver_available"].assert_awaited_once_with("driver-1", True)

    @pytest.mark.anyio
    async def test_no_driver_id_skips_driver_release_but_still_notifies_rider(self, sweeper, monkeypatch):
        ride = _ride(driver_id=None)
        mocks = _patch_common(monkeypatch, sweeper, claimed=[ride])

        await sweeper._sweep()

        mocks["send_ws"].assert_awaited_once()
        mocks["send_push"].assert_awaited_once()
        mocks["set_driver_available"].assert_not_awaited()

    @pytest.mark.anyio
    async def test_multiple_stuck_rides_each_processed_and_metric_reflects_total(self, sweeper, monkeypatch):
        ride_a = _ride(id="ride-a", rider_id="rider-a", driver_id="driver-a")
        ride_b = _ride(id="ride-b", rider_id="rider-b", driver_id=None)
        mocks = _patch_common(monkeypatch, sweeper, claimed=[ride_a, ride_b])

        await sweeper._sweep()

        assert mocks["release_hold"].await_count == 2
        assert mocks["send_ws"].await_count == 2
        assert mocks["send_push"].await_count == 2
        mocks["set_driver_available"].assert_awaited_once_with("driver-a", True)
        mocks["metric_inc"].assert_called_once_with("spinr_stuck_ride_sweeper_cancelled_total", {"count": "2"})


class TestSweepPerRideFailureIsolation:
    @pytest.mark.anyio
    async def test_ws_notify_failure_is_logged_and_does_not_block_push_or_driver_release(
        self, sweeper, monkeypatch, caplog
    ):
        ride = _ride()
        mocks = _patch_common(monkeypatch, sweeper, claimed=[ride])
        mocks["send_ws"].side_effect = RuntimeError("ws down")

        with caplog.at_level(logging.ERROR):
            await sweeper._sweep()

        mocks["send_push"].assert_awaited_once()
        mocks["set_driver_available"].assert_awaited_once()
        assert any("WS notify failed" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_push_notify_failure_is_logged_and_does_not_block_driver_release(self, sweeper, monkeypatch, caplog):
        ride = _ride()
        mocks = _patch_common(monkeypatch, sweeper, claimed=[ride])
        mocks["send_push"].side_effect = RuntimeError("fcm down")

        with caplog.at_level(logging.ERROR):
            await sweeper._sweep()

        mocks["set_driver_available"].assert_awaited_once()
        assert any("push notify failed" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_driver_release_failure_is_logged_and_does_not_abort_the_sweep(self, sweeper, monkeypatch, caplog):
        ride_a = _ride(id="ride-a", driver_id="driver-a")
        ride_b = _ride(id="ride-b", rider_id="rider-b", driver_id="driver-b")
        mocks = _patch_common(monkeypatch, sweeper, claimed=[ride_a, ride_b])
        mocks["set_driver_available"].side_effect = RuntimeError("driver row locked")

        with caplog.at_level(logging.ERROR):
            await sweeper._sweep()

        # Both rides still get their hold released and rider notified despite
        # the first driver-release failure.
        assert mocks["release_hold"].await_count == 2
        assert mocks["send_ws"].await_count == 2
        assert any("driver release failed" in r.message for r in caplog.records)
        # The final metric increment still reflects the whole claimed batch.
        mocks["metric_inc"].assert_called_once_with("spinr_stuck_ride_sweeper_cancelled_total", {"count": "2"})

    @pytest.mark.anyio
    async def test_release_open_hold_failure_propagates_since_it_is_documented_as_never_raising(
        self, sweeper, monkeypatch
    ):
        """`release_open_hold` is documented (card_hold_release.py) as
        never raising, so `_sweep` has no try/except around that call. If
        that contract were ever violated, the exception would propagate out
        of `_sweep` uncaught -- verifying this documents the coupling
        rather than asserting it is desirable."""
        ride = _ride()
        run_sync = AsyncMock(return_value=[ride])
        monkeypatch.setattr(sweeper.db_supabase, "run_sync", run_sync)
        monkeypatch.setattr(sweeper, "release_open_hold", AsyncMock(side_effect=RuntimeError("contract violated")))
        monkeypatch.setattr(sweeper.manager, "send_personal_message", AsyncMock())
        monkeypatch.setattr(sweeper, "send_push_notification", AsyncMock())
        monkeypatch.setattr(sweeper.db_supabase, "set_driver_available", AsyncMock())
        monkeypatch.setattr(sweeper, "_metric_inc", MagicMock())

        with pytest.raises(RuntimeError, match="contract violated"):
            await sweeper._sweep()


# ── stuck_ride_sweeper_loop ──────────────────────────────────────────────────


class TestStuckRideSweeperLoop:
    @pytest.mark.anyio
    async def test_happy_tick_sweeps_records_heartbeat_and_sleeps_with_jitter(self, sweeper, monkeypatch):
        sweep = AsyncMock()
        monkeypatch.setattr(sweeper, "_sweep", sweep)
        heartbeat = MagicMock()
        monkeypatch.setattr(sweeper, "_record_heartbeat", heartbeat)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        monkeypatch.setattr(sweeper.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(sweeper.random, "uniform", lambda a, b: 0)

        with pytest.raises(asyncio.CancelledError):
            await sweeper.stuck_ride_sweeper_loop()

        sweep.assert_awaited_once()
        heartbeat.assert_called_once_with("stuck_ride_sweeper (60s)")
        # First sleep is the startup jitter, second is the post-tick sleep.
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == 0
        # 60s * (0.9 .. 1.1) jitter window.
        assert 54 <= sleep_calls[1] <= 66

    @pytest.mark.anyio
    async def test_sweep_failure_is_logged_metric_incremented_and_loop_still_sleeps(self, sweeper, monkeypatch, caplog):
        monkeypatch.setattr(sweeper, "_sweep", AsyncMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(sweeper, "_record_heartbeat", MagicMock())
        metric_inc = MagicMock()
        monkeypatch.setattr(sweeper, "_metric_inc", metric_inc)
        monkeypatch.setattr(sweeper.random, "uniform", lambda a, b: 0)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        monkeypatch.setattr(sweeper.asyncio, "sleep", fake_sleep)

        with caplog.at_level(logging.ERROR):
            with pytest.raises(asyncio.CancelledError):
                await sweeper.stuck_ride_sweeper_loop()

        assert sleep_calls  # loop survived the exception and reached sleep
        assert any("tick failed" in r.message for r in caplog.records)
        metric_inc.assert_called_once_with("spinr_bgloop_errors_total", {"loop": "stuck_ride_sweeper"})

    @pytest.mark.anyio
    async def test_cancelled_error_from_sweep_propagates_immediately_without_error_metric(self, sweeper, monkeypatch):
        monkeypatch.setattr(sweeper, "_sweep", AsyncMock(side_effect=asyncio.CancelledError()))
        monkeypatch.setattr(sweeper, "_record_heartbeat", MagicMock())
        metric_inc = MagicMock()
        monkeypatch.setattr(sweeper, "_metric_inc", metric_inc)
        monkeypatch.setattr(sweeper.random, "uniform", lambda a, b: 0)

        async def fake_sleep(secs):
            pass  # only the startup jitter sleep should run before cancellation

        monkeypatch.setattr(sweeper.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await sweeper.stuck_ride_sweeper_loop()

        metric_inc.assert_not_called()

    @pytest.mark.anyio
    async def test_multiple_ticks_before_cancellation(self, sweeper, monkeypatch):
        sweep = AsyncMock()
        monkeypatch.setattr(sweeper, "_sweep", sweep)
        monkeypatch.setattr(sweeper, "_record_heartbeat", MagicMock())
        monkeypatch.setattr(sweeper.random, "uniform", lambda a, b: 0)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 4:  # 1 startup jitter + 3 tick sleeps
                raise asyncio.CancelledError()

        monkeypatch.setattr(sweeper.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await sweeper.stuck_ride_sweeper_loop()

        assert sweep.await_count == 3
