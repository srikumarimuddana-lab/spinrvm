"""Money movements for corporate wallets.

All deltas (top-ups, ride debits, adjustments, refunds) go through this
service. It wraps the Postgres function `corporate_wallet_apply_delta`
which enforces row-level locking, idempotency on stripe_payment_intent_id,
and optional soft-negative-floor enforcement.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional, Union

try:
    from ..db_supabase import run_sync  # type: ignore
    from ..supabase_client import supabase  # type: ignore
    from ..utils.error_handling import DuplicateRecordError, db_error_text  # type: ignore
except ImportError:
    from db_supabase import run_sync  # type: ignore
    from supabase_client import supabase  # type: ignore
    from utils.error_handling import DuplicateRecordError, db_error_text  # type: ignore

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


async def _wallet_scope(wallet_id: str) -> str:
    """Derive the ledger scope from the wallet row (M5.4): 'master' for the
    company wallet, 'section:<uuid>' for a department wallet. Top-ups land
    via the wallet-agnostic Stripe webhook, so the scope must come from the
    wallet itself — hardcoding 'master' mislabeled section-wallet ledger rows."""

    def _fn():
        return supabase.table("corporate_wallets").select("id, section_id").eq("id", wallet_id).limit(1).execute()

    resp = await run_sync(_fn)
    rows = getattr(resp, "data", None) or []
    if not rows:
        # Surface loudly (CLAUDE.md): a missing wallet must never silently
        # ledger as 'master' — the webhook 5xxes and Stripe retries/reconciles.
        raise RuntimeError(f"wallet not found for scope derivation: {wallet_id}")
    section_id = rows[0].get("section_id")
    return f"section:{section_id}" if section_id else "master"


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
        scope=await _wallet_scope(wallet_id),
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
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Signed adjustment to the master wallet (support/refund). Notes required.

    idempotency_key (M5.5): rides pass a deterministic synthetic key
    ('ride:<id>:master') through the ledger's stripe_payment_intent_id
    idempotency channel so a settlement retry after a partial failure is a
    no-op at the DB layer instead of a double debit."""
    delta = Decimal(str(amount))
    if delta == 0:
        raise ValueError("adjustment amount cannot be zero")
    return await _apply(
        wallet_id=wallet_id,
        scope="master",
        type_="adjustment",
        delta=delta,
        actor_user_id=actor_user_id,
        stripe_payment_intent_id=idempotency_key,
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


class TransferBelowFloor(Exception):
    """Source wallet would breach its floor (section: 0; master: credit floor)."""


async def transfer_between_wallets(
    *,
    from_wallet_id: str,
    to_wallet_id: str,
    amount: _Numeric,
    actor_user_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    notes: Optional[str] = None,
    source_floor: Optional[_Numeric] = None,
) -> Dict[str, Any]:
    """Atomic two-wallet allocation via corporate_wallet_transfer (migration 227).

    The RPC derives the source floor server-side (a SECTION source is always
    floored at 0 — Q9 as a DB invariant; a MASTER source may be tightened but
    never loosened below its own soft_negative_floor), locks both rows in id
    order, and writes paired 'allocation' ledger legs idempotent on
    '<key>:out'/'<key>:in'.

    Reviewer-flagged race: two concurrent FIRST attempts with the same
    idempotency key can both pass the RPC's pre-lock ledger check; the loser
    then hits the ledger's unique index and its transaction aborts. That is
    fail-safe (no double-move) but not idempotent-transparent — so on a
    unique-violation-shaped error we re-query the ledger by key and return
    the winner's legs instead of surfacing a 500 to a retrying client.
    """
    delta = Decimal(str(amount))
    if delta <= 0:
        raise ValueError("transfer amount must be positive")

    params = {
        "p_from_wallet_id": from_wallet_id,
        "p_to_wallet_id": to_wallet_id,
        "p_amount": _money_str(delta),
        "p_actor_user_id": actor_user_id,
        "p_idempotency_key": idempotency_key,
        "p_notes": notes,
        "p_source_floor": _money_str(source_floor) if source_floor is not None else None,
    }

    def _fn():
        return supabase.rpc("corporate_wallet_transfer", params).execute()

    try:
        resp = await run_sync(_fn)
    except Exception as e:  # noqa: BLE001 — inspected and re-raised below
        # run_sync wraps DB errors (DatabaseError/DuplicateRecordError) whose
        # str() is a generic sentinel — the real message lives in
        # details['original']; db_error_text joins all layers.
        msg = db_error_text(e)
        if "wallet_below_floor" in msg:
            raise TransferBelowFloor(msg) from e
        is_duplicate = isinstance(e, DuplicateRecordError) or "duplicate" in msg or "23505" in msg or "unique" in msg
        if idempotency_key and is_duplicate:
            # Concurrent same-key first-attempt lost the race — the winner's
            # legs are in the ledger; return them as the idempotent result.
            replay = await _lookup_transfer_by_key(idempotency_key)
            if replay:
                return replay
        raise

    rows = getattr(resp, "data", None) or []
    if not rows:
        raise RuntimeError("wallet transfer RPC returned no row")
    return rows[0]


async def _lookup_transfer_by_key(idempotency_key: str) -> Optional[Dict[str, Any]]:
    def _fn():
        return (
            supabase.table("corporate_wallet_transactions")
            .select("id, balance_after, stripe_payment_intent_id")
            .in_(
                "stripe_payment_intent_id",
                [f"{idempotency_key}:out", f"{idempotency_key}:in"],
            )
            .execute()
        )

    resp = await run_sync(_fn)
    rows = getattr(resp, "data", None) or []
    if not rows:
        return None
    out = next((r for r in rows if str(r.get("stripe_payment_intent_id", "")).endswith(":out")), None)
    inn = next((r for r in rows if str(r.get("stripe_payment_intent_id", "")).endswith(":in")), None)
    return {
        "out_txn_id": (out or {}).get("id"),
        "in_txn_id": (inn or {}).get("id"),
        "from_balance": (out or {}).get("balance_after"),
        "to_balance": (inn or {}).get("balance_after"),
    }


async def apply_section_ride_debit(
    *,
    wallet_id: str,
    section_id: str,
    amount: _Numeric,
    ride_id: str,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Debit a SECTION wallet for a ride leg (M5.5 settlement chain tier 2).

    Floor 0 (Q9): the RPC enforces it server-side for 'section:%' scopes —
    a race that would push the department negative raises wallet_below_floor
    and the caller falls through to the master tier instead."""
    delta = Decimal(str(amount))
    if delta <= 0:
        raise ValueError("section debit must be positive")
    return await _apply(
        wallet_id=wallet_id,
        scope=f"section:{section_id}",
        type_="ride_debit",
        delta=-delta,
        ride_id=ride_id,
        actor_user_id=actor_user_id,
        # Deterministic per-leg idempotency: a settlement retry re-running
        # this leg is a DB-level no-op (money-audit blocker fix).
        stripe_payment_intent_id=f"ride:{ride_id}:section",
        notes=notes,
        floor=Decimal("0"),
    )


async def refund_corporate_ride_legs(
    *,
    ride_id: str,
    payment_source: Dict[str, Any],
    company_id: str,
    actor_user_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Reverse a corporate ride settlement PER LEG (Q13): each portion goes
    back to the wallet that paid — allowance leg to the allowance, section
    leg to the section wallet, master leg to the master wallet.

    ``payment_source`` is the ride's ride_payment_sources row (the frozen
    settlement attribution). Legs are reversed independently; a failed leg
    raises after the earlier legs committed — caller logs and retries, the
    ledger stays consistent (each leg is idempotent-safe to re-run only via
    caller-level bookkeeping; this primitive is for staff/refund flows that
    already track completion).
    """
    try:
        from ..db_supabase import get_master_wallet, get_member_allowance, get_section_wallet  # type: ignore
        from . import corporate_allowance_service  # type: ignore
    except ImportError:
        from db_supabase import get_master_wallet, get_member_allowance, get_section_wallet  # type: ignore
        from services import corporate_allowance_service  # type: ignore

    reversed_legs: Dict[str, Any] = {"allowance": None, "section": None, "master": None}
    note = notes or f"per-leg refund ride:{ride_id}"

    allowance_amt = Decimal(str(payment_source.get("allowance_debit_amount") or 0))
    section_amt = Decimal(str(payment_source.get("section_debit_amount") or 0))
    master_amt = Decimal(str(payment_source.get("master_fallback_amount") or 0))

    master = await get_master_wallet(company_id) or {}

    if allowance_amt > 0 and payment_source.get("member_id") and master.get("id"):
        allowance = await get_member_allowance(payment_source["member_id"]) or {}
        if allowance.get("id"):
            reversed_legs["allowance"] = await corporate_allowance_service.apply_grant(
                wallet_id=master["id"],
                allowance_id=allowance["id"],
                member_id=payment_source["member_id"],
                # Decimal-safe string — never float on money (audit blocker).
                amount=_money_str(allowance_amt),
                notes=f"{note}:allowance",
            )

    if section_amt > 0 and payment_source.get("section_id"):
        section_wallet = await get_section_wallet(company_id=company_id, section_id=payment_source["section_id"])
        if section_wallet and section_wallet.get("id"):
            reversed_legs["section"] = await _apply(
                wallet_id=section_wallet["id"],
                scope=f"section:{payment_source['section_id']}",
                type_="refund",
                delta=section_amt,
                ride_id=ride_id,
                actor_user_id=actor_user_id,
                stripe_payment_intent_id=f"ride:{ride_id}:section_refund",
                notes=f"{note}:section",
            )

    if master_amt > 0 and master.get("id"):
        reversed_legs["master"] = await _apply(
            wallet_id=master["id"],
            scope="master",
            type_="refund",
            delta=master_amt,
            ride_id=ride_id,
            actor_user_id=actor_user_id,
            stripe_payment_intent_id=f"ride:{ride_id}:master_refund",
            notes=f"{note}:master",
        )

    return reversed_legs
