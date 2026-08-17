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

``idempotency_key = "ride-charge-{ride_id}-{amount_cents}-{payment_method_id}"``
on the create path and ``"ride-confirm-{ride_id}-{amount_cents}"`` on the
confirm (retry / 3DS) path — Stripe dedupes identical keys for 24h. The amount
is part of the key so a legitimate re-charge at a different total (e.g. tip
updated after a decline) gets a fresh key instead of an IdempotencyError; the
payment_method is part of the create key so a re-charge on a DIFFERENT card
(the "Change Card" escape) is not replayed as the prior card's decline.
payment_retry.py uses the same ride-confirm key scheme so both confirm
code paths share Stripe-side deduplication. Combined with the caller's
atomic ``payment_status = 'processing'`` DB lock, a double-tap or retry
after a dropped response cannot result in a double charge.

Currency
--------

CAD, hardcoded. See P0-5 scoping doc §9 for the multi-currency question.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional, Union

try:
    from ..settings_loader import get_app_settings
    from .money import dollars_to_cents, to_decimal
except ImportError:
    from settings_loader import get_app_settings
    from utils.money import dollars_to_cents, to_decimal

try:
    import stripe

    # stripe v10+ exposes errors directly on the top-level package;
    # the stripe.error sub-module was removed in stripe-python v15.
    _StripeCardError = stripe.CardError  # type: ignore[attr-defined]
    _StripeBaseError = stripe.StripeError  # type: ignore[attr-defined]
except (ImportError, AttributeError):  # pragma: no cover
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
    charged_amount: Decimal = Decimal("0.00")
    decline_code: Optional[str] = None
    error_message: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    # Whether THIS authorization can later have its amount raised via
    # PaymentIntent.increment_authorization (see ``increment_authorization``).
    # Set by ``authorize_ride`` from the card's own capability flag — it varies
    # per card brand and issuer, so it is read back from Stripe rather than
    # assumed. False means a post-trip tip must be a separate charge.
    incremental_authorization_supported: bool = False


async def charge_ride(
    *,
    ride: Dict[str, Any],
    rider_id: str,
    total_amount: Union[Decimal, float],
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
        return ChargeOutcome(status="succeeded", charged_amount=Decimal("0.00"))

    if stripe is None:
        logger.error("stripe package not installed; cannot charge card")
        return ChargeOutcome(status="unconfigured", error_message="stripe not installed")

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "") or ""
    if not stripe_secret:
        logger.error(
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
    amount_cents = dollars_to_cents(total_amount)

    # Idempotency: a retry of the SAME ride for the SAME amount on the SAME card
    # must not double-charge — same key → Stripe returns the original
    # PaymentIntent. The amount (cents) is part of the key so a legitimate
    # re-charge at a DIFFERENT total (e.g. the rider updated their tip after a
    # declined attempt) gets a fresh key. The payment_method is ALSO part of the
    # key: when the rider picks a DIFFERENT card after a decline (the in-app
    # "Change Card" escape), the same ride+amount on a new card must mint a fresh
    # key — otherwise Stripe replays the prior decline / raises a parameter
    # mismatch and the new card is never charged.
    idempotency_key = f"ride-charge-{ride_id}-{amount_cents}-{payment_method_id}"

    fare_amount = Decimal(str(ride.get("total_fare", 0) or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tip = Decimal(str(total_amount)) - fare_amount
    tip_str = str(max(tip, Decimal("0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

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
        # Enable payment methods but never the redirect-based ones (e.g.
        # iDEAL, Klarna) the account may have toggled on in the Stripe
        # Dashboard. A server-side off_session confirm has no browser to
        # redirect back to, so without this Stripe rejects the call with
        # invalid_request_error ("you must provide a `return_url`"), which
        # broke every ride charge. Card-only keeps the hold/charge path
        # non-redirecting and return_url-free.
        "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
        "metadata": {
            "ride_id": ride_id,
            "rider_id": rider_id,
            "driver_id": ride.get("driver_id") or "",
            "fare_amount": str(fare_amount),
            "tip_amount": tip_str,
            "surge_multiplier": str(ride.get("surge_multiplier") or "1.0"),
            "payment_method_type": ride.get("payment_method") or "card",
            "source": "ride_completion_charge",
        },
    }

    try:
        if payment_intent_id:
            # Confirm an already-created PaymentIntent (e.g. retry after 3DS
            # requires_action, or a PI created at booking time).
            # Idempotency key guards against two replicas both confirming and
            # both charging when they simultaneously pass the DB claim race.
            intent = await asyncio.to_thread(
                lambda: stripe.PaymentIntent.confirm(
                    payment_intent_id,
                    api_key=stripe_secret,
                    idempotency_key=f"ride-confirm-{ride_id}-{amount_cents}",
                )
            )
        else:
            intent = await asyncio.to_thread(
                lambda: stripe.PaymentIntent.create(
                    **params,
                    api_key=stripe_secret,
                    idempotency_key=idempotency_key,
                )
            )
    except _StripeCardError as e:
        # Card explicitly declined (insufficient_funds, card_declined, etc.).
        # This is the "surface retry UX to the rider" case.
        err = getattr(e, "error", None)
        decline_code = getattr(err, "decline_code", None) or getattr(err, "code", None)
        logger.info(
            "Card declined for ride=%s rider=%s code=%s: %s",
            ride_id,
            rider_id,
            decline_code,
            e,
        )
        return ChargeOutcome(
            status="declined",
            decline_code=decline_code,
            error_message=str(getattr(err, "message", None) or e),
        )
    except _StripeBaseError as e:
        # Non-decline Stripe error (api_connection_error, authentication_error,
        # invalid_request_error, rate_limit_error, etc.). Not the rider's fault.
        logger.error("Stripe error charging ride=%s rider=%s: %s", ride_id, rider_id, e)
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
            charged_amount=Decimal(str(total_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
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
    logger.error(
        "Unhandled PaymentIntent status=%s for ride=%s pi=%s",
        status,
        ride_id,
        pi_id,
    )
    return ChargeOutcome(
        status="failed",
        payment_intent_id=pi_id,
        error_message=f"Unhandled PaymentIntent status: {status}",
    )


async def charge_ancillary_fee(
    *,
    ride: Dict[str, Any],
    rider_id: str,
    amount: Union[Decimal, float],
    payment_method_id: Optional[str],
    stripe_customer_id: Optional[str],
    fee_type: str,
    extra_metadata: Optional[Dict[str, str]] = None,
) -> ChargeOutcome:
    """Charge a rider's saved card for a fee outside normal trip settlement
    (e.g. a rider-initiated cancellation fee).

    Deliberately separate from ``charge_ride``: that function's metadata and
    idempotency key assume the amount is a fare+tip total (it computes
    ``tip = total_amount - ride.total_fare``), which would be nonsense for an
    unrelated ancillary charge and would pollute Stripe-side reporting. Same
    off-session card-charge mechanics, own metadata/idempotency namespace.

    ``fee_type`` (e.g. ``"cancellation_fee"``) tags the Stripe metadata and
    idempotency key so retries of two different fee types on the same ride
    never collide, and so the charge is identifiable in the Stripe dashboard.
    """
    if amount <= 0:
        return ChargeOutcome(status="succeeded", charged_amount=Decimal("0.00"))

    if stripe is None:
        logger.error("stripe package not installed; cannot charge %s", fee_type)
        return ChargeOutcome(status="unconfigured", error_message="stripe not installed")

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "") or ""
    if not stripe_secret:
        logger.error(
            "stripe_secret_key not configured; skipping %s charge for ride=%s",
            fee_type,
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
    amount_dec = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amount_cents = dollars_to_cents(amount_dec)

    # Own idempotency namespace (prefixed with fee_type) so a retry of this
    # exact fee on this ride/amount/card dedupes against itself only — never
    # against a same-ride-and-amount fare charge or a different fee type.
    idempotency_key = f"{fee_type}-{ride_id}-{amount_cents}-{payment_method_id}"

    params: Dict[str, Any] = {
        "amount": amount_cents,
        "currency": CURRENCY,
        "customer": stripe_customer_id,
        "payment_method": payment_method_id,
        "off_session": True,
        "confirm": True,
        "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
        "metadata": {
            "ride_id": ride_id,
            "rider_id": rider_id,
            "fee_amount": str(amount_dec),
            "source": fee_type,
            # extra_metadata lets a caller that charges for MORE than one ride
            # (a batched tip charge) name every ride it covers. Without it the
            # Stripe dashboard shows a single anchor ride_id for a charge
            # spanning several, and a rider asking "what is this?" cannot be
            # answered from the charge alone. Stripe caps metadata values at
            # 500 chars, so callers must truncate.
            **(extra_metadata or {}),
        },
    }

    try:
        intent = await asyncio.to_thread(
            lambda: stripe.PaymentIntent.create(
                **params,
                api_key=stripe_secret,
                idempotency_key=idempotency_key,
            )
        )
    except _StripeCardError as e:
        err = getattr(e, "error", None)
        decline_code = getattr(err, "decline_code", None) or getattr(err, "code", None)
        logger.info(
            "Card declined for %s ride=%s rider=%s code=%s: %s",
            fee_type,
            ride_id,
            rider_id,
            decline_code,
            e,
        )
        return ChargeOutcome(
            status="declined",
            decline_code=decline_code,
            error_message=str(getattr(err, "message", None) or e),
        )
    except _StripeBaseError as e:
        logger.error("Stripe error charging %s for ride=%s rider=%s: %s", fee_type, ride_id, rider_id, e)
        return ChargeOutcome(status="failed", error_message=str(e))
    except Exception as e:  # pragma: no cover — defence-in-depth
        logger.exception("Unexpected error charging %s for ride=%s: %s", fee_type, ride_id, e)
        return ChargeOutcome(status="failed", error_message=str(e))

    status = getattr(intent, "status", "") or ""
    pi_id = getattr(intent, "id", None)
    client_secret = getattr(intent, "client_secret", None)

    if status == "succeeded":
        return ChargeOutcome(status="succeeded", payment_intent_id=pi_id, charged_amount=amount_dec)

    if status in ("requires_action", "requires_source_action"):
        return ChargeOutcome(status="requires_action", payment_intent_id=pi_id, client_secret=client_secret)

    if status in ("requires_payment_method", "requires_confirmation"):
        return ChargeOutcome(
            status="declined",
            payment_intent_id=pi_id,
            error_message=f"PaymentIntent unexpectedly in state: {status}",
        )

    logger.error("Unhandled PaymentIntent status=%s for %s ride=%s pi=%s", status, fee_type, ride_id, pi_id)
    return ChargeOutcome(
        status="failed",
        payment_intent_id=pi_id,
        error_message=f"Unhandled PaymentIntent status: {status}",
    )


async def _resolve_stripe_secret(ride_id: str) -> Optional[str]:
    """Shared secret lookup for the authorize/capture helpers.

    Returns the configured ``stripe_secret_key`` or ``None`` when Stripe is not
    wired up (dev/test). Callers map ``None`` → ``unconfigured`` so the system
    doesn't wedge when Stripe isn't configured yet.
    """
    if stripe is None:
        logger.error("stripe package not installed; cannot reach Stripe")
        return None
    settings = await get_app_settings()
    secret = settings.get("stripe_secret_key", "") or ""
    if not secret:
        logger.error("stripe_secret_key not configured; ride=%s", ride_id)
        return None
    return secret


def _reads_incremental_support(intent: Any) -> bool:
    """Whether Stripe granted incremental-authorization support on this hold.

    Support is per-card, not per-account: Visa/Mastercard grant it broadly, some
    Amex issuers refuse, and Discover restricts by merchant category. So the
    answer is read off the charge Stripe actually created rather than inferred
    from our account settings — a wrong guess here would either strand a tip or
    fire a doomed increment call.

    Returns False on any missing/odd shape. False is the safe direction: it only
    costs one extra Stripe fixed fee (the tip becomes its own charge), whereas a
    false True would fail the increment at settlement.
    """
    try:
        charge = getattr(intent, "latest_charge", None)
        if charge is None or isinstance(charge, str):
            # Not expanded (or expand silently dropped) — cannot tell, assume no.
            return False
        details = getattr(charge, "payment_method_details", None) or {}
        card = (details.get("card") if isinstance(details, dict) else getattr(details, "card", None)) or {}
        value = (
            card.get("incremental_authorization_supported")
            if isinstance(card, dict)
            else getattr(card, "incremental_authorization_supported", None)
        )
        return bool(value)
    except Exception:  # pragma: no cover — never let a probe break authorization
        logger.debug("could not read incremental_authorization_supported", exc_info=True)
        return False


async def authorize_ride(
    *,
    ride: Dict[str, Any],
    rider_id: str,
    amount: Union[Decimal, int],
    payment_method_id: Optional[str] = None,
    stripe_customer_id: Optional[str] = None,
    off_session: bool = False,
) -> ChargeOutcome:
    """Place a manual-capture authorization HOLD on the rider's card at booking.

    This is the front-of-ride counterpart to ``charge_ride``: instead of
    capturing immediately, it creates a PaymentIntent with
    ``capture_method="manual"`` so the funds are *held* (estimated fare +
    buffer) without moving money. The hold is captured later, once at
    settlement, by ``capture_ride`` — letting a post-trip tip ride on the SAME
    PaymentIntent (one Stripe fee) and surfacing a dead card BEFORE dispatch.

    ``off_session`` defaults to ``False`` because authorization happens while
    the rider is present at booking (Apple Pay / 3DS can prompt live). A
    ``requires_action`` outcome hands the client_secret back so the rider-app
    runs the SCA / biometric sheet and re-confirms.

    ``ChargeOutcome.status`` for this path:
        "authorized"       hold placed (Stripe PI status ``requires_capture``);
                           ``charged_amount`` carries the held amount
        "requires_action"  SCA / 3DS challenge; return client_secret to app
        "declined"         card declined (insufficient_funds, card_declined, …);
                           caller may retry at a smaller amount (fare-only)
        "failed"           non-decline Stripe/ops error
        "unconfigured"     Stripe not installed/configured (dev/test)

    Never raises — callers switch on ``outcome.status``.
    """
    if amount <= 0:
        # Nothing to hold. Treat as a no-op success so booking proceeds; the
        # settlement path handles a genuinely $0 ride without a Stripe round-trip.
        return ChargeOutcome(status="authorized", charged_amount=Decimal("0.00"))

    secret = await _resolve_stripe_secret(ride.get("id") or "")
    if secret is None:
        return ChargeOutcome(status="unconfigured", error_message="Payment processing is not configured")
    if not stripe_customer_id:
        return ChargeOutcome(status="failed", error_message="No Stripe customer on file for rider")
    if not payment_method_id:
        return ChargeOutcome(status="failed", error_message="No default payment method on file")

    ride_id = ride.get("id") or ""
    amount_cents = dollars_to_cents(amount)
    # Amount is part of the key so a re-auth at a different hold (e.g. a revised
    # fare estimate) gets a fresh key instead of an IdempotencyError; identical
    # retries (double-tap / dropped response) dedupe to the original hold.
    idempotency_key = f"ride-auth-{ride_id}-{amount_cents}"

    params: Dict[str, Any] = {
        "amount": amount_cents,
        "currency": CURRENCY,
        "customer": stripe_customer_id,
        "payment_method": payment_method_id,
        "capture_method": "manual",
        "confirm": True,
        "off_session": off_session,
        # Ask Stripe to keep this authorization incrementable so a post-trip tip
        # can be added to THIS hold rather than charged separately (one Stripe
        # fixed fee instead of two). Requesting it is free and never fails the
        # authorization — Stripe reports back, per card, whether it was actually
        # granted, which we read below. Requires capture_method="manual", which
        # this path already uses.
        "request_incremental_authorization_support": True,
        # Needed to read the granted capability off the charge below.
        "expand": ["latest_charge"],
        # Disable redirect-based payment methods (see charge_ride above):
        # a confirmed PaymentIntent with redirect methods enabled requires a
        # `return_url`, which a server-side hold can't supply — Stripe was
        # rejecting every booking pre-auth with invalid_request_error
        # ("[preauth] authorization ops error"). Card-only avoids it.
        "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
        "metadata": {
            "ride_id": ride_id,
            "rider_id": rider_id,
            "authorized_amount": str(to_decimal(amount)),
            "payment_method_type": ride.get("payment_method") or "card",
            "source": "ride_booking_authorization",
        },
    }

    try:
        intent = await asyncio.to_thread(
            lambda: stripe.PaymentIntent.create(
                **params,
                api_key=secret,
                idempotency_key=idempotency_key,
            )
        )
    except _StripeCardError as e:
        err = getattr(e, "error", None)
        decline_code = getattr(err, "decline_code", None) or getattr(err, "code", None)
        logger.info("Auth declined for ride=%s rider=%s code=%s", ride_id, rider_id, decline_code)
        return ChargeOutcome(
            status="declined",
            decline_code=decline_code,
            error_message=str(getattr(err, "message", None) or e),
        )
    except _StripeBaseError as e:
        logger.error("Stripe error authorizing ride=%s rider=%s: %s", ride_id, rider_id, e)
        return ChargeOutcome(status="failed", error_message=str(e))
    except Exception as e:  # pragma: no cover — defence-in-depth
        logger.exception("Unexpected error authorizing ride=%s: %s", ride_id, e)
        return ChargeOutcome(status="failed", error_message=str(e))

    status = getattr(intent, "status", "") or ""
    pi_id = getattr(intent, "id", None)
    client_secret = getattr(intent, "client_secret", None)

    if status == "requires_capture":
        # Hold placed successfully — funds reserved, nothing captured yet.
        return ChargeOutcome(
            status="authorized",
            payment_intent_id=pi_id,
            charged_amount=to_decimal(amount),
            incremental_authorization_supported=_reads_incremental_support(intent),
        )
    if status in ("requires_action", "requires_source_action"):
        return ChargeOutcome(status="requires_action", payment_intent_id=pi_id, client_secret=client_secret)
    if status in ("requires_payment_method", "requires_confirmation"):
        return ChargeOutcome(
            status="declined",
            payment_intent_id=pi_id,
            error_message=f"PaymentIntent unexpectedly in state: {status}",
        )

    logger.error("Unhandled auth PaymentIntent status=%s for ride=%s pi=%s", status, ride_id, pi_id)
    return ChargeOutcome(
        status="failed",
        payment_intent_id=pi_id,
        error_message=f"Unhandled auth PaymentIntent status: {status}",
    )


async def verify_authorization(
    *,
    ride_id: str,
    payment_intent_id: str,
    expected_customer_id: Optional[str] = None,
    min_amount_cents: Optional[int] = None,
) -> ChargeOutcome:
    """Verify an on-device-confirmed authorization hold (SCA two-step at booking).

    After ``authorize_ride`` returns ``requires_action`` and the rider-app runs
    the Stripe confirm sheet (3DS / Apple Pay biometric), the PaymentIntent
    should land in ``requires_capture``. This re-reads it from Stripe to confirm
    the hold is real before create_ride attaches it — we never trust the client's
    word that authentication succeeded.

    SECURITY — the client supplies the PI id, so we never attach it blindly:
      - ``expected_customer_id``: the PI's ``customer`` MUST match the requesting
        rider's Stripe customer, else a rider could attach someone else's hold.
      - ``min_amount_cents``: the held amount MUST cover this ride's fare, else a
        rider could replay a smaller hold from a cancelled/cheaper booking.
    A mismatch on either is treated as ``declined`` (logged as a security event).

    ``ChargeOutcome.status``:
        "authorized"    PI is requires_capture, owned by this rider, large enough
        "declined"      auth not completed / wrong owner / too small / wrong PM
        "failed"        Stripe ops error / unexpected PI status
        "unconfigured"  Stripe not installed/configured (dev/test)

    Never raises — callers switch on ``outcome.status``.
    """
    if not payment_intent_id:
        return ChargeOutcome(status="failed", error_message="No PaymentIntent to verify")

    secret = await _resolve_stripe_secret(ride_id)
    if secret is None:
        return ChargeOutcome(status="unconfigured", error_message="Payment processing is not configured")

    try:
        intent = await asyncio.to_thread(lambda: stripe.PaymentIntent.retrieve(payment_intent_id, api_key=secret))
    except _StripeBaseError as e:
        logger.error("Stripe error verifying auth ride=%s pi=%s: %s", ride_id, payment_intent_id, e)
        return ChargeOutcome(status="failed", payment_intent_id=payment_intent_id, error_message=str(e))
    except Exception as e:  # pragma: no cover — defence-in-depth
        logger.exception("Unexpected error verifying auth ride=%s: %s", ride_id, e)
        return ChargeOutcome(status="failed", payment_intent_id=payment_intent_id, error_message=str(e))

    status = getattr(intent, "status", "") or ""
    pi_id = getattr(intent, "id", None) or payment_intent_id

    # Ownership: the PI must belong to THIS rider's Stripe customer.
    if expected_customer_id is not None:
        pi_customer = getattr(intent, "customer", None)
        if pi_customer != expected_customer_id:
            logger.error(
                "[preauth][security] PI customer mismatch ride=%s pi=%s (declining attach)",
                ride_id,
                pi_id,
            )
            return ChargeOutcome(
                status="declined",
                payment_intent_id=pi_id,
                error_message="Authorization does not belong to this account",
            )

    # Amount: the hold must be at least the ride's fare (no replay of a smaller
    # hold from a cancelled/cheaper booking).
    if min_amount_cents is not None:
        pi_amount = int(getattr(intent, "amount", 0) or 0)
        if pi_amount < int(min_amount_cents):
            logger.error(
                "[preauth][security] PI amount too small ride=%s pi=%s held=%s need=%s (declining attach)",
                ride_id,
                pi_id,
                pi_amount,
                min_amount_cents,
            )
            return ChargeOutcome(
                status="declined",
                payment_intent_id=pi_id,
                error_message="Authorization amount is insufficient for this ride",
            )

    if status == "requires_capture":
        amount_cents = getattr(intent, "amount", 0) or 0
        return ChargeOutcome(
            status="authorized",
            payment_intent_id=pi_id,
            charged_amount=to_decimal(Decimal(int(amount_cents)) / Decimal("100")),
        )
    if status in ("requires_action", "requires_source_action", "requires_payment_method", "requires_confirmation"):
        # Authentication not completed (rider abandoned the sheet, or it failed).
        return ChargeOutcome(
            status="declined",
            payment_intent_id=pi_id,
            error_message=f"Authorization not completed (PI status: {status})",
        )
    if status == "succeeded":
        # Already captured — treat as authorized so the caller attaches it and
        # settlement's idempotent capture is a no-op rather than a double charge.
        return ChargeOutcome(
            status="authorized",
            payment_intent_id=pi_id,
            charged_amount=to_decimal(Decimal(int(getattr(intent, "amount", 0) or 0)) / Decimal("100")),
        )

    logger.error("Unexpected verify PI status=%s for ride=%s pi=%s", status, ride_id, pi_id)
    return ChargeOutcome(
        status="failed",
        payment_intent_id=pi_id,
        error_message=f"Unexpected PaymentIntent status: {status}",
    )


async def increment_authorization(
    *,
    ride_id: str,
    payment_intent_id: str,
    new_total: Union[Decimal, int],
) -> ChargeOutcome:
    """Raise an existing hold to ``new_total`` so a tip rides on the same charge.

    This is the whole reason the booking hold no longer carries a tip buffer. We
    hold exactly the quoted fare, and if the rider tips we ask the issuer for the
    difference on the SAME PaymentIntent, then capture once — one Stripe fixed
    fee, one line on the rider's statement, and no money reserved "just in case".

    Only works BEFORE capture. Stripe: "After it's captured, a PaymentIntent can
    no longer be incremented." A tip that arrives after settlement must be a
    separate charge — callers must not reach for this to fix that case.

    Eligibility was recorded at authorization time (``rides.auth_incrementable``)
    from the card's own capability flag, so callers should gate on that rather
    than calling this speculatively. It is still safe if they don't: an ineligible
    PI comes back "failed", not an exception.

    ``ChargeOutcome.status``:
        "authorized"    hold raised; ``charged_amount`` is the new total
        "declined"      issuer refused the extra amount (e.g. the rider's
                        available balance moved since booking). The original
                        hold survives untouched — capture it and charge the tip
                        separately.
        "failed"        not incrementable / already captured / Stripe-ops error
        "unconfigured"  Stripe not installed/configured (dev/test)

    Never raises — callers switch on ``outcome.status``.
    """
    if not payment_intent_id:
        return ChargeOutcome(status="failed", error_message="No authorization PaymentIntent to increment")

    secret = await _resolve_stripe_secret(ride_id)
    if secret is None:
        return ChargeOutcome(status="unconfigured", error_message="Payment processing is not configured")

    amount_cents = dollars_to_cents(new_total)
    try:
        intent = await asyncio.to_thread(
            lambda: stripe.PaymentIntent.increment_authorization(
                payment_intent_id,
                amount=amount_cents,
                api_key=secret,
                # Keyed on the target total: two replicas racing to add the same
                # tip dedupe, but a genuinely different total (a corrected tip)
                # gets its own key rather than silently returning the old result.
                idempotency_key=f"ride-increment-{ride_id}-{amount_cents}",
            )
        )
    except _StripeCardError as e:
        err = getattr(e, "error", None)
        decline_code = getattr(err, "decline_code", None) or getattr(err, "code", None)
        logger.info("Increment declined for ride=%s pi=%s code=%s", ride_id, payment_intent_id, decline_code)
        return ChargeOutcome(
            status="declined",
            payment_intent_id=payment_intent_id,
            decline_code=decline_code,
            error_message=str(getattr(err, "message", None) or e),
        )
    except _StripeBaseError as e:
        # Not incrementable, already captured, or a Stripe-ops problem. Info, not
        # error: the caller's fallback (capture + separate tip charge) is a
        # designed path, not a failure — it just costs one extra fixed fee.
        logger.info("Increment unavailable for ride=%s pi=%s: %s", ride_id, payment_intent_id, e)
        return ChargeOutcome(status="failed", payment_intent_id=payment_intent_id, error_message=str(e))
    except Exception as e:  # pragma: no cover — defence-in-depth
        logger.exception("Unexpected error incrementing ride=%s: %s", ride_id, e)
        return ChargeOutcome(status="failed", payment_intent_id=payment_intent_id, error_message=str(e))

    status = getattr(intent, "status", "") or ""
    pi_id = getattr(intent, "id", None) or payment_intent_id
    if status == "requires_capture":
        return ChargeOutcome(
            status="authorized",
            payment_intent_id=pi_id,
            charged_amount=to_decimal(new_total),
        )

    logger.error("Unhandled increment PI status=%s for ride=%s pi=%s", status, ride_id, pi_id)
    return ChargeOutcome(
        status="failed",
        payment_intent_id=pi_id,
        error_message=f"Unhandled increment PaymentIntent status: {status}",
    )


async def capture_ride(
    *,
    ride_id: str,
    payment_intent_id: str,
    amount: Union[Decimal, int],
) -> ChargeOutcome:
    """Capture a previously-authorized hold for ``amount`` (fare + tip).

    Captures against the manual-capture PaymentIntent created by
    ``authorize_ride``. ``amount`` MUST be ≤ the currently authorized hold —
    Stripe rejects a capture larger than the authorization. To fold a tip in,
    raise the hold first with ``increment_authorization``; a tip that cannot be
    covered that way is charged separately rather than folded into this call.

    ``ChargeOutcome.status``:
        "captured"      funds captured (terminal success)
        "declined"      card declined at capture (rare — e.g. issuer reversal)
        "failed"        hold expired / amount_too_large / Stripe-ops error;
                        caller falls back to a fresh ``charge_ride``
        "unconfigured"  Stripe not installed/configured (dev/test)

    Never raises — callers switch on ``outcome.status``.
    """
    if amount <= 0:
        return ChargeOutcome(status="captured", charged_amount=Decimal("0.00"))
    if not payment_intent_id:
        return ChargeOutcome(status="failed", error_message="No authorization PaymentIntent to capture")

    secret = await _resolve_stripe_secret(ride_id)
    if secret is None:
        return ChargeOutcome(status="unconfigured", error_message="Payment processing is not configured")

    amount_cents = dollars_to_cents(amount)
    try:
        intent = await asyncio.to_thread(
            lambda: stripe.PaymentIntent.capture(
                payment_intent_id,
                amount_to_capture=amount_cents,
                api_key=secret,
                # Same PI + same captured amount must not double-capture if two
                # replicas race past the DB claim; Stripe dedupes the identical key.
                idempotency_key=f"ride-capture-{ride_id}-{amount_cents}",
            )
        )
    except _StripeCardError as e:
        err = getattr(e, "error", None)
        decline_code = getattr(err, "decline_code", None) or getattr(err, "code", None)
        logger.info("Capture declined for ride=%s pi=%s code=%s", ride_id, payment_intent_id, decline_code)
        return ChargeOutcome(
            status="declined",
            payment_intent_id=payment_intent_id,
            decline_code=decline_code,
            error_message=str(getattr(err, "message", None) or e),
        )
    except _StripeBaseError as e:
        # Hold expired, amount_too_large, already-captured, etc. Not a card
        # decline — caller re-drives via a fresh charge_ride.
        logger.error("Stripe error capturing ride=%s pi=%s: %s", ride_id, payment_intent_id, e)
        return ChargeOutcome(status="failed", payment_intent_id=payment_intent_id, error_message=str(e))
    except Exception as e:  # pragma: no cover — defence-in-depth
        logger.exception("Unexpected error capturing ride=%s: %s", ride_id, e)
        return ChargeOutcome(status="failed", payment_intent_id=payment_intent_id, error_message=str(e))

    status = getattr(intent, "status", "") or ""
    pi_id = getattr(intent, "id", None) or payment_intent_id
    if status == "succeeded":
        return ChargeOutcome(
            status="captured",
            payment_intent_id=pi_id,
            charged_amount=to_decimal(amount),
        )

    logger.error("Unhandled capture PaymentIntent status=%s for ride=%s pi=%s", status, ride_id, pi_id)
    return ChargeOutcome(
        status="failed",
        payment_intent_id=pi_id,
        error_message=f"Unhandled capture PaymentIntent status: {status}",
    )


async def capture_cancellation_fee(
    *,
    ride_id: str,
    payment_intent_id: str,
    fee: Union[Decimal, int],
    authorized_amount: Union[Decimal, int],
) -> ChargeOutcome:
    """Take a cancellation fee out of the booking hold, releasing the rest.

    A partial capture: Stripe captures ``fee`` and automatically releases the
    remainder of the authorization. Preferred over cancelling the hold and then
    charging a fresh PaymentIntent, because the funds are *already reserved* —
    this fee cannot be declined for insufficient funds, whereas a new charge
    against the same card can (and did, whenever a rider's balance moved between
    booking and cancelling).

    ``fee`` is capped at ``authorized_amount``. Stripe rejects a capture larger
    than the authorization, but the cap is also a policy decision: on a $4 fare
    with a $5 cancellation fee we take the $4 and let the rest go rather than
    chase the shortfall with a second charge. A cancellation fee larger than the
    ride itself is not defensible to a rider or a regulator, and the extra dollar
    is not worth the support ticket.

    ``ChargeOutcome.status``:
        "captured"      fee taken, remainder released. ``charged_amount`` is what
                        was ACTUALLY captured, which may be less than ``fee``.
        "declined"      card declined at capture (rare — issuer reversal)
        "failed"        hold expired / already captured / Stripe-ops error;
                        caller falls back to charging a fresh PaymentIntent
        "unconfigured"  Stripe not installed/configured (dev/test)

    Never raises — callers switch on ``outcome.status``.
    """
    capped = min(to_decimal(fee), to_decimal(authorized_amount))
    if capped < to_decimal(fee):
        logger.info(
            "[CANCEL] fee capped to held amount ride=%s fee=%s held=%s uncollected=%s",
            ride_id,
            to_decimal(fee),
            to_decimal(authorized_amount),
            to_decimal(fee) - capped,
        )
    if capped <= 0:
        return ChargeOutcome(status="captured", charged_amount=Decimal("0.00"))
    if not payment_intent_id:
        return ChargeOutcome(status="failed", error_message="No authorization PaymentIntent to capture")

    secret = await _resolve_stripe_secret(ride_id)
    if secret is None:
        return ChargeOutcome(status="unconfigured", error_message="Payment processing is not configured")

    amount_cents = dollars_to_cents(capped)
    try:
        intent = await asyncio.to_thread(
            lambda: stripe.PaymentIntent.capture(
                payment_intent_id,
                amount_to_capture=amount_cents,
                api_key=secret,
                # Distinct namespace from ride-capture: a cancelled ride and a
                # completed one are different events on the same PI, and reusing
                # the settlement key would let one dedupe against the other.
                idempotency_key=f"ride-cancelfee-{ride_id}-{amount_cents}",
            )
        )
    except _StripeCardError as e:
        err = getattr(e, "error", None)
        decline_code = getattr(err, "decline_code", None) or getattr(err, "code", None)
        logger.info("[CANCEL] fee capture declined ride=%s pi=%s code=%s", ride_id, payment_intent_id, decline_code)
        return ChargeOutcome(
            status="declined",
            payment_intent_id=payment_intent_id,
            decline_code=decline_code,
            error_message=str(getattr(err, "message", None) or e),
        )
    except _StripeBaseError as e:
        logger.error("[CANCEL] Stripe error capturing fee ride=%s pi=%s: %s", ride_id, payment_intent_id, e)
        return ChargeOutcome(status="failed", payment_intent_id=payment_intent_id, error_message=str(e))
    except Exception as e:  # pragma: no cover — defence-in-depth
        logger.exception("[CANCEL] unexpected error capturing fee ride=%s: %s", ride_id, e)
        return ChargeOutcome(status="failed", payment_intent_id=payment_intent_id, error_message=str(e))

    status = getattr(intent, "status", "") or ""
    pi_id = getattr(intent, "id", None) or payment_intent_id
    if status == "succeeded":
        return ChargeOutcome(status="captured", payment_intent_id=pi_id, charged_amount=capped)

    logger.error("[CANCEL] unhandled fee-capture PI status=%s ride=%s pi=%s", status, ride_id, pi_id)
    return ChargeOutcome(
        status="failed",
        payment_intent_id=pi_id,
        error_message=f"Unhandled cancellation-fee capture status: {status}",
    )


async def cancel_authorization(*, ride_id: str, payment_intent_id: str) -> bool:
    """Cancel an uncaptured pre-authorization hold so the funds are released.

    Used when a rider abandons the booking-time card (the "Change Card" escape):
    the new card is charged on a fresh PaymentIntent, so the old hold must be
    released or the rider's funds stay reserved until Stripe's ~7-day auth
    expiry. Returns True if the hold is cancelled (or already in a terminal
    state that cannot/need not be cancelled). Best-effort — never raises; a
    failure is logged (a stuck hold is a payment-path anomaly) and the caller
    proceeds, since the fresh charge is the real settlement.
    """
    if not payment_intent_id:
        return False
    secret = await _resolve_stripe_secret(ride_id)
    if stripe is None or secret is None:
        return False

    # Call Stripe directly (same pattern as capture_ride / authorize_ride in this
    # module) — there is no run_sync helper here.
    try:
        await asyncio.to_thread(
            lambda: stripe.PaymentIntent.cancel(
                payment_intent_id,
                api_key=secret,
                idempotency_key=f"ride-cancelauth-{ride_id}-{payment_intent_id}",
            )
        )
        return True
    except _StripeBaseError as e:
        # Already captured/canceled, or a transient ops error. Surface it
        # (a lingering hold ties up the rider's funds) but don't block the
        # fresh charge that actually settles the ride.
        logger.error(
            "Could not cancel pre-auth hold pi=%s for ride=%s: %s",
            payment_intent_id,
            ride_id,
            e,
        )
        return False
    except Exception as e:  # pragma: no cover — defence-in-depth
        logger.exception("Unexpected error cancelling pre-auth hold ride=%s: %s", ride_id, e)
        return False
