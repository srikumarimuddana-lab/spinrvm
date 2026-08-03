"""
Coverage-closing tests for backend/utils/stripe_charge.py.

test_stripe_charge.py already covers charge_ride() and cancel_authorization()'s
happy/error paths in detail. This file closes the remaining gaps found via
`pytest tests/test_stripe_charge.py --cov=utils.stripe_charge --cov-report=term-missing`
(and the wider payment-test sweep that also touches this module):

    * charge_ancillary_fee()   — the WHOLE function had zero direct coverage
                                  before this file (amount<=0, unconfigured,
                                  missing customer/payment-method, success,
                                  requires_action, declined-by-status,
                                  CardError, StripeError, unhandled status)
    * _resolve_stripe_secret() — the `stripe is None` branch (only the
                                  `secret missing` branch was exercised
                                  elsewhere)
    * authorize_ride()         — amount<=0 no-op, unconfigured (stripe=None),
                                  missing customer, missing payment method,
                                  requires_capture success, requires_action,
                                  declined-by-status, CardError, StripeError,
                                  unhandled status
    * verify_authorization()   — missing payment_intent_id, unconfigured,
                                  StripeError on retrieve, customer mismatch
                                  (security), amount-too-small (security),
                                  requires_capture, declined-by-status,
                                  succeeded (already-captured idempotent
                                  attach), unexpected status
    * capture_ride()           — amount<=0 no-op, missing payment_intent_id,
                                  unconfigured, CardError, StripeError,
                                  success, unhandled status
    * cancel_authorization()   — the `stripe is None` / `secret is None`
                                  short-circuit branch (previously only the
                                  no-payment_intent_id and Stripe-raises
                                  branches were covered)
    * charge_ride()             — the `stripe is None` guard specifically
                                  (charge_ride's own early-return, distinct
                                  from the shared `_resolve_stripe_secret`
                                  helper the other functions use)

Every money value constructed in this file is a Decimal — never a float —
per CLAUDE.md's money-arithmetic convention.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _patch_settings(secret: str = "sk_test_xxx"):
    """Patch the settings loader bound name used by stripe_charge (same
    pattern as test_stripe_charge.py: the module binds get_app_settings at
    import time, so the patch target is the bound name in this module)."""

    async def _settings():
        return {"stripe_secret_key": secret}

    return patch("backend.utils.stripe_charge.get_app_settings", AsyncMock(side_effect=_settings))


def _patch_stripe(
    create_return=None,
    create_raises=None,
    capture_return=None,
    capture_raises=None,
    retrieve_return=None,
    retrieve_raises=None,
    cancel_raises=None,
):
    """Install a MagicMock stripe module on the charge module."""
    mock_stripe = MagicMock()
    if create_raises is not None:
        mock_stripe.PaymentIntent.create.side_effect = create_raises
    elif create_return is not None:
        mock_stripe.PaymentIntent.create.return_value = create_return
    if capture_raises is not None:
        mock_stripe.PaymentIntent.capture.side_effect = capture_raises
    elif capture_return is not None:
        mock_stripe.PaymentIntent.capture.return_value = capture_return
    if retrieve_raises is not None:
        mock_stripe.PaymentIntent.retrieve.side_effect = retrieve_raises
    elif retrieve_return is not None:
        mock_stripe.PaymentIntent.retrieve.return_value = retrieve_return
    if cancel_raises is not None:
        mock_stripe.PaymentIntent.cancel.side_effect = cancel_raises
    return patch("backend.utils.stripe_charge.stripe", mock_stripe), mock_stripe


class _FakeErrObj:
    code = "card_declined"
    decline_code = "insufficient_funds"
    message = "Your card has insufficient funds."


class _FakeCardError(Exception):
    def __init__(self):
        super().__init__("Your card has insufficient funds.")
        self.error = _FakeErrObj()


class _FakeStripeError(Exception):
    pass


class _NeverMatches(Exception):
    """Stand-in that never matches an except clause — used to force a
    raised exception past the CardError handler into the base-error one."""


_ANC_KW = dict(
    ride={"id": "ride_anc_1"},
    rider_id="rider_anc_1",
    amount=Decimal("15.00"),
    payment_method_id="pm_anc",
    stripe_customer_id="cus_anc",
    fee_type="cancellation_fee",
)


@pytest.mark.asyncio
class TestChargeAncillaryFeeGuardBranches:
    async def test_zero_amount_short_circuits(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        outcome = await charge_ancillary_fee(**{**_ANC_KW, "amount": Decimal("0.00")})
        assert outcome.status == "succeeded"
        assert outcome.charged_amount == Decimal("0.00")

    async def test_negative_amount_short_circuits(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        outcome = await charge_ancillary_fee(**{**_ANC_KW, "amount": Decimal("-5.00")})
        assert outcome.status == "succeeded"

    async def test_stripe_not_installed_returns_unconfigured(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        with patch("backend.utils.stripe_charge.stripe", None):
            outcome = await charge_ancillary_fee(**_ANC_KW)
        assert outcome.status == "unconfigured"

    async def test_no_secret_key_returns_unconfigured(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        stripe_patch, _ = _patch_stripe()
        with _patch_settings(secret=""), stripe_patch:
            outcome = await charge_ancillary_fee(**_ANC_KW)
        assert outcome.status == "unconfigured"

    async def test_no_customer_returns_failed(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        stripe_patch, _ = _patch_stripe()
        with _patch_settings(), stripe_patch:
            outcome = await charge_ancillary_fee(**{**_ANC_KW, "stripe_customer_id": None})
        assert outcome.status == "failed"
        assert "Stripe customer" in (outcome.error_message or "")

    async def test_no_payment_method_returns_failed(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        stripe_patch, _ = _patch_stripe()
        with _patch_settings(), stripe_patch:
            outcome = await charge_ancillary_fee(**{**_ANC_KW, "payment_method_id": None})
        assert outcome.status == "failed"
        assert "payment method" in (outcome.error_message or "").lower()


@pytest.mark.asyncio
class TestChargeAncillaryFeeSuccessAndStatusBranches:
    async def test_success_uses_fee_scoped_idempotency_key_and_metadata(self):
        """The idempotency namespace must be fee_type-prefixed so a
        cancellation_fee retry never collides with a same-ride/amount fare
        charge or a different fee type on the same ride."""
        from backend.utils.stripe_charge import charge_ancillary_fee

        intent = MagicMock(id="pi_fee_1", status="succeeded", client_secret=None)
        stripe_patch, mock_stripe = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ancillary_fee(**_ANC_KW)

        assert outcome.status == "succeeded"
        assert outcome.payment_intent_id == "pi_fee_1"
        assert outcome.charged_amount == Decimal("15.00")
        kwargs = mock_stripe.PaymentIntent.create.call_args.kwargs
        assert kwargs["amount"] == 1500
        assert kwargs["idempotency_key"] == "cancellation_fee-ride_anc_1-1500-pm_anc"
        assert kwargs["metadata"]["source"] == "cancellation_fee"
        assert kwargs["metadata"]["fee_amount"] == "15.00"
        assert kwargs["metadata"]["ride_id"] == "ride_anc_1"

    async def test_different_fee_type_yields_different_idempotency_key(self):
        """Two different fee types on the same ride/amount/card must not
        collide — each fee_type gets its own namespace."""
        from backend.utils.stripe_charge import charge_ancillary_fee

        intent = MagicMock(id="pi_fee_2", status="succeeded", client_secret=None)
        stripe_patch, mock_stripe = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            await charge_ancillary_fee(**_ANC_KW)
            await charge_ancillary_fee(**{**_ANC_KW, "fee_type": "no_show_fee"})

        keys = [call.kwargs["idempotency_key"] for call in mock_stripe.PaymentIntent.create.call_args_list]
        assert keys[0] != keys[1]
        assert keys[1] == "no_show_fee-ride_anc_1-1500-pm_anc"

    async def test_requires_action_returns_client_secret(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        intent = MagicMock(id="pi_fee_ra", status="requires_action", client_secret="secret_fee_ra")
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ancillary_fee(**_ANC_KW)

        assert outcome.status == "requires_action"
        assert outcome.client_secret == "secret_fee_ra"

    async def test_requires_source_action_treated_as_requires_action(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        intent = MagicMock(id="pi_fee_rsa", status="requires_source_action", client_secret="secret_rsa")
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ancillary_fee(**_ANC_KW)

        assert outcome.status == "requires_action"

    async def test_requires_payment_method_status_treated_as_declined(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        intent = MagicMock(id="pi_fee_rpm", status="requires_payment_method", client_secret=None)
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ancillary_fee(**_ANC_KW)

        assert outcome.status == "declined"
        assert outcome.payment_intent_id == "pi_fee_rpm"

    async def test_requires_confirmation_status_treated_as_declined(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        intent = MagicMock(id="pi_fee_rc", status="requires_confirmation", client_secret=None)
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ancillary_fee(**_ANC_KW)

        assert outcome.status == "declined"

    async def test_unhandled_status_returns_failed(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        intent = MagicMock(id="pi_fee_weird", status="processing", client_secret=None)
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ancillary_fee(**_ANC_KW)

        assert outcome.status == "failed"
        assert "processing" in (outcome.error_message or "").lower()

    async def test_card_error_returns_declined_with_decline_code(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.side_effect = _FakeCardError()

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _FakeCardError),
        ):
            outcome = await charge_ancillary_fee(**_ANC_KW)

        assert outcome.status == "declined"
        assert outcome.decline_code == "insufficient_funds"

    async def test_stripe_base_error_returns_failed(self):
        from backend.utils.stripe_charge import charge_ancillary_fee

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.side_effect = _FakeStripeError("rate_limit_error")

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _NeverMatches),
            patch("backend.utils.stripe_charge._StripeBaseError", _FakeStripeError),
        ):
            outcome = await charge_ancillary_fee(**_ANC_KW)

        assert outcome.status == "failed"
        assert "rate_limit_error" in (outcome.error_message or "")


_AUTH_KW = dict(
    ride={"id": "ride_auth_1", "payment_method": "card"},
    rider_id="rider_auth_1",
    amount=Decimal("40.00"),
    payment_method_id="pm_auth",
    stripe_customer_id="cus_auth",
)


@pytest.mark.asyncio
class TestAuthorizeRideGuardBranches:
    async def test_zero_amount_short_circuits_to_authorized_noop(self):
        from backend.utils.stripe_charge import authorize_ride

        outcome = await authorize_ride(**{**_AUTH_KW, "amount": Decimal("0.00")})
        assert outcome.status == "authorized"
        assert outcome.charged_amount == Decimal("0.00")

    async def test_negative_amount_short_circuits(self):
        from backend.utils.stripe_charge import authorize_ride

        outcome = await authorize_ride(**{**_AUTH_KW, "amount": Decimal("-1.00")})
        assert outcome.status == "authorized"

    async def test_stripe_not_installed_returns_unconfigured(self):
        """Exercises _resolve_stripe_secret's `stripe is None` branch."""
        from backend.utils.stripe_charge import authorize_ride

        with patch("backend.utils.stripe_charge.stripe", None):
            outcome = await authorize_ride(**_AUTH_KW)
        assert outcome.status == "unconfigured"

    async def test_no_secret_key_returns_unconfigured(self):
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

    async def test_no_payment_method_returns_failed(self):
        from backend.utils.stripe_charge import authorize_ride

        stripe_patch, _ = _patch_stripe()
        with _patch_settings(), stripe_patch:
            outcome = await authorize_ride(**{**_AUTH_KW, "payment_method_id": None})
        assert outcome.status == "failed"
        assert "payment method" in (outcome.error_message or "").lower()


@pytest.mark.asyncio
class TestAuthorizeRideStatusBranches:
    async def test_requires_capture_returns_authorized_with_held_amount(self):
        from backend.utils.stripe_charge import authorize_ride

        intent = MagicMock(id="pi_hold_1", status="requires_capture", client_secret=None)
        stripe_patch, mock_stripe = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "authorized"
        assert outcome.payment_intent_id == "pi_hold_1"
        assert outcome.charged_amount == Decimal("40.00")
        kwargs = mock_stripe.PaymentIntent.create.call_args.kwargs
        assert kwargs["capture_method"] == "manual"
        assert kwargs["off_session"] is False
        assert kwargs["idempotency_key"] == "ride-auth-ride_auth_1-4000"

    async def test_requires_action_returns_client_secret(self):
        from backend.utils.stripe_charge import authorize_ride

        intent = MagicMock(id="pi_hold_ra", status="requires_action", client_secret="hold_secret")
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "requires_action"
        assert outcome.client_secret == "hold_secret"

    async def test_requires_source_action_treated_as_requires_action(self):
        from backend.utils.stripe_charge import authorize_ride

        intent = MagicMock(id="pi_hold_rsa", status="requires_source_action", client_secret="hold_secret_2")
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "requires_action"

    async def test_requires_payment_method_treated_as_declined(self):
        from backend.utils.stripe_charge import authorize_ride

        intent = MagicMock(id="pi_hold_rpm", status="requires_payment_method", client_secret=None)
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "declined"

    async def test_requires_confirmation_treated_as_declined(self):
        from backend.utils.stripe_charge import authorize_ride

        intent = MagicMock(id="pi_hold_rc", status="requires_confirmation", client_secret=None)
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "declined"

    async def test_unhandled_status_returns_failed(self):
        from backend.utils.stripe_charge import authorize_ride

        intent = MagicMock(id="pi_hold_weird", status="canceled", client_secret=None)
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "failed"
        assert "canceled" in (outcome.error_message or "").lower()

    async def test_card_error_returns_declined_with_decline_code(self):
        from backend.utils.stripe_charge import authorize_ride

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

    async def test_stripe_base_error_returns_failed(self):
        from backend.utils.stripe_charge import authorize_ride

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.side_effect = _FakeStripeError("authentication_error")

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _NeverMatches),
            patch("backend.utils.stripe_charge._StripeBaseError", _FakeStripeError),
        ):
            outcome = await authorize_ride(**_AUTH_KW)

        assert outcome.status == "failed"
        assert "authentication_error" in (outcome.error_message or "")


@pytest.mark.asyncio
class TestVerifyAuthorization:
    async def test_no_payment_intent_id_returns_failed(self):
        from backend.utils.stripe_charge import verify_authorization

        outcome = await verify_authorization(ride_id="r1", payment_intent_id="")
        assert outcome.status == "failed"
        assert "No PaymentIntent" in (outcome.error_message or "")

    async def test_stripe_not_installed_returns_unconfigured(self):
        from backend.utils.stripe_charge import verify_authorization

        with patch("backend.utils.stripe_charge.stripe", None):
            outcome = await verify_authorization(ride_id="r1", payment_intent_id="pi_1")
        assert outcome.status == "unconfigured"

    async def test_no_secret_key_returns_unconfigured(self):
        from backend.utils.stripe_charge import verify_authorization

        stripe_patch, _ = _patch_stripe()
        with _patch_settings(secret=""), stripe_patch:
            outcome = await verify_authorization(ride_id="r1", payment_intent_id="pi_1")
        assert outcome.status == "unconfigured"

    async def test_stripe_error_on_retrieve_returns_failed(self):
        from backend.utils.stripe_charge import verify_authorization

        stripe_patch, _ = _patch_stripe(retrieve_raises=_FakeStripeError("api_connection_error"))
        with (
            _patch_settings(),
            stripe_patch,
            patch("backend.utils.stripe_charge._StripeBaseError", _FakeStripeError),
        ):
            outcome = await verify_authorization(ride_id="r1", payment_intent_id="pi_1")
        assert outcome.status == "failed"
        assert outcome.payment_intent_id == "pi_1"

    async def test_customer_mismatch_declines_as_security_event(self):
        """A rider must never attach someone else's authorization hold."""
        from backend.utils.stripe_charge import verify_authorization

        intent = MagicMock(id="pi_owned_by_other", status="requires_capture", customer="cus_someone_else", amount=4000)
        stripe_patch, _ = _patch_stripe(retrieve_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await verify_authorization(
                ride_id="r1",
                payment_intent_id="pi_owned_by_other",
                expected_customer_id="cus_this_rider",
            )

        assert outcome.status == "declined"
        assert "does not belong" in (outcome.error_message or "").lower()

    async def test_amount_too_small_declines_as_security_event(self):
        """A rider must not replay a smaller hold from a cancelled/cheaper
        booking against a more expensive ride."""
        from backend.utils.stripe_charge import verify_authorization

        intent = MagicMock(id="pi_small_hold", status="requires_capture", customer="cus_this_rider", amount=1000)
        stripe_patch, _ = _patch_stripe(retrieve_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await verify_authorization(
                ride_id="r1",
                payment_intent_id="pi_small_hold",
                expected_customer_id="cus_this_rider",
                min_amount_cents=4000,
            )

        assert outcome.status == "declined"
        assert "insufficient" in (outcome.error_message or "").lower()

    async def test_requires_capture_returns_authorized(self):
        from backend.utils.stripe_charge import verify_authorization

        intent = MagicMock(id="pi_ok", status="requires_capture", customer="cus_this_rider", amount=4000)
        stripe_patch, _ = _patch_stripe(retrieve_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await verify_authorization(
                ride_id="r1",
                payment_intent_id="pi_ok",
                expected_customer_id="cus_this_rider",
                min_amount_cents=4000,
            )

        assert outcome.status == "authorized"
        assert outcome.charged_amount == Decimal("40.00")

    async def test_requires_action_family_declines_as_not_completed(self):
        from backend.utils.stripe_charge import verify_authorization

        for status in ("requires_action", "requires_source_action", "requires_payment_method", "requires_confirmation"):
            intent = MagicMock(id="pi_incomplete", status=status, customer=None, amount=0)
            stripe_patch, _ = _patch_stripe(retrieve_return=intent)
            with _patch_settings(), stripe_patch:
                outcome = await verify_authorization(ride_id="r1", payment_intent_id="pi_incomplete")
            assert outcome.status == "declined", f"status={status} should decline"
            assert "not completed" in (outcome.error_message or "").lower()

    async def test_succeeded_status_treated_as_authorized_idempotent_attach(self):
        """Already-captured PI (e.g. a second confirm attempt landed) is
        treated as authorized so settlement's capture is a no-op, not a
        double charge."""
        from backend.utils.stripe_charge import verify_authorization

        intent = MagicMock(id="pi_already_captured", status="succeeded", customer=None, amount=4000)
        stripe_patch, _ = _patch_stripe(retrieve_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await verify_authorization(ride_id="r1", payment_intent_id="pi_already_captured")

        assert outcome.status == "authorized"
        assert outcome.charged_amount == Decimal("40.00")

    async def test_unexpected_status_returns_failed(self):
        from backend.utils.stripe_charge import verify_authorization

        intent = MagicMock(id="pi_weird", status="canceled", customer=None, amount=0)
        stripe_patch, _ = _patch_stripe(retrieve_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await verify_authorization(ride_id="r1", payment_intent_id="pi_weird")

        assert outcome.status == "failed"
        assert "canceled" in (outcome.error_message or "").lower()


@pytest.mark.asyncio
class TestCaptureRide:
    async def test_zero_amount_short_circuits_to_captured_noop(self):
        from backend.utils.stripe_charge import capture_ride

        outcome = await capture_ride(ride_id="r1", payment_intent_id="pi_1", amount=Decimal("0.00"))
        assert outcome.status == "captured"
        assert outcome.charged_amount == Decimal("0.00")

    async def test_negative_amount_short_circuits(self):
        from backend.utils.stripe_charge import capture_ride

        outcome = await capture_ride(ride_id="r1", payment_intent_id="pi_1", amount=Decimal("-1.00"))
        assert outcome.status == "captured"

    async def test_no_payment_intent_id_returns_failed(self):
        from backend.utils.stripe_charge import capture_ride

        outcome = await capture_ride(ride_id="r1", payment_intent_id="", amount=Decimal("10.00"))
        assert outcome.status == "failed"
        assert "No authorization" in (outcome.error_message or "")

    async def test_stripe_not_installed_returns_unconfigured(self):
        from backend.utils.stripe_charge import capture_ride

        with patch("backend.utils.stripe_charge.stripe", None):
            outcome = await capture_ride(ride_id="r1", payment_intent_id="pi_1", amount=Decimal("10.00"))
        assert outcome.status == "unconfigured"

    async def test_no_secret_key_returns_unconfigured(self):
        from backend.utils.stripe_charge import capture_ride

        stripe_patch, _ = _patch_stripe()
        with _patch_settings(secret=""), stripe_patch:
            outcome = await capture_ride(ride_id="r1", payment_intent_id="pi_1", amount=Decimal("10.00"))
        assert outcome.status == "unconfigured"

    async def test_success_returns_captured_with_idempotency_key(self):
        from backend.utils.stripe_charge import capture_ride

        intent = MagicMock(id="pi_cap_1", status="succeeded")
        stripe_patch, mock_stripe = _patch_stripe(capture_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await capture_ride(ride_id="r1", payment_intent_id="pi_cap_1", amount=Decimal("40.00"))

        assert outcome.status == "captured"
        assert outcome.payment_intent_id == "pi_cap_1"
        assert outcome.charged_amount == Decimal("40.00")
        kwargs = mock_stripe.PaymentIntent.capture.call_args.kwargs
        assert kwargs["amount_to_capture"] == 4000
        assert kwargs["idempotency_key"] == "ride-capture-r1-4000"

    async def test_card_error_returns_declined_with_decline_code(self):
        from backend.utils.stripe_charge import capture_ride

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.capture.side_effect = _FakeCardError()

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _FakeCardError),
        ):
            outcome = await capture_ride(ride_id="r1", payment_intent_id="pi_cap_declined", amount=Decimal("40.00"))

        assert outcome.status == "declined"
        assert outcome.decline_code == "insufficient_funds"
        assert outcome.payment_intent_id == "pi_cap_declined"

    async def test_stripe_base_error_returns_failed(self):
        """Hold expired / amount_too_large / already-captured — not a card
        decline, caller falls back to a fresh charge_ride."""
        from backend.utils.stripe_charge import capture_ride

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.capture.side_effect = _FakeStripeError("amount_too_large")

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _NeverMatches),
            patch("backend.utils.stripe_charge._StripeBaseError", _FakeStripeError),
        ):
            outcome = await capture_ride(ride_id="r1", payment_intent_id="pi_cap_expired", amount=Decimal("40.00"))

        assert outcome.status == "failed"
        assert "amount_too_large" in (outcome.error_message or "")

    async def test_unhandled_status_returns_failed(self):
        from backend.utils.stripe_charge import capture_ride

        intent = MagicMock(id="pi_cap_weird", status="processing")
        stripe_patch, _ = _patch_stripe(capture_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await capture_ride(ride_id="r1", payment_intent_id="pi_cap_weird", amount=Decimal("40.00"))

        assert outcome.status == "failed"
        assert "processing" in (outcome.error_message or "").lower()


@pytest.mark.asyncio
class TestCancelAuthorizationSecretGuard:
    async def test_stripe_secret_missing_short_circuits_to_false(self):
        """Covers the `secret is None` half of `if stripe is None or secret
        is None: return False` — the stripe module is installed (not None)
        but stripe_secret_key is unconfigured, so `_resolve_stripe_secret`
        returns None and the guard must trip WITHOUT calling PaymentIntent.cancel."""
        from backend.utils.stripe_charge import cancel_authorization

        stripe_patch, mock_stripe = _patch_stripe()
        with _patch_settings(secret=""), stripe_patch:
            ok = await cancel_authorization(ride_id="r1", payment_intent_id="pi_hold")

        assert ok is False
        mock_stripe.PaymentIntent.cancel.assert_not_called()

    async def test_stripe_not_installed_short_circuits_to_false(self):
        from backend.utils.stripe_charge import cancel_authorization

        with patch("backend.utils.stripe_charge.stripe", None):
            ok = await cancel_authorization(ride_id="r1", payment_intent_id="pi_hold")

        assert ok is False


@pytest.mark.asyncio
class TestChargeRideStripeNotInstalled:
    async def test_stripe_not_installed_returns_unconfigured(self):
        """charge_ride() has its own `stripe is None` early-return (it
        doesn't go through _resolve_stripe_secret like the auth/capture
        helpers do) — exercise it directly."""
        from backend.utils.stripe_charge import charge_ride

        with patch("backend.utils.stripe_charge.stripe", None):
            outcome = await charge_ride(
                ride={"id": "ride_no_stripe"},
                rider_id="rider_no_stripe",
                total_amount=Decimal("10.00"),
                stripe_customer_id="cus_x",
                payment_method_id="pm_x",
            )

        assert outcome.status == "unconfigured"
        assert "not installed" in (outcome.error_message or "").lower()
