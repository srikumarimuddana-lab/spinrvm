"""Allowance movement — wrapper around `corporate_allowance_apply_delta` RPC.

Every allowance grant, reset, or rollback goes through this service. The RPC
locks the master wallet + allowance rows atomically and writes paired ledger
entries. Callers pass the master `wallet_id` and the target `allowance_id`;
the RPC validates they exist before mutating anything.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional, Union

try:
    from ..db_supabase import run_sync  # type: ignore
    from ..supabase_client import supabase  # type: ignore
except ImportError:  # pragma: no cover - dual import path
    from db_supabase import run_sync  # type: ignore
    from supabase_client import supabase  # type: ignore


async def _apply(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    type_: str,
    amount: Union[Decimal, float],
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    floor: Optional[Union[Decimal, float]] = None,
    ride_id: Optional[str] = None,
) -> Dict[str, Any]:
    params = {
        "p_wallet_id": wallet_id,
        "p_allowance_id": allowance_id,
        "p_member_id": member_id,
        "p_type": type_,
        # supabase-py serialises params via stdlib json which cannot handle
        # Decimal. Follow the str() convention used by wallet_increment_balance
        # and wallet_pay_for_ride in db_supabase.py — Postgres numeric accepts
        # string literals and preserves full precision.
        "p_amount": str(amount),
        "p_actor_user_id": actor_user_id,
        "p_notes": notes,
        "p_floor": str(floor) if floor is not None else None,
    }
    # ride-scoped idempotency (migration 297) — a retried settle_corporate
    # call for the same ride returns the original ledger pair untouched
    # instead of debiting/crediting a second time. Only ride_debit and
    # ride_debit_reversal pass this; grant/reset/rollback are not ride-scoped
    # and never call _apply with one.
    #
    # p_ride_id is a NEW parameter on corporate_allowance_apply_delta
    # (migration 297) that the pre-297 function signature does not accept at
    # all — PostgREST resolves RPC calls by exact named-parameter match, so
    # sending an unrecognized key makes EVERY call fail (function does not
    # exist) against a Supabase instance that hasn't had migration 297
    # applied yet. Only include the key when ride_id is actually set, so
    # grant/reset/rollback (which never pass one) keep working regardless of
    # migration/deploy ordering — see this migration's Change Impact Log
    # ("mandatory deploy sequence") for why ride_debit/ride_debit_reversal
    # still require the migration to land first.
    if ride_id is not None:
        params["p_ride_id"] = ride_id

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
    amount: Union[Decimal, float],
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    floor: Optional[Union[Decimal, float]] = None,
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


async def apply_ride_debit(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    amount: Union[Decimal, float],
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    floor: Optional[Union[Decimal, float]] = None,
    ride_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Charge the allowance-covered portion of a ride.

    master -amount (the company actually pays) and used +amount (the member's
    allowance is consumed). Ride settlement previously called apply_rollback for
    this, whose master delta is POSITIVE — so every allowance-covered ride
    credited the company instead of charging it. See migration 248.

    Pass ``ride_id`` so a retried settle_corporate call for the same ride is
    deduped by the RPC instead of debiting the allowance a second time
    (migration 297).
    """
    if amount <= 0:
        raise ValueError("ride debit amount must be positive")
    return await _apply(
        wallet_id=wallet_id,
        allowance_id=allowance_id,
        member_id=member_id,
        type_="ride_debit",
        amount=amount,
        actor_user_id=actor_user_id,
        notes=notes,
        floor=floor,
        ride_id=ride_id,
    )


async def apply_late_tip_debit(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    amount: Union[Decimal, float],
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    floor: Optional[Union[Decimal, float]] = None,
    ride_id: str,
) -> Dict[str, Any]:
    """Charge the allowance-covered portion of a tip added AFTER settlement.

    Same master/used math as apply_ride_debit (master -amount, used
    +amount) — it IS a ride debit, just applied after the fact — but uses
    ``type_="late_tip_debit"``, a distinct dedup key (migration 319) from
    the original settlement's ``"ride_debit"`` for the same ride_id.
    Reusing ``"ride_debit"`` here would silently deduplicate against the
    original settlement row and apply zero additional money movement.

    ``ride_id`` is required (not optional, unlike apply_ride_debit) — a
    late-tip debit only ever happens in the context of a specific already-
    settled ride, and the dedup protection this function exists for
    depends on it always being passed.
    """
    if amount <= 0:
        raise ValueError("late tip debit amount must be positive")
    return await _apply(
        wallet_id=wallet_id,
        allowance_id=allowance_id,
        member_id=member_id,
        type_="late_tip_debit",
        amount=amount,
        actor_user_id=actor_user_id,
        notes=notes,
        floor=floor,
        ride_id=ride_id,
    )


async def apply_ride_debit_reversal(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    amount: Union[Decimal, float],
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
    ride_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Exact inverse of apply_ride_debit: master +amount, used -amount.

    Used to compensate the allowance charge when a later step of the same
    settlement fails. apply_grant must NOT be used for this — its master delta
    is negative, so it would charge the company a second time.

    Pass ``ride_id`` for the same dedup reason as apply_ride_debit — note this
    reversal uses ``type_="ride_debit_reversal"``, a distinct dedup key from
    the debit it compensates, so a retried reversal is deduped independently.
    """
    if amount <= 0:
        raise ValueError("ride debit reversal amount must be positive")
    return await _apply(
        wallet_id=wallet_id,
        allowance_id=allowance_id,
        member_id=member_id,
        type_="ride_debit_reversal",
        amount=amount,
        actor_user_id=actor_user_id,
        notes=notes,
        ride_id=ride_id,
    )


async def apply_rollback(
    *,
    wallet_id: str,
    allowance_id: str,
    member_id: str,
    amount: Union[Decimal, float],
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Undo a prior grant: master +amount, used +amount.

    NOT for ride settlement — use apply_ride_debit for that.
    """
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
