"""Money movements for corporate wallets.

All deltas (top-ups, ride debits, adjustments, refunds) go through this
service. It wraps the Postgres function `corporate_wallet_apply_delta`
which enforces row-level locking, idempotency on stripe_payment_intent_id,
and optional soft-negative-floor enforcement.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from ..db_supabase import run_sync  # type: ignore
    from ..supabase_client import supabase  # type: ignore
except ImportError:
    from db_supabase import run_sync  # type: ignore
    from supabase_client import supabase  # type: ignore


async def _apply(
    *,
    wallet_id: str,
    scope: str,
    type_: str,
    delta: float,
    ride_id: Optional[str] = None,
    member_id: Optional[str] = None,
    stripe_payment_intent_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    floor: Optional[float] = None,
) -> Dict[str, Any]:
    params = {
        "p_wallet_id": wallet_id,
        "p_scope": scope,
        "p_type": type_,
        "p_delta": delta,
        "p_ride_id": ride_id,
        "p_member_id": member_id,
        "p_stripe_pi": stripe_payment_intent_id,
        "p_actor_user_id": actor_user_id,
        "p_notes": notes,
        "p_floor": floor,
    }

    def _fn():
        return supabase.rpc("corporate_wallet_apply_delta", params).execute()

    resp = await run_sync(_fn)
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise RuntimeError("wallet RPC returned no row")
    return rows[0]


async def apply_topup(
    *,
    wallet_id: str,
    amount: float,
    stripe_payment_intent_id: str,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    if amount <= 0:
        raise ValueError("top-up amount must be positive")
    return await _apply(
        wallet_id=wallet_id,
        scope="master",
        type_="topup",
        delta=amount,
        stripe_payment_intent_id=stripe_payment_intent_id,
        actor_user_id=actor_user_id,
        notes=notes,
    )


async def apply_adjustment(
    *,
    wallet_id: str,
    amount: float,
    notes: str,
    actor_user_id: str,
    floor: Optional[float] = None,
) -> Dict[str, Any]:
    """Signed adjustment to the master wallet (support/refund). Notes required."""
    if amount == 0:
        raise ValueError("adjustment amount cannot be zero")
    return await _apply(
        wallet_id=wallet_id,
        scope="master",
        type_="adjustment",
        delta=amount,
        actor_user_id=actor_user_id,
        notes=notes,
        floor=floor,
    )


async def apply_refund(
    *,
    wallet_id: str,
    amount: float,
    ride_id: str,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    if amount <= 0:
        raise ValueError("refund amount must be positive")
    return await _apply(
        wallet_id=wallet_id,
        scope="master",
        type_="refund",
        delta=amount,
        ride_id=ride_id,
        actor_user_id=actor_user_id,
        notes=notes,
    )
