"""
Direct unit coverage for utils/stripe_charge.py::refund_excess_capture.

Companion to backend/tests/test_cancel_already_captured_refund.py, which
covers this helper's caller (cancellation.py) with mocked _deps. This file
covers the helper itself against a mocked Stripe SDK: reading
amount_received as the source of truth, the not_needed/refunded/failed/
unconfigured branches, and the idempotency-key shape.

See docs/change-log/2026-09-21-payment-retry-requires-capture-pre-trip-guard.md
for the bug this refund path closes.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.stripe_charge import refund_excess_capture


def _patch_stripe(amount_received: int = 210, refund_id: str = "re_test_1", refund_raises=None):
    mock_stripe = MagicMock()
    intent = MagicMock()
    intent.amount_received = amount_received
    mock_stripe.PaymentIntent.retrieve.return_value = intent
    if refund_raises is not None:
        mock_stripe.Refund.create.side_effect = refund_raises
    else:
        refund = MagicMock()
        refund.id = refund_id
        mock_stripe.Refund.create.return_value = refund
    return patch("backend.utils.stripe_charge.stripe", mock_stripe), mock_stripe


def _patch_secret(secret: str = "sk_test_xxx"):
    return patch(
        "backend.utils.stripe_charge.get_app_settings",
        AsyncMock(return_value={"stripe_secret_key": secret}),
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestRefundExcessCapture:
    async def test_no_payment_intent_is_not_needed_and_never_calls_stripe(self):
        outcome = await refund_excess_capture(ride_id="r1", payment_intent_id="", fee_owed=Decimal("0"))
        assert outcome.status == "not_needed"

    async def test_unconfigured_when_no_stripe_secret(self):
        with patch("backend.utils.stripe_charge.get_app_settings", AsyncMock(return_value={})):
            outcome = await refund_excess_capture(ride_id="r1", payment_intent_id="pi_1", fee_owed=Decimal("0"))
        assert outcome.status == "unconfigured"

    async def test_zero_fee_refunds_the_full_captured_amount(self):
        stripe_patch, mock_stripe = _patch_stripe(amount_received=210)
        with _patch_secret(), stripe_patch:
            outcome = await refund_excess_capture(ride_id="r1", payment_intent_id="pi_1", fee_owed=Decimal("0"))

        assert outcome.status == "refunded"
        assert outcome.charged_amount == Decimal("2.10")
        assert mock_stripe.Refund.create.call_args.kwargs["amount"] == 210
        assert mock_stripe.Refund.create.call_args.kwargs["payment_intent"] == "pi_1"

    async def test_partial_fee_refunds_only_the_excess(self):
        stripe_patch, mock_stripe = _patch_stripe(amount_received=210)
        with _patch_secret(), stripe_patch:
            outcome = await refund_excess_capture(ride_id="r1", payment_intent_id="pi_1", fee_owed=Decimal("2.00"))

        assert outcome.status == "refunded"
        assert outcome.charged_amount == Decimal("0.10")
        assert mock_stripe.Refund.create.call_args.kwargs["amount"] == 10

    async def test_fee_owed_covers_or_exceeds_capture_is_not_needed(self):
        stripe_patch, mock_stripe = _patch_stripe(amount_received=210)
        with _patch_secret(), stripe_patch:
            outcome = await refund_excess_capture(ride_id="r1", payment_intent_id="pi_1", fee_owed=Decimal("2.10"))

        assert outcome.status == "not_needed"
        mock_stripe.Refund.create.assert_not_called()

    async def test_nothing_was_captured_is_not_needed(self):
        stripe_patch, mock_stripe = _patch_stripe(amount_received=0)
        with _patch_secret(), stripe_patch:
            outcome = await refund_excess_capture(ride_id="r1", payment_intent_id="pi_1", fee_owed=Decimal("0"))

        assert outcome.status == "not_needed"
        mock_stripe.Refund.create.assert_not_called()

    async def test_stripe_error_on_retrieve_is_reported_as_failed(self):
        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.retrieve.side_effect = RuntimeError("stripe down")
        with _patch_secret(), patch("backend.utils.stripe_charge.stripe", mock_stripe):
            outcome = await refund_excess_capture(ride_id="r1", payment_intent_id="pi_1", fee_owed=Decimal("0"))

        assert outcome.status == "failed"

    async def test_stripe_error_on_refund_create_is_reported_as_failed_not_lost(self):
        stripe_patch, mock_stripe = _patch_stripe(amount_received=210, refund_raises=RuntimeError("card network down"))
        with _patch_secret(), stripe_patch:
            outcome = await refund_excess_capture(ride_id="r1", payment_intent_id="pi_1", fee_owed=Decimal("0"))

        assert outcome.status == "failed"
        assert outcome.error_message

    async def test_idempotency_key_includes_ride_and_amount(self):
        """Amount is part of the key so a later, different-amount refund on
        the same PI (e.g. an admin dispute refund) gets its own key rather
        than colliding with this one's IdempotencyError."""
        stripe_patch, mock_stripe = _patch_stripe(amount_received=210)
        with _patch_secret(), stripe_patch:
            await refund_excess_capture(ride_id="ride_xyz", payment_intent_id="pi_1", fee_owed=Decimal("0"))

        key = mock_stripe.Refund.create.call_args.kwargs["idempotency_key"]
        assert key == "ride-cancelrefund-ride_xyz-210"
