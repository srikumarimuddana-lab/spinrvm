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


class _FakeErrObj:
    message = (
        "This account is not eligible for the requested card features. "
        "See https://stripe.com/docs/payments/flexible-payments for more details."
    )


class _FakeAccountIneligibleError(Exception):
    def __init__(self):
        super().__init__(_FakeErrObj.message)
        self.error = _FakeErrObj()


class _FakeOtherStripeError(Exception):
    pass


class _NeverMatches(Exception):
    """Patched in for _StripeCardError so a fake StripeError never mismatches
    into the card-decline branch, regardless of whether the real `stripe`
    package is installed in this environment."""


@pytest.mark.asyncio
class TestAccountIneligibleForIncrementalAuth:
    """Covers the production incident (Sentry issue 7726298620): this Stripe
    account isn't enrolled for incremental/extended authorizations, so Stripe
    rejects the WHOLE PaymentIntent.create call with a 400 invalid_request_error
    instead of just reporting the capability unavailable on the charge. Without
    a fallback, every booking authorization on this account failed outright —
    silently dropping the pre-auth hold (dead-card-before-dispatch protection)
    for every ride, not just this one.
    """

    async def test_retries_without_incremental_auth_and_still_places_the_hold(self):
        from backend.utils import stripe_charge

        mock_stripe = MagicMock()
        good_intent = _intent()
        mock_stripe.PaymentIntent.create.side_effect = [
            _FakeAccountIneligibleError(),
            good_intent,
        ]

        with (
            _patch_settings(),
            patch.object(stripe_charge, "stripe", mock_stripe),
            patch.object(stripe_charge, "_StripeCardError", _NeverMatches),
            patch.object(stripe_charge, "_StripeBaseError", Exception),
        ):
            outcome = await stripe_charge.authorize_ride(
                ride={"id": "ride_1"},
                rider_id="rider_1",
                amount=Decimal("25.00"),
                payment_method_id="pm_1",
                stripe_customer_id="cus_1",
            )

        assert outcome.status == "authorized"
        assert mock_stripe.PaymentIntent.create.call_count == 2

        first_kwargs = mock_stripe.PaymentIntent.create.call_args_list[0].kwargs
        second_kwargs = mock_stripe.PaymentIntent.create.call_args_list[1].kwargs
        assert first_kwargs["payment_method_options"]["card"]["request_incremental_authorization"] == "if_available"
        assert "request_incremental_authorization" not in second_kwargs["payment_method_options"]["card"]
        # A fresh idempotency key — the retry's params differ from the first
        # attempt, so reusing the original key would raise a Stripe idempotency
        # mismatch error instead of retrying.
        assert second_kwargs["idempotency_key"] != first_kwargs["idempotency_key"]

    async def test_unrelated_stripe_error_does_not_retry(self):
        """A different invalid_request_error (or any other StripeError) must
        surface as `failed` as before — only this specific account-eligibility
        message triggers the fallback retry."""
        from backend.utils import stripe_charge

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.side_effect = _FakeOtherStripeError("something else went wrong")

        with (
            _patch_settings(),
            patch.object(stripe_charge, "stripe", mock_stripe),
            patch.object(stripe_charge, "_StripeCardError", _NeverMatches),
            patch.object(stripe_charge, "_StripeBaseError", Exception),
        ):
            outcome = await stripe_charge.authorize_ride(
                ride={"id": "ride_1"},
                rider_id="rider_1",
                amount=Decimal("25.00"),
                payment_method_id="pm_1",
                stripe_customer_id="cus_1",
            )

        assert outcome.status == "failed"
        assert mock_stripe.PaymentIntent.create.call_count == 1

    async def test_retry_also_failing_returns_failed(self):
        from backend.utils import stripe_charge

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.side_effect = [
            _FakeAccountIneligibleError(),
            _FakeOtherStripeError("still broken"),
        ]

        with (
            _patch_settings(),
            patch.object(stripe_charge, "stripe", mock_stripe),
            patch.object(stripe_charge, "_StripeCardError", _NeverMatches),
            patch.object(stripe_charge, "_StripeBaseError", Exception),
        ):
            outcome = await stripe_charge.authorize_ride(
                ride={"id": "ride_1"},
                rider_id="rider_1",
                amount=Decimal("25.00"),
                payment_method_id="pm_1",
                stripe_customer_id="cus_1",
            )

        assert outcome.status == "failed"
        assert mock_stripe.PaymentIntent.create.call_count == 2


@pytest.fixture(autouse=True)
def _clear_incremental_auth_cache():
    """The account-ineligibility cache is module-level state.

    Without this, whichever test first triggers the refusal would silently
    change what every later test observes on its FIRST PaymentIntent.create —
    including the assertion above that the first attempt asks for incremental
    authorization. Reset on both sides so the suite stays order-independent.
    """
    from backend.utils import stripe_charge

    stripe_charge._reset_incremental_auth_eligibility_cache()
    yield
    stripe_charge._reset_incremental_auth_eligibility_cache()


@pytest.mark.asyncio
class TestIneligibilityIsRememberedAcrossBookings:
    """Once Stripe says the ACCOUNT cannot request incremental authorization,
    asking again only mints a PaymentIntent certain to fail — and each failure
    fires a payment_intent.payment_failed webhook for a ride row the booking
    flow has not inserted yet, which the webhook handler answers with a 500 so
    Stripe retries. That was one orphaned failed PI, one 500 and one Sentry
    error per booking, observed 4/4 on 2026-09-12.
    """

    async def test_second_booking_does_not_request_incremental_auth_at_all(self):
        from backend.utils import stripe_charge

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.side_effect = [
            _FakeAccountIneligibleError(),  # booking 1, attempt 1 — refused
            _intent(),  # booking 1, attempt 2 — succeeds
            _intent(),  # booking 2 — must succeed FIRST try
        ]

        with (
            _patch_settings(),
            patch.object(stripe_charge, "stripe", mock_stripe),
            patch.object(stripe_charge, "_StripeCardError", _NeverMatches),
            patch.object(stripe_charge, "_StripeBaseError", Exception),
        ):
            first = await stripe_charge.authorize_ride(
                ride={"id": "ride_1"},
                rider_id="rider_1",
                amount=Decimal("25.00"),
                payment_method_id="pm_1",
                stripe_customer_id="cus_1",
            )
            second = await stripe_charge.authorize_ride(
                ride={"id": "ride_2"},
                rider_id="rider_1",
                amount=Decimal("25.00"),
                payment_method_id="pm_1",
                stripe_customer_id="cus_1",
            )

        assert first.status == "authorized"
        assert second.status == "authorized"
        # Three creates total, not four: the second booking skipped the doomed one.
        assert mock_stripe.PaymentIntent.create.call_count == 3

        third_kwargs = mock_stripe.PaymentIntent.create.call_args_list[2].kwargs
        assert "request_incremental_authorization" not in third_kwargs["payment_method_options"]["card"]

    async def test_the_skip_does_not_survive_a_cache_reset(self):
        """Eligibility is cached, not persisted — a restart must re-probe."""
        from backend.utils import stripe_charge

        stripe_charge._mark_incremental_auth_ineligible()
        stripe_charge._reset_incremental_auth_eligibility_cache()

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.side_effect = [_intent()]
        with (
            _patch_settings(),
            patch.object(stripe_charge, "stripe", mock_stripe),
            patch.object(stripe_charge, "_StripeCardError", _NeverMatches),
            patch.object(stripe_charge, "_StripeBaseError", Exception),
        ):
            await stripe_charge.authorize_ride(
                ride={"id": "ride_3"},
                rider_id="rider_1",
                amount=Decimal("25.00"),
                payment_method_id="pm_1",
                stripe_customer_id="cus_1",
            )

        kwargs = mock_stripe.PaymentIntent.create.call_args_list[0].kwargs
        assert kwargs["payment_method_options"]["card"]["request_incremental_authorization"] == "if_available"

    async def test_first_observation_logs_once_not_per_booking(self):
        from backend.utils import stripe_charge

        assert stripe_charge._mark_incremental_auth_ineligible() is True
        assert stripe_charge._mark_incremental_auth_ineligible() is False
        assert stripe_charge._mark_incremental_auth_ineligible() is False

    async def test_cached_skip_reuses_the_basic_idempotency_key(self):
        """A key must stay 1:1 with the params it was first used with.

        The no-incremental param shape has always been paired with
        "<key>-basic" by the fallback. When the cache makes the FIRST call that
        same shape, it must reuse that same key — otherwise a later authorize
        for the same ride+amount sends "ride-auth-..." with params that differ
        from the ones Stripe recorded against it on the first booking, and
        Stripe answers with an idempotency error. That error is not the
        ineligibility string, so it would fall through to status="failed" and
        silently drop the pre-auth hold.
        """
        from backend.utils import stripe_charge

        mock_stripe = MagicMock()
        mock_stripe.PaymentIntent.create.side_effect = [
            _FakeAccountIneligibleError(),  # booking 1, attempt 1
            _intent(),  # booking 1, attempt 2 (fallback)
            _intent(),  # booking 2, single attempt
        ]

        with (
            _patch_settings(),
            patch.object(stripe_charge, "stripe", mock_stripe),
            patch.object(stripe_charge, "_StripeCardError", _NeverMatches),
            patch.object(stripe_charge, "_StripeBaseError", Exception),
        ):
            for ride_id in ("ride_kx", "ride_kx"):
                await stripe_charge.authorize_ride(
                    ride={"id": ride_id},
                    rider_id="rider_1",
                    amount=Decimal("25.00"),
                    payment_method_id="pm_1",
                    stripe_customer_id="cus_1",
                )

        calls = mock_stripe.PaymentIntent.create.call_args_list
        first_key = calls[0].kwargs["idempotency_key"]  # with incremental
        fallback_key = calls[1].kwargs["idempotency_key"]  # without
        cached_key = calls[2].kwargs["idempotency_key"]  # without, via cache

        assert fallback_key == f"{first_key}-basic"
        # The cached-skip call carries the SAME params shape as the fallback, so
        # it must carry the same key — never the one already bound to the
        # incremental-request shape.
        assert cached_key == fallback_key
        assert cached_key != first_key
