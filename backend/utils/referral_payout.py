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
    from ..utils.error_handling import DuplicateRecordError  # type: ignore
    from ..utils.referral_terms import (  # type: ignore
        area_id_for_rider,
        resolve_referral_terms,
    )
except ImportError:
    import db_supabase  # type: ignore
    from core.config import settings  # type: ignore
    from utils.error_handling import DuplicateRecordError  # type: ignore
    from utils.referral_terms import (  # type: ignore
        area_id_for_rider,
        resolve_referral_terms,
    )

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 300  # every 5 minutes
_TWO = Decimal("0.01")


def _d(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_TWO, rounding=ROUND_HALF_UP)


def _f(v: Decimal) -> str:
    return str(v)


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

    # Referees we've already claimed/paid/failed — skip them. ('failed' rows are
    # NOT deleted: they stay in the table (and in this `done` set) to block a
    # re-claim and the double-credit it would risk; they need manual
    # reconciliation, not auto-retry — see the credit-failure block below.)
    existing = await db_supabase.get_rows("referral_payouts", {}, columns="referee_user_id", limit=20000)
    done = {r["referee_user_id"] for r in existing}

    # Referred users. A not-null filter isn't supported by the query translator,
    # so project minimal columns and filter in memory (bounded; fine for the
    # current fleet — move to an RPC/rollup if the user table grows large).
    users = await db_supabase.get_rows(
        "users", {}, columns="id,referral_code_used,referred_by,referral_applied_at", limit=10000
    )
    for u in users:
        code = u.get("referral_code_used")
        if not code or u["id"] in done:
            continue
        try:
            await _process_one(u, code)
        except Exception:
            logger.error("referral_payout: processing referee failed", exc_info=True, extra={"referee_id": u["id"]})


async def _process_one(referee: dict, code: str) -> None:
    referee_id = referee["id"]
    is_rider = str(code).upper().startswith("RIDE")
    kind = "rider" if is_rider else "driver"

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

    # Count only rides completed AFTER the referral was applied — never pay
    # retroactively for rides that predate the referral. (Legacy rows with no
    # referral_applied_at fall back to lifetime count.)
    applied_at = referee.get("referral_applied_at")
    since = {"created_at": {"$gte": applied_at}} if applied_at else {}

    # Resolve the referee's service area and its per-area reward terms. The ride
    # threshold is itself per-area, so this must precede the threshold check.
    # area_id == None → resolve_referral_terms falls back to the global default.
    if is_rider:
        area_id = await area_id_for_rider(referee_id, applied_at)
        completed = await db_supabase.count_documents("rides", {"rider_id": referee_id, "status": "completed", **since})
    else:
        ref_as_driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": referee_id}, limit=1)
        )
        if not ref_as_driver:
            return
        area_id = ref_as_driver.get("service_area_id")
        completed = await db_supabase.count_documents(
            "rides", {"driver_id": ref_as_driver["id"], "status": "completed", **since}
        )

    t = await resolve_referral_terms(area_id, kind)
    if completed < t["rides"]:
        return

    # resolve_referral_terms already returns Decimal; _d re-quantises defensively.
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
                # Snapshot the area whose terms were applied (NULL = global
                # default) so later admin edits never retro-change this payout.
                "service_area_id": area_id,
                "referrer_reward": _f(referrer_reward),
                "referee_reward": _f(referee_reward),
                "status": "processing",
                "created_at": now_iso,
            },
        )
    except DuplicateRecordError:
        # Another replica/tick already claimed this referee — expected, skip.
        logger.info(f"referral_payout: claim already exists for referee {referee_id} — skipping")
        return
    # Any other DB error (table missing, RLS/schema, connectivity) propagates to
    # the outer handler in _tick, which logs it as an error rather than silently
    # treating it as 'already claimed'.

    # Credit the referrer, then the referee (rider only). Each _credit is
    # self-atomic — it reverses its own increment if the ledger write fails, so
    # money is never left unrecorded. On ANY credit failure we mark the claim
    # 'failed' and stop: we deliberately do NOT delete/retry, because the claim
    # row staying in place is exactly what prevents a re-claim and a double
    # credit on the next tick. 'failed' rows surface for manual reconciliation.
    # Each side's credit timestamp is persisted IMMEDIATELY after that credit
    # succeeds (before attempting the next side), so a later failure/crash leaves
    # a durable record of exactly which wallets were credited. Reconciliation of
    # a 'failed' row then reads: referrer_credited_at set + referee_credited_at
    # NULL → pay only the referee; both NULL → neither paid.
    meta = {"kind": kind, "referee_id": referee_id, "referrer_user_id": referrer_user_id}
    try:
        await _credit(referrer_user_id, referrer_reward, kind, referee_id, "referral_reward", meta)
        await db_supabase.update_one(
            "referral_payouts",
            {"referee_user_id": referee_id},
            {"$set": {"referrer_credited_at": datetime.now(timezone.utc).isoformat()}},
        )
        if referee_reward > 0:
            await _credit(referee_id, referee_reward, kind, referee_id, "referral_bonus", meta)
            await db_supabase.update_one(
                "referral_payouts",
                {"referee_user_id": referee_id},
                {"$set": {"referee_credited_at": datetime.now(timezone.utc).isoformat()}},
            )
    except Exception:
        logger.error(
            "referral_payout: credit failed — marking claim 'failed' for manual reconciliation",
            exc_info=True,
            extra=meta,
        )
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
    """Credit a wallet and write the immutable ledger entry — atomically.

    If the ledger write fails after the balance already moved, reverse the
    increment so money is never left without a matching ledger entry, then
    re-raise. (Same compensate-on-ledger-failure pattern as the quest-reward
    payout.) Decimal-only.
    """
    try:
        from ..routes.wallet import _record_transaction, get_or_create_wallet  # type: ignore
    except ImportError:
        from routes.wallet import _record_transaction, get_or_create_wallet  # type: ignore

    wallet = await get_or_create_wallet(user_id)
    new_balance = await db_supabase.wallet_increment_balance(wallet["id"], amount)
    try:
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
    except Exception:
        # Ledger write failed after the balance moved — reverse the increment so
        # no money is left unrecorded, then surface the error to the caller.
        try:
            await db_supabase.wallet_increment_balance(wallet["id"], -amount)
        except Exception:
            logger.error(
                "referral_payout: ledger write AND its reversal failed — wallet %s left with an "
                "unrecorded %s credit; manual reconciliation required",
                wallet["id"],
                _f(amount),
                exc_info=True,
            )
        raise
