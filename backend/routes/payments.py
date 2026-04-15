from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from ..db import db
    from ..dependencies import get_current_user
    from ..settings_loader import get_app_settings
except ImportError:
    from db import db
    from dependencies import get_current_user
    from settings_loader import get_app_settings
import logging

import stripe

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/payments", tags=["Payments"])


class PaymentIntentRequest(BaseModel):
    """Create-intent request body.

    Validates that `amount` is a positive number within a sane upper bound
    so an attacker can't submit a negative (refund-to-self) or absurdly
    large charge. The previous endpoint accepted `Dict[str, Any]` and
    trusted whatever the client sent — this schema closes that gap.
    """

    amount: float = Field(..., gt=0, le=100000, description="Amount in CAD dollars")
    ride_id: Optional[str] = Field(None, max_length=64)
    payment_method_id: Optional[str] = Field(None, max_length=128)


async def get_or_create_stripe_customer(user_id: str, stripe_secret: str):
    """Get or create a Stripe customer for the given user.

    Accepts stripe_secret explicitly to avoid setting the global stripe.api_key
    (which is not thread-safe under async FastAPI).
    """
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    stripe_customer_id = user.get("stripe_customer_id")

    if not stripe_customer_id:
        # Create a new Stripe customer
        customer = stripe.Customer.create(
            email=user.get("email"),
            name=f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
            metadata={"user_id": user_id},
            api_key=stripe_secret,
        )
        stripe_customer_id = customer.id
        await db.users.update_one({"id": user_id}, {"$set": {"stripe_customer_id": stripe_customer_id}})

    return stripe_customer_id


@api_router.post("/create-intent")
async def create_payment_intent(body: PaymentIntentRequest, current_user: dict = Depends(get_current_user)):
    """Create a Stripe payment intent.

    `amount` is validated by Pydantic (positive, ≤ 100000 CAD) before we
    reach Stripe. Rejecting at the boundary gives a 422 response on bad
    input instead of a 500 after Stripe refuses it.
    """
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    if not stripe_secret:
        raise HTTPException(
            status_code=503,
            detail="Payment processing is not configured. Please contact support.",
        )

    try:
        amount = int(body.amount * 100)  # Convert dollars → cents

        # Get or create customer for saved payments
        stripe_customer_id = await get_or_create_stripe_customer(current_user["id"], stripe_secret)

        intent_params = {
            "amount": amount,
            "currency": "cad",
            "automatic_payment_methods": {"enabled": True},
            "metadata": {"user_id": current_user["id"], "ride_id": body.ride_id or ""},
        }

        if stripe_customer_id:
            intent_params["customer"] = stripe_customer_id

        if body.payment_method_id:
            intent_params["payment_method"] = body.payment_method_id

        intent = stripe.PaymentIntent.create(**intent_params, api_key=stripe_secret)

        return {"client_secret": intent.client_secret, "payment_intent_id": intent.id, "mock": False}
    except Exception as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@api_router.post("/confirm")
async def confirm_payment(request: Dict[str, Any], current_user: dict = Depends(get_current_user)):
    """Confirm payment was successful"""
    payment_intent_id = request.get("payment_intent_id")
    ride_id = request.get("ride_id")

    if payment_intent_id and payment_intent_id.startswith("pi_mock_"):
        # Mock payment
        if ride_id:
            await db.rides.update_one(
                {"id": ride_id}, {"$set": {"payment_status": "paid", "payment_intent_id": payment_intent_id}}
            )
        return {"status": "succeeded", "mock": True}

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    if stripe_secret:
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id, api_key=stripe_secret)

            if ride_id:
                await db.rides.update_one(
                    {"id": ride_id}, {"$set": {"payment_status": intent.status, "payment_intent_id": payment_intent_id}}
                )

            return {"status": intent.status, "mock": False}
        except Exception as e:
            logger.error(f"Stripe error: {e}")
            raise HTTPException(status_code=500, detail=str(e)) from e

    return {"status": "unknown", "mock": True}


@api_router.post("/setup-intent")
async def create_setup_intent(current_user: dict = Depends(get_current_user)):
    """Create a SetupIntent to save a new payment method"""
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    if not stripe_secret:
        return {"client_secret": "mock_setup_secret", "mock": True}

    try:
        customer_id = await get_or_create_stripe_customer(current_user["id"], stripe_secret)

        if not customer_id:
            raise HTTPException(status_code=400, detail="Could not create Stripe customer")

        setup_intent = stripe.SetupIntent.create(
            customer=customer_id,
            payment_method_types=["card"],
            api_key=stripe_secret,
        )

        return {
            "client_secret": setup_intent.client_secret,
            "setup_intent_id": setup_intent.id,
            "customer_id": customer_id,
            "mock": False,
        }
    except Exception as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@api_router.get("/methods")
async def get_payment_methods(current_user: dict = Depends(get_current_user)):
    """Get saved payment methods for the user"""
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    if not stripe_secret:
        return {"methods": [], "mock": True}

    try:
        user = await db.users.find_one({"id": current_user["id"]})
        stripe_customer_id = user.get("stripe_customer_id") if user else None

        if not stripe_customer_id:
            return {"methods": [], "mock": False}

        methods = stripe.PaymentMethod.list(
            customer=stripe_customer_id,
            type="card",
            api_key=stripe_secret,
        )

        return {
            "methods": [
                {
                    "id": m.id,
                    "brand": m.card.brand,
                    "last4": m.card.last4,
                    "exp_month": m.card.exp_month,
                    "exp_year": m.card.exp_year,
                }
                for m in methods.data
            ],
            "mock": False,
        }
    except Exception as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ─── Cards CRUD via Stripe ───


@api_router.get("/cards")
async def get_cards(current_user: dict = Depends(get_current_user)):
    """Get user's saved cards from Stripe. Last4, brand, expiry all from Stripe."""
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    if not stripe_secret:
        # Demo mode — return empty
        return []

    try:
        customer_id = await get_or_create_stripe_customer(current_user["id"], stripe_secret)
        methods = stripe.PaymentMethod.list(customer=customer_id, type="card", api_key=stripe_secret)
        user = await db.users.find_one({"id": current_user["id"]})
        default_pm = user.get("default_payment_method") if user else None
        return [
            {
                "id": m.id,
                "brand": m.card.brand.capitalize(),
                "last4": m.card.last4,
                "exp_month": m.card.exp_month,
                "exp_year": m.card.exp_year,
                "is_default": m.id == default_pm,
            }
            for m in methods.data
        ]
    except Exception as e:
        logger.error(f"Get cards error: {e}")
        return []


class AddCardRequest(BaseModel):
    """Add-card request body.

    Accepts only a Stripe `payment_method_id` created client-side via
    Stripe.js or @stripe/stripe-react-native. Raw card fields (PAN, CVC,
    expiry) must NEVER flow through the backend — accepting them puts
    the server in PCI-DSS SAQ-D scope. See AUDIT C-PAY-01.
    """

    payment_method_id: str = Field(..., min_length=1, max_length=128)


# Field names that indicate the caller sent raw card data (PAN/CVV/expiry).
# If ANY of these are present in the request body, we refuse to process
# the request at all — before any logging, before JSON parsing by pydantic,
# before touching Stripe. This is the PCI-DSS perimeter.
_RAW_CARD_FIELDS = frozenset(
    {
        "card_number",
        "number",
        "cvc",
        "cvv",
        "cvv2",
        "exp_month",
        "exp_year",
        "expiry",
        "expiration",
    }
)


@api_router.post("/cards")
async def add_card(request: Request, current_user: dict = Depends(get_current_user)):
    """Add a saved card. Requires client-side tokenization.

    Contract:
      - Body must be ``{"payment_method_id": "pm_..."}``.
      - Body must NOT contain any raw-card fields (PAN, CVC, expiry).
      - If stripe_secret_key is unset (demo mode), a fake record is
        returned with a random last4 — the payment_method_id is still
        required for contract parity so the mobile app integrates with
        a single API shape across environments.

    PCI-DSS note: we deliberately raise a plain 400 BEFORE reading the
    full JSON for logging/metrics so an attacker probing the endpoint
    with a PAN does not get it persisted in access logs. The error
    response intentionally does NOT echo the offending keys back.
    """
    # Parse once; reject immediately if the body shape hints at raw card data.
    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    forbidden = _RAW_CARD_FIELDS.intersection(data.keys())
    if forbidden:
        # Log that a raw-card POST was attempted but DO NOT log the keys'
        # values. The client needs tokenization; tell them where to look.
        logger.warning(
            f"Rejected raw-card POST to /payments/cards from user={current_user.get('id')}: {sorted(forbidden)} present"
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "Raw card data is not accepted. Tokenize card details "
                "client-side using Stripe.js / @stripe/stripe-react-native "
                "and submit only {'payment_method_id': 'pm_...'}."
            ),
        )

    # Validate the allowed shape. Pydantic rejects missing / wrong types with 422.
    try:
        body = AddCardRequest(**data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid request: {exc}") from exc

    payment_method_id = body.payment_method_id

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    if not stripe_secret:
        # Demo mode — fabricate a response. Requires payment_method_id for
        # shape parity with production so mobile has one integration path.
        logger.info(f"[DEMO] Card added via {payment_method_id[:8]}...")
        return {
            "id": payment_method_id,
            "brand": "Visa",
            "last4": "4242",
            "exp_month": 12,
            "exp_year": 2030,
            "is_default": True,
        }

    try:
        customer_id = await get_or_create_stripe_customer(current_user["id"], stripe_secret)

        # Attach the client-tokenized PaymentMethod to the customer.
        # The PAN never touched our server — the token came from Stripe.js.
        pm = stripe.PaymentMethod.attach(payment_method_id, customer=customer_id, api_key=stripe_secret)

        # Confirm with SetupIntent — saves card for future off-session use.
        si = stripe.SetupIntent.create(
            customer=customer_id,
            payment_method=pm.id,
            confirm=True,
            automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            api_key=stripe_secret,
        )

        # Set as default if first card
        user = await db.users.find_one({"id": current_user["id"]})
        if not user.get("default_payment_method"):
            await db.users.update_one({"id": current_user["id"]}, {"$set": {"default_payment_method": pm.id}})
            stripe.Customer.modify(
                customer_id, invoice_settings={"default_payment_method": pm.id}, api_key=stripe_secret
            )

        logger.info(f"Card added: {pm.card.brand} ****{pm.card.last4} | SetupIntent: {si.id} ({si.status})")

        return {
            "id": pm.id,
            "brand": pm.card.brand.capitalize(),
            "last4": pm.card.last4,
            "exp_month": pm.card.exp_month,
            "exp_year": pm.card.exp_year,
            "is_default": not bool(user.get("default_payment_method")),
            "setup_intent_id": si.id,
            "setup_intent_status": si.status,
        }
    except stripe.error.CardError as e:
        raise HTTPException(status_code=400, detail=e.user_message or "Card declined") from e
    except Exception as e:
        logger.error(f"Add card error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@api_router.post("/cards/{card_id}/default")
async def set_default_card(card_id: str, current_user: dict = Depends(get_current_user)):
    """Set card as default. Updates both our DB and Stripe customer."""
    await db.users.update_one({"id": current_user["id"]}, {"$set": {"default_payment_method": card_id}})

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if stripe_secret:
        try:
            user = await db.users.find_one({"id": current_user["id"]})
            cid = user.get("stripe_customer_id")
            if cid:
                stripe.Customer.modify(cid, invoice_settings={"default_payment_method": card_id}, api_key=stripe_secret)
        except Exception as e:
            logger.warning(f"Stripe set default: {e}")

    return {"success": True}


@api_router.delete("/cards/{card_id}")
async def delete_card(card_id: str, current_user: dict = Depends(get_current_user)):
    """Detach card from Stripe and clear default if needed."""
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if stripe_secret:
        try:
            stripe.PaymentMethod.detach(card_id, api_key=stripe_secret)
        except Exception as e:
            logger.warning(f"Stripe detach: {e}")

    user = await db.users.find_one({"id": current_user["id"]})
    if user and user.get("default_payment_method") == card_id:
        await db.users.update_one({"id": current_user["id"]}, {"$set": {"default_payment_method": None}})

    return {"success": True}
