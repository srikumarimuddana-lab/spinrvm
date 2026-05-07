"""Stripe error specificity at POST /payments/create-intent.

Pins the contract that the rider app's error-branching depends on:

  * ``stripe.error.CardError``               → 402 (PaymentMethodInvalid)
                                                so the SDK switches to
                                                "try another card" UX.
  * ``stripe.error.RateLimitError``          → 429 + ``Retry-After`` so
                                                the Axios interceptor
                                                backs off rather than
                                                hammering Stripe.
  * 3-D Secure ``authentication_required``   → 402 with
                                                ``detail['code'] ==
                                                'action_required'`` so
                                                the SDK runs the
                                                next-action flow.

Re-creates the file dropped during the merge of PR #555 (the original
landed with all three tests marked ``xfail`` because the route still
returned a generic 500); xfail is now removed and the assertions are
positive.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe

try:
    from backend.routes.payments import PaymentIntentRequest, create_payment_intent
    from backend.utils.error_handling import PaymentMethodInvalidException, SpinrException
except ImportError:  # backend/ on sys.path (pytest from inside backend/)
    from routes.payments import PaymentIntentRequest, create_payment_intent
    from utils.error_handling import PaymentMethodInvalidException, SpinrException


_USER = {"id": "usr-001", "stripe_customer_id": "cus_existing"}
_STRIPE_SECRET = "sk_test_secret"


def _settings(with_secret: bool = True):
    return {
        "stripe_secret_key": _STRIPE_SECRET if with_secret else "",
        "stripe_publishable_key": "pk_test_pub",
    }


def _mock_request() -> MagicMock:
    """Minimal Request stand-in for the route's signature.

    The route reads ``request.headers`` only via the idempotent_endpoint
    decorator; a MagicMock with an empty headers dict is enough.
    """
    req = MagicMock()
    req.headers = {}
    return req


@pytest.mark.anyio
async def test_card_error_returns_402_not_500():
    """Generic decline → 402 PaymentMethodInvalid (was 500 before this PR)."""
    card_error = stripe.error.CardError(
        message="Your card was declined.",
        param="number",
        code="card_declined",
    )

    with (
        patch(
            "backend.routes.payments.get_app_settings",
            new_callable=AsyncMock,
            return_value=_settings(),
        ),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentIntent.create",
            side_effect=card_error,
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        with pytest.raises(PaymentMethodInvalidException) as exc_info:
            await create_payment_intent(
                body=PaymentIntentRequest(amount="10.00"),
                request=_mock_request(),
                current_user=_USER,
            )

    # The mobile SDK keys off the 402 status code to switch into "try
    # another card" UX.  500 would route the user into the generic
    # error toast and lose the intent.
    assert exc_info.value.status_code == 402
    # No `action_required` for a plain decline — that's only set for
    # 3-D Secure challenges (covered in test_requires_action_3ds_*).
    details = exc_info.value.details or {}
    assert details.get("code") != "action_required"


@pytest.mark.anyio
async def test_rate_limit_error_returns_429_not_500():
    """Stripe throttling → 429 + Retry-After so the Axios interceptor backs off."""
    rate_error = stripe.error.RateLimitError("Too many requests")

    with (
        patch(
            "backend.routes.payments.get_app_settings",
            new_callable=AsyncMock,
            return_value=_settings(),
        ),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentIntent.create",
            side_effect=rate_error,
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        with pytest.raises(SpinrException) as exc_info:
            await create_payment_intent(
                body=PaymentIntentRequest(amount="10.00"),
                request=_mock_request(),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 429
    # Retry-After header lets the rider-app's Axios interceptor back off
    # rather than retrying instantly and worsening the throttle.
    assert exc_info.value.headers.get("Retry-After") == "30"


@pytest.mark.anyio
async def test_requires_action_3ds_returns_402_action_required():
    """3-D Secure synchronous CardError → 402 with detail.code == 'action_required'.

    Stripe surfaces SCA challenges either as ``intent.status ==
    'requires_action'`` on the success path or as a synchronous
    ``CardError(code='authentication_required')`` on the error path.
    Both must produce the same envelope for the mobile SDK so
    ``handleNextAction()`` is invoked uniformly.
    """
    auth_error = stripe.error.CardError(
        message="Authentication required.",
        param=None,
        code="authentication_required",
    )

    with (
        patch(
            "backend.routes.payments.get_app_settings",
            new_callable=AsyncMock,
            return_value=_settings(),
        ),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentIntent.create",
            side_effect=auth_error,
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        with pytest.raises(PaymentMethodInvalidException) as exc_info:
            await create_payment_intent(
                body=PaymentIntentRequest(amount="10.00"),
                request=_mock_request(),
                current_user=_USER,
            )

    assert exc_info.value.status_code == 402
    details = exc_info.value.details or {}
    assert details.get("code") == "action_required"
