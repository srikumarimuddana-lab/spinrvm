"""
The Stripe param/response SHAPES for incremental authorization.

These exist because three blockers got through review by hiding behind mocks:
test_ride_preauth_booking.py patches `authorize_ride` itself, so the real
`stripe.PaymentIntent.create(**params)` call was never exercised, and neither
was the read-back of the capability off the charge.

Both fields are easy to get subtly wrong because Stripe has a near-identical
pair with different nesting, spelling and type:

  payment_method_options.card.request_incremental_authorization
      -> Literal["if_available","never"]     CARD-NOT-PRESENT (what we use)
  payment_method_options.card_present.request_incremental_authorization_support
      -> bool                                TERMINAL / in-person only

and on the resulting Charge:

  payment_method_details.card.incremental_authorization.status
      -> Literal["available","unavailable"]  CARD-NOT-PRESENT (what we read)
  payment_method_details.card_present.incremental_authorization_supported
      -> bool                                TERMINAL / in-person only

Getting either wrong fails in opposite, equally bad ways: a bad create param
risks a 400 on EVERY booking authorization, and a bad read pins every ride to
the two-charge fallback forever while looking like it works.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _patch_settings():
    return patch(
        "backend.utils.stripe_charge.get_app_settings",
        AsyncMock(return_value={"stripe_secret_key": "sk_test_xxx"}),
    )


def _intent(status="requires_capture", latest_charge=None):
    pi = MagicMock()
    pi.status = status
    pi.id = "pi_auth"
    pi.client_secret = "cs_x"
    pi.latest_charge = latest_charge
    return pi


def _charge_with(incremental_status=None, card_present_bool=None):
    """A Charge whose payment_method_details mirrors the real SDK shape."""
    card: dict = {}
    if incremental_status is not None:
        card["incremental_authorization"] = {"status": incremental_status}
    details: dict = {"card": card}
    if card_present_bool is not None:
        details["card_present"] = {"incremental_authorization_supported": card_present_bool}
    charge = MagicMock()
    charge.payment_method_details = details
    return charge


async def _authorize(intent):
    from backend.utils import stripe_charge

    mock_stripe = MagicMock()
    mock_stripe.PaymentIntent.create.return_value = intent
    with _patch_settings(), patch.object(stripe_charge, "stripe", mock_stripe):
        outcome = await stripe_charge.authorize_ride(
            ride={"id": "ride_1"},
            rider_id="rider_1",
            amount=Decimal("25.00"),
            payment_method_id="pm_1",
            stripe_customer_id="cus_1",
        )
    return outcome, mock_stripe


@pytest.mark.asyncio
class TestCreateParamShape:
    async def test_requests_increment_under_payment_method_options_card(self):
        _, mock_stripe = await _authorize(_intent())
        params = mock_stripe.PaymentIntent.create.call_args.kwargs

        assert params["payment_method_options"]["card"]["request_incremental_authorization"] == "if_available"

    async def test_does_not_send_the_terminal_only_top_level_param(self):
        """`request_incremental_authorization_support` is card_present-only and is
        not a valid top-level create param. Sending it risks a 400 that would
        break every card booking, not just tips."""
        _, mock_stripe = await _authorize(_intent())
        params = mock_stripe.PaymentIntent.create.call_args.kwargs

        assert "request_incremental_authorization_support" not in params

    async def test_expands_latest_charge_so_the_capability_is_readable(self):
        _, mock_stripe = await _authorize(_intent())
        params = mock_stripe.PaymentIntent.create.call_args.kwargs

        assert "latest_charge" in params["expand"]

    async def test_still_a_manual_capture_hold(self):
        _, mock_stripe = await _authorize(_intent())
        params = mock_stripe.PaymentIntent.create.call_args.kwargs

        assert params["capture_method"] == "manual"
        assert params["confirm"] is True


@pytest.mark.asyncio
class TestCapabilityReadBack:
    async def test_available_status_is_granted(self):
        outcome, _ = await _authorize(_intent(latest_charge=_charge_with(incremental_status="available")))
        assert outcome.status == "authorized"
        assert outcome.incremental_authorization_supported is True

    async def test_unavailable_status_is_not_granted(self):
        outcome, _ = await _authorize(_intent(latest_charge=_charge_with(incremental_status="unavailable")))
        assert outcome.incremental_authorization_supported is False

    async def test_terminal_only_boolean_is_not_mistaken_for_the_cnp_field(self):
        """A charge carrying ONLY the card_present boolean must not read as
        granted — that field never appears on a card-not-present charge, and
        treating it as the source of truth is how this silently broke."""
        outcome, _ = await _authorize(_intent(latest_charge=_charge_with(card_present_bool=True)))
        assert outcome.incremental_authorization_supported is False

    async def test_missing_or_unexpanded_charge_degrades_to_not_granted(self):
        for charge in (None, "ch_unexpanded_string"):
            outcome, _ = await _authorize(_intent(latest_charge=charge))
            assert outcome.incremental_authorization_supported is False

    async def test_a_probe_failure_never_breaks_the_authorization(self):
        broken = MagicMock()
        type(broken).payment_method_details = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        outcome, _ = await _authorize(_intent(latest_charge=broken))
        # The hold still stands; only the capability degrades.
        assert outcome.status == "authorized"
        assert outcome.incremental_authorization_supported is False
