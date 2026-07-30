"""Unit tests for backend/utils/stuck_ride_sweeper.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tests._factories import ride_row

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OLD_ISO = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
_RECENT_ISO = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()


def _sweep():
    """Import and return the _sweep coroutine after patches are in place."""
    import importlib

    import backend.utils.stuck_ride_sweeper as mod
    importlib.reload(mod)
    return mod._sweep


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.unit
async def test_stuck_searching_ride_is_cancelled():
    """A searching ride older than 5 min is claimed and WS event is sent."""
    stuck_ride = ride_row(
        id="ride-stuck-1",
        rider_id="rider-abc",
        status="searching",
        ride_requested_at=_OLD_ISO,
    )

    mock_ws = AsyncMock()
    mock_push = AsyncMock()

    with (
        patch("backend.utils.stuck_ride_sweeper.supabase", MagicMock()),
        patch("backend.db_supabase.run_sync", AsyncMock(return_value=[stuck_ride])),
        patch("backend.utils.stuck_ride_sweeper.manager.send_personal_message", mock_ws),
        patch("backend.utils.stuck_ride_sweeper.send_push_notification", mock_push),
        patch("backend.db_supabase.set_driver_available", AsyncMock()),
        patch("backend.utils.stuck_ride_sweeper._metric_inc", MagicMock()),
    ):
        from backend.utils.stuck_ride_sweeper import _sweep
        await _sweep()

    mock_ws.assert_awaited_once()
    ws_call = mock_ws.await_args[0]
    assert ws_call[0]["type"] == "ride_cancelled"
    assert ws_call[1] == "rider_rider-abc"


@pytest.mark.anyio
@pytest.mark.unit
async def test_in_progress_ride_not_cancelled():
    """An in_progress ride is never returned by the WHERE clause and never touched."""
    mock_ws = AsyncMock()

    # run_sync returns empty list — the WHERE eq("status","searching") excluded it
    with (
        patch("backend.utils.stuck_ride_sweeper.supabase", MagicMock()),
        patch("backend.db_supabase.run_sync", AsyncMock(return_value=[])),
        patch("backend.utils.stuck_ride_sweeper.manager.send_personal_message", mock_ws),
        patch("backend.utils.stuck_ride_sweeper._metric_inc", MagicMock()),
    ):
        from backend.utils.stuck_ride_sweeper import _sweep
        await _sweep()

    mock_ws.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.unit
async def test_recent_searching_ride_not_cancelled():
    """A searching ride less than 5 min old is excluded by the lt() cutoff."""
    mock_ws = AsyncMock()

    # run_sync returns empty list — the lt("ride_requested_at", cutoff) excluded it
    with (
        patch("backend.utils.stuck_ride_sweeper.supabase", MagicMock()),
        patch("backend.db_supabase.run_sync", AsyncMock(return_value=[])),
        patch("backend.utils.stuck_ride_sweeper.manager.send_personal_message", mock_ws),
        patch("backend.utils.stuck_ride_sweeper._metric_inc", MagicMock()),
    ):
        from backend.utils.stuck_ride_sweeper import _sweep
        await _sweep()

    mock_ws.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.unit
async def test_no_double_cancel_when_replica_already_claimed():
    """When UPDATE returns 0 rows (another replica claimed it), no WS or push is sent."""
    mock_ws = AsyncMock()
    mock_push = AsyncMock()

    with (
        patch("backend.utils.stuck_ride_sweeper.supabase", MagicMock()),
        patch("backend.db_supabase.run_sync", AsyncMock(return_value=[])),
        patch("backend.utils.stuck_ride_sweeper.manager.send_personal_message", mock_ws),
        patch("backend.utils.stuck_ride_sweeper.send_push_notification", mock_push),
        patch("backend.utils.stuck_ride_sweeper._metric_inc", MagicMock()),
    ):
        from backend.utils.stuck_ride_sweeper import _sweep
        await _sweep()

    mock_ws.assert_not_awaited()
    mock_push.assert_not_awaited()


# ---------------------------------------------------------------------------
# Booking-time card hold release (T2b)
#
# routes/rides/booking.py places a manual-capture PaymentIntent hold for
# grand_total + RIDE_AUTH_BUFFER_CAD BEFORE dispatch, so a ride sitting in
# `searching` has live funds reserved on the rider's card. The interactive cancel
# path releases it (routes/rides/cancellation.py, "WS-8 finding 11"); this sweeper
# cancelled the ride with a direct table UPDATE and never did, so the hold survived
# to Stripe's ~7-day auth expiry. Nothing else covered it: preauth_capture only
# looks at status='completed', stripe_reconcile only heals stuck *processing* rides.
# ---------------------------------------------------------------------------

from contextlib import ExitStack  # noqa: E402


def _held_ride(**over):
    """A stuck searching ride carrying an open booking-time hold."""
    base = dict(
        id="ride-held-1",
        rider_id="rider-held",
        status="searching",
        ride_requested_at=_OLD_ISO,
        payment_method="card",
        payment_intent_id="pi_booking_hold_1",
        auth_status="authorized",
        authorized_amount="27.50",
        grand_total="22.50",
    )
    base.update(over)
    return ride_row(**base)


def _run_sweep(rides, cancel_auth, update_one=None, metric=None, ws=None, push=None):
    """Enter every patch via ExitStack and return (sweep_coro, update_one, metric).

    ExitStack rather than a parenthesized `with`, because the patch set is built
    dynamically and context managers cannot be splatted into a `with` statement.
    """
    update_one = update_one if update_one is not None else AsyncMock(return_value={})
    metric = metric if metric is not None else MagicMock()
    stack = ExitStack()
    for cm in (
        patch("backend.db_supabase.run_sync", AsyncMock(return_value=rides)),
        patch("backend.utils.stuck_ride_sweeper.supabase", MagicMock()),
        patch("backend.utils.card_hold_release.cancel_authorization", cancel_auth),
        patch("backend.db_supabase.update_one", update_one),
        patch("backend.utils.stuck_ride_sweeper.manager.send_personal_message", ws or AsyncMock()),
        patch("backend.utils.stuck_ride_sweeper.send_push_notification", push or AsyncMock()),
        patch("backend.db_supabase.set_driver_available", AsyncMock()),
        patch("backend.utils.card_hold_release._metric_inc", metric),
    ):
        stack.enter_context(cm)
    return stack, update_one, metric


def _ride_updates(update_one):
    return [c for c in update_one.await_args_list if c.args and c.args[0] == "rides"]


def _outcomes(metric):
    return [c.args[1].get("outcome") for c in metric.call_args_list if len(c.args) > 1 and c.args[1]]


@pytest.mark.anyio
@pytest.mark.unit
async def test_hold_is_released_when_sweeper_cancels_a_searching_ride():
    """The bug this fixes: rider books, no drivers for 5 min, ride auto-cancelled,
    and $27.50 stayed reserved on their card for ~7 days."""
    cancel_auth = AsyncMock(return_value=True)
    stack, update_one, _ = _run_sweep([_held_ride()], cancel_auth)
    with stack:
        from backend.utils.stuck_ride_sweeper import _sweep

        await _sweep()

    cancel_auth.assert_awaited_once()
    kwargs = cancel_auth.await_args.kwargs
    assert kwargs["ride_id"] == "ride-held-1"
    assert kwargs["payment_intent_id"] == "pi_booking_hold_1"

    # And the hold is marked released so preauth_capture / reconcilers do not
    # later treat it as an open authorization.
    marked = _ride_updates(update_one)
    assert marked, "auth_status was not written"
    filters, update = marked[-1].args[1], marked[-1].args[2]
    assert filters["id"] == "ride-held-1"
    assert update["auth_status"] == "released"
    # Filtered on the open states so a concurrent writer cannot be overwritten.
    assert filters["auth_status"] == {"$in": ["authorized", "fare_only"]}


@pytest.mark.anyio
@pytest.mark.unit
@pytest.mark.parametrize("auth_status", ["authorized", "fare_only"])
async def test_both_open_hold_states_are_released(auth_status):
    """migration 156 defines two open states; a fare-only fallback hold reserves
    the rider's money just as much as a buffered one."""
    cancel_auth = AsyncMock(return_value=True)
    stack, _, _ = _run_sweep([_held_ride(auth_status=auth_status)], cancel_auth)
    with stack:
        from backend.utils.stuck_ride_sweeper import _sweep

        await _sweep()
    cancel_auth.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.unit
@pytest.mark.parametrize(
    "ride_over",
    [
        {"auth_status": "captured"},  # cancelling would reverse a real charge
        {"auth_status": "released"},  # nothing left to cancel
        {"auth_status": None},  # no pre-auth (wallet / corporate ride)
        {"payment_intent_id": None},  # no PaymentIntent recorded
        {"payment_intent_id": None, "auth_status": None},
    ],
)
async def test_no_release_attempt_when_there_is_nothing_to_release(ride_over):
    """Over-reach here would be worse than the bug: calling PaymentIntent.cancel on
    a captured intent attempts to reverse money the rider actually owes."""
    cancel_auth = AsyncMock(return_value=True)
    stack, update_one, _ = _run_sweep([_held_ride(**ride_over)], cancel_auth)
    with stack:
        from backend.utils.stuck_ride_sweeper import _sweep

        await _sweep()

    cancel_auth.assert_not_awaited()
    assert not _ride_updates(update_one)


@pytest.mark.anyio
@pytest.mark.unit
async def test_failed_release_does_not_mark_the_hold_released():
    """A false 'released' would hide a live hold from every future reconciler, so a
    failure must leave auth_status untouched and surface loudly."""
    cancel_auth = AsyncMock(return_value=False)
    stack, update_one, metric = _run_sweep([_held_ride()], cancel_auth)
    with stack:
        from backend.utils.stuck_ride_sweeper import _sweep

        await _sweep()

    assert not _ride_updates(update_one), "must not mark released when the Stripe cancel failed"
    assert "failed" in _outcomes(metric)


@pytest.mark.anyio
@pytest.mark.unit
async def test_release_failure_still_cancels_the_ride_and_notifies():
    """A Stripe outage must not strand the ride — the rider still needs telling that
    no drivers were found, and the row is already cancelled by the atomic claim."""
    ws = AsyncMock()
    cancel_auth = AsyncMock(side_effect=RuntimeError("stripe down"))
    stack, _, _ = _run_sweep([_held_ride()], cancel_auth, ws=ws)
    with stack:
        from backend.utils.stuck_ride_sweeper import _sweep

        await _sweep()  # must not raise

    ws.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.unit
async def test_bookkeeping_write_failure_is_not_reported_as_a_release_failure():
    """The money is already free at that point; conflating the two would send ops
    hunting for a stranded hold that does not exist."""
    cancel_auth = AsyncMock(return_value=True)
    stack, _, metric = _run_sweep(
        [_held_ride()], cancel_auth, update_one=AsyncMock(side_effect=RuntimeError("db down"))
    )
    with stack:
        from backend.utils.stuck_ride_sweeper import _sweep

        await _sweep()

    outcomes = _outcomes(metric)
    assert "released_unmarked" in outcomes
    assert "failed" not in outcomes


@pytest.mark.anyio
@pytest.mark.unit
async def test_hold_release_precedes_the_notification_awaits():
    """Ordering is deliberate: push/WS are network round-trips that can block for
    seconds, and money integrity must not queue behind a notification."""
    order: list[str] = []

    async def _cancel(**_kw):
        order.append("release")
        return True

    async def _ws(*_a, **_kw):
        order.append("ws")

    async def _push(*_a, **_kw):
        order.append("push")

    stack, _, _ = _run_sweep([_held_ride()], _cancel, ws=_ws, push=_push)
    with stack:
        from backend.utils.stuck_ride_sweeper import _sweep

        await _sweep()

    assert order and order[0] == "release", f"expected release first, got {order}"


@pytest.mark.anyio
@pytest.mark.unit
async def test_every_ride_in_a_multi_ride_sweep_gets_its_hold_released():
    """One rider's Stripe failure must not strand the next rider's funds."""
    rides = [
        _held_ride(id="r1", payment_intent_id="pi_1"),
        _held_ride(id="r2", payment_intent_id="pi_2"),
        _held_ride(id="r3", payment_intent_id="pi_3"),
    ]
    calls: list[str] = []

    async def _cancel(*, ride_id, payment_intent_id):
        calls.append(payment_intent_id)
        if payment_intent_id == "pi_2":
            raise RuntimeError("stripe blip")
        return True

    stack, _, _ = _run_sweep(rides, _cancel)
    with stack:
        from backend.utils.stuck_ride_sweeper import _sweep

        await _sweep()

    assert calls == ["pi_1", "pi_2", "pi_3"], f"sweep stopped early: {calls}"
