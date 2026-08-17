"""
Cancellation fee taken OUT of the booking hold, rather than charged fresh.

Previously a rider cancelling past the free window had their booking-time hold
cancelled outright, and the fee charged on a brand-new PaymentIntent. That has a
real failure mode: the new charge can be declined for insufficient funds even
though we were holding the rider's money moments earlier — we released our own
collateral and then asked for it back.

Now the fee is a PARTIAL CAPTURE of the existing hold: Stripe takes the fee and
releases the remainder. Because the funds are already reserved, the fee cannot
be declined for insufficient funds.

These tests pin the routing decision (capture vs release vs fall back), which
the pre-existing test_cancellation_fee_card_charge.py cannot cover — those rides
carry no hold fields at all, so they exercise only the fresh-charge path.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.stripe_charge import ChargeOutcome

RIDER_ID = "rider_hold_cancel"
DRIVER_USER_ID = "driver_user_hold_cancel"
DRIVER_ID = "driver_hold_cancel"
RIDE_ID = "ride_hold_cancel_001"

SETTINGS = {"cancellation_fee_admin": 0.50, "cancellation_fee_driver": 4.00}
NO_FEE_SETTINGS = {"cancellation_fee_admin": 0.0, "cancellation_fee_driver": 0.0}


def _ride(status: str, **extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "status": status,
        "payment_method": "card",
        "payment_method_id": "pm_test_123",
        "driver_accepted_at": None,
        # The live booking hold — what the old tests omit.
        "payment_intent_id": "pi_booking_hold",
        "auth_status": "authorized",
        "authorized_amount": 25.00,
    }
    row.update(extra)
    return row


def _base_patches(settings=None):
    """Everything cancel_ride_rider touches that is not the subject of the test."""
    return (
        patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=_ride("driver_arrived"))),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value=settings or SETTINGS)),
        patch(
            "backend.routes.rides._deps.db_supabase.get_user_by_id",
            AsyncMock(return_value={"id": RIDER_ID, "stripe_customer_id": "cus_test_123"}),
        ),
        patch(
            "backend.routes.rides._deps.db_supabase.get_driver_by_id",
            AsyncMock(return_value={"id": DRIVER_ID, "user_id": DRIVER_USER_ID, "name": "T"}),
        ),
        patch("backend.routes.rides._deps.db.update_one", AsyncMock()),
        patch("backend.routes.rides._deps.db.insert_one", AsyncMock()),
        patch("backend.routes.rides._deps.record_ledger_event", AsyncMock(return_value="evt_1")),
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride("cancelled"))),
        patch("backend.routes.rides._deps.db_supabase.set_driver_available", AsyncMock()),
        patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides._deps.manager.broadcast_ride_status", AsyncMock()),
        patch("backend.routes.rides._deps.manager.broadcast_to_admins", AsyncMock()),
        patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
    )


@contextmanager
def _patch_all(*extra, settings=None):
    """ExitStack rather than a parenthesized `with`: the base patches arrive as a
    sequence, and `with (*seq, ...)` is not valid unpacking syntax."""
    with ExitStack() as stack:
        for p in _base_patches(settings):
            stack.enter_context(p)
        for p in extra:
            stack.enter_context(p)
        yield


async def _run_cancel():
    from backend.routes import rides as rides_mod

    fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
    return await fn(request=MagicMock(), ride_id=RIDE_ID, reason="", current_user={"id": RIDER_ID})


@pytest.mark.e2e
@pytest.mark.asyncio
class TestCancellationFeeFromHold:
    async def test_fee_is_captured_from_the_hold_not_charged_fresh(self):
        update_ride_mock = AsyncMock()
        capture_mock = AsyncMock(
            return_value=ChargeOutcome(
                status="captured", payment_intent_id="pi_booking_hold", charged_amount=Decimal("4.50")
            )
        )
        charge_mock = AsyncMock()
        cancel_auth_mock = AsyncMock(return_value=True)

        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides._deps.capture_cancellation_fee", capture_mock),
            patch("backend.routes.rides._deps.charge_ancillary_fee", charge_mock),
            patch("backend.routes.rides._deps.cancel_authorization", cancel_auth_mock),
        ):
            result = await _run_cancel()

        assert result["success"] is True
        capture_mock.assert_awaited_once()
        kwargs = capture_mock.call_args.kwargs
        assert kwargs["payment_intent_id"] == "pi_booking_hold"
        assert kwargs["fee"] == Decimal("4.50")
        assert kwargs["authorized_amount"] == Decimal("25.00")

        # The whole point: no second PaymentIntent, and the hold is NOT released
        # separately — the partial capture releases the remainder itself.
        charge_mock.assert_not_awaited()
        cancel_auth_mock.assert_not_awaited()

        written = update_ride_mock.call_args_list[0].args[1]
        assert written["payment_status"] == "paid"
        assert written["cancel_fee_payment_intent_id"] == "pi_booking_hold"

    async def test_no_fee_releases_the_hold_in_full(self):
        capture_mock = AsyncMock()
        cancel_auth_mock = AsyncMock(return_value=True)

        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.capture_cancellation_fee", capture_mock),
            patch("backend.routes.rides._deps.charge_ancillary_fee", AsyncMock()),
            patch("backend.routes.rides._deps.cancel_authorization", cancel_auth_mock),
            settings=NO_FEE_SETTINGS,
        ):
            await _run_cancel()

        # Nothing owed -> the rider gets every cent back, and we never touch a
        # capture. This is the rider-cancels-in-time and driver-cancels case.
        cancel_auth_mock.assert_awaited_once()
        capture_mock.assert_not_awaited()

    async def test_failed_capture_falls_back_without_releasing_the_hold(self):
        """A failed capture must not release our only reserved funds.

        The fee is still owed and the fresh-charge fallback runs; cancelling the
        hold first would throw away the collateral before knowing whether that
        fallback lands.
        """
        capture_mock = AsyncMock(return_value=ChargeOutcome(status="failed", error_message="hold expired"))
        charge_mock = AsyncMock(
            return_value=ChargeOutcome(status="succeeded", payment_intent_id="pi_fresh", charged_amount=Decimal("4.50"))
        )
        cancel_auth_mock = AsyncMock(return_value=True)

        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.capture_cancellation_fee", capture_mock),
            patch("backend.routes.rides._deps.charge_ancillary_fee", charge_mock),
            patch("backend.routes.rides._deps.cancel_authorization", cancel_auth_mock),
        ):
            await _run_cancel()

        capture_mock.assert_awaited_once()
        charge_mock.assert_awaited_once()
        cancel_auth_mock.assert_not_awaited()


def _patch_stripe_capture(status: str = "succeeded"):
    """MagicMock stripe module whose capture returns an intent in `status`."""
    mock_stripe = MagicMock()
    intent = MagicMock()
    intent.status = status
    intent.id = "pi_booking_hold"
    mock_stripe.PaymentIntent.capture.return_value = intent
    return patch("backend.utils.stripe_charge.stripe", mock_stripe), mock_stripe


def _patch_secret():
    return patch(
        "backend.utils.stripe_charge.get_app_settings",
        AsyncMock(return_value={"stripe_secret_key": "sk_test_xxx"}),
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestCancellationFeeCapping:
    """Edge case: the fee is larger than the amount we are holding.

    Stripe rejects a capture above the authorization, so a cap is technically
    required — but WHERE we cap is a policy call. We take what is held and write
    off the rest rather than chasing the shortfall with a second charge: a
    cancellation fee larger than the ride itself is not defensible to a rider or
    a regulator, and the extra dollar is not worth the support ticket.
    """

    async def test_fee_larger_than_hold_captures_only_what_is_held(self):
        from backend.utils.stripe_charge import capture_cancellation_fee

        stripe_patch, mock_stripe = _patch_stripe_capture()
        with _patch_secret(), stripe_patch:
            outcome = await capture_cancellation_fee(
                ride_id="ride_cap",
                payment_intent_id="pi_booking_hold",
                fee=Decimal("5.00"),
                authorized_amount=Decimal("4.00"),
            )

        assert outcome.status == "captured"
        # Reports what was ACTUALLY taken, so the ledger cannot book revenue
        # that never moved.
        assert outcome.charged_amount == Decimal("4.00")
        assert mock_stripe.PaymentIntent.capture.call_args.kwargs["amount_to_capture"] == 400

    async def test_fee_within_hold_captures_the_full_fee(self):
        from backend.utils.stripe_charge import capture_cancellation_fee

        stripe_patch, mock_stripe = _patch_stripe_capture()
        with _patch_secret(), stripe_patch:
            outcome = await capture_cancellation_fee(
                ride_id="ride_cap",
                payment_intent_id="pi_booking_hold",
                fee=Decimal("4.50"),
                authorized_amount=Decimal("25.00"),
            )

        assert outcome.charged_amount == Decimal("4.50")
        assert mock_stripe.PaymentIntent.capture.call_args.kwargs["amount_to_capture"] == 450

    async def test_zero_hold_captures_nothing_and_never_calls_stripe(self):
        from backend.utils.stripe_charge import capture_cancellation_fee

        stripe_patch, mock_stripe = _patch_stripe_capture()
        with _patch_secret(), stripe_patch:
            outcome = await capture_cancellation_fee(
                ride_id="ride_cap",
                payment_intent_id="pi_booking_hold",
                fee=Decimal("5.00"),
                authorized_amount=Decimal("0"),
            )

        assert outcome.status == "captured"
        assert outcome.charged_amount == Decimal("0.00")
        mock_stripe.PaymentIntent.capture.assert_not_called()

    async def test_idempotency_key_is_distinct_from_settlement_capture(self):
        """A cancelled ride and a completed one are different events on the same
        PI. Sharing ride-capture's key would let one dedupe against the other."""
        from backend.utils.stripe_charge import capture_cancellation_fee

        stripe_patch, mock_stripe = _patch_stripe_capture()
        with _patch_secret(), stripe_patch:
            await capture_cancellation_fee(
                ride_id="ride_cap",
                payment_intent_id="pi_booking_hold",
                fee=Decimal("4.50"),
                authorized_amount=Decimal("25.00"),
            )

        key = mock_stripe.PaymentIntent.capture.call_args.kwargs["idempotency_key"]
        assert key == "ride-cancelfee-ride_cap-450"
        assert not key.startswith("ride-capture-")


@pytest.mark.e2e
@pytest.mark.asyncio
class TestAuthStatusReflectsReality:
    """`auth_status` is a strict 4-state contract (migration 156) that the
    orphaned-hold reconciler and card_hold_release both select on. Writing
    'released' when the hold was actually captured — or when it was left open
    after a failed capture — either misreports money movement or hides a live
    hold from the sweepers that exist to find it.

    The original version of this suite asserted payment_status and the fee PI
    but never auth_status, which is exactly how the bug survived review.
    """

    async def test_captured_fee_records_captured_not_released(self):
        update_ride_mock = AsyncMock()
        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch(
                "backend.routes.rides._deps.capture_cancellation_fee",
                AsyncMock(
                    return_value=ChargeOutcome(
                        status="captured", payment_intent_id="pi_booking_hold", charged_amount=Decimal("4.50")
                    )
                ),
            ),
            patch("backend.routes.rides._deps.charge_ancillary_fee", AsyncMock()),
            patch("backend.routes.rides._deps.cancel_authorization", AsyncMock(return_value=True)),
        ):
            await _run_cancel()

        written = update_ride_mock.call_args_list[0].args[1]
        # Money WAS taken from the hold. "released" would claim it wasn't.
        assert written["auth_status"] == "captured"

    async def test_full_release_records_released(self):
        update_ride_mock = AsyncMock()
        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides._deps.capture_cancellation_fee", AsyncMock()),
            patch("backend.routes.rides._deps.charge_ancillary_fee", AsyncMock()),
            patch("backend.routes.rides._deps.cancel_authorization", AsyncMock(return_value=True)),
            settings=NO_FEE_SETTINGS,
        ):
            await _run_cancel()

        written = update_ride_mock.call_args_list[0].args[1]
        assert written["auth_status"] == "released"

    async def test_failed_capture_leaves_auth_status_open_for_the_reconciler(self):
        """The hold is deliberately NOT cancelled when the capture fails, so it
        must stay in an OPEN_AUTH_STATES value. Marking it released would hide a
        genuinely open hold from orphaned_hold_reconciler and strand the rider's
        funds until Stripe's ~7-day expiry."""
        update_ride_mock = AsyncMock()
        with _patch_all(
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch(
                "backend.routes.rides._deps.capture_cancellation_fee",
                AsyncMock(return_value=ChargeOutcome(status="failed", error_message="hold expired")),
            ),
            patch(
                "backend.routes.rides._deps.charge_ancillary_fee",
                AsyncMock(
                    return_value=ChargeOutcome(
                        status="succeeded", payment_intent_id="pi_fresh", charged_amount=Decimal("4.50")
                    )
                ),
            ),
            patch("backend.routes.rides._deps.cancel_authorization", AsyncMock(return_value=True)),
        ):
            await _run_cancel()

        written = update_ride_mock.call_args_list[0].args[1]
        assert "auth_status" not in written or written["auth_status"] in ("authorized", "fare_only")
