"""Referral reward payout loop (rider + driver).

Pays a referrer once their referee reaches the ride threshold WITHIN the
completion deadline (window_days from referral_terms; 0 = no deadline):
  - driver referral: referrer earns $10 once the referee (a driver) completes
    REFERRAL_RIDES_REQUIRED (10) rides.
  - rider referral: referrer AND referee each earn $5 once the referee completes
    RIDER_REFERRAL_RIDES_REQUIRED (1) ride ("first-ride bonus, both sides").

If the deadline passes before the threshold is met, the referral is recorded as
'expired' (rewards 0) and never paid — this forces referred drivers to complete
their rides promptly.

Money safety:
  - Reward amounts are admin-controlled per service area (see referral_terms).
    A per-area reward of 0 means that side is not paid — there is no global
    on/off flag; "don't pay" is expressed by setting the amount to 0.
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
    from ..utils.error_handling import DuplicateRecordError  # type: ignore
    from ..utils.referral_terms import (  # type: ignore
        area_id_for_rider,
        resolve_referral_terms,
    )
except ImportError:
    import db_supabase  # type: ignore
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


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp to a tz-aware UTC datetime (assume UTC if naive)."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def referral_payout_loop() -> None:
    """Every 5 min, pay any newly-qualified referral rewards. Replay-safe."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.error("referral_payout tick failed", exc_info=True)
        await asyncio.sleep(INTERVAL_SECONDS)


async def _tick() -> None:
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

    # Never pay retroactively for rides that predate the referral. (Legacy rows
    # with no referral_applied_at fall back to a lifetime count.)
    applied_at = referee.get("referral_applied_at")

    # Resolve the referee's service area + the ride base filter. The ride
    # threshold AND the completion deadline are both per-area, so resolve terms
    # before counting. area_id == None → resolve_referral_terms falls back to the
    # global default.
    if is_rider:
        area_id = await area_id_for_rider(referee_id, applied_at)
        ride_filter: dict = {"rider_id": referee_id, "status": "completed"}
    else:
        ref_as_driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": referee_id}, limit=1)
        )
        if not ref_as_driver:
            return
        area_id = ref_as_driver.get("service_area_id")
        ride_filter = {"driver_id": ref_as_driver["id"], "status": "completed"}

    t = await resolve_referral_terms(area_id, kind)

    # Completion deadline: the referee must reach the ride threshold within
    # window_days of referral_applied_at, or the referral expires unpaid. This is
    # what forces referred drivers to complete their rides promptly. window_days
    # <= 0 (or no applied_at, e.g. a legacy referral) → no deadline, preserving
    # the original "count forever" behaviour.
    window_days = int(t.get("window_days") or 0)
    deadline = None
    if applied_at and window_days > 0:
        deadline = _parse_iso(applied_at) + timedelta(days=window_days)

    # Count completed rides from referral_applied_at, capped at the deadline so a
    # ride taken after the window closed can never qualify the referral. The
    # filter translator honours only one operator per field, so AND the two
    # bounds via $and (see repositories/_base._apply_filters).
    bounds = []
    if applied_at:
        bounds.append({"created_at": {"$gte": applied_at}})
    if deadline is not None:
        bounds.append({"created_at": {"$lte": deadline.isoformat()}})
    count_filter = dict(ride_filter)
    if bounds:
        count_filter["$and"] = bounds
    completed = await db_supabase.count_documents("rides", count_filter)

    if completed < t["rides"]:
        # Threshold not met. If the window has closed, the referral has expired:
        # record a terminal 'expired' claim (rewards 0) so it's never paid and the
        # referee is excluded from every future tick by the same unique claim.
        # Until the deadline passes, just wait for the next tick.
        if deadline is not None and datetime.now(timezone.utc) > deadline:
            await _record_expiry(referee_id, referrer_user_id, kind, area_id)
        return

    # resolve_referral_terms already returns Decimal; _d re-quantises defensively.
    referrer_reward = _d(t["referrer"])
    referee_reward = _d(t["referee"])
    now_iso = datetime.now(timezone.utc).isoformat()

    # Admin set BOTH sides to 0 for this area → there is nothing to pay. Don't
    # burn the UNIQUE(referee_user_id) claim on a $0 row: leaving the referee
    # unclaimed means that if an admin later raises the area's reward above 0,
    # this referral becomes payable again on a future tick. (A one-sided 0 still
    # claims-and-pays the non-zero side below.)
    if referrer_reward <= 0 and referee_reward <= 0:
        return

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

    # Credit each side only when its admin-configured reward is > 0 (a per-area
    # reward of 0 means "don't pay this side"). _credit routes DRIVER rewards to
    # the payable driver_bonuses ledger (append-only insert) and RIDER rewards to
    # the wallet (self-atomic: reverses its own increment if the ledger write
    # fails, so money is never left unrecorded).
    # On ANY credit failure we mark the claim
    # 'failed' and stop: we deliberately do NOT delete/retry, because the claim
    # row staying in place is exactly what prevents a re-claim and a double
    # credit on the next tick. 'failed' rows surface for manual reconciliation.
    #
    # Which side(s) actually got credited is tracked in memory and written as
    # part of the SAME row update that records the outcome (paid OR failed) —
    # never in a separate write between the wallet credit and the status update.
    # That closes the window where an intermediate timestamp write could fail and
    # leave a referrer-paid row looking like "neither paid". A 'failed' row then
    # reads: referrer_credited_at set + referee_credited_at NULL → pay only the
    # referee; both NULL → neither side paid.
    meta = {"kind": kind, "referee_id": referee_id, "referrer_user_id": referrer_user_id}
    referrer_paid = False
    referee_paid = False
    try:
        if referrer_reward > 0:
            await _credit(referrer_user_id, referrer_reward, kind, referee_id, "referral_reward", meta)
            referrer_paid = True
        if referee_reward > 0:
            await _credit(referee_id, referee_reward, kind, referee_id, "referral_bonus", meta)
            referee_paid = True
    except Exception:
        logger.error(
            "referral_payout: credit failed — marking claim 'failed' for manual reconciliation",
            exc_info=True,
            extra={**meta, "referrer_paid": referrer_paid, "referee_paid": referee_paid},
        )
        await db_supabase.update_one(
            "referral_payouts",
            {"referee_user_id": referee_id},
            {"$set": _credit_marks("failed", referrer_paid, referee_paid)},
        )
        return

    await db_supabase.update_one(
        "referral_payouts",
        {"referee_user_id": referee_id},
        {"$set": _credit_marks("paid", referrer_paid, referee_paid)},
    )
    logger.info(f"referral_payout: paid {kind} referral reward for referee {referee_id}")


async def _record_expiry(referee_id: str, referrer_user_id: str, kind: str, area_id) -> None:
    """Record a terminal, never-paid 'expired' claim for a referee who missed the
    completion deadline. Reuses the UNIQUE(referee_user_id) claim (rewards 0) so
    the referee is excluded from every future tick and is never paid. A duplicate
    means another replica/tick already finalised this referee — expected, skip."""
    try:
        await db_supabase.insert_one(
            "referral_payouts",
            {
                "referee_user_id": referee_id,
                "referrer_user_id": referrer_user_id,
                "kind": kind,
                "service_area_id": area_id,
                "referrer_reward": _f(_d(0)),
                "referee_reward": _f(_d(0)),
                "status": "expired",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.info(
            "referral_payout: referral expired unpaid (completion deadline missed)",
            extra={"referee_id": referee_id, "kind": kind},
        )
    except DuplicateRecordError:
        logger.info(f"referral_payout: claim already exists for referee {referee_id} — skipping expiry")


def _credit_marks(status: str, referrer_paid: bool, referee_paid: bool) -> dict:
    """Build the referral_payouts update recording the outcome + which sides were
    actually credited. Timestamps come from in-memory flags so a row never
    reports a paid side as un-paid."""
    now_iso = datetime.now(timezone.utc).isoformat()
    marks: dict = {"status": status}
    if status == "paid":
        marks["paid_at"] = now_iso
    if referrer_paid:
        marks["referrer_credited_at"] = now_iso
    if referee_paid:
        marks["referee_credited_at"] = now_iso
    return marks


class ReferralClaimNotFound(Exception):
    """No 'failed' referral_payouts claim exists for the given referee (or it was
    concurrently taken). Callers map this to a 404 / skip."""


def _merge_marks(row: dict, status: str, referrer_paid_now: bool, referee_paid_now: bool) -> dict:
    """Build the referral_payouts update for a RE-credit pass. Unlike _credit_marks
    (which stamps from scratch), this preserves any credited-at timestamp already on
    the row — it never overwrites a prior credit's time — and only stamps a side that
    was credited in THIS pass."""
    now_iso = datetime.now(timezone.utc).isoformat()
    marks: dict = {"status": status}
    if status == "paid":
        marks["paid_at"] = now_iso
    if referrer_paid_now and not row.get("referrer_credited_at"):
        marks["referrer_credited_at"] = now_iso
    if referee_paid_now and not row.get("referee_credited_at"):
        marks["referee_credited_at"] = now_iso
    return marks


async def recredit_failed_claim(referee_user_id: str) -> dict:
    """Re-credit ONLY the not-yet-credited side(s) of a FAILED referral claim and
    finalise it 'paid'. Side-aware via referrer_credited_at / referee_credited_at,
    so a referrer who was already paid (only the referee credit had failed — e.g.
    the pre-migration-199 'referral_bonus' constraint bug, where the referrer's
    'referral_reward' lands but the referee's 'referral_bonus' is rejected) is
    NEVER double-paid. The old delete-and-let-the-loop-re-pay path re-credited BOTH.

    Credits the amounts SNAPSHOTTED on the claim row, so a later admin change to the
    area's rewards can't retro-alter this payout, and a deadline that has since
    passed can't strand a referee who legitimately qualified at claim time.

    Atomic: claims the row failed -> processing first; a concurrent re-credit (or a
    double-click) that already took it raises ReferralClaimNotFound. On a credit
    failure the row is returned to 'failed' (recording whatever DID land this pass)
    and the error re-raised — the same no-double-credit contract as the payout loop.

    Returns {id, referee_user_id, kind, credited: [...], status}.
    Raises ReferralClaimNotFound when there is no failed claim to re-credit.

    Do NOT call this for a claim whose logs show "ledger write AND its reversal
    failed" — that row already moved money while its credited-at stayed NULL, so it
    looks owed and would double-credit. Those need manual reconciliation.
    """
    row = await db_supabase.find_one("referral_payouts", {"referee_user_id": referee_user_id, "status": "failed"})
    if not row:
        raise ReferralClaimNotFound(referee_user_id)

    # Atomic claim: only one caller can move this row failed -> processing.
    claimed = await db_supabase.update_one(
        "referral_payouts",
        {"id": row["id"], "status": "failed"},
        {"$set": {"status": "processing"}},
    )
    if not claimed:
        raise ReferralClaimNotFound(referee_user_id)

    kind = row.get("kind") or "rider"
    referrer_user_id = row.get("referrer_user_id")
    referrer_reward = _d(row.get("referrer_reward"))
    referee_reward = _d(row.get("referee_reward"))

    # A side is owed only if its snapshotted reward is > 0 AND it was not already
    # credited on the original (partial) attempt.
    referrer_owed = referrer_reward > 0 and not row.get("referrer_credited_at")
    referee_owed = referee_reward > 0 and not row.get("referee_credited_at")

    meta = {"kind": kind, "referee_id": referee_user_id, "referrer_user_id": referrer_user_id, "requeued": True}
    referrer_paid_now = False
    referee_paid_now = False
    try:
        if referrer_owed and referrer_user_id:
            await _credit(referrer_user_id, referrer_reward, kind, referee_user_id, "referral_reward", meta)
            referrer_paid_now = True
        if referee_owed:
            await _credit(referee_user_id, referee_reward, kind, referee_user_id, "referral_bonus", meta)
            referee_paid_now = True
    except Exception:
        logger.error(
            "referral_payout: re-credit failed — returning claim to 'failed' for manual reconciliation",
            exc_info=True,
            extra={**meta, "referrer_paid_now": referrer_paid_now, "referee_paid_now": referee_paid_now},
        )
        await db_supabase.update_one(
            "referral_payouts",
            {"id": row["id"]},
            {"$set": _merge_marks(row, "failed", referrer_paid_now, referee_paid_now)},
        )
        raise

    await db_supabase.update_one(
        "referral_payouts",
        {"id": row["id"]},
        {"$set": _merge_marks(row, "paid", referrer_paid_now, referee_paid_now)},
    )
    credited = [s for s, ok in (("referrer", referrer_paid_now), ("referee", referee_paid_now)) if ok]
    logger.info("referral_payout: re-credited failed referral claim", extra={**meta, "credited": credited})
    return {"id": row["id"], "referee_user_id": referee_user_id, "kind": kind, "credited": credited, "status": "paid"}


async def _credit(user_id: str, amount: Decimal, kind: str, reference_id: str, txn_type: str, metadata: dict) -> None:
    """Credit a referral reward. Decimal-only.

    DRIVER referrals pay a driver — record a PAYABLE driver_bonuses row (folds
    into payable_balance + the Stripe payout), not the rider wallet the driver
    app can't see. RIDER referrals keep the wallet credit + immutable ledger
    entry; if that ledger write fails after the balance moved, reverse the
    increment so money is never left unrecorded, then re-raise.
    """
    if kind == "driver":
        # driver_bonuses is append-only and idempotent at the referral_payouts
        # claim level (one _credit call per side, no retry on failure), so no
        # source_id dedup is needed here. id defaults to gen_random_uuid().
        driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": user_id}, limit=1)
        )
        if not driver:
            raise RuntimeError(f"referral_payout: no driver row for user {user_id} — cannot credit referral bonus")
        await db_supabase.insert_one(
            "driver_bonuses",
            {
                "driver_id": driver["id"],
                "user_id": user_id,
                "amount": _f(amount),
                "kind": "referral",
                "description": f"{kind.capitalize()} referral reward",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return

    # Rider referral → wallet credit + immutable ledger entry.
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
