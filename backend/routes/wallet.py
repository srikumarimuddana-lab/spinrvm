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
from weakref import WeakValueDictionary

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

try:
    from ..db import db
    from ..dependencies import get_current_user
    from ..utils.idempotency import idempotent_endpoint
except ImportError:
    from db import db
    from dependencies import get_current_user
    from utils.idempotency import idempotent_endpoint

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/wallet", tags=["Wallet"])

_TWO = Decimal("0.01")

# Per-wallet locks prevent concurrent transfers from racing on the same balance.
# WeakValueDictionary drops the lock entry once no coroutine holds a reference,
# so memory does not grow unbounded across many wallets.
# NOTE: this only protects within a single process replica. A distributed lock
# (e.g. Redis SETNX) is needed for multi-replica deployments.
_wallet_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_wallet_locks_mu = asyncio.Lock()


def _d(v) -> Decimal:
    return Decimal(str(v)).quantize(_TWO, rounding=ROUND_HALF_UP)


async def _get_wallet_lock(wallet_id: str) -> asyncio.Lock:
    async with _wallet_locks_mu:
        lock = _wallet_locks.get(wallet_id)
        if lock is None:
            lock = asyncio.Lock()
            _wallet_locks[wallet_id] = lock
        return lock


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
    amount: Decimal = Field(..., gt=0, le=200)


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

    if not wallet.get("is_active", True):
        raise HTTPException(status_code=403, detail="Wallet is suspended")

    old_balance = _d(wallet.get("balance", 0))
    new_balance = old_balance + _d(req.amount)

    await db.update_one(
        "wallets",
        {"id": wallet["id"]},
        {"$set": {"balance": float(new_balance), "updated_at": datetime.now(timezone.utc).isoformat()}},
    )

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

    if not wallet.get("is_active", True):
        raise HTTPException(status_code=403, detail="Wallet is suspended")

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

    old_balance = _d(wallet.get("balance", 0))

    if old_balance < debit_amount:
        raise HTTPException(status_code=400, detail="Insufficient wallet balance")

    new_balance = old_balance - debit_amount

    await db.update_one(
        "wallets",
        {"id": wallet["id"]},
        {"$set": {"balance": float(new_balance), "updated_at": datetime.now(timezone.utc).isoformat()}},
    )

    # Mark ride as paid via wallet
    await db.update_one(
        "rides",
        {"id": req.ride_id},
        {"$set": {"payment_status": "paid", "payment_method": "wallet"}},
    )

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

    if not sender_wallet.get("is_active", True):
        raise HTTPException(status_code=403, detail="Your wallet is suspended")

    # Acquire per-wallet locks in a consistent order (lower ID first) to avoid
    # deadlock when two users transfer to each other simultaneously.
    ids_sorted = sorted([sender_wallet["id"], recipient_wallet["id"]])
    lock_a = await _get_wallet_lock(ids_sorted[0])
    lock_b = await _get_wallet_lock(ids_sorted[1])

    async with lock_a, lock_b:
        # Re-fetch balances inside the lock so we see the latest committed value.
        sender_wallet = await get_or_create_wallet(current_user["id"])
        recipient_wallet = await get_or_create_wallet(recipient["id"])

        sender_balance = _d(sender_wallet.get("balance", 0))
        transfer_amount = _d(req.amount)

        if sender_balance < transfer_amount:
            raise HTTPException(status_code=400, detail="Insufficient wallet balance")

        new_sender_balance = sender_balance - transfer_amount
        new_recipient_balance = _d(recipient_wallet.get("balance", 0)) + transfer_amount

        # Debit sender
        await db.update_one(
            "wallets",
            {"id": sender_wallet["id"]},
            {"$set": {"balance": float(new_sender_balance), "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        await _record_transaction(
            wallet_id=sender_wallet["id"],
            user_id=current_user["id"],
            txn_type="fare_split_sent",
            amount=-float(transfer_amount),
            balance_after=float(new_sender_balance),
            description=f"Transfer to {req.recipient_phone}",
        )

        # Credit recipient
        await db.update_one(
            "wallets",
            {"id": recipient_wallet["id"]},
            {"$set": {"balance": float(new_recipient_balance), "updated_at": datetime.now(timezone.utc).isoformat()}},
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
