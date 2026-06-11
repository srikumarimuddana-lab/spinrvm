"""
Unit tests for backend/utils/stripe_charge.py::charge_ride().

charge_ride() is the single Stripe-facing helper used by both
rides.py::process_payment (sync, ride-completion) and
payment_retry.py (async loop). Testing it in isolation gives us fast
deterministic coverage of every Stripe lifecycle branch without
spinning up FastAPI or touching the real Stripe network.

Branches exercised:
    * $0 amount short-circuits to succeeded without touching Stripe
    * stripe module not installed → unconfigured
    * stripe_secret_key missing in settings → unconfigured
    * No stripe_customer_id on rider → failed with clear message
    * No default_payment_method on rider → failed with clear message
    * Stripe returns status=succeeded → succeeded + pi_id
    * Stripe returns status=requires_action → requires_action + client_secret
    * Stripe returns status=requires_payment_method → declined
    * Stripe raises CardError → declined + decline_code
    * Stripe raises a non-card StripeError → failed (ops issue)
    * Stripe returns an unhandled status → failed
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

_KW = dict(
    ride={"id": "ride_helper_1"},
    rider_id="rider_helper_1",
    total_amount=Decimal("25.50"),
    stripe_customer_id="cus_helper",
    payment_method_id="pm_helper",
)


def _patch_settings(secret: str = "sk_test_xxx"):
    """Patch the settings loader used by stripe_charge. The module
    binds get_app_settings at import time via `from ..settings_loader
    import get_app_settings`, so the patch target is the bound name."""
    from unittest.mock import AsyncMock as _AsyncMock

    async def _settings():
        return {"stripe_secret_key": secret}

    return patch("backend.utils.stripe_charge.get_app_settings", _AsyncMock(side_effect=_settings))


def _patch_stripe(create_return=None, create_raises=None):
    """Install a MagicMock stripe module on the charge module. Either
    return a mock intent (create_return) OR raise (create_raises)."""
    mock_stripe = MagicMock()
    if create_raises is not None:
        mock_stripe.PaymentIntent.create.side_effect = create_raises
    else:
        mock_stripe.PaymentIntent.create.return_value = create_return
    return patch("backend.utils.stripe_charge.stripe", mock_stripe), mock_stripe


@pytest.mark.asyncio
class TestZeroAmount:
    async def test_zero_amount_succeeds_without_touching_stripe(self):
        from backend.utils.stripe_charge import charge_ride

        # No settings or stripe patching — should not be reached
        outcome = await charge_ride(ride={"id": "r"}, rider_id="u", total_amount=0.0)
        assert outcome.status == "succeeded"
        assert outcome.charged_amount == 0.0
        assert outcome.payment_intent_id is None

    async def test_negative_amount_also_short_circuits(self):
        from backend.utils.stripe_charge import charge_ride

        outcome = await charge_ride(ride={"id": "r"}, rider_id="u", total_amount=-5.0)
        assert outcome.status == "succeeded"


@pytest.mark.asyncio
class TestUnconfigured:
    async def test_no_stripe_key_returns_unconfigured(self):
        from backend.utils.stripe_charge import charge_ride

        # Patch stripe to a real MagicMock so the `stripe is None` guard is
        # bypassed and the empty-key guard is reached instead.
        stripe_patch, _ = _patch_stripe()
        with _patch_settings(secret=""), stripe_patch:
            outcome = await charge_ride(**_KW)
        assert outcome.status == "unconfigured"
        assert "not configured" in (outcome.error_message or "").lower()

    async def test_no_customer_on_file_returns_failed(self):
        from backend.utils.stripe_charge import charge_ride

        kw = {**_KW, "stripe_customer_id": None}
        stripe_patch, _ = _patch_stripe()
        with _patch_settings(), stripe_patch:
            outcome = await charge_ride(**kw)
        assert outcome.status == "failed"
        assert "Stripe customer" in (outcome.error_message or "")

    async def test_no_payment_method_returns_failed(self):
        from backend.utils.stripe_charge import charge_ride

        kw = {**_KW, "payment_method_id": None}
        stripe_patch, _ = _patch_stripe()
        with _patch_settings(), stripe_patch:
            outcome = await charge_ride(**kw)
        assert outcome.status == "failed"
        assert "payment method" in (outcome.error_message or "").lower()


@pytest.mark.asyncio
class TestSuccess:
    async def test_succeeded_status_returns_pi_id_and_amount(self):
        from backend.utils.stripe_charge import charge_ride

        intent = MagicMock(id="pi_success_xxx", status="succeeded", client_secret=None)
        stripe_patch, mock_stripe = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ride(**_KW)

        assert outcome.status == "succeeded"
        assert outcome.payment_intent_id == "pi_success_xxx"
        assert outcome.charged_amount == Decimal("25.50")
        # Stripe PaymentIntent.create called with correct shape
        mock_stripe.PaymentIntent.create.assert_called_once()
        kwargs = mock_stripe.PaymentIntent.create.call_args.kwargs
        assert kwargs["amount"] == 2550  # cents
        assert kwargs["currency"] == "cad"
        assert kwargs["customer"] == "cus_helper"
        assert kwargs["payment_method"] == "pm_helper"
        assert kwargs["off_session"] is True
        assert kwargs["confirm"] is True
        assert kwargs["metadata"]["ride_id"] == "ride_helper_1"
        assert kwargs["metadata"]["rider_id"] == "rider_helper_1"
        # Idempotency key pins future retries to the same PI; the amount is
        # part of the key so a re-charge at a different total gets a fresh key.
        assert kwargs["idempotency_key"] == "ride-charge-ride_helper_1-2550"

    async def test_amount_rounded_to_cents_correctly(self):
        """Float 19.999 must round to 2000 cents, not 1999 or 2001."""
        from backend.utils.stripe_charge import charge_ride

        intent = MagicMock(id="pi_1", status="succeeded", client_secret=None)
        stripe_patch, mock_stripe = _patch_stripe(create_return=intent)

        kw = {**_KW, "total_amount": 19.999}
        with _patch_settings(), stripe_patch:
            await charge_ride(**kw)

        kwargs = mock_stripe.PaymentIntent.create.call_args.kwargs
        assert kwargs["amount"] == 2000


@pytest.mark.asyncio
class TestRequiresAction:
    async def test_requires_action_returns_client_secret(self):
        """3DS / SCA path — rider-app needs the client_secret to run
        the Stripe confirmPayment() sheet. Don't swallow it."""
        from backend.utils.stripe_charge import charge_ride

        intent = MagicMock(
            id="pi_ra_1",
            status="requires_action",
            client_secret="pi_ra_1_secret_yyy",
        )
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ride(**_KW)

        assert outcome.status == "requires_action"
        assert outcome.payment_intent_id == "pi_ra_1"
        assert outcome.client_secret == "pi_ra_1_secret_yyy"

    async def test_requires_source_action_also_handled(self):
        """Legacy alias Stripe sometimes returns — treat as requires_action."""
        from backend.utils.stripe_charge import charge_ride

        intent = MagicMock(
            id="pi_rsa",
            status="requires_source_action",
            client_secret="pi_rsa_secret",
        )
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ride(**_KW)

        assert outcome.status == "requires_action"
        assert outcome.client_secret == "pi_rsa_secret"


@pytest.mark.asyncio
class TestCardDecline:
    async def test_card_error_returns_declined_with_code(self):
        """stripe.error.CardError → ChargeOutcome(status='declined')
        with the decline_code propagated for the rider-app to surface."""
        from backend.utils.stripe_charge import charge_ride

        # Build a CardError-shaped exception that matches Stripe's API.
        # Stripe's real CardError has an `.error` attribute with {code,
        # decline_code, message}. We shape a stand-in so we don't need
        # the real Stripe package to instantiate.
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
            outcome = await charge_ride(**_KW)

        assert outcome.status == "declined"
        assert outcome.decline_code == "insufficient_funds"
        assert "insufficient" in (outcome.error_message or "").lower()

    async def test_requires_payment_method_status_treated_as_declined(self):
        """Stripe sometimes returns requires_payment_method instead of
        raising CardError — same rider-facing outcome: try another card."""
        from backend.utils.stripe_charge import charge_ride

        intent = MagicMock(
            id="pi_rpm",
            status="requires_payment_method",
            client_secret=None,
        )
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ride(**_KW)

        assert outcome.status == "declined"
        assert outcome.payment_intent_id == "pi_rpm"


@pytest.mark.asyncio
class TestStripeOpsError:
    async def test_non_card_stripe_error_returns_failed(self):
        """api_connection_error / rate_limit_error / authentication_error —
        not the rider's fault. Caller should 5xx, not show a decline UI."""
        from backend.utils.stripe_charge import charge_ride

        class _FakeStripeError(Exception):
            pass

        # _NeverMatches ensures _FakeStripeError is NOT caught by the
        # _StripeCardError handler first (both default to Exception when the
        # stripe package isn't installed, so we must replace the card-error
        # class with something that won't match).
        class _NeverMatches(Exception):
            pass

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.side_effect = _FakeStripeError("api_connection_error: Unable to reach Stripe")

        with (
            _patch_settings(),
            patch("backend.utils.stripe_charge.stripe", mock_stripe),
            patch("backend.utils.stripe_charge._StripeCardError", _NeverMatches),
            patch("backend.utils.stripe_charge._StripeBaseError", _FakeStripeError),
        ):
            outcome = await charge_ride(**_KW)

        assert outcome.status == "failed"
        assert "api_connection_error" in (outcome.error_message or "")

    async def test_unhandled_pi_status_returns_failed(self):
        """Stripe returns something we don't know how to handle (canceled,
        processing, etc.) — don't silently mark the ride paid."""
        from backend.utils.stripe_charge import charge_ride

        intent = MagicMock(id="pi_weird", status="canceled", client_secret=None)
        stripe_patch, _ = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            outcome = await charge_ride(**_KW)

        assert outcome.status == "failed"
        assert "canceled" in (outcome.error_message or "").lower()


@pytest.mark.asyncio
class TestIdempotencyKey:
    async def test_same_ride_id_yields_same_idempotency_key(self):
        """Two calls for the same ride must use the same key so Stripe
        dedupes the charge (returns the original PI on the second call)."""
        from backend.utils.stripe_charge import charge_ride

        intent = MagicMock(id="pi_1", status="succeeded", client_secret=None)
        stripe_patch, mock_stripe = _patch_stripe(create_return=intent)

        with _patch_settings(), stripe_patch:
            await charge_ride(**_KW)
            await charge_ride(**_KW)

        keys = [call.kwargs["idempotency_key"] for call in mock_stripe.PaymentIntent.create.call_args_list]
        assert keys == ["ride-charge-ride_helper_1-2550", "ride-charge-ride_helper_1-2550"]

    async def test_confirm_path_carries_idempotency_key(self):
        """The retry/3DS confirm path must be idempotent too: two replicas
        that both pass the DB claim race must not both charge. The key is
        deterministic — ride-confirm-{ride_id}-{amount_cents} — so Stripe
        returns the original confirmation to the loser."""
        from backend.utils.stripe_charge import charge_ride

        intent = MagicMock(id="pi_existing", status="succeeded", client_secret=None)
        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.confirm.return_value = intent

        with _patch_settings(), patch("backend.utils.stripe_charge.stripe", mock_stripe):
            outcome = await charge_ride(**_KW, payment_intent_id="pi_existing")

        assert outcome.status == "succeeded"
        mock_stripe.PaymentIntent.create.assert_not_called()
        mock_stripe.PaymentIntent.confirm.assert_called_once()
        kwargs = mock_stripe.PaymentIntent.confirm.call_args.kwargs
        assert kwargs["idempotency_key"] == "ride-confirm-ride_helper_1-2550"
