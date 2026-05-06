"""Coverage-gap test: Stripe error specificity in ``create_payment_intent``.

Source under test: ``backend/routes/payments.py`` — the
``create_payment_intent`` handler currently catches ``Exception`` (line
~156) and returns a generic 500. That hides three failure modes the
client needs to distinguish:

  - ``stripe.error.CardError`` → declined / wrong CVC / expired card.
    The user can fix this (try another card). Correct status: 402.
  - ``stripe.error.RateLimitError`` → upstream throttling. The client
    should back off + retry. Correct status: 429.
  - 3-D Secure ``requires_action`` (currently raised as a CardError on
    ``confirm`` or returned as an intent with ``status='requires_action'``)
    → the SDK must run the next-action flow. Correct status: 402 with a
    machine-readable ``code='action_required'``.

These tests are intentionally ``xfail``: the fix is not yet wired up,
but we want the gap visible in CI so the next PR that touches this
handler trips and converts these to passing assertions.

DO NOT modify ``backend/routes/payments.py`` from this file — the
production fix is tracked separately.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from fastapi import HTTPException

# Underlying coroutine for the @idempotent_endpoint-wrapped route. The
# decorator stores the original on ``__wrapped__`` so tests can call it
# without setting up the Redis-backed idempotency store.
from backend.routes.payments import PaymentIntentRequest, create_payment_intent

_USER = {"id": "usr-pmt-001", "stripe_customer_id": "cus_test"}
_RIDE = {"id": "ride-pmt-001", "total_fare": "12.50", "rider_id": _USER["id"]}
_SETTINGS = {"stripe_secret_key": "sk_test_secret"}


def _unwrapped(fn):
    """Return the underlying coroutine for an @idempotent_endpoint route."""
    return getattr(fn, "__wrapped__", fn)


def _patch_common():
    """Patch the IO-bound deps shared by every test in this file."""
    db = MagicMock()
    db.get_ride = AsyncMock(return_value=_RIDE)
    db.get_user_by_id = AsyncMock(return_value=_USER)
    db.update_one = AsyncMock()
    return [
        patch(
            "backend.routes.payments.get_app_settings",
            new_callable=AsyncMock,
            return_value=_SETTINGS,
        ),
        patch("backend.routes.payments.db_supabase", db),
        # get_or_create_stripe_customer touches stripe.Customer.create only
        # if the user has no stripe_customer_id; _USER already has one so
        # this stays a pure DB read.
    ]


def _build_request_body() -> PaymentIntentRequest:
    return PaymentIntentRequest(
        amount=Decimal(_RIDE["total_fare"]),
        ride_id=_RIDE["id"],
        client_idempotency_key="test-key-001",
    )


@pytest.mark.anyio
@pytest.mark.xfail(
    reason=(
        "create_payment_intent currently catches Exception → 500. "
        "Should return 402 for stripe.error.CardError. "
        "Tracked as error-specificity gap (issue #TBD)."
    ),
    strict=False,
)
async def test_card_error_returns_402_not_500():
    """A CardError (declined / expired / wrong CVC) must surface as 402.

    402 'Payment Required' is the HTTP code the rider app uses to switch
    to the 'try another card' UX. Returning 500 sends the rider to a
    generic error screen with no actionable next step.
    """
    err = stripe.error.CardError(
        message="Your card was declined.",
        param="number",
        code="card_declined",
    )

    fake_request = MagicMock()
    fake_request.headers = {}
    fake_request.client = MagicMock(host="127.0.0.1")

    with (
        *_patch_common(),
        patch("backend.routes.payments.stripe.PaymentIntent.create", side_effect=err),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _unwrapped(create_payment_intent)(
                body=_build_request_body(),
                request=fake_request,
                current_user=_USER,
            )

    assert exc_info.value.status_code == 402, f"CardError must map to 402, got {exc_info.value.status_code}"


@pytest.mark.anyio
@pytest.mark.xfail(
    reason=(
        "create_payment_intent currently catches Exception → 500. "
        "Should return 429 for stripe.error.RateLimitError. "
        "Tracked as error-specificity gap (issue #TBD)."
    ),
    strict=False,
)
async def test_rate_limit_error_returns_429_not_500():
    """A Stripe RateLimitError must propagate as 429 so the client backs off.

    The rider app's Axios interceptor reads 429 + Retry-After to decide
    when to retry; collapsing it into 500 makes every transient blip
    look like a hard backend failure and short-circuits retry logic.
    """
    err = stripe.error.RateLimitError(message="Too many requests")

    fake_request = MagicMock()
    fake_request.headers = {}
    fake_request.client = MagicMock(host="127.0.0.1")

    with (
        *_patch_common(),
        patch("backend.routes.payments.stripe.PaymentIntent.create", side_effect=err),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _unwrapped(create_payment_intent)(
                body=_build_request_body(),
                request=fake_request,
                current_user=_USER,
            )

    assert exc_info.value.status_code == 429, f"RateLimitError must map to 429, got {exc_info.value.status_code}"


@pytest.mark.anyio
@pytest.mark.xfail(
    reason=(
        "create_payment_intent does not yet surface 3DS requires_action "
        "as 402 with action_required code. "
        "Tracked as error-specificity gap (issue #TBD)."
    ),
    strict=False,
)
async def test_requires_action_3ds_returns_402_action_required():
    """3-D Secure challenge must return 402 with a machine-readable code.

    When Stripe returns an intent in ``requires_action`` (or raises a
    CardError with code='authentication_required'), the rider SDK must
    trigger the next-action flow. The current handler treats this as a
    success (returning the client_secret) OR as a 500 — both paths leave
    the rider stuck without the 3DS prompt.

    Expected contract: HTTP 402, detail['code'] == 'action_required'.
    """
    # Stripe surfaces 3DS requirement via CardError with this code, OR via
    # a successful response carrying status='requires_action'. Test the
    # error path (the success path is a separate xfail concern).
    err = stripe.error.CardError(
        message="Authentication required",
        param=None,
        code="authentication_required",
    )

    fake_request = MagicMock()
    fake_request.headers = {}
    fake_request.client = MagicMock(host="127.0.0.1")

    with (
        *_patch_common(),
        patch("backend.routes.payments.stripe.PaymentIntent.create", side_effect=err),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _unwrapped(create_payment_intent)(
                body=_build_request_body(),
                request=fake_request,
                current_user=_USER,
            )

    assert exc_info.value.status_code == 402
    detail = exc_info.value.detail
    # Detail must be a structured dict carrying a stable code; a plain
    # string blocks the SDK from branching on the failure type.
    assert isinstance(detail, dict), "402 detail must be a structured dict"
    assert detail.get("code") == "action_required", (
        f"3DS requirement must surface as code='action_required', got {detail!r}"
    )
