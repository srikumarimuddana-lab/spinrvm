"""Money movements for corporate wallets.

All deltas (top-ups, ride debits, adjustments, refunds) go through this
service. It wraps the Postgres function `corporate_wallet_apply_delta`
which enforces row-level locking, idempotency on stripe_payment_intent_id
(top-ups) or ride_id (internal ride-settlement debits — migration 297),
and optional soft-negative-floor enforcement.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional, Union

try:
    from ..db_supabase import run_sync  # type: ignore
    from ..supabase_client import supabase  # type: ignore
except ImportError:
    from db_supabase import run_sync  # type: ignore
    from supabase_client import supabase  # type: ignore

# Money values cross the JSON boundary into the Postgres RPC. We always
# normalize to Decimal and serialize as a string to avoid IEEE-754 drift —
# Postgres' numeric type accepts string literals losslessly.
_TWO = Decimal("0.01")
_Numeric = Union[Decimal, int, float, str]


def _money_str(v: _Numeric) -> str:
    return str(Decimal(str(v)).quantize(_TWO, rounding=ROUND_HALF_UP))


async def _apply(
    *,
    wallet_id: str,
    scope: str,
    type_: str,
    delta: Decimal,
    ride_id: Optional[str] = None,
    member_id: Optional[str] = None,
    stripe_payment_intent_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    floor: Optional[Decimal] = None,
) -> Dict[str, Any]:
    params = {
        "p_wallet_id": wallet_id,
        "p_scope": scope,
        "p_type": type_,
        "p_delta": _money_str(delta),
        "p_ride_id": ride_id,
        "p_member_id": member_id,
        "p_stripe_pi": stripe_payment_intent_id,
        "p_actor_user_id": actor_user_id,
        "p_notes": notes,
        "p_floor": _money_str(floor) if floor is not None else None,
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
    amount: _Numeric,
    stripe_payment_intent_id: str,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    delta = Decimal(str(amount))
    if delta <= 0:
        raise ValueError("top-up amount must be positive")
    return await _apply(
        wallet_id=wallet_id,
        scope="master",
        type_="topup",
        delta=delta,
        stripe_payment_intent_id=stripe_payment_intent_id,
        actor_user_id=actor_user_id,
        notes=notes,
    )


async def apply_adjustment(
    *,
    wallet_id: str,
    amount: _Numeric,
    notes: str,
    actor_user_id: str,
    floor: Optional[_Numeric] = None,
    ride_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Signed adjustment to the master wallet (support/refund). Notes required.

    ``ride_id``, when passed, enables the RPC's ride-scoped idempotency
    (migration 297) — a retried settle_corporate call for the same ride is a
    no-op instead of a second debit. Omit for ad-hoc admin adjustments that
    aren't tied to a specific ride.
    """
    delta = Decimal(str(amount))
    if delta == 0:
        raise ValueError("adjustment amount cannot be zero")
    return await _apply(
        wallet_id=wallet_id,
        scope="master",
        type_="adjustment",
        delta=delta,
        ride_id=ride_id,
        actor_user_id=actor_user_id,
        notes=notes,
        floor=Decimal(str(floor)) if floor is not None else None,
    )


async def apply_refund(
    *,
    wallet_id: str,
    amount: _Numeric,
    ride_id: str,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    delta = Decimal(str(amount))
    if delta <= 0:
        raise ValueError("refund amount must be positive")
    return await _apply(
        wallet_id=wallet_id,
        scope="master",
        type_="refund",
        delta=delta,
        ride_id=ride_id,
        actor_user_id=actor_user_id,
        notes=notes,
    )
