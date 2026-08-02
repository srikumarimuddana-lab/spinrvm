"""
Integration test for dispatch-time pre-authorization of scheduled rides
(backend/utils/scheduled_rides.py::_dispatch_scheduled_ride).

A scheduled ride may be booked days out — beyond Stripe's ~7-day auth lifetime —
so the card hold is placed when the ride goes live, not at booking. This pins:
  - a card scheduled ride calls _preauthorize_ride_card(block_on_decline=False)
    at dispatch and persists the returned hold fields
  - a decline at dispatch does NOT raise (ride still dispatches)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _claimed_card_ride():
    return {
        "id": "ride_sched_1",
        "rider_id": "rider_sched_1",
        "payment_method": "card",
        "payment_method_id": "pm_sched",
        "grand_total": "30.00",
        "total_fare": "30.00",
        "auth_status": None,
        "dropoff_address": "Somewhere",
    }


def _dispatch_patches(*, preauth_fields, update_returns):
    """Patch the full dependency surface of _dispatch_scheduled_ride."""
    from backend.routes.rides import _PreauthOutcome

    S = "backend.utils.scheduled_rides."
    update_mock = AsyncMock(side_effect=update_returns)
    preauth_mock = AsyncMock(return_value=_PreauthOutcome(fields=preauth_fields))
    return (
        [
            patch(S + "db.update_one", update_mock),
            patch(S + "db.get_user_by_id", AsyncMock(return_value={"stripe_customer_id": "cus_sched"})),
            patch(S + "manager.broadcast_ride_status", AsyncMock()),
            patch(S + "manager.broadcast_to_admins", AsyncMock()),
            patch(S + "send_push_notification", AsyncMock()),
            patch("backend.routes.rides.booking._preauthorize_ride_card", preauth_mock),
            patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()),
            patch("backend.routes.rides.matching.ride_search_timeout", AsyncMock()),
            patch("backend.routes.admin.monitoring.build_monitoring_ride", lambda *a, **k: {}),
        ],
        update_mock,
        preauth_mock,
    )


@pytest.mark.asyncio
class TestScheduledDispatchPreauth:
    async def test_card_ride_authorizes_at_dispatch_and_persists(self):
        from contextlib import ExitStack

        from backend.utils.scheduled_rides import _dispatch_scheduled_ride

        fields = {"payment_intent_id": "pi_hold", "authorized_amount": "40.00", "auth_status": "authorized"}
        # update_one calls: [0]=claim (returns the claimed ride), [1]=persist hold
        patches, update_mock, preauth_mock = _dispatch_patches(
            preauth_fields=fields,
            update_returns=[_claimed_card_ride(), {"id": "ride_sched_1"}],
        )

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            await _dispatch_scheduled_ride({"id": "ride_sched_1", "rider_id": "rider_sched_1"})

        # pre-auth invoked with block_on_decline=False (rider not present)
        preauth_mock.assert_called_once()
        assert preauth_mock.call_args.kwargs["block_on_decline"] is False
        # hold fields persisted via a second update_one carrying $set
        assert update_mock.call_count == 2
        persisted = update_mock.call_args_list[1].args[2]["$set"]
        assert persisted["auth_status"] == "authorized"
        assert persisted["payment_intent_id"] == "pi_hold"

    async def test_decline_at_dispatch_does_not_persist_or_raise(self):
        from contextlib import ExitStack

        from backend.utils.scheduled_rides import _dispatch_scheduled_ride

        # preauth returns {} (declined, degraded) → no persist update_one call
        patches, update_mock, preauth_mock = _dispatch_patches(
            preauth_fields={},
            update_returns=[_claimed_card_ride()],
        )

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            # Must not raise even though the card "declined"
            await _dispatch_scheduled_ride({"id": "ride_sched_1", "rider_id": "rider_sched_1"})

        preauth_mock.assert_called_once()
        # only the claim update_one fired; no hold persisted
        assert update_mock.call_count == 1

    async def test_genuine_preauth_exception_does_not_block_dispatch(self):
        """Finding #15: _preauthorize_ride_card is documented as 'never
        raises' (declines/failures degrade to an empty _PreauthOutcome), but
        the surrounding try/except in _dispatch_scheduled_ride must still
        catch a genuine exception (a real Stripe/network fault escaping that
        contract) without blocking dispatch -- unlike the documented decline
        path above, this exercises an actual raise, not a `{}` return."""
        from contextlib import ExitStack

        from backend.utils.scheduled_rides import _dispatch_scheduled_ride

        S = "backend.utils.scheduled_rides."
        match_mock = AsyncMock()
        update_mock = AsyncMock(return_value=_claimed_card_ride())
        patches = [
            patch(S + "db.update_one", update_mock),
            patch(S + "db.get_user_by_id", AsyncMock(return_value={"stripe_customer_id": "cus_sched"})),
            patch(S + "manager.broadcast_ride_status", AsyncMock()),
            patch(S + "manager.broadcast_to_admins", AsyncMock()),
            patch(S + "send_push_notification", AsyncMock()),
            # A genuine exception escaping the pre-auth call, not the
            # documented empty-outcome degrade.
            patch(
                "backend.routes.rides.booking._preauthorize_ride_card",
                AsyncMock(side_effect=RuntimeError("stripe api unreachable")),
            ),
            patch("backend.routes.rides.matching.match_driver_to_ride", match_mock),
            patch("backend.routes.rides.matching.ride_search_timeout", AsyncMock()),
            patch("backend.routes.admin.monitoring.build_monitoring_ride", lambda *a, **k: {}),
            patch("asyncio.create_task", lambda coro: coro.close()),
        ]

        with ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            # Must not raise, and must still proceed to matching.
            await _dispatch_scheduled_ride({"id": "ride_sched_1", "rider_id": "rider_sched_1"})

        match_mock.assert_awaited_once_with("ride_sched_1")
        # Only the claim update_one fired -- no hold fields persisted from a
        # call that raised rather than returning fields.
        assert update_mock.call_count == 1
