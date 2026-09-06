"""A redelivered payment_failed must not overwrite a paid ride (audit N1).

`payment_intent.payment_failed` used to write:

    await db_supabase.update_ride(ride_id, {"payment_status": "failed",
                                            "payment_intent_id": <this PI>, ...})

filtered on `id` alone — no predicate on the current payment_status and no check
on which PaymentIntent the ride is actually settled against. The failure mode:

  1. the PI on card 1 fails                       -> event A
  2. a DB blip hits the `unclaim` so A is redelivered minutes later
  3. meanwhile the rider retries and event B settles the $42.50 ride `paid`
  4. redelivered A flips it back to `failed`

The rider is then prompted to pay again and `settle_card` mints a fresh PI under
a *different* idempotency key, so the second charge is not deduped — a real
double charge.

Fixed by reading first and compare-and-swapping on exactly what was read.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

_RIDE_ID = "ride-wh-1"
_THIS_PI = "pi_failed_1"


def _data_object(pi=_THIS_PI, ride_id=_RIDE_ID):
    return {
        "id": pi,
        "metadata": {"ride_id": ride_id, "user_id": "user-1"},
        "last_payment_error": {"message": "Your card was declined."},
    }


async def _dispatch(ride_row, *, update_result={"id": _RIDE_ID}):
    """Run the payment_failed branch with the ride read stubbed."""
    from backend.routes import webhooks

    get_ride = AsyncMock(return_value=ride_row)
    update_one = AsyncMock(return_value=update_result)
    update_ride = AsyncMock(return_value={"id": _RIDE_ID})
    unclaim = AsyncMock()

    with (
        patch.object(webhooks.db_supabase, "get_ride", get_ride),
        patch.object(webhooks.db_supabase, "update_one", update_one),
        patch.object(webhooks.db_supabase, "update_ride", update_ride),
        patch.object(webhooks, "unclaim_stripe_event", unclaim),
        patch.object(webhooks, "send_push_notification", AsyncMock()),
    ):
        await webhooks._dispatch_stripe_event("evt_1", "payment_intent.payment_failed", {}, _data_object())
    return {"update_one": update_one, "update_ride": update_ride, "unclaim": unclaim}


class TestStaleFailureIsIgnored:
    @pytest.mark.parametrize("settled", ["paid", "waived_admin", "refunded"])
    async def test_settled_ride_is_never_relabelled_failed(self, settled):
        m = await _dispatch({"id": _RIDE_ID, "payment_status": settled, "payment_intent_id": "pi_ok_2"})
        m["update_one"].assert_not_awaited()
        m["update_ride"].assert_not_awaited()

    async def test_stale_event_is_acked_not_retried(self):
        """Must NOT unclaim: an unclaimed event is redelivered by Stripe for
        days and re-runs this same overwrite attempt."""
        m = await _dispatch({"id": _RIDE_ID, "payment_status": "paid", "payment_intent_id": "pi_ok_2"})
        m["unclaim"].assert_not_awaited()

    async def test_failure_for_a_superseded_payment_intent_is_ignored(self):
        """A different PI is linked, so this failure belongs to an attempt that
        has been replaced — recording it would relabel the live one."""
        m = await _dispatch({"id": _RIDE_ID, "payment_status": "pending", "payment_intent_id": "pi_other_9"})
        m["update_one"].assert_not_awaited()

    async def test_losing_the_cas_is_treated_as_stale(self):
        """A concurrent write landed between the read and the CAS. Their write
        is newer, so this failure is stale — ack, don't retry."""
        m = await _dispatch(
            {"id": _RIDE_ID, "payment_status": "pending", "payment_intent_id": None},
            update_result=None,
        )
        m["update_one"].assert_awaited_once()
        m["unclaim"].assert_not_awaited()


class TestGenuineFailureStillRecorded:
    @pytest.mark.parametrize("status", ["pending", "processing", "retrying", "failed", None])
    async def test_non_settled_ride_records_the_failure(self, status):
        """Guard against over-correcting. Every non-terminal state — including
        NULL, which `{"col": None}` compiles to `is.null`, and a repeat failure
        — must still be recorded."""
        m = await _dispatch({"id": _RIDE_ID, "payment_status": status, "payment_intent_id": None})
        m["update_one"].assert_awaited_once()
        args = m["update_one"].await_args.args
        assert args[2]["$set"]["payment_status"] == "failed"
        assert args[2]["$set"]["payment_intent_id"] == _THIS_PI

    async def test_cas_predicate_pins_exactly_what_was_read(self):
        m = await _dispatch({"id": _RIDE_ID, "payment_status": "pending", "payment_intent_id": _THIS_PI})
        filters = m["update_one"].await_args.args[1]
        assert filters == {
            "id": _RIDE_ID,
            "payment_status": "pending",
            "payment_intent_id": _THIS_PI,
        }

    async def test_same_pi_retry_is_still_recorded(self):
        """The ride is already linked to *this* PI (e.g. a re-sent failure for
        the same attempt) — that is not a superseded attempt, so it records."""
        m = await _dispatch({"id": _RIDE_ID, "payment_status": "processing", "payment_intent_id": _THIS_PI})
        m["update_one"].assert_awaited_once()


class TestRideReadFailureIsNotSilentlyDropped:
    async def test_ride_read_failure_unclaims_and_503s(self):
        """The event is ALREADY claimed when the CAS re-read runs. If that read
        raises and nothing unclaims, Stripe's retry short-circuits as a
        duplicate and the payment failure is lost FOREVER — worse than the
        mislabelling N1 was about. Caught by CI on the first real run of this
        branch: two pre-existing tests blanket-patched get_ride to raise and
        went red because the read was unguarded."""
        from fastapi import HTTPException

        from backend.routes import webhooks

        unclaim = AsyncMock()
        with (
            patch.object(webhooks.db_supabase, "get_ride", AsyncMock(side_effect=RuntimeError("db blip"))),
            patch.object(webhooks, "unclaim_stripe_event", unclaim),
            patch.object(webhooks, "send_push_notification", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc:
                await webhooks._dispatch_stripe_event("evt_1", "payment_intent.payment_failed", {}, _data_object())
        # 503 (DB error the client retries), and the claim released first so the
        # retry can actually re-process rather than being deduped away.
        assert exc.value.status_code == 503
        unclaim.assert_awaited_once_with("evt_1")


class TestMissingRideStillRetries:
    async def test_unknown_ride_unclaims_and_500s_so_stripe_retries(self):
        from fastapi import HTTPException

        from backend.routes import webhooks

        unclaim = AsyncMock()
        with (
            patch.object(webhooks.db_supabase, "get_ride", AsyncMock(return_value=None)),
            patch.object(webhooks, "unclaim_stripe_event", unclaim),
            patch.object(webhooks, "send_push_notification", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc:
                await webhooks._dispatch_stripe_event("evt_1", "payment_intent.payment_failed", {}, _data_object())
        assert exc.value.status_code == 500
        unclaim.assert_awaited_once()
