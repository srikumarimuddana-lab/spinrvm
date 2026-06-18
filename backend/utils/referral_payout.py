"""Referral reward payout loop (rider + driver).

Pays a referrer once their referee reaches the ride threshold:
  - driver referral: referrer earns $10 once the referee (a driver) completes
    REFERRAL_RIDES_REQUIRED (10) rides.
  - rider referral: referrer AND referee each earn $5 once the referee completes
    RIDER_REFERRAL_RIDES_REQUIRED (1) ride ("first-ride bonus, both sides").

Money safety:
  - OFF by default (settings.REFERRAL_PAYOUTS_ENABLED) — enable after staging.
  - Idempotent: a payout is claimed by INSERTing a referral_payouts row whose
    UNIQUE(referee_user_id) makes a duplicate/concurrent claim fail, so each
    referral is paid at most once even across replicas and retries.
  - Decimal-only money; credits go through the same wallet RPC + immutable
    ledger entry used by the quest-reward payout.
  - A credit failure marks the row 'failed' (NO auto-retry) so it surfaces for
    manual reconciliation rather than risking a double-credit race.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

try:
    from .. import db_supabase  # type: ignore
    from ..core.config import settings  # type: ignore
except ImportError:
    import db_supabase  # type: ignore
    from core.config import settings  # type: ignore

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300  # every 5 minutes
_TWO = Decimal("0.01")


def _d(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_TWO, rounding=ROUND_HALF_UP)


def _f(v: Decimal) -> str:
    return str(v)


def _terms() -> dict:
    """Reward terms, imported lazily to avoid circular imports at module load."""
    try:
        from ..routes.drivers import REFERRAL_REWARD_AMOUNT, REFERRAL_RIDES_REQUIRED  # type: ignore
        from ..routes.users import (  # type: ignore
            RIDER_REFEREE_REWARD,
            RIDER_REFERRAL_RIDES_REQUIRED,
            RIDER_REFERRER_REWARD,
        )
    except ImportError:
        from routes.drivers import REFERRAL_REWARD_AMOUNT, REFERRAL_RIDES_REQUIRED  # type: ignore
        from routes.users import (  # type: ignore
            RIDER_REFEREE_REWARD,
            RIDER_REFERRAL_RIDES_REQUIRED,
            RIDER_REFERRER_REWARD,
        )
    return {
        "driver": {"rides": REFERRAL_RIDES_REQUIRED, "referrer": REFERRAL_REWARD_AMOUNT, "referee": 0},
        "rider": {"rides": RIDER_REFERRAL_RIDES_REQUIRED, "referrer": RIDER_REFERRER_REWARD, "referee": RIDER_REFEREE_REWARD},
    }


async def referral_payout_loop() -> None:
    """Every 5 min, pay any newly-qualified referral rewards. Replay-safe."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.error("referral_payout tick failed", exc_info=True)
        await asyncio.sleep(INTERVAL_SECONDS)


async def _tick() -> None:
    if not settings.REFERRAL_PAYOUTS_ENABLED:
        return
    terms = _terms()

    # Reclaim crash-stranded claims: a row stuck 'processing' past the grace
    # window means a replica died between claiming and finalising. We can't tell
    # whether the credit landed, so mark it 'failed' for manual review rather
    # than risk a double-credit by auto-retrying.
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    try:
        stale = await db_supabase.get_rows(
            "referral_payouts",
            {"status": "processing", "created_at": {"$lt": cutoff}},
            columns="id,referee_user_id",
            limit=500,
        )
        for s in stale:
            await db_supabase.update_one(
                "referral_payouts", {"id": s["id"], "status": "processing"}, {"$set": {"status": "failed"}}
            )
            logger.error(
                "referral_payout: stale 'processing' claim marked 'failed' for manual review",
                extra={"referee_id": s.get("referee_user_id")},
            )
    except Exception:
        logger.error("referral_payout: stale-claim sweep failed", exc_info=True)

    # Referees we've already claimed/paid/failed — skip them. (Transient credit
    # failures DELETE their claim row, so those are absent here and get retried.)
    existing = await db_supabase.get_rows("referral_payouts", {}, columns="referee_user_id", limit=20000)
    done = {r["referee_user_id"] for r in existing}

    # Referred users. A not-null filter isn't supported by the query translator,
    # so project minimal columns and filter in memory (bounded; fine for the
    # current fleet — move to an RPC/rollup if the user table grows large).
    users = await db_supabase.get_rows("users", {}, columns="id,referral_code_used,referred_by", limit=10000)
    for u in users:
        code = u.get("referral_code_used")
        if not code or u["id"] in done:
            continue
        try:
            await _process_one(u, code, terms)
        except Exception:
            logger.error("referral_payout: processing referee failed", exc_info=True, extra={"referee_id": u["id"]})


async def _process_one(referee: dict, code: str, terms: dict) -> None:
    referee_id = referee["id"]
    is_rider = str(code).upper().startswith("RIDE")
    kind = "rider" if is_rider else "driver"
    t = terms[kind]

    # Resolve the referrer's USER id (wallets are per user).
    referrer_user_id = None
    if is_rider:
        # rider referral stores referred_by = referrer's user id
        referrer_user_id = referee.get("referred_by")
    else:
        # driver referral stores referred_by = referrer's DRIVER id
        ref_driver_id = referee.get("referred_by")
        if ref_driver_id:
            ref_driver = await db_supabase.get_driver_by_id(ref_driver_id)
            referrer_user_id = (ref_driver or {}).get("user_id")
    if not referrer_user_id or referrer_user_id == referee_id:
        return  # unresolved or self — nothing to pay

    # Has the referee reached the ride threshold?
    if is_rider:
        completed = await db_supabase.count_documents("rides", {"rider_id": referee_id, "status": "completed"})
    else:
        ref_as_driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": referee_id}, limit=1)
        )
        if not ref_as_driver:
            return
        completed = await db_supabase.count_documents("rides", {"driver_id": ref_as_driver["id"], "status": "completed"})
    if completed < t["rides"]:
        return

    referrer_reward = _d(t["referrer"])
    referee_reward = _d(t["referee"])
    now_iso = datetime.now(timezone.utc).isoformat()

    # Atomic claim: the UNIQUE(referee_user_id) means only one replica/tick wins
    # this INSERT; a duplicate raises and we skip (already being handled).
    try:
        await db_supabase.insert_one(
            "referral_payouts",
            {
                "referee_user_id": referee_id,
                "referrer_user_id": referrer_user_id,
                "kind": kind,
                "referrer_reward": _f(referrer_reward),
                "referee_reward": _f(referee_reward),
                "status": "processing",
                "created_at": now_iso,
            },
        )
    except Exception as e:
        logger.info(f"referral_payout: claim skipped for referee {referee_id} (already claimed): {e}")
        return

    # Credit the referrer first, then the referee (rider only). Make a partial
    # failure recoverable: if the second credit fails, reverse the first with a
    # compensating debit and DELETE the claim so the next tick retries cleanly.
    # Only if the compensation itself fails do we leave the row 'failed' for
    # manual reconciliation — never a silent half-applied state.
    meta = {"kind": kind, "referee_id": referee_id, "referrer_user_id": referrer_user_id}
    referrer_paid = False
    try:
        await _credit(referrer_user_id, referrer_reward, kind, referee_id, "referral_reward", meta)
        referrer_paid = True
        if referee_reward > 0:
            await _credit(referee_id, referee_reward, kind, referee_id, "referral_bonus", meta)
    except Exception:
        logger.error(
            "referral_payout: credit failed — compensating and releasing claim",
            exc_info=True,
            extra=meta,
        )
        compensated = True
        if referrer_paid:
            try:
                await _credit(referrer_user_id, -referrer_reward, kind, referee_id, "referral_reversal", meta)
            except Exception:
                compensated = False
                logger.error(
                    "referral_payout: compensation debit FAILED — manual reconciliation required",
                    exc_info=True,
                    extra=meta,
                )
        if compensated:
            # Release the claim so a future tick retries from a clean slate.
            try:
                await db_supabase.delete_one("referral_payouts", {"referee_user_id": referee_id})
            except Exception:
                await db_supabase.update_one(
                    "referral_payouts", {"referee_user_id": referee_id}, {"$set": {"status": "failed"}}
                )
        else:
            await db_supabase.update_one(
                "referral_payouts", {"referee_user_id": referee_id}, {"$set": {"status": "failed"}}
            )
        return

    await db_supabase.update_one(
        "referral_payouts",
        {"referee_user_id": referee_id},
        {"$set": {"status": "paid", "paid_at": datetime.now(timezone.utc).isoformat()}},
    )
    logger.info(f"referral_payout: paid {kind} referral reward for referee {referee_id}")


async def _credit(user_id: str, amount: Decimal, kind: str, reference_id: str, txn_type: str, metadata: dict) -> None:
    """Credit (or, with a negative amount, reverse) a wallet and write the
    immutable ledger entry. Decimal-only."""
    try:
        from ..routes.wallet import _record_transaction, get_or_create_wallet  # type: ignore
    except ImportError:
        from routes.wallet import _record_transaction, get_or_create_wallet  # type: ignore

    wallet = await get_or_create_wallet(user_id)
    new_balance = await db_supabase.wallet_increment_balance(wallet["id"], amount)
    await _record_transaction(
        wallet_id=wallet["id"],
        user_id=user_id,
        txn_type=txn_type,
        amount=_f(amount),
        balance_after=_f(new_balance),
        reference_id=reference_id,
        description=f"{kind.capitalize()} referral reward",
        metadata=metadata,
    )
