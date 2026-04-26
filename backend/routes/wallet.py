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
from pydantic import BaseModel, Field

try:
    from ..db import db
    from ..db_supabase import rpc as db_rpc
    from ..dependencies import get_current_user
    from ..utils.idempotency import idempotent_endpoint
except ImportError:
    from db import db
    from dependencies import get_current_user
    from utils.idempotency import idempotent_endpoint

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/wallet", tags=["Wallet"])

_TWO = Decimal("0.01")


def _d(v) -> Decimal:
    return Decimal(str(v)).quantize(_TWO, rounding=ROUND_HALF_UP)


# ── Helpers ──────────────────────────────────────────────────────────


async def get_or_create_wallet(user_id: str) -> dict:
    """Return the user's wallet, creating one if it doesn't exist."""
    wallet = await db.find_one("wallets", {"user_id": user_id})
    if wallet:
        return wallet

    wallet_data = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "balance": 0.0,
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
    amount: float,
    balance_after: float,
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
    recipient_phone: str
    amount: float = Field(..., gt=0, le=200)


# ── Endpoints ────────────────────────────────────────────────────────


@api_router.get("")
async def get_wallet(current_user: dict = Depends(get_current_user)):
    """Get the current user's wallet balance and info."""
    wallet = await get_or_create_wallet(current_user["id"])
    return {
        "id": wallet["id"],
        "balance": float(wallet.get("balance", 0)),
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

    try:
        result = await db_rpc(
            "wallet_increment_balance", {"p_wallet_id": wallet["id"], "p_amount": str(_d(req.amount))}
        )
    except Exception as e:
        msg = str(e).lower()
        if "suspended" in msg or "not found" in msg:
            raise HTTPException(status_code=403, detail="Wallet is suspended") from e
        raise HTTPException(status_code=503, detail="Wallet update failed") from e

    new_balance = _d(result)

    txn = await _record_transaction(
        wallet_id=wallet["id"],
        user_id=current_user["id"],
        txn_type="top_up",
        amount=float(_d(req.amount)),
        balance_after=float(new_balance),
        description=f"Wallet top-up ${req.amount:.2f}",
    )

    return {
        "balance": float(new_balance),
        "transaction_id": txn["id"],
    }


@api_router.post("/pay")
async def wallet_pay(req: WalletPayRequest, current_user: dict = Depends(get_current_user)):
    """Pay for a ride using wallet balance."""
    wallet = await get_or_create_wallet(current_user["id"])

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
        result = await db_rpc(
            "wallet_pay_for_ride",
            {"p_wallet_id": wallet["id"], "p_ride_id": req.ride_id, "p_amount": str(debit_amount)},
        )
    except Exception as e:
        msg = str(e).lower()
        if "insufficient" in msg:
            raise HTTPException(status_code=400, detail="Insufficient wallet balance") from e
        if "suspended" in msg or "not found" in msg:
            raise HTTPException(status_code=403, detail="Wallet is suspended") from e
        raise HTTPException(status_code=503, detail="Wallet update failed") from e

    new_balance = _d(result)

    txn = await _record_transaction(
        wallet_id=wallet["id"],
        user_id=current_user["id"],
        txn_type="ride_payment",
        amount=-float(debit_amount),
        balance_after=float(new_balance),
        reference_id=req.ride_id,
        description=f"Ride payment ${req.amount:.2f}",
    )

    return {
        "balance": float(new_balance),
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

    try:
        txns = await db.get_rows(
            "wallet_transactions",
            {"wallet_id": wallet["id"]},
            limit=limit,
            skip=offset,
            order="created_at",
        )
    except Exception as e:
        logger.error(f"Error fetching transactions: {e}")
        txns = []

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
        raise HTTPException(status_code=404, detail="Recipient not found")

    if recipient["id"] == current_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot transfer to yourself")

    sender_wallet = await get_or_create_wallet(current_user["id"])
    recipient_wallet = await get_or_create_wallet(recipient["id"])
    transfer_amount = _d(req.amount)

    try:
        rows = await db_rpc(
            "wallet_transfer",
            {
                "p_sender_wallet_id": sender_wallet["id"],
                "p_recipient_wallet_id": recipient_wallet["id"],
                "p_amount": str(transfer_amount),
            },
        )
    except Exception as e:
        msg = str(e).lower()
        if "insufficient" in msg:
            raise HTTPException(status_code=400, detail="Insufficient wallet balance") from e
        if "suspended" in msg or "not found" in msg:
            raise HTTPException(status_code=403, detail="Wallet is suspended") from e
        raise HTTPException(status_code=503, detail="Wallet transfer failed") from e

    # wallet_transfer returns a single row with sender_balance, recipient_balance
    row = rows[0] if isinstance(rows, list) else rows
    new_sender_balance = _d(row["sender_balance"])
    new_recipient_balance = _d(row["recipient_balance"])

    await _record_transaction(
        wallet_id=sender_wallet["id"],
        user_id=current_user["id"],
        txn_type="fare_split_sent",
        amount=-float(transfer_amount),
        balance_after=float(new_sender_balance),
        description=f"Transfer to {req.recipient_phone}",
    )
    await _record_transaction(
        wallet_id=recipient_wallet["id"],
        user_id=recipient["id"],
        txn_type="fare_split_received",
        amount=float(transfer_amount),
        balance_after=float(new_recipient_balance),
        description=f"Received from {current_user.get('phone', 'user')}",
    )

    return {"balance": float(new_sender_balance), "success": True}
