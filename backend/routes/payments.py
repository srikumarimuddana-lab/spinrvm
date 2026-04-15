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
import uuid

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


@api_router.post("/cards")
async def add_card(request: Request, current_user: dict = Depends(get_current_user)):
    """Add card via Stripe. Creates PaymentMethod + SetupIntent, saves ack."""
    data = await request.json()
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    if not stripe_secret:
        # Demo — fake card
        num = data.get("card_number", "")
        last4 = num[-4:] if len(num) >= 4 else "0000"
        brand = "Visa" if num.startswith("4") else "Mastercard" if num[:2] in ("51", "52", "53", "54", "55") else "Card"
        logger.info(f"[DEMO] Card added: {brand} ****{last4}")
        return {
            "id": str(uuid.uuid4()),
            "brand": brand,
            "last4": last4,
            "exp_month": data.get("exp_month", 1),
            "exp_year": data.get("exp_year", 2030),
            "is_default": True,
        }

    try:
        customer_id = await get_or_create_stripe_customer(current_user["id"], stripe_secret)

        # Create PaymentMethod (in prod use Stripe.js tokenization on frontend)
        pm = stripe.PaymentMethod.create(
            type="card",
            card={
                "number": data.get("card_number"),
                "exp_month": int(data.get("exp_month")),
                "exp_year": int(data.get("exp_year")),
                "cvc": data.get("cvc"),
            },
            api_key=stripe_secret,
        )

        # Attach to customer
        stripe.PaymentMethod.attach(pm.id, customer=customer_id, api_key=stripe_secret)

        # Confirm with SetupIntent — saves card for future use
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
