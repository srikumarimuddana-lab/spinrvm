"""Allowance movement — wrapper around `corporate_allowance_apply_delta` RPC.

Every allowance grant, reset, or rollback goes through this service. The RPC
locks the master wallet + allowance rows atomically and writes paired ledger
entries. Callers pass the master `wallet_id` and the target `allowance_id`;
the RPC validates they exist before mutating anything.
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


def _to_numeric_str(v: Union[Decimal, float, int, str]) -> str:
    """Round to 2 dp and serialise as string for PostgreSQL NUMERIC RPC params.

    supabase-py serialises RPC params via json.dumps; Decimal is not
    JSON-serialisable, and float(45.57) may produce 45.56999... on some
    platforms. Passing a string lets Postgres cast to NUMERIC losslessly.
    """
    return str(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def _apply(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    type_: str,
    amount: Union[Decimal, float, int],
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    floor: Optional[Union[Decimal, float, int]] = None,
) -> Dict[str, Any]:
    params = {
        "p_wallet_id": wallet_id,
        "p_allowance_id": allowance_id,
        "p_member_id": member_id,
        "p_type": type_,
        "p_amount": _to_numeric_str(amount),
        "p_actor_user_id": actor_user_id,
        "p_notes": notes,
        "p_floor": _to_numeric_str(floor) if floor is not None else None,
    }

    def _fn():
        return supabase.rpc("corporate_allowance_apply_delta", params).execute()

    resp = await run_sync(_fn)
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise RuntimeError("allowance RPC returned no row")
    return rows[0]


async def apply_grant(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    amount: Union[Decimal, float, int],
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    floor: Optional[Union[Decimal, float, int]] = None,
) -> Dict[str, Any]:
    if amount <= 0:
        raise ValueError("grant amount must be positive")
    return await _apply(
        wallet_id=wallet_id,
        allowance_id=allowance_id,
        member_id=member_id,
        type_="allowance_grant",
        amount=amount,
        actor_user_id=actor_user_id,
        notes=notes,
        floor=floor,
    )


async def apply_reset(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Zero out the `used` counter at the start of a new period."""
    return await _apply(
        wallet_id=wallet_id,
        allowance_id=allowance_id,
        member_id=member_id,
        type_="allowance_reset",
        amount=0,
        actor_user_id=actor_user_id,
        notes=notes,
    )


async def apply_rollback(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    amount: Union[Decimal, float, int],
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    if amount <= 0:
        raise ValueError("rollback amount must be positive")
    return await _apply(
        wallet_id=wallet_id,
        allowance_id=allowance_id,
        member_id=member_id,
        type_="allowance_rollback",
        amount=amount,
        actor_user_id=actor_user_id,
        notes=notes,
    )
