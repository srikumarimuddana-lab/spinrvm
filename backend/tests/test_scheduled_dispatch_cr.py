"""Regression tests for the scheduled-ride dispatch fixes (CR-1, CR-2).

CR-1: create_ride must NOT dispatch a live driver for a future-dated ride.
      A deferred scheduled ride is parked in status 'scheduled' and
      match_driver_to_ride is skipped at booking time.

CR-2: the scheduled dispatcher must query status='scheduled' (not 'searching')
      and atomically transition scheduled → searching before matching, so the
      timed-dispatch + reminder loop actually sees correctly-stored rides.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RIDE_ID = "ride_cr_001"
RIDER_ID = "rider_cr_001"


def _scheduled_row(**extra) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": "scheduled",
        "is_scheduled": True,
        "scheduled_time": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "dropoff_address": "456 Broadway",
        **extra,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CR-2: dispatcher claim + transition
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestScheduledDispatch:
    async def test_dispatch_claims_scheduled_to_searching_then_matches(self):
        from backend.utils import scheduled_rides as sr

        ride = _scheduled_row()
        update_calls: list = []

        async def _update_one(table, filters, update):
            update_calls.append((table, filters, update))
            return {**ride, "status": "searching"}  # claim succeeds

        match_mock = AsyncMock()

        async def _timeout(*_a, **_k):  # stand-in for ride_search_timeout
            return None

        # Patch the exact module path the dispatcher imports from. conftest
        # puts backend/ on sys.path, so `from routes.rides import ...` (the
        # first branch in _dispatch_scheduled_ride) resolves to the `routes.rides`
        # module — patch there, not the `backend.`-qualified alias.
        with (
            patch.object(sr.db, "update_one", AsyncMock(side_effect=_update_one)),
            patch.object(sr.db, "get_user_by_id", AsyncMock(return_value={"id": RIDER_ID, "first_name": "Ada"})),
            patch("routes.rides.matching.match_driver_to_ride", match_mock),
            patch("routes.rides.matching.ride_search_timeout", _timeout),
            patch.object(sr.manager, "broadcast_ride_status", AsyncMock()) as bcast,
            patch.object(sr.manager, "broadcast_to_admins", AsyncMock()) as admin_bcast,
            patch.object(sr, "send_push_notification", AsyncMock()),
            # Swallow the fire-and-forget ride_search_timeout task without leaking
            # an un-awaited coroutine.
            patch("asyncio.create_task", lambda coro: coro.close()),
        ):
            await sr._dispatch_scheduled_ride(ride)

        # Atomic claim filters on status='scheduled' and writes status='searching'.
        assert update_calls, "no DB claim attempted"
        _, filters, update = update_calls[0]
        assert filters == {"id": RIDE_ID, "status": "scheduled"}
        assert update["$set"]["status"] == "searching"
        # Driver matching ran for the claimed ride.
        match_mock.assert_awaited_once_with(RIDE_ID)
        # State-change WS event emitted to rider + admins.
        bcast.assert_awaited_once()
        # A full ride_requested event is emitted so admin monitoring adds the
        # newly-live ride as a new row (nested MonitoringRide contract).
        admin_bcast.assert_awaited_once()
        admin_payload = admin_bcast.await_args.args[0]
        assert admin_payload["type"] == "ride_requested"
        assert admin_payload["ride"]["id"] == RIDE_ID
        assert admin_payload["ride"]["status"] == "searching"

    async def test_dispatch_aborts_when_claim_lost(self):
        """If another replica already flipped the row, update_one returns None
        and we must NOT run driver matching again."""
        from backend.utils import scheduled_rides as sr

        ride = _scheduled_row()
        match_mock = AsyncMock()

        with (
            patch.object(sr.db, "update_one", AsyncMock(return_value=None)),
            patch("routes.rides.matching.match_driver_to_ride", match_mock),
            patch.object(sr.manager, "broadcast_ride_status", AsyncMock()) as bcast,
            patch.object(sr, "send_push_notification", AsyncMock()),
        ):
            await sr._dispatch_scheduled_ride(ride)

        match_mock.assert_not_awaited()
        bcast.assert_not_awaited()

    async def test_dispatch_defers_on_active_ride_conflict(self):
        """If the rider has a live trip, the scheduled→searching claim collides
        with rides_one_active_per_rider. That conflict must be handled
        gracefully: no driver matching, ride left for retry, rider notified once
        — and the exception must NOT bubble out of the dispatcher."""
        from backend.utils import scheduled_rides as sr

        ride = _scheduled_row()
        match_mock = AsyncMock()

        def _raise_conflict(*_a, **_k):
            raise RuntimeError('duplicate key value violates unique constraint "rides_one_active_per_rider"')

        with (
            patch.object(sr.db, "update_one", AsyncMock(side_effect=_raise_conflict)),
            patch("routes.rides.matching.match_driver_to_ride", match_mock),
            patch.object(sr.manager, "broadcast_ride_status", AsyncMock()) as bcast,
            patch.object(sr, "redis_set_nx", AsyncMock(return_value=True)),
            patch.object(sr, "send_push_notification", AsyncMock()) as push,
        ):
            # Must not raise.
            await sr._dispatch_scheduled_ride(ride)

        match_mock.assert_not_awaited()
        bcast.assert_not_awaited()
        # Rider told once that the ride is waiting on their current trip.
        push.assert_awaited_once()
        assert push.await_args.args[0] == RIDER_ID

    async def test_defer_below_threshold_sends_routine_notice_only(self):
        """Finding #03: below the escalation threshold, only the routine
        'waiting on your other trip' push fires — no error log, no metric,
        no admin broadcast."""
        from backend.utils import scheduled_rides as sr

        ride = _scheduled_row()

        def _raise_conflict(*_a, **_k):
            raise RuntimeError('duplicate key value violates unique constraint "rides_one_active_per_rider"')

        with (
            patch.object(sr.db, "update_one", AsyncMock(side_effect=_raise_conflict)),
            patch.object(sr, "redis_set_nx", AsyncMock(return_value=True)),
            patch.object(sr, "redis_incr", AsyncMock(return_value=1)),
            patch.object(sr, "redis_expire", AsyncMock()),
            patch.object(sr.manager, "broadcast_to_admins", AsyncMock()) as admin_bcast,
            patch.object(sr, "send_push_notification", AsyncMock()) as push,
        ):
            await sr._dispatch_scheduled_ride(ride)

        admin_bcast.assert_not_awaited()
        push.assert_awaited_once()
        assert push.await_args.args[1] == "Your scheduled ride is waiting"

    async def test_defer_at_threshold_escalates_once(self):
        """At exactly _SCHEDULE_DEFER_ESCALATE_AFTER deferrals, escalate:
        error log + metric + admin broadcast + the distinct escalated push —
        not a hard cancel, the ride keeps retrying."""
        from backend.utils import scheduled_rides as sr

        ride = _scheduled_row()

        def _raise_conflict(*_a, **_k):
            raise RuntimeError('duplicate key value violates unique constraint "rides_one_active_per_rider"')

        with (
            patch.object(sr.db, "update_one", AsyncMock(side_effect=_raise_conflict)),
            patch.object(sr, "redis_set_nx", AsyncMock(return_value=True)),
            patch.object(sr, "redis_incr", AsyncMock(return_value=sr._SCHEDULE_DEFER_ESCALATE_AFTER)),
            patch.object(sr, "redis_expire", AsyncMock()),
            patch.object(sr, "_metric_inc") as metric_inc,
            patch.object(sr.manager, "broadcast_to_admins", AsyncMock()) as admin_bcast,
            patch.object(sr, "send_push_notification", AsyncMock()) as push,
        ):
            await sr._dispatch_scheduled_ride(ride)

        admin_bcast.assert_awaited_once()
        admin_payload = admin_bcast.await_args.args[0]
        assert admin_payload["type"] == "scheduled_ride_stuck"
        assert admin_payload["ride_id"] == RIDE_ID
        metric_inc.assert_called_once_with("spinr_dispatch_scheduled_defer_exhausted_total")
        push.assert_awaited_once()
        assert push.await_args.args[1] == "Still working on your scheduled ride"

    async def test_defer_past_threshold_does_not_re_escalate_every_tick(self):
        """Once past the threshold, the admin broadcast and metric fire only
        on the tick that first crosses it — not on every subsequent tick —
        so ops isn't paged repeatedly for the same still-stuck ride."""
        from backend.utils import scheduled_rides as sr

        ride = _scheduled_row()

        def _raise_conflict(*_a, **_k):
            raise RuntimeError('duplicate key value violates unique constraint "rides_one_active_per_rider"')

        with (
            patch.object(sr.db, "update_one", AsyncMock(side_effect=_raise_conflict)),
            patch.object(sr, "redis_set_nx", AsyncMock(return_value=True)),
            patch.object(sr, "redis_incr", AsyncMock(return_value=sr._SCHEDULE_DEFER_ESCALATE_AFTER + 5)),
            patch.object(sr, "redis_expire", AsyncMock()),
            patch.object(sr, "_metric_inc") as metric_inc,
            patch.object(sr.manager, "broadcast_to_admins", AsyncMock()) as admin_bcast,
            patch.object(sr, "send_push_notification", AsyncMock()) as push,
        ):
            await sr._dispatch_scheduled_ride(ride)

        admin_bcast.assert_not_awaited()
        metric_inc.assert_not_called()
        # The rider still gets nudged (its own 1h dedupe key governs repeats).
        push.assert_awaited_once()

    async def test_check_queries_scheduled_status(self):
        """check_scheduled_rides must read rows in status='scheduled'."""
        from backend.utils import scheduled_rides as sr

        get_rows_calls: list = []

        async def _get_rows(table, filters, **kwargs):
            get_rows_calls.append((table, filters))
            return []

        with (
            patch.object(sr, "redis_set_nx", AsyncMock(return_value=True)),
            patch.object(sr.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        ):
            await sr.check_scheduled_rides()

        assert get_rows_calls, "scheduled rides were never queried"
        _, filters = get_rows_calls[0]
        assert filters.get("status") == "scheduled"
        assert filters.get("is_scheduled") is True
