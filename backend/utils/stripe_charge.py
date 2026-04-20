"""
Stripe card-charge helper for ride completion.

Wraps the ``stripe.PaymentIntent`` lifecycle into a single function that
returns a structured outcome. Used by:

    - backend/routes/rides.py::process_payment  (sync charge at completion)
    - backend/utils/payment_retry.py            (async retry loop)

A single helper means both paths handle decline / 3DS / success identically
and the post-charge bookkeeping (receipt email, lock release, etc.) lives
in exactly one place — the caller.

Outcome variants
----------------

``ChargeOutcome`` has a ``status`` field with one of:

    "succeeded"         → card charged; caller should mark ride paid
    "requires_action"   → 3DS / SCA required; caller returns client_secret
                          to the rider-app, which runs the 3DS sheet and
                          retries process_payment
    "declined"          → card declined (stripe.error.CardError); caller
                          releases the processing lock so rider can retry
                          with a different card
    "failed"            → other Stripe-side error (config, invalid request,
                          rate limit, etc.); caller releases lock; this is
                          not a user-facing card decline, it's an ops issue
    "unconfigured"      → no stripe_secret_key in settings; used only in
                          test / dev environments so the system doesn't
                          wedge when Stripe isn't wired up yet

Idempotency
-----------

``idempotency_key = "ride-charge-{ride_id}"`` — Stripe dedupes identical
keys for 24h. Combined with the caller's atomic ``payment_status =
'processing'`` DB lock, a double-tap or retry after a dropped response
cannot result in a double charge.

Currency
--------

CAD, hardcoded. See P0-5 scoping doc §9 for the multi-currency question.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

try:
    from ..settings_loader import get_app_settings
except ImportError:
    from settings_loader import get_app_settings

try:
    import stripe
    from stripe.error import CardError as _StripeCardError
    from stripe.error import StripeError as _StripeBaseError
except ImportError:  # pragma: no cover — stripe is a runtime dep in prod
    stripe = None  # type: ignore[assignment]
    _StripeCardError = Exception  # type: ignore[misc,assignment]
    _StripeBaseError = Exception  # type: ignore[misc,assignment]


logger = logging.getLogger(__name__)

CURRENCY = "cad"


@dataclass
class ChargeOutcome:
    status: str  # one of: succeeded, requires_action, declined, failed, unconfigured
    payment_intent_id: Optional[str] = None
    client_secret: Optional[str] = None
    charged_amount: float = 0.0
    decline_code: Optional[str] = None
    error_message: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


async def charge_ride(
    *,
    ride: Dict[str, Any],
    rider_id: str,
    total_amount: float,
    payment_method_id: Optional[str] = None,
    stripe_customer_id: Optional[str] = None,
    payment_intent_id: Optional[str] = None,
) -> ChargeOutcome:
    """Charge the rider's card for a completed ride.

    Parameters
    ----------
    ride
        The ride row (or dict) — used for metadata + idempotency_key.
    rider_id
        Used for metadata so Stripe dashboard searches work.
    total_amount
        Fare + tip in dollars; converted to cents for Stripe.
    payment_method_id
        Stripe pm_xxx to charge. Required unless you rely on the customer's
        default attached payment method.
    stripe_customer_id
        Stripe cus_xxx. Required for off-session charges against a saved card.
    payment_intent_id
        If the ride already has a PaymentIntent (e.g. from a prior
        requires_action response), confirm it rather than creating a new one.

    Returns
    -------
    ChargeOutcome — never raises. Callers switch on ``outcome.status``.
    """
    if total_amount <= 0:
        # A $0 ride is probably a bug upstream, but we don't want to
        # hit Stripe for it. Treat as a no-op success — no charge needed.
        return ChargeOutcome(status="succeeded", charged_amount=0.0)

    if stripe is None:
        logger.error("stripe package not installed; cannot charge card")
        return ChargeOutcome(status="unconfigured", error_message="stripe not installed")

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "") or ""
    if not stripe_secret:
        logger.warning(
            "stripe_secret_key not configured; skipping real charge for ride=%s",
            ride.get("id"),
        )
        return ChargeOutcome(
            status="unconfigured",
            error_message="Payment processing is not configured",
        )

    if not stripe_customer_id:
        return ChargeOutcome(
            status="failed",
            error_message="No Stripe customer on file for rider",
        )

    if not payment_method_id:
        return ChargeOutcome(
            status="failed",
            error_message="No default payment method on file",
        )

    ride_id = ride.get("id") or ""
    amount_cents = int(round(float(total_amount) * 100))

    # Idempotency: the same ride can only be charged once within 24h
    # regardless of how many retries the client makes. If an existing
    # PaymentIntent exists for this ride, pass it through — Stripe will
    # return the original PI on matching idempotency_key.
    idempotency_key = f"ride-charge-{ride_id}"

    params: Dict[str, Any] = {
        "amount": amount_cents,
        "currency": CURRENCY,
        "customer": stripe_customer_id,
        "payment_method": payment_method_id,
        # off_session + confirm=True: charge the saved card without a
        # second user prompt. If Stripe decides SCA is needed, the
        # response comes back as requires_action and the rider-app
        # runs the 3DS sheet with the returned client_secret.
        "off_session": True,
        "confirm": True,
        "metadata": {
            "ride_id": ride_id,
            "rider_id": rider_id,
            "source": "ride_completion_charge",
        },
    }

    try:
        if payment_intent_id:
            # Confirm an already-created PaymentIntent (e.g. retry after 3DS
            # requires_action, or a PI created at booking time).
            intent = stripe.PaymentIntent.confirm(
                payment_intent_id,
                api_key=stripe_secret,
            )
        else:
            intent = stripe.PaymentIntent.create(
                **params,
                api_key=stripe_secret,
                idempotency_key=idempotency_key,
            )
    except _StripeCardError as e:
        # Card explicitly declined (insufficient_funds, card_declined, etc.).
        # This is the "surface retry UX to the rider" case.
        err = getattr(e, "error", None)
        decline_code = getattr(err, "decline_code", None) or getattr(err, "code", None)
        logger.info(
            "Card declined for ride=%s rider=%s code=%s: %s",
            ride_id, rider_id, decline_code, e,
        )
        return ChargeOutcome(
            status="declined",
            decline_code=decline_code,
            error_message=str(getattr(err, "message", None) or e),
        )
    except _StripeBaseError as e:
        # Non-decline Stripe error (api_connection_error, authentication_error,
        # invalid_request_error, rate_limit_error, etc.). Not the rider's fault.
        logger.error(
            "Stripe error charging ride=%s rider=%s: %s", ride_id, rider_id, e
        )
        return ChargeOutcome(
            status="failed",
            error_message=str(e),
        )
    except Exception as e:  # pragma: no cover — defence-in-depth
        logger.exception("Unexpected error charging ride=%s: %s", ride_id, e)
        return ChargeOutcome(status="failed", error_message=str(e))

    # Stripe returned without raising. Branch on the intent status.
    status = getattr(intent, "status", "") or ""
    pi_id = getattr(intent, "id", None)
    client_secret = getattr(intent, "client_secret", None)

    if status == "succeeded":
        return ChargeOutcome(
            status="succeeded",
            payment_intent_id=pi_id,
            charged_amount=float(total_amount),
        )

    if status == "requires_action" or status == "requires_source_action":
        # 3DS / SCA challenge. Hand the client_secret back so the
        # rider-app's Stripe SDK can run confirmPayment().
        return ChargeOutcome(
            status="requires_action",
            payment_intent_id=pi_id,
            client_secret=client_secret,
        )

    if status in ("requires_payment_method", "requires_confirmation"):
        # No usable card attached, or the confirm didn't stick. Treated
        # as a decline so the rider sees the same "try another card" UX.
        return ChargeOutcome(
            status="declined",
            payment_intent_id=pi_id,
            error_message=f"PaymentIntent unexpectedly in state: {status}",
        )

    # Any other status (canceled, processing) is a failure we don't
    # automatically retry — processing should resolve via webhook;
    # canceled means someone else killed the PI.
    logger.warning(
        "Unhandled PaymentIntent status=%s for ride=%s pi=%s",
        status, ride_id, pi_id,
    )
    return ChargeOutcome(
        status="failed",
        payment_intent_id=pi_id,
        error_message=f"Unhandled PaymentIntent status: {status}",
    )
