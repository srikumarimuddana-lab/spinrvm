"""In-app wallet — top-up, balance, transaction history, and wallet-based payments.

Riders and drivers each have a single wallet (auto-created on first access).
All balance mutations go through the ledger (wallet_transactions) so the
audit trail is immutable.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

try:
    from ..utils.audit_logger import log_user_action as _audit_log_user
except ImportError:
    from utils.audit_logger import log_user_action as _audit_log_user
from pydantic import BaseModel, Field

try:
    from ..db import db
    from ..db_supabase import wallet_pay_for_ride
    from ..dependencies import get_current_user
    from ..settings_loader import get_app_settings
    from ..utils.error_handling import ErrorCode, SpinrException
    from ..utils.error_keys import ErrorKeys
    from ..utils.idempotency import idempotent_endpoint
    from ..utils.money import dollars_to_cents
    from .payments import with_customer_repair
except ImportError:
    from db import db
    from db_supabase import wallet_pay_for_ride
    from dependencies import get_current_user
    from routes.payments import with_customer_repair
    from settings_loader import get_app_settings
    from utils.error_handling import ErrorCode, SpinrException
    from utils.error_keys import ErrorKeys
    from utils.idempotency import idempotent_endpoint
    from utils.money import dollars_to_cents

import stripe

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/wallet", tags=["Wallet"])

_TWO = Decimal("0.01")


def _d(v) -> Decimal:
    return Decimal(str(v)).quantize(_TWO, rounding=ROUND_HALF_UP)


def _money_str(v) -> str:
    return str(_d(v))


# ── Helpers ──────────────────────────────────────────────────────────


async def get_or_create_wallet(user_id: str) -> dict:
    """Return the user's wallet, creating one if it doesn't exist."""
    wallet = await db.find_one("wallets", {"user_id": user_id})
    if wallet:
        return wallet

    wallet_data = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "balance": "0.00",
        "currency": "CAD",
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.insert_one("wallets", wallet_data)
    return wallet_data


async def _record_transaction(
    wallet_id: str,
    user_id: str,
    txn_type: str,
    amount: str,
    balance_after: str,
    reference_id: str | None = None,
    description: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Insert an immutable ledger entry."""
    txn = {
        "id": str(uuid.uuid4()),
        "wallet_id": wallet_id,
        "user_id": user_id,
        "type": txn_type,
        "amount": amount,
        "balance_after": balance_after,
        "reference_id": reference_id,
        "description": description,
        "metadata": metadata or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.insert_one("wallet_transactions", txn)
    return txn


# ── Request Schemas ──────────────────────────────────────────────────


class TopUpRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, le=500, description="Amount in CAD (max $500)")


class WalletPayRequest(BaseModel):
    ride_id: str
    amount: Decimal = Field(..., gt=0, le=500, description="Amount in CAD (max $500)")


# ── Endpoints ────────────────────────────────────────────────────────


@api_router.get("")
async def get_wallet(current_user: dict = Depends(get_current_user)):
    """Get the current user's wallet balance and info."""
    wallet = await get_or_create_wallet(current_user["id"])
    return {
        "id": wallet["id"],
        "balance": _money_str(wallet.get("balance", 0)),
        "currency": wallet.get("currency", "CAD"),
        "is_active": wallet.get("is_active", True),
    }


@api_router.post("/top-up")
@idempotent_endpoint(scope="wallet_topup")
async def top_up_wallet(
    req: TopUpRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Create a Stripe PaymentIntent for wallet top-up.

    Returns the PaymentSheet secrets so the client can present the Stripe
    payment UI. The wallet balance is only credited after Stripe confirms
    the payment via webhook (payment_intent.succeeded with scope=wallet_topup).
    """
    wallet = await get_or_create_wallet(current_user["id"])

    if not wallet.get("is_active", True):
        raise HTTPException(status_code=403, detail="Your wallet is suspended")

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    stripe_pk = settings.get("stripe_publishable_key", "")

    if not stripe_secret:
        raise HTTPException(
            status_code=503,
            detail="Payment processing is not configured. Please contact support.",
        )

    # No separate user fetch: get_or_create_stripe_customer (via
    # with_customer_repair below) reads the row itself and raises the same
    # 404 "User not found". Keeping a second read here only duplicated a
    # cached lookup on every top-up.
    amount_cents = dollars_to_cents(req.amount)

    import time as _time

    idempotency_key = f"wallet-topup-{current_user['id']}-{int(_time.time() // 60)}"

    try:
        # Shared with the cards path rather than duplicated here. The local
        # copy this replaces created the customer with the rider's email and
        # legal name, contradicting the PIPEDA rule the payments.py helper
        # documents (no rider PII to Stripe, a US processor) — and which of the
        # two shapes a rider ended up with depended purely on whether they hit
        # top-up or cards first. with_customer_repair additionally recovers a
        # customer stranded by a test→live key rotation, which previously made
        # every top-up fail `resource_missing` with no way out.
        #
        # Sync Stripe SDK: threadpool so these round-trips don't block the
        # event loop (idempotency key makes the PI create retry-safe).
        async def _ephemeral(cid: str):
            return await asyncio.to_thread(
                lambda: stripe.EphemeralKey.create(
                    customer=cid,
                    stripe_version=stripe.api_version,
                    api_key=stripe_secret,
                )
            )

        stripe_customer_id, ephemeral_key = await with_customer_repair(
            current_user["id"], stripe_secret, _ephemeral
        )

        intent = await asyncio.to_thread(
            lambda: stripe.PaymentIntent.create(
                amount=amount_cents,
                currency="cad",
                customer=stripe_customer_id,
                automatic_payment_methods={"enabled": True},
                metadata={
                    "scope": "wallet_topup",
                    "user_id": current_user["id"],
                    "wallet_id": wallet["id"],
                    "amount_cad": _money_str(req.amount),
                },
                api_key=stripe_secret,
                idempotency_key=idempotency_key,
            )
        )

        return {
            "paymentIntent": intent.client_secret,
            "ephemeralKey": ephemeral_key.secret,
            "customer": stripe_customer_id,
            "publishableKey": stripe_pk,
        }
    except stripe.error.StripeError as e:
        logger.error("Stripe wallet top-up error", exc_info=True)
        raise HTTPException(status_code=502, detail="Payment provider error. Please try again.") from e


@api_router.post("/pay")
async def wallet_pay(req: WalletPayRequest, current_user: dict = Depends(get_current_user)):
    """Pay for a ride using wallet balance."""
    wallet = await get_or_create_wallet(current_user["id"])

    if not wallet.get("is_active", True):
        raise HTTPException(status_code=403, detail="Your wallet is suspended")

    # Validate the ride exists, belongs to this rider, and is in a payable state.
    # Also validate the client-supplied amount is within [server_fare, server_fare+0.01]
    # so neither underpayment nor overpayment is possible via this endpoint.
    ride = await db.find_one("rides", {"id": req.ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="ERR_FORBIDDEN")
    if ride.get("status") != "completed":
        raise HTTPException(status_code=400, detail="ERR_RIDE_NOT_PAYABLE")
    server_fare = _d(ride.get("total_fare", 0))
    debit_amount = _d(req.amount)
    if debit_amount > server_fare + _d("0.01"):
        raise HTTPException(status_code=400, detail="ERR_FARE_EXCEEDED")
    if debit_amount < server_fare - _d("0.01"):
        raise HTTPException(status_code=400, detail="ERR_FARE_UNDERPAID")

    try:
        new_balance = await wallet_pay_for_ride(wallet["id"], req.ride_id, debit_amount)
    except ValueError as exc:
        err = str(exc)
        if "insufficient_funds" in err:
            raise SpinrException(
                message="Insufficient wallet balance",
                error_code=ErrorCode.PAYMENT_INSUFFICIENT_FUNDS,
                status_code=400,
                message_key=ErrorKeys.PAYMENT_INSUFFICIENT_FUNDS,
                action_hint="Top up your wallet",
            ) from exc
        if "fare_underpaid" in err:
            raise HTTPException(status_code=400, detail="ERR_FARE_UNDERPAID") from exc
        if "ride_not_payable" in err:
            raise HTTPException(status_code=400, detail="ERR_RIDE_NOT_PAYABLE") from exc
        raise HTTPException(status_code=503, detail="Wallet payment failed — please retry") from exc

    # wallet_pay_for_ride returns None when the ride is already paid (idempotent
    # no-op from migration 107 — no money moved, wallet balance unchanged).
    # Re-fetch the wallet so the balance we return is post-debit (a concurrent
    # first request may have already debited while we were waiting inside the RPC
    # FOR UPDATE lock — returning the pre-RPC snapshot would show an inflated
    # balance to the client).
    if new_balance is None:
        fresh = await db.find_one("wallets", {"id": wallet["id"]})
        current_balance = _money_str(_d((fresh or wallet).get("balance", 0)))
        logger.info("[WALLET] /pay ride %s already paid — no-op, skipping ledger write", req.ride_id)
        return {
            "balance": current_balance,
            "transaction_id": None,
            "already_paid": True,
        }

    # Mark the ride payment method (the RPC already set payment_status='paid')
    await db.update_one(
        "rides",
        {"id": req.ride_id},
        {"$set": {"payment_method": "wallet"}},
    )

    txn = await _record_transaction(
        wallet_id=wallet["id"],
        user_id=current_user["id"],
        txn_type="ride_payment",
        amount="-" + _money_str(debit_amount),
        balance_after=_money_str(new_balance),
        reference_id=req.ride_id,
        description=f"Ride payment ${req.amount:.2f}",
    )

    import asyncio

    asyncio.create_task(
        _audit_log_user(
            current_user,
            "wallet_payment",
            "wallets",
            wallet["id"],
            {
                "amount": _money_str(req.amount),
                "ride_id": req.ride_id,
                "transaction_id": txn["id"],
            },
        )
    )
    return {
        "balance": _money_str(new_balance),
        "transaction_id": txn["id"],
    }


@api_router.get("/transactions")
async def get_transactions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Get wallet transaction history for the current user."""
    wallet = await get_or_create_wallet(current_user["id"])

    txns = await db.get_rows(
        "wallet_transactions",
        {"wallet_id": wallet["id"]},
        limit=limit,
        offset=offset,
        order="created_at",
        desc=True,
    )

    return {
        "transactions": [
            {
                "id": t["id"],
                "type": t["type"],
                "amount": t["amount"],
                "balance_after": t["balance_after"],
                "description": t.get("description"),
                "reference_id": t.get("reference_id"),
                "metadata": t.get("metadata") or {},
                "created_at": t.get("created_at"),
            }
            for t in txns
        ],
        "total": len(txns),
    }
