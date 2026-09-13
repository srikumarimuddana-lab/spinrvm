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


class TestCasWriteFailureIsNotSilentlyDropped:
    async def test_ride_write_failure_unclaims_and_503s(self):
        """B42: the event is ALREADY claimed when the CAS write runs. If that
        write raises and nothing unclaims, Stripe's retry short-circuits as a
        duplicate and the payment failure is lost FOREVER — the exact failure
        mode that went unnoticed in production for two months (53 of 55
        payment_intent.payment_failed events never processed, 2026-07-15
        through 2026-09-11) because `payment_failure_reason` was written to
        `rides` with no migration ever having created the column, so this
        write raised on every real invocation. Migration 414 fixes the
        schema gap; this test guards the write itself the same way
        TestRideReadFailureIsNotSilentlyDropped already guards the read a
        few lines above it in the real handler."""
        from fastapi import HTTPException

        from backend.routes import webhooks

        unclaim = AsyncMock()
        with (
            patch.object(
                webhooks.db_supabase,
                "get_ride",
                AsyncMock(return_value={"id": _RIDE_ID, "payment_status": "pending", "payment_intent_id": None}),
            ),
            patch.object(
                webhooks.db_supabase, "update_one", AsyncMock(side_effect=RuntimeError("column does not exist"))
            ),
            patch.object(webhooks, "unclaim_stripe_event", unclaim),
            patch.object(webhooks, "send_push_notification", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc:
                await webhooks._dispatch_stripe_event("evt_1", "payment_intent.payment_failed", {}, _data_object())
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


class TestPreauthStageFailureIsAcked:
    """A booking-time pre-auth failure is not a settlement failure.

    `utils/stripe_charge.authorize_ride` creates the hold BEFORE
    `routes/rides/booking.py` inserts the ride row, so the resulting
    `payment_intent.payment_failed` can arrive while no row exists — and on a
    genuine decline the booking raises 402 and the row never exists at all.
    Observed in production 2026-09-12/13: one 500 per booking, every booking,
    and `TestMissingRideStillRetries` below is what made it a 3-day Stripe
    retry loop rather than a single error.

    Note `rides.created_at` is stamped when the Pydantic model is built, ahead
    of the pre-auth, so it is NOT the insert time — the row genuinely is not
    there yet even though created_at looks earlier than the webhook.

    Every other test in this file keeps a `source`-less metadata dict, so they
    collectively pin that this guard does not over-reach.
    """

    _PREAUTH = {
        "id": _THIS_PI,
        "metadata": {
            "ride_id": _RIDE_ID,
            "user_id": "user-1",
            "source": "ride_booking_authorization",
        },
        "last_payment_error": {"message": "Your card was declined."},
    }

    @staticmethod
    async def _run(data_object, *, ride_row=None, flag=True):
        from backend.routes import webhooks

        marks = AsyncMock()
        unclaim = AsyncMock()
        update_one = AsyncMock(return_value={"id": _RIDE_ID})
        push = AsyncMock()
        with (
            patch.object(webhooks.db_supabase, "get_ride", AsyncMock(return_value=ride_row)),
            patch.object(webhooks.db_supabase, "update_one", update_one),
            patch.object(webhooks.db_supabase, "update_ride", AsyncMock(return_value={"id": _RIDE_ID})),
            patch.object(webhooks, "mark_stripe_event_processed", marks),
            patch.object(webhooks, "unclaim_stripe_event", unclaim),
            patch.object(webhooks, "send_push_notification", push),
            patch.object(
                webhooks,
                "get_app_settings",
                AsyncMock(return_value={"webhook_preauth_failure_ack_enabled": flag}),
            ),
        ):
            result = await webhooks._dispatch_stripe_event("evt_1", "payment_intent.payment_failed", {}, data_object)
        return {
            "result": result,
            "mark": marks,
            "unclaim": unclaim,
            "update_one": update_one,
            "push": push,
        }

    async def test_missing_ride_is_acked_not_retried_forever(self):
        """The headline fix: no HTTPException, and the event is marked done so
        Stripe stops redelivering an event that can never resolve."""
        m = await self._run(self._PREAUTH, ride_row=None)
        m["mark"].assert_awaited_once_with("evt_1")
        m["unclaim"].assert_not_awaited()
        assert m["result"]["preauth_stage"] is True
        assert m["result"]["received"] is True

    async def test_capture_decline_on_an_existing_ride_is_still_recorded(self):
        """The blocker this guard was nearly shipped with.

        Stripe metadata is stamped once at PaymentIntent creation and never
        updated, so `capture_ride` at settlement reuses the SAME PI carrying the
        SAME `source: ride_booking_authorization`. A capture declined at
        settlement (services/payment_service.py::_settle_against_hold) is a real
        payment failure on a real, linked, completed ride — and this handler's
        pushes below are its ONLY rider/driver notification, because settle_card
        returns early before its own push block.

        An earlier revision of this guard keyed on metadata.source alone and
        acked this event, silently dropping both notifications AND making the
        event unreplayable (admin replay refuses an already-processed event).
        Gating the ack on the ride row being absent makes that impossible.
        """
        m = await self._run(
            self._PREAUTH,
            ride_row={"id": _RIDE_ID, "payment_status": "pending", "payment_intent_id": _THIS_PI},
        )
        m["update_one"].assert_awaited_once()
        assert m["update_one"].await_args.args[2]["$set"]["payment_status"] == "failed"
        assert "preauth_stage" not in m["result"]
        m["push"].assert_awaited()  # the rider/driver notification survives

    async def test_doomed_preauth_on_a_ride_linked_to_another_pi_is_ignored(self):
        """The in-flight-ride concern, covered by the PRE-EXISTING superseded-PI
        guard rather than by the new ack: the ride settled on the fallback hold,
        so the doomed PI is a replaced attempt and must not relabel it."""
        m = await self._run(
            self._PREAUTH,
            ride_row={"id": _RIDE_ID, "payment_status": "pending", "payment_intent_id": "pi_fallback_ok"},
        )
        m["update_one"].assert_not_awaited()

    async def test_sends_no_payment_failed_push(self):
        """The booking is about to succeed on the fallback hold; the rider must
        not be told their payment failed."""
        m = await self._run(self._PREAUTH, ride_row=None)
        m["push"].assert_not_awaited()

    async def test_guard_does_not_over_reach_to_completion_charges(self):
        """A real settlement charge for the same ride still records."""
        settlement = {
            **self._PREAUTH,
            "metadata": {**self._PREAUTH["metadata"], "source": "ride_completion_charge"},
        }
        m = await self._run(
            settlement,
            ride_row={"id": _RIDE_ID, "payment_status": "pending", "payment_intent_id": None},
        )
        m["update_one"].assert_awaited_once()
        # Not asserting on mark_stripe_event_processed: the handler marks EVERY
        # successfully-processed event at the end of the normal path, so it is
        # awaited here too. `preauth_stage` is the discriminator for the early
        # ack — its absence is what proves the guard did not take this branch.
        assert "preauth_stage" not in m["result"]

    async def test_completion_charge_for_unknown_ride_still_500s(self):
        """An unlinked *captured* charge is real money — it must keep retrying."""
        from fastapi import HTTPException

        settlement = {
            **self._PREAUTH,
            "metadata": {**self._PREAUTH["metadata"], "source": "ride_completion_charge"},
        }
        with pytest.raises(HTTPException) as exc:
            await self._run(settlement, ride_row=None)
        assert exc.value.status_code == 500

    async def test_kill_switch_restores_the_old_500(self):
        """CLAUDE.md pre-merge gate 3: revertible without a deploy."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await self._run(self._PREAUTH, ride_row=None, flag=False)
        assert exc.value.status_code == 500

    async def test_settings_read_failure_defaults_the_guard_on(self):
        """A settings outage must not resurrect the 3-day retry loop."""
        from backend.routes import webhooks

        marks = AsyncMock()
        with (
            patch.object(webhooks.db_supabase, "get_ride", AsyncMock(return_value=None)),
            patch.object(webhooks, "mark_stripe_event_processed", marks),
            patch.object(webhooks, "unclaim_stripe_event", AsyncMock()),
            patch.object(webhooks, "send_push_notification", AsyncMock()),
            patch.object(webhooks, "get_app_settings", AsyncMock(side_effect=RuntimeError("settings down"))),
        ):
            result = await webhooks._dispatch_stripe_event("evt_1", "payment_intent.payment_failed", {}, self._PREAUTH)
        assert result["preauth_stage"] is True
        marks.assert_awaited_once_with("evt_1")
