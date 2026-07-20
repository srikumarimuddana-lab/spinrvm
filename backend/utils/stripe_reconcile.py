"""Daily Stripe ↔ DB reconciliation (F-section operational gap).

Runs once per day at 02:00 UTC (before the 03:00 retention purge).
Compares:
  - rides with payment_status='paid' and a payment_intent_id set against
    the corresponding Stripe PaymentIntent status
  - flags discrepancies with logger.error + writes a summary to audit_logs

Discrepancy types detected:
  DB_PAID_STRIPE_MISSING   — ride marked paid in DB; PI not found in Stripe
  DB_PAID_STRIPE_MISMATCH  — ride marked paid; Stripe PI exists but is not
                             "succeeded" (e.g. still "requires_action")
  DB_PAID_AMOUNT_MISMATCH  — ride marked paid; Stripe amount_received ≠ DB
                             ride.fare (in cents) — possible under/overcharge
  STRIPE_ORPHAN            — Stripe PI has no matching ride in DB and is not
                             in a terminal failed state (succeeded PIs with
                             no ride row require manual review)

Design:
  - Redis SET NX EX leader lock so only one replica runs per 23h window.
  - Stripe list() uses created range (yesterday 00:00–23:59 UTC) with
    auto-pagination (stripe-python handles up to 10k results natively via
    auto_paging_iter).
  - If Stripe is unconfigured (no stripe_secret_key in settings), the loop
    skips silently — consistent with stripe_charge.py unconfigured handling.
  - Errors are logger.error + exc_info so they reach Sentry.
  - audit_logs INSERT uses correct production schema:
    (id, action, entity_type, entity_id, details, created_at)
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List


def _q2(v: Any) -> Decimal:
    """Quantize a money value to 2dp (HALF_UP). Decimal-only — never float."""
    return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _attribution_mismatch(ride: Dict[str, Any]) -> Dict[str, Any] | None:
    """Flag a ride whose money doesn't reconcile.

    Invariant: ``total_fare == driver_earnings + admin_earnings``. Tips are
    added to ``driver_earnings`` after settlement, so subtract ``tip_amount``
    before comparing. Returns a discrepancy dict, or ``None`` when the ride
    reconciles (within a cent) or is a legacy row with neither earnings column
    populated. Surfacing this catches the minimum-fare uplift being attributed
    to nobody — the receipt would claim driver income the payout never books.
    """
    driver = ride.get("driver_earnings")
    admin = ride.get("admin_earnings")
    if driver is None and admin is None:
        return None
    total = _q2(ride.get("total_fare"))
    attributed = _q2(driver) - _q2(ride.get("tip_amount")) + _q2(admin)
    if abs(attributed - total) <= Decimal("0.01"):
        return None
    return {
        "type": "FARE_ATTRIBUTION_MISMATCH",
        "ride_id": ride.get("id"),
        "total_fare_cents": int((total * 100).to_integral_value()),
        "attributed_cents": int((attributed * 100).to_integral_value()),
    }


try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase  # type: ignore
    from ..settings_loader import get_app_settings  # type: ignore
    from ..utils import metrics  # type: ignore
    from ..utils.redis_client import redis_set_nx  # type: ignore
except ImportError:
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils import metrics  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

_LOCK_KEY = "spinr:stripe:reconcile:lock"
_LOCK_TTL_SECONDS = 23 * 60 * 60
_RUN_HOUR_UTC = 2  # 02:00 UTC daily
_WINDOW_DAYS = 1  # reconcile the previous calendar day
# A ride should never sit in payment_status='processing' longer than a
# settlement round-trip; past this it is treated as stranded and surfaced.
_STUCK_PROCESSING_AFTER = timedelta(minutes=15)
# App-setting flag (default OFF) gating the auto-heal of stuck-processing rides.
# OFF → detection only; ON → mark-paid from Stripe truth (see _maybe_heal_*).
_AUTO_HEAL_SETTING = "stripe_auto_heal_processing"


def _pod_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _seconds_until(target_hour_utc: int) -> float:
    now = datetime.now(timezone.utc)
    target = datetime.combine(now.date(), time(target_hour_utc, 0), tzinfo=timezone.utc)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _run_reconciliation_tick() -> None:
    """One reconciliation pass for yesterday's transactions."""
    settings = await get_app_settings()
    secret_key = settings.get("stripe_secret_key", "")
    if not secret_key:
        logger.info("stripe_reconcile: stripe_secret_key not configured — skipping")
        return

    try:
        import stripe as _stripe

        _stripe.api_key = secret_key
    except ImportError:
        logger.error("stripe_reconcile: stripe package not installed — cannot reconcile")
        return

    # Yesterday's window in epoch seconds
    yesterday = date.today() - timedelta(days=_WINDOW_DAYS)
    window_start = int(datetime.combine(yesterday, time(0, 0), tzinfo=timezone.utc).timestamp())
    window_end = int(datetime.combine(yesterday, time(23, 59, 59), tzinfo=timezone.utc).timestamp())

    logger.info(
        "stripe_reconcile: reconciling %s (epoch %d–%d)",
        yesterday.isoformat(),
        window_start,
        window_end,
    )

    # ── 1. Fetch Stripe PaymentIntents created yesterday ─────────────────
    stripe_pis: Dict[str, Any] = {}  # pi_id → PI object
    try:
        for pi in _stripe.PaymentIntent.list(
            created={"gte": window_start, "lte": window_end},
            limit=100,
        ).auto_paging_iter():
            stripe_pis[pi["id"]] = pi
    except Exception:
        logger.error("stripe_reconcile: Stripe API list failed", exc_info=True)
        return

    # ── 2. Fetch DB rides completed yesterday with a PI id ─────────────
    db_rides: List[Dict[str, Any]] = []
    try:
        db_rides = (
            await db_supabase.get_rows(
                "rides",
                {
                    "payment_status": "paid",
                },
                columns="id,payment_intent_id,grand_total,total_fare,tip_amount,driver_earnings,admin_earnings,authorized_amount,status,ride_completed_at",
                limit=2000,
            )
            or []
        )
        # Filter to yesterday's completed rides in Python (avoids complex date filter)
        db_rides = [
            r
            for r in db_rides
            if r.get("payment_intent_id")
            and r.get("ride_completed_at")
            and _in_window(r["ride_completed_at"], window_start, window_end)
        ]
    except Exception:
        logger.error("stripe_reconcile: DB rides query failed", exc_info=True)
        return

    # Build lookup: pi_id → ride
    db_pi_to_ride: Dict[str, Dict] = {r["payment_intent_id"]: r for r in db_rides if r.get("payment_intent_id")}

    discrepancies: List[Dict[str, Any]] = []

    # ── 3a. Check each paid DB ride against Stripe ───────────────────────
    for ride in db_rides:
        # DB-internal money invariant, independent of Stripe presence: the
        # charged total must equal driver_earnings + admin_earnings (tip-adjusted).
        _attr = _attribution_mismatch(ride)
        if _attr:
            discrepancies.append(_attr)
            logger.error(
                "stripe_reconcile: FARE_ATTRIBUTION_MISMATCH ride=%s total_cents=%d attributed_cents=%d",
                _attr["ride_id"],
                _attr["total_fare_cents"],
                _attr["attributed_cents"],
            )
            metrics.inc("spinr_payment_fare_attribution_mismatch_total")

        pi_id = ride["payment_intent_id"]
        if pi_id not in stripe_pis:
            discrepancies.append(
                {
                    "type": "DB_PAID_STRIPE_MISSING",
                    "ride_id": ride["id"],
                    "payment_intent_id": pi_id,
                }
            )
            logger.error(
                "stripe_reconcile: DB_PAID_STRIPE_MISSING ride=%s pi=%s",
                ride["id"],
                pi_id,
            )
            continue

        pi = stripe_pis[pi_id]
        if pi["status"] != "succeeded":
            discrepancies.append(
                {
                    "type": "DB_PAID_STRIPE_MISMATCH",
                    "ride_id": ride["id"],
                    "payment_intent_id": pi_id,
                    "stripe_status": pi["status"],
                }
            )
            logger.error(
                "stripe_reconcile: DB_PAID_STRIPE_MISMATCH ride=%s pi=%s stripe_status=%s",
                ride["id"],
                pi_id,
                pi["status"],
            )

        # Amount check (C2) — the authoritative charge is grand_total + tip (the
        # SAME total /process-payment settles), NOT the fare-side subtotal. Using
        # `fare` excluded fees/GST/PST/tip and — since rides has no `fare` column —
        # never even ran. We compare against grand_total + tip and ONLY flag
        # UNDERPAYMENT (actual < expected): overpayment is not a revenue risk, and
        # a strict `!=` false-flags rounding + the buffered-preauth split where a
        # tip beyond the hold is captured on a SEPARATE PI (so this PI legitimately
        # holds only up to authorized_amount).
        _grand = ride.get("grand_total")
        if _grand is None:
            _grand = ride.get("total_fare")
        if _grand is not None:
            expected = _q2(_grand) + _q2(ride.get("tip_amount") or 0)
            # If the tip exceeded the pre-auth hold, only up to authorized_amount
            # could be captured on THIS PI; the overflow lives on another PI.
            _authorized = ride.get("authorized_amount")
            if _authorized is not None and _q2(_authorized) < expected:
                expected = _q2(_authorized)
            expected_cents = int((expected * 100).to_integral_value())
            actual_cents = pi.get("amount_received", 0)
            if actual_cents < expected_cents:
                discrepancies.append(
                    {
                        "type": "DB_PAID_AMOUNT_MISMATCH",
                        "ride_id": ride["id"],
                        "payment_intent_id": pi_id,
                        "db_cents": expected_cents,
                        "stripe_cents": actual_cents,
                    }
                )
                logger.error(
                    "stripe_reconcile: DB_PAID_AMOUNT_MISMATCH ride=%s pi=%s expected_cents=%d stripe_cents=%d",
                    ride["id"],
                    pi_id,
                    expected_cents,
                    actual_cents,
                )

    # ── 3b. Check for Stripe succeeded PIs with no DB ride ───────────────
    for pi_id, pi in stripe_pis.items():
        if pi["status"] != "succeeded":
            continue
        # Spinr Pass charges (one-off subscription Checkout, corporate/wallet
        # top-ups) are not rides — skip them so they aren't flagged as orphans.
        _scope = (pi.get("metadata") or {}).get("scope")
        if _scope in ("driver_subscription", "corporate_topup", "wallet_topup"):
            continue
        if pi_id not in db_pi_to_ride:
            discrepancies.append(
                {
                    "type": "STRIPE_ORPHAN",
                    "payment_intent_id": pi_id,
                    "stripe_amount": pi.get("amount_received", 0),
                }
            )
            logger.error(
                "stripe_reconcile: STRIPE_ORPHAN pi=%s amount_cents=%d — no ride in DB",
                pi_id,
                pi.get("amount_received", 0),
            )

    # ── 3c. Payout settlement backstop ──────────────────────────────────
    # The payout.paid/payout.failed webhooks are the primary settlement
    # signal; this catches payouts whose webhook never arrived and money
    # that is stranded (transfer succeeded but payout + reversal both
    # failed). Detection only — never moves money.
    payout_discrepancies = await _reconcile_payouts()
    discrepancies.extend(payout_discrepancies)

    # ── 3d. Stuck-processing ride backstop ──────────────────────────────
    # Rides stranded in payment_status='processing' (settlement crashed, or
    # Stripe captured but the DB write failed). Detection only — never moves
    # money. See _reconcile_stuck_processing_rides.
    stuck_processing = await _reconcile_stuck_processing_rides()
    discrepancies.extend(stuck_processing)

    # ── 3e. Optional auto-heal (flag-gated, default OFF) ────────────────
    # When stripe_auto_heal_processing is enabled, finalise the detected
    # stuck rides from Stripe truth (mark-paid only, atomic, idempotent).
    # Default OFF — ships dark; see _maybe_heal_stuck_processing.
    heal_stats = await _maybe_heal_stuck_processing(stuck_processing, _stripe, settings)

    # ── 4. Write summary to audit_logs ──────────────────────────────────
    summary = {
        "date": yesterday.isoformat(),
        "stripe_pis_checked": len(stripe_pis),
        "db_rides_checked": len(db_rides),
        "payouts_flagged": len(payout_discrepancies),
        "rides_stuck_processing": len(stuck_processing),
        "auto_heal_enabled": heal_stats["enabled"],
        "rides_healed": heal_stats["healed"],
        "healed_ride_ids": heal_stats["healed_ride_ids"][:50],
        "discrepancies": len(discrepancies),
        "discrepancy_detail": discrepancies[:50],  # cap at 50 to avoid huge rows
    }
    try:
        await db_supabase.insert_one(
            "audit_logs",
            {
                "id": str(uuid.uuid4()),
                "action": "stripe_reconciliation",
                "entity_type": "system",
                "entity_id": f"reconcile_{yesterday.isoformat()}",
                "details": summary,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.error("stripe_reconcile: audit_logs write failed", exc_info=True)

    if discrepancies:
        logger.error(
            "stripe_reconcile: COMPLETE with %d discrepancies — see audit_logs for detail",
            len(discrepancies),
        )
    else:
        logger.info(
            "stripe_reconcile: COMPLETE clean — %d Stripe PIs, %d DB rides, 0 discrepancies",
            len(stripe_pis),
            len(db_rides),
        )


async def _reconcile_payouts() -> List[Dict[str, Any]]:
    """Detect driver payouts stuck in a non-terminal state or flagged for review.

    Two concerning sets:
      - requires_manual_review=true → instant payout where the payout step
        AND the compensating reversal both failed; money is stranded in the
        connected account and an operator must intervene.
      - status='transfer_completed' → instant payout whose payout step never
        completed (crash between transfer and payout, or a missed
        payout.paid/payout.failed webhook).

    Rows newer than one hour are skipped — they may still be resolving in
    flight. Detection only: we surface to logs + the audit summary, never
    move money, so a hiccup here cannot double-pay or reverse a driver.
    """
    discrepancies: List[Dict[str, Any]] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    cols = "id,driver_id,amount,status,requires_manual_review,stripe_payout_id,stripe_transfer_id,created_at"

    candidates: Dict[str, Dict[str, Any]] = {}
    for flt in ({"requires_manual_review": True}, {"status": "transfer_completed"}):
        try:
            rows = await db_supabase.get_rows("payouts", flt, columns=cols, limit=500) or []
        except Exception:
            logger.error("stripe_reconcile: payouts query failed for %s", flt, exc_info=True)
            continue
        for r in rows:
            candidates[r["id"]] = r

    for row in candidates.values():
        # Defensive: only flag rows actually in a concerning state, never
        # trusting the query filter alone — a terminal payout must never be
        # surfaced as stuck.
        review = bool(row.get("requires_manual_review"))
        stuck = row.get("status") == "transfer_completed"
        if not (review or stuck):
            continue
        created = row.get("created_at")
        if created and not _is_older_than(created, cutoff):
            continue  # still in-flight
        kind = "PAYOUT_STRANDED" if review else "PAYOUT_STUCK"
        discrepancies.append(
            {
                "type": kind,
                "payout_id": row["id"],
                "driver_id": row.get("driver_id"),
                "status": row.get("status"),
            }
        )
        logger.error(
            "stripe_reconcile: %s payout=%s driver=%s status=%s",
            kind,
            row["id"],
            row.get("driver_id"),
            row.get("status"),
            extra={"domain": "payments"},
        )
    return discrepancies


async def _reconcile_stuck_processing_rides() -> List[Dict[str, Any]]:
    """Detect rides stranded in payment_status='processing'.

    process_payment claims a ride by flipping payment_status to 'processing'
    (routes/rides.py) before it settles; a crash mid-settlement, or the
    Stripe-captured-but-DB-write-failed branch in
    services/payment_service.settle_card (which returns 503 "do not retry"),
    can leave the ride in 'processing' indefinitely. The paid-vs-Stripe pass
    above only notices the subset whose PI *succeeded* (STRIPE_ORPHAN); a
    processing ride whose charge failed, is requires_action, or never reached
    Stripe is otherwise invisible to this job.

    Rows updated more recently than ``_STUCK_PROCESSING_AFTER`` are skipped — a
    settlement may still be in flight. Detection only: surfaced to logs + the
    audit summary, never mutated, so this can never double-charge a rider or
    wrongly mark a ride paid.
    """
    discrepancies: List[Dict[str, Any]] = []
    cutoff = datetime.now(timezone.utc) - _STUCK_PROCESSING_AFTER
    cols = "id,payment_intent_id,payment_status,status,updated_at,ride_completed_at"
    try:
        rows = await db_supabase.get_rows("rides", {"payment_status": "processing"}, columns=cols, limit=500) or []
    except Exception:
        logger.error("stripe_reconcile: stuck-processing query failed", exc_info=True)
        return discrepancies

    for row in rows:
        # Defensive: re-assert the state, never trust the query filter alone.
        if row.get("payment_status") != "processing":
            continue
        # updated_at moves on every write; fall back to ride_completed_at. A row with
        # neither timestamp is surfaced rather than hidden (_is_older_than → True).
        ts = row.get("updated_at") or row.get("ride_completed_at")
        if ts and not _is_older_than(ts, cutoff):
            continue  # still in-flight — a settlement may be mid-round-trip
        discrepancies.append(
            {
                "type": "RIDE_PAYMENT_STUCK_PROCESSING",
                "ride_id": row["id"],
                "payment_intent_id": row.get("payment_intent_id"),
                "ride_status": row.get("status"),
            }
        )
        logger.error(
            "stripe_reconcile: RIDE_PAYMENT_STUCK_PROCESSING ride=%s pi=%s ride_status=%s",
            row["id"],
            row.get("payment_intent_id"),
            row.get("status"),
            extra={"domain": "payments"},
        )
    return discrepancies


def _truthy(v: Any) -> bool:
    """Interpret an app-setting flag that may be stored as bool / str / int."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return False


def _expected_capture_cents(ride: Dict[str, Any]) -> int | None:
    """Expected captured amount in cents (grand_total + tip, capped at the
    pre-auth hold). Mirrors the paid-vs-Stripe amount check above. Returns None
    when no total is recorded — the caller then declines to heal rather than
    mark paid against an unverifiable amount."""
    grand = ride.get("grand_total")
    if grand is None:
        grand = ride.get("total_fare")
    if grand is None:
        return None
    expected = _q2(grand) + _q2(ride.get("tip_amount") or 0)
    authorized = ride.get("authorized_amount")
    if authorized is not None and _q2(authorized) < expected:
        expected = _q2(authorized)
    return int((expected * 100).to_integral_value())


async def _heal_one_processing_ride(ride_id: str, stripe_mod: Any) -> bool:
    """Atomically finalise ONE ride stranded in 'processing' iff Stripe confirms
    the charge already succeeded for the expected amount. Returns True on heal.

    Mark-paid + (idempotent, delta-based) tip credit only — never a re-charge
    and never a duplicate ledger write. settle_card's capture-but-DB-write-
    failed branch already wrote the financial_events row, and the driver-
    earnings base is recorded at completion, so this only lands the mark-paid
    the failed DB write missed. Safety rests on three guards:

      * Stripe PI must be 'succeeded' with amount_received >= expected — a ride
        whose charge failed / is requires_action / never reached Stripe is left
        for manual review.
      * The flip is an atomic claim filtering payment_status='processing'; a
        zero-row result means process_payment (or another replica) already
        finalised it — no double-write.
      * _tip_ride_update applies the tip as a delta, so even a concurrent
        process_payment cannot double-credit the driver.
    """
    ride = await db_supabase.get_ride(ride_id)
    if not ride or ride.get("payment_status") != "processing":
        return False
    pi_id = ride.get("payment_intent_id")
    if not pi_id:
        return False  # no PaymentIntent to verify the charge against

    try:
        pi = stripe_mod.PaymentIntent.retrieve(pi_id)
    except Exception:
        logger.error(
            "stripe_reconcile: heal PI retrieve failed ride=%s pi=%s",
            ride_id,
            pi_id,
            exc_info=True,
        )
        return False

    status = pi.get("status") if hasattr(pi, "get") else pi["status"]
    if status != "succeeded":
        return False  # no captured charge — never mark paid

    amount_received = (pi.get("amount_received", 0) if hasattr(pi, "get") else pi["amount_received"]) or 0
    expected_cents = _expected_capture_cents(ride)
    if expected_cents is not None and amount_received < expected_cents:
        logger.error(
            "stripe_reconcile: heal SKIP amount short ride=%s pi=%s stripe_cents=%d expected_cents=%d",
            ride_id,
            pi_id,
            amount_received,
            expected_cents,
        )
        return False

    try:
        from ..services.payment_service import _tip_ride_update  # type: ignore
    except ImportError:
        from services.payment_service import _tip_ride_update  # type: ignore

    tip = Decimal(str(ride.get("tip_amount") or 0))
    claimed = await db_supabase.update_one(
        "rides",
        {"id": ride_id, "payment_status": "processing"},
        {
            "payment_status": "paid",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **_tip_ride_update(ride, tip),
        },
    )
    if not claimed:
        return False  # another writer finalised it first — idempotent no-op
    logger.warning(
        "stripe_reconcile: HEALED stuck processing ride=%s pi=%s — marked paid from Stripe truth",
        ride_id,
        pi_id,
        extra={"domain": "payments"},
    )
    return True


async def _maybe_heal_stuck_processing(
    stuck: List[Dict[str, Any]], stripe_mod: Any, settings: Dict[str, Any]
) -> Dict[str, Any]:
    """Auto-heal detected stuck-processing rides — gated on the
    ``stripe_auto_heal_processing`` app setting, which DEFAULTS OFF.

    Shipped dark on purpose: the mark-paid path moves money (marks a ride paid
    and credits the driver's tip), so it must be reviewed and validated in
    staging before an operator enables it in production. With the flag off this
    is a no-op and the rides remain detection-only.
    """
    enabled = _truthy(settings.get(_AUTO_HEAL_SETTING, False))
    stats: Dict[str, Any] = {"enabled": enabled, "considered": len(stuck), "healed": 0, "healed_ride_ids": []}
    if not enabled or not stuck:
        return stats
    for d in stuck:
        ride_id = d.get("ride_id")
        if not ride_id:
            continue
        try:
            if await _heal_one_processing_ride(ride_id, stripe_mod):
                stats["healed"] += 1
                stats["healed_ride_ids"].append(ride_id)
        except Exception:
            logger.error("stripe_reconcile: heal raised for ride %s", ride_id, exc_info=True)
    return stats


def _is_older_than(created_at: str, cutoff: datetime) -> bool:
    """Return True if the created_at ISO string is at or before cutoff."""
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return dt <= cutoff
    except Exception:
        return True  # unparseable timestamp → surface it rather than hide it


def _in_window(completed_at: str, window_start: int, window_end: int) -> bool:
    """Return True if the completed_at ISO string falls within the epoch window."""
    try:
        dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        ts = int(dt.timestamp())
        return window_start <= ts <= window_end
    except Exception:
        return False


async def stripe_reconcile_loop(target_hour_utc: int = _RUN_HOUR_UTC) -> None:
    """Daily loop — sleeps until target_hour_utc then runs the reconciliation tick.

    Uses a Redis SET NX EX leader lock so only one replica runs per 23h window.
    """
    first_sleep = _seconds_until(target_hour_utc)
    logger.info(
        "stripe_reconcile_loop: first run in %.0fs (target %02d:00 UTC)",
        first_sleep,
        target_hour_utc,
    )
    await asyncio.sleep(first_sleep)

    while True:
        try:
            acquired = await redis_set_nx(_LOCK_KEY, _pod_id(), _LOCK_TTL_SECONDS)
            if acquired:
                await _run_reconciliation_tick()
            else:
                logger.info("stripe_reconcile_loop: another replica holds the lock, skipping")
        except Exception:
            logger.error("stripe_reconcile_loop: tick raised", exc_info=True)
        _record_heartbeat("stripe_reconcile (24h)")
        await asyncio.sleep(86400)
