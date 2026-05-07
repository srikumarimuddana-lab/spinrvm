"""In-app wallet — top-up, balance, transaction history, and wallet-based payments.

Riders and drivers each have a single wallet (auto-created on first access).
All balance mutations go through the ledger (wallet_transactions) so the
audit trail is immutable.
"""

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
    from ..db_supabase import insert_one as _db_insert_one
    from ..db_supabase import wallet_increment_balance, wallet_pay_for_ride
    from ..db_supabase import wallet_transfer as _wallet_transfer_rpc
    from ..dependencies import get_current_user
    from ..utils.error_handling import ErrorCode, SpinrException
    from ..utils.error_keys import ErrorKeys
    from ..utils.idempotency import idempotent_endpoint
except ImportError:
    from db import db
    from db_supabase import insert_one as _db_insert_one
    from db_supabase import wallet_increment_balance, wallet_pay_for_ride
    from db_supabase import wallet_transfer as _wallet_transfer_rpc
    from dependencies import get_current_user
    from utils.error_handling import ErrorCode, SpinrException
    from utils.error_keys import ErrorKeys
    from utils.idempotency import idempotent_endpoint

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


class TransferRequest(BaseModel):
    recipient_phone: str = Field(..., pattern=r"^\+1\d{10}$")
    amount: Decimal = Field(..., gt=0, le=200, description="Amount in CAD (max $200)")


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
    """Add funds to wallet. In production this would charge via Stripe first."""
    wallet = await get_or_create_wallet(current_user["id"])

    if not wallet.get("is_active", True):
        raise HTTPException(status_code=403, detail="Your wallet is suspended")

    new_balance = await wallet_increment_balance(wallet["id"], _d(req.amount))

    txn = await _record_transaction(
        wallet_id=wallet["id"],
        user_id=current_user["id"],
        txn_type="top_up",
        amount=_money_str(req.amount),
        balance_after=_money_str(new_balance),
        description=f"Wallet top-up ${req.amount:.2f}",
    )

    import asyncio
    asyncio.create_task(
        _audit_log_user(
            current_user,
            "wallet_top_up",
            "wallets",
            wallet["id"],
            {"amount": _money_str(req.amount), "transaction_id": txn["id"]},
        )
    )
    return {
        "balance": _money_str(new_balance),
        "transaction_id": txn["id"],
    }


@api_router.post("/pay")
async def wallet_pay(req: WalletPayRequest, current_user: dict = Depends(get_current_user)):
    """Pay for a ride using wallet balance."""
    wallet = await get_or_create_wallet(current_user["id"])

    if not wallet.get("is_active", True):
        raise HTTPException(status_code=403, detail="Your wallet is suspended")

    # R-P1-19: Validate client-supplied amount against the server-stored ride fare
    # to prevent a malicious caller from paying less than the actual fare.
    ride = await db.find_one("rides", {"id": req.ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="ERR_FORBIDDEN")
    server_fare = _d(ride.get("total_fare", 0))
    debit_amount = _d(req.amount)
    if debit_amount > server_fare + _d("0.01"):
        raise HTTPException(status_code=400, detail="ERR_FARE_EXCEEDED")

    try:
        new_balance = await wallet_pay_for_ride(wallet["id"], req.ride_id, debit_amount)
    except ValueError as exc:
        if "insufficient_funds" in str(exc):
            raise SpinrException(
                message="Insufficient wallet balance",
                error_code=ErrorCode.PAYMENT_INSUFFICIENT_FUNDS,
                status_code=400,
                message_key=ErrorKeys.PAYMENT_INSUFFICIENT_FUNDS,
                action_hint="Top up your wallet",
            ) from exc
        raise HTTPException(status_code=503, detail="Wallet payment failed — please retry") from exc

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
            {"amount": _money_str(req.amount), "ride_id": req.ride_id, "transaction_id": txn["id"]},
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
        skip=offset,
        order="created_at",
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
                "created_at": t.get("created_at"),
            }
            for t in txns
        ],
        "total": len(txns),
    }


@api_router.post("/transfer")
@idempotent_endpoint(scope="wallet_transfer")
async def transfer_to_user(
    req: TransferRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Transfer wallet balance to another user by phone number."""
    # Find recipient
    recipient = await db.find_one("users", {"phone": req.recipient_phone})
    if not recipient:
        raise SpinrException(
            message="Recipient not found",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
            status_code=404,
            message_key=ErrorKeys.WALLET_TRANSFER_RECIPIENT_NOT_FOUND,
            action_hint="Check the phone number",
        )

    if recipient["id"] == current_user["id"]:
        raise SpinrException(
            message="Cannot transfer to yourself",
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            message_key=ErrorKeys.WALLET_TRANSFER_SELF,
        )

    sender_wallet = await get_or_create_wallet(current_user["id"])
    recipient_wallet = await get_or_create_wallet(recipient["id"])

    if not sender_wallet.get("is_active", True):
        raise HTTPException(status_code=403, detail="Your wallet is suspended")

    transfer_amount = _d(req.amount)

    try:
        new_sender_balance, new_recipient_balance = await _wallet_transfer_rpc(
            sender_wallet["id"], recipient_wallet["id"], transfer_amount
        )
    except ValueError as exc:
        if "insufficient_funds" in str(exc):
            raise SpinrException(
                message="Insufficient wallet balance",
                error_code=ErrorCode.PAYMENT_INSUFFICIENT_FUNDS,
                status_code=400,
                message_key=ErrorKeys.PAYMENT_INSUFFICIENT_FUNDS,
                action_hint="Top up your wallet",
            ) from exc
        raise HTTPException(status_code=503, detail="Transfer failed — please retry") from exc

    await _record_transaction(
        wallet_id=sender_wallet["id"],
        user_id=current_user["id"],
        txn_type="fare_split_sent",
        amount="-" + _money_str(transfer_amount),
        balance_after=_money_str(new_sender_balance),
        description=f"Transfer to {req.recipient_phone}",
    )
    await _record_transaction(
        wallet_id=recipient_wallet["id"],
        user_id=recipient["id"],
        txn_type="fare_split_received",
        amount=_money_str(transfer_amount),
        balance_after=_money_str(new_recipient_balance),
        description=f"Received from {current_user.get('phone', 'user')}",
    )

    try:
        await _db_insert_one(
            "audit_logs",
            {
                "id": str(uuid.uuid4()),
                "action": "wallet_transfer",
                "entity_type": "wallets",
                "entity_id": sender_wallet["id"],
                "actor_id": current_user["id"],
                "details": {
                    "amount": _money_str(transfer_amount),
                    "recipient_id": recipient["id"],
                    "new_sender_balance": _money_str(new_sender_balance),
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.warning("audit_log write failed for wallet_transfer", exc_info=True)

    return {"balance": _money_str(new_sender_balance), "success": True}
