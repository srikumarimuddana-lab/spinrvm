"""
Unit tests for the buffered pre-authorization helpers in
backend/utils/stripe_charge.py — ``authorize_ride`` and ``capture_ride``.

These two functions implement the front-of-ride hold + single-capture model:

    authorize_ride  → at booking, place a manual-capture hold for
                      (estimated fare + RIDE_AUTH_BUFFER). Surfaces a dead card
                      BEFORE dispatch; a "declined" outcome lets the caller fall
                      back to a fare-only hold.
    capture_ride    → at settlement, capture (fare + tip) against that hold in a
                      single transaction, so a tip rides on the same PaymentIntent
                      (one Stripe fee). Capture > authorization is rejected by
                      Stripe, hence tip-over-buffer is charged separately.

Mirrors test_stripe_charge.py: stripe + settings are patched on the bound names
in the module, so no real Stripe network or package is required.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_AUTH_KW = dict(
    ride={"id": "ride_pa_1", "payment_method": "card"},
    rider_id="rider_pa_1",
    amount=Decimal("35.00"),  # estimated fare 25.00 + 10.00 buffer
    stripe_customer_id="cus_pa",
    payment_method_id="pm_pa",
)


def _patch_settings(secret: str = "sk_test_xxx"):
    async def _settings():
        return {"stripe_secret_key": secret}

    return patch(
        "backend.utils.stripe_charge.get_app_settings",
        AsyncMock(side_effect=_settings),
    )


def _patch_stripe(create_return=None, capture_return=None, retrieve_return=None):
    mock_stripe = MagicMock()
    if create_return is not None:
        mock_stripe.PaymentIntent.create.return_value = create_return
    if capture_return is not None:
        mock_stripe.PaymentIntent.capture.return_value = capture_return
    if retrieve_return is not None:
        mock_stripe.PaymentIntent.retrieve.return_value = retrieve_return
    return patch("backend.utils.stripe_charge.stripe", mock_stripe), mock_stripe


# --------------------------------------------------------------------------- #
# authorize_ride
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
class TestAuthorize:
    async def test_zero_amount_is_noop_authorized(self):
        from backend.utils.stripe_charge import authorize_ride

        outcome = await authorize_ride(ride={"id": "r"}, rider_id="u", amount=0)
        assert outcome.status == "authorized"
        assert outcome.charged_amount == Decimal("0.00")
        assert outcome.payment_intent_id is None

    async def test_unconfigured_when_no_secret(self):
        from backend.utils.stripe_charge import authorize_ride

        stripe_patch, _ = _patch_stripe()
        with _patch_settings(secret=""), stripe_patch:
            outcome = await authorize_ride(**_AUTH_KW)
        assert outcome.status == "unconfigured"

    async def test_no_customer_returns_failed(self):
        from backend.utils.stripe_charge import authorize_ride

        stripe_patch, _ = _patch_stripe()
        with _patch_settings(), stripe_patch:
            outcome = await authorize_ride(**{**_AUTH_KW, "stripe_customer_id": None})
        assert outcome.status == "failed"
        assert "Stripe customer" in (outcome.error_message or "")

    async def test_requires_capture_returns_authorized(self):
        """The happy path: Stripe holds funds and reports requires_capture.
        No money has moved yet; charged_amount carries the held amount."""
        from backend.utils.stripe_charge import authorize_ride

        intent = MagicMock(id="pi_hold_1", status="requires_capture", client_secret=None)
        stripe_patch, mock_stripe = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "authorized"
        assert outcome.payment_intent_id == "pi_hold_1"
        assert outcome.charged_amount == Decimal("35.00")

        kwargs = mock_stripe.PaymentIntent.create.call_args.kwargs
        assert kwargs["amount"] == 3500  # cents, HALF_UP
        assert kwargs["currency"] == "cad"
        assert kwargs["capture_method"] == "manual"  # HOLD, not charge
        assert kwargs["confirm"] is True
        assert kwargs["off_session"] is False  # rider present at booking
        assert kwargs["metadata"]["source"] == "ride_booking_authorization"
        assert kwargs["metadata"]["authorized_amount"] == "35.00"
        # amount-pinned idempotency key: a re-auth at a revised estimate gets a
        # fresh key; an identical double-tap dedupes to the same hold.
        assert kwargs["idempotency_key"] == "ride-auth-ride_pa_1-3500"

    async def test_requires_action_returns_client_secret(self):
        """Apple Pay / 3DS at booking — hand the client_secret back so the
        rider-app runs the biometric / SCA sheet."""
        from backend.utils.stripe_charge import authorize_ride

        intent = MagicMock(id="pi_ra", status="requires_action", client_secret="pi_ra_secret")
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "requires_action"
        assert outcome.client_secret == "pi_ra_secret"

    async def test_insufficient_funds_returns_declined(self):
        """A declined buffered hold is the signal the caller uses to fall back
        to a fare-only authorization — decline_code must be propagated."""
        from backend.utils.stripe_charge import authorize_ride

        class _FakeErrObj:
            code = "card_declined"
            decline_code = "insufficient_funds"
            message = "Your card has insufficient funds."

        class _FakeCardError(Exception):
            def __init__(self):
                super().__init__("Your card has insufficient funds.")
                self.error = _FakeErrObj()

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.side_effect = _FakeCardError()

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _FakeCardError),
        ):
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "declined"
        assert outcome.decline_code == "insufficient_funds"

    async def test_requires_payment_method_treated_as_declined(self):
        from backend.utils.stripe_charge import authorize_ride

        intent = MagicMock(id="pi_rpm", status="requires_payment_method", client_secret=None)
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "declined"

    async def test_non_card_error_returns_failed(self):
        from backend.utils.stripe_charge import authorize_ride

        class _FakeStripeError(Exception):
            pass

        class _NeverMatches(Exception):
            pass

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.side_effect = _FakeStripeError("rate_limit_error")

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _NeverMatches),
            patch("backend.utils.stripe_charge._StripeBaseError", _FakeStripeError),
        ):
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "failed"


# --------------------------------------------------------------------------- #
# capture_ride
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
class TestCapture:
    async def test_zero_amount_is_noop_captured(self):
        from backend.utils.stripe_charge import capture_ride

        outcome = await capture_ride(ride_id="r", payment_intent_id="pi_x", amount=0)
        assert outcome.status == "captured"

    async def test_missing_pi_returns_failed(self):
        from backend.utils.stripe_charge import capture_ride

        outcome = await capture_ride(ride_id="r", payment_intent_id="", amount=Decimal("10"))
        assert outcome.status == "failed"
        assert "capture" in (outcome.error_message or "").lower()

    async def test_capture_succeeds(self):
        """Fare 25.00 + tip 5.00 captured against the 35.00 hold in one go."""
        from backend.utils.stripe_charge import capture_ride

        intent = MagicMock(id="pi_hold_1", status="succeeded")
        stripe_patch, mock_stripe = _patch_stripe(capture_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await capture_ride(
                ride_id="ride_pa_1",
                payment_intent_id="pi_hold_1",
                amount=Decimal("30.00"),
            )

        assert outcome.status == "captured"
        assert outcome.payment_intent_id == "pi_hold_1"
        assert outcome.charged_amount == Decimal("30.00")

        kwargs = mock_stripe.PaymentIntent.capture.call_args.kwargs
        assert kwargs["amount_to_capture"] == 3000  # cents
        assert kwargs["idempotency_key"] == "ride-capture-ride_pa_1-3000"

    async def test_expired_hold_returns_failed_for_fresh_charge_fallback(self):
        """A non-card Stripe error at capture (expired hold, amount_too_large)
        is 'failed' — the caller re-drives via a fresh charge_ride, NOT a
        rider-facing decline."""
        from backend.utils.stripe_charge import capture_ride

        class _FakeStripeError(Exception):
            pass

        class _NeverMatches(Exception):
            pass

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.capture.side_effect = _FakeStripeError(
            "This PaymentIntent could not be captured because it has expired."
        )

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _NeverMatches),
            patch("backend.utils.stripe_charge._StripeBaseError", _FakeStripeError),
        ):
            outcome = await capture_ride(ride_id="ride_pa_1", payment_intent_id="pi_hold_1", amount=Decimal("30.00"))

        assert outcome.status == "failed"
        assert "expired" in (outcome.error_message or "").lower()

    async def test_capture_decline_propagates_code(self):
        """Rare: issuer reverses the hold and Stripe raises CardError at capture."""
        from backend.utils.stripe_charge import capture_ride

        class _FakeErrObj:
            code = "card_declined"
            decline_code = "issuer_not_available"
            message = "The card issuer could not be reached."

        class _FakeCardError(Exception):
            def __init__(self):
                super().__init__("The card issuer could not be reached.")
                self.error = _FakeErrObj()

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.capture.side_effect = _FakeCardError()

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _FakeCardError),
        ):
            outcome = await capture_ride(ride_id="ride_pa_1", payment_intent_id="pi_hold_1", amount=Decimal("30.00"))

        assert outcome.status == "declined"
        assert outcome.decline_code == "issuer_not_available"


# --------------------------------------------------------------------------- #
# verify_authorization (SCA two-step: confirm an on-device-authed hold)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
class TestVerifyAuthorization:
    async def test_requires_capture_is_authorized(self):
        """The PI the app confirmed on-device landed in requires_capture →
        hold is real; charged_amount comes from Stripe's cents."""
        from backend.utils.stripe_charge import verify_authorization

        intent = MagicMock(id="pi_sca", status="requires_capture", amount=3500)
        stripe_patch, _ = _patch_stripe(retrieve_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await verify_authorization(ride_id="r", payment_intent_id="pi_sca")

        assert outcome.status == "authorized"
        assert outcome.payment_intent_id == "pi_sca"
        assert outcome.charged_amount == Decimal("35.00")

    async def test_succeeded_treated_as_authorized(self):
        """Already captured (e.g. duplicate) → authorized so the caller attaches
        it; settlement's idempotent capture is a no-op, never a double charge."""
        from backend.utils.stripe_charge import verify_authorization

        intent = MagicMock(id="pi_s", status="succeeded", amount=2500)
        stripe_patch, _ = _patch_stripe(retrieve_return=intent)
        with _patch_settings(), stripe_patch:
            outcome = await verify_authorization(ride_id="r", payment_intent_id="pi_s")
        assert outcome.status == "authorized"
        assert outcome.charged_amount == Decimal("25.00")

    async def test_unconfirmed_pi_is_declined(self):
        """Rider abandoned/failed the SCA sheet → PI still needs action/method."""
        from backend.utils.stripe_charge import verify_authorization

        intent = MagicMock(id="pi_x", status="requires_payment_method", amount=3500)
        stripe_patch, _ = _patch_stripe(retrieve_return=intent)
        with _patch_settings(), stripe_patch:
            outcome = await verify_authorization(ride_id="r", payment_intent_id="pi_x")
        assert outcome.status == "declined"

    async def test_missing_pi_returns_failed(self):
        from backend.utils.stripe_charge import verify_authorization

        outcome = await verify_authorization(ride_id="r", payment_intent_id="")
        assert outcome.status == "failed"

    async def test_stripe_error_returns_failed(self):
        from backend.utils.stripe_charge import verify_authorization

        class _FakeStripeError(Exception):
            pass

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.retrieve.side_effect = _FakeStripeError("api_error")
        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeBaseError", _FakeStripeError),
        ):
            outcome = await verify_authorization(ride_id="r", payment_intent_id="pi_e")
        assert outcome.status == "failed"
