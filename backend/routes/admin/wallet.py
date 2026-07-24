"""Admin wallet operations — credit a user's wallet, view balance + history.

Every handler runs behind the ``get_admin_user`` dependency attached at
the admin_router level (see routes/admin/__init__.py), so no handler
here re-checks auth. Credits and debits recorded via this module carry
``admin_id`` in the ledger metadata so refunds/adjustments are auditable.
"""

import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...db import db
    from ...dependencies import get_admin_user
    from ...utils.rate_limiter import admin_wallet_limit
    from ..wallet import _money_str, get_or_create_wallet
except ImportError:
    import db_supabase
    from db import db
    from dependencies import get_admin_user
    from routes.wallet import _money_str, get_or_create_wallet
    from utils.rate_limiter import admin_wallet_limit

router = APIRouter(prefix="/wallet", tags=["Admin Wallet"])

_TWO = Decimal("0.01")


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(_TWO, rounding=ROUND_HALF_UP)


class AdminCreditRequest(BaseModel):
    user_id: str
    amount: Decimal = Field(
        ...,
        gt=Decimal("0.01"),
        le=Decimal("10000"),
        description="CAD amount to credit (max $10,000/txn)",
    )
    reason: str = Field(..., min_length=3, max_length=200)
    idempotency_key: str | None = Field(
        None,
        description="Caller-supplied key; duplicate requests return the original result",
    )


class AdminDebitRequest(BaseModel):
    user_id: str
    amount: float = Field(..., gt=0, le=10_000)
    reason: str = Field(..., min_length=3, max_length=200)
    idempotency_key: str | None = Field(
        None,
        description="Caller-supplied key; duplicate requests return the original result",
    )


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
            "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
            or user.get("phone")
            or user.get("email"),
            "phone": user.get("phone"),
            "email": user.get("email"),
        },
        "wallet": {
            "id": wallet["id"],
            "balance": _money_str(wallet.get("balance", 0)),
            "currency": wallet.get("currency", "CAD"),
            "is_active": wallet.get("is_active", True),
        },
        "transactions": [
            {
                "id": t["id"],
                "type": t["type"],
                "amount": _money_str(t["amount"]),
                "balance_after": _money_str(t["balance_after"]),
                "description": t.get("description"),
                "reference_id": t.get("reference_id"),
                "metadata": t.get("metadata") or {},
                "created_at": t.get("created_at"),
            }
            for t in txns
        ],
    }


@router.post("/credit")
@admin_wallet_limit
async def admin_credit_wallet(
    request: Request,
    req: AdminCreditRequest,
    admin: dict = Depends(get_admin_user),
):
    """Credit a user's wallet. Writes an audited ledger entry."""
    # Idempotency guard (F-37): if a key was supplied, return the existing
    # transaction rather than applying the credit a second time on retry.
    if req.idempotency_key:
        existing_txns = await db_supabase.get_rows(
            "wallet_transactions",
            {"reference_id": req.idempotency_key, "type": "admin_credit"},
            limit=1,
        )
        if existing_txns:
            t = existing_txns[0]
            return {
                "balance": _money_str(t["balance_after"]),
                "transaction_id": t["id"],
            }

    user = await db_supabase.get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    wallet = await get_or_create_wallet(req.user_id)
    if not wallet.get("is_active", True):
        raise HTTPException(status_code=403, detail="Wallet is suspended")

    old_balance = _q(wallet.get("balance", 0))
    credit = _q(req.amount)

    # WS-6: atomic locked delta. The previous read-modify-write wrote the
    # balance back filtered on {"id": wallet_id} only, so any concurrent
    # mutation (ride payment, cancellation fee, another admin action) landing
    # between the read above and the write was silently overwritten.
    # floor=None: a credit must never be floor-checked — a wallet already below
    # zero would otherwise reject the very top-up that fixes it.
    txn = await db_supabase.wallet_apply_delta(
        wallet_id=wallet["id"],
        user_id=req.user_id,
        type_="admin_credit",
        delta=credit,
        reference_id=req.idempotency_key,
        description=f"Admin credit: {req.reason}",
        metadata={"admin_id": admin["id"], "reason": req.reason},
        floor=None,
    )
    new_balance = _q(txn["balance_after"])

    audit_id = str(uuid.uuid4())
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": audit_id,
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "wallet_credit",
            "entity_type": "user",
            "entity_id": req.user_id,
            "details": {
                "amount": _money_str(credit),
                "old_balance": _money_str(old_balance),
                "new_balance": _money_str(new_balance),
                "reason": req.reason,
                "transaction_id": txn["id"],
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {
        "balance": _money_str(new_balance),
        "transaction_id": txn["id"],
        "audit_log_id": audit_id,
    }


@router.post("/debit")
@admin_wallet_limit
async def admin_debit_wallet(
    request: Request,
    req: AdminDebitRequest,
    admin: dict = Depends(get_admin_user),
):
    """Debit (deduct from) a user's wallet — refunds, correction, fraud clawback."""
    # Idempotency guard (F-37): return existing result on retry.
    if req.idempotency_key:
        existing_txns = await db_supabase.get_rows(
            "wallet_transactions",
            {"reference_id": req.idempotency_key, "type": "admin_debit"},
            limit=1,
        )
        if existing_txns:
            t = existing_txns[0]
            return {
                "balance": _money_str(t["balance_after"]),
                "transaction_id": t["id"],
            }

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

    # WS-6: atomic locked delta. The sufficiency check above is advisory only —
    # it races, which is exactly how a debit could previously succeed against a
    # balance that no longer existed. The RPC re-checks the floor under the row
    # lock and refuses to overdraw; translate that into the same 400 the
    # advisory check returns.
    try:
        txn = await db_supabase.wallet_apply_delta(
            wallet_id=wallet["id"],
            user_id=req.user_id,
            type_="admin_debit",
            delta=-debit,
            reference_id=req.idempotency_key,
            description=f"Admin debit: {req.reason}",
            metadata={"admin_id": admin["id"], "reason": req.reason},
            floor=Decimal("0"),
        )
    except Exception as exc:
        if "wallet_below_floor" in str(exc) or "wallet_below_floor" in str(getattr(exc, "details", "")):
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance. Need ${debit}, have ${old_balance}",
            ) from exc
        raise
    new_balance = _q(txn["balance_after"])

    audit_id = str(uuid.uuid4())
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": audit_id,
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "wallet_debit",
            "entity_type": "user",
            "entity_id": req.user_id,
            "details": {
                "amount": _money_str(debit),
                "old_balance": _money_str(old_balance),
                "new_balance": _money_str(new_balance),
                "reason": req.reason,
                "transaction_id": txn["id"],
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {
        "balance": _money_str(new_balance),
        "transaction_id": txn["id"],
        "audit_log_id": audit_id,
    }
