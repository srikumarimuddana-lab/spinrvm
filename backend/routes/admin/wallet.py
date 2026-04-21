"""Admin wallet operations — credit a user's wallet, view balance + history.

Every handler runs behind the ``get_admin_user`` dependency attached at
the admin_router level (see routes/admin/__init__.py), so no handler
here re-checks auth. Credits and debits recorded via this module carry
``admin_id`` in the ledger metadata so refunds/adjustments are auditable.
"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...db import db
    from ...dependencies import get_admin_user
    from ..wallet import _record_transaction, get_or_create_wallet
except ImportError:
    import db_supabase
    from db import db
    from dependencies import get_admin_user
    from routes.wallet import _record_transaction, get_or_create_wallet

router = APIRouter(prefix="/wallet", tags=["Admin Wallet"])

_TWO = Decimal("0.01")


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(_TWO, rounding=ROUND_HALF_UP)


class AdminCreditRequest(BaseModel):
    user_id: str
    amount: float = Field(..., gt=0, le=10_000, description="CAD amount to credit (max $10,000/txn)")
    reason: str = Field(..., min_length=3, max_length=200)


class AdminDebitRequest(BaseModel):
    user_id: str
    amount: float = Field(..., gt=0, le=10_000)
    reason: str = Field(..., min_length=3, max_length=200)


@router.get("/{user_id}")
async def admin_get_wallet(
    user_id: str,
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_admin_user),
):
    """Return a user's wallet + recent ledger entries for the admin view."""
    user = await db_supabase.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    wallet = await get_or_create_wallet(user_id)
    txns = await db.get_rows(
        "wallet_transactions",
        {"wallet_id": wallet["id"]},
        limit=limit,
        order="created_at",
    )

    return {
        "user": {
            "id": user["id"],
            "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or user.get("phone") or user.get("email"),
            "phone": user.get("phone"),
            "email": user.get("email"),
        },
        "wallet": {
            "id": wallet["id"],
            "balance": float(wallet.get("balance", 0)),
            "currency": wallet.get("currency", "CAD"),
            "is_active": wallet.get("is_active", True),
        },
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
    }


@router.post("/credit")
async def admin_credit_wallet(req: AdminCreditRequest, admin: dict = Depends(get_admin_user)):
    """Credit a user's wallet. Writes an audited ledger entry."""
    user = await db_supabase.get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    wallet = await get_or_create_wallet(req.user_id)
    if not wallet.get("is_active", True):
        raise HTTPException(status_code=403, detail="Wallet is suspended")

    old_balance = _q(wallet.get("balance", 0))
    credit = _q(req.amount)
    new_balance = old_balance + credit

    await db.update_one(
        "wallets",
        {"id": wallet["id"]},
        {"$set": {"balance": float(new_balance), "updated_at": datetime.utcnow().isoformat()}},
    )
    txn = await _record_transaction(
        wallet_id=wallet["id"],
        user_id=req.user_id,
        txn_type="admin_credit",
        amount=float(credit),
        balance_after=float(new_balance),
        description=f"Admin credit: {req.reason}",
        metadata={"admin_id": admin["id"], "reason": req.reason},
    )

    return {
        "balance": float(new_balance),
        "transaction_id": txn["id"],
    }


@router.post("/debit")
async def admin_debit_wallet(req: AdminDebitRequest, admin: dict = Depends(get_admin_user)):
    """Debit (deduct from) a user's wallet — refunds, correction, fraud clawback."""
    user = await db_supabase.get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    wallet = await get_or_create_wallet(req.user_id)
    old_balance = _q(wallet.get("balance", 0))
    debit = _q(req.amount)
    if old_balance < debit:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient balance. Need ${debit}, have ${old_balance}",
        )

    new_balance = old_balance - debit
    await db.update_one(
        "wallets",
        {"id": wallet["id"]},
        {"$set": {"balance": float(new_balance), "updated_at": datetime.utcnow().isoformat()}},
    )
    txn = await _record_transaction(
        wallet_id=wallet["id"],
        user_id=req.user_id,
        txn_type="admin_debit",
        amount=-float(debit),
        balance_after=float(new_balance),
        description=f"Admin debit: {req.reason}",
        metadata={"admin_id": admin["id"], "reason": req.reason},
    )

    return {
        "balance": float(new_balance),
        "transaction_id": txn["id"],
    }
