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
  STRIPE_EVENT_STUCK_UNPROCESSED — stripe_events row still processed_at=NULL
                             past the grace window (ACTION_ITEMS.md C10);
                             see _reconcile_stuck_stripe_events
  STRIPE_ORPHAN_HOLD       — uncaptured (requires_capture) PI with no ride row
                             referencing it, e.g. booking crashed between the
                             hold and the ride INSERT; 8-day lookback, see
                             _reconcile_orphan_holds
  CANCELLED_CAPTURED_UNREFUNDED — cancelled ride whose hold was captured and
                             never refunded; read-only scheduled dry run of
                             scripts/reconcile_cancelled_captured_refunds.py,
                             see _detect_cancelled_captured
  PAYOUT_STUCK_RESERVED    — payouts row still status='reserved' more than
                             1h after creation (process died between the
                             reserve INSERT and the Stripe Transfer outcome
                             write); read-only, see
                             _reconcile_stuck_reserved_payouts

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
from typing import Any, Dict, List, Optional


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
    from ..utils.money import dollars_to_cents, to_decimal  # type: ignore
    from ..utils.redis_client import redis_set_nx  # type: ignore
    from ..utils.stripe_config import stripe_get  # type: ignore
except ImportError:
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils import metrics  # type: ignore
    from utils.money import dollars_to_cents, to_decimal  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore
    from utils.stripe_config import stripe_get  # type: ignore

logger = logging.getLogger(__name__)

_LOCK_KEY = "spinr:stripe:reconcile:lock"
_LOCK_TTL_SECONDS = 23 * 60 * 60
_RUN_HOUR_UTC = 2  # 02:00 UTC daily
_WINDOW_DAYS = 1  # reconcile the previous calendar day
# A ride should never sit in payment_status='processing' longer than a
# settlement round-trip; past this it is treated as stranded and surfaced.
_STUCK_PROCESSING_AFTER = timedelta(minutes=15)
# App-setting flag (default OFF) gating the auto-heal of stuck-processing rides.
# OFF → detection only; ON → mark-paid only from exact aggregate-ledger and
# Stripe component proof (see _maybe_heal_*).
_AUTO_HEAL_SETTING = "stripe_auto_heal_processing"
# migrations/22_stripe_events.sql's own original comment: "received_at older
# than ~5 minutes but processed_at = NULL indicate events that crashed
# mid-processing". Matches that original design intent.
_STUCK_STRIPE_EVENT_AFTER = timedelta(minutes=5)
# STRIPE_ORPHAN_HOLD lookback. Deliberately NOT the 1-day window the paid-ride
# checks use: those reconcile a daily delta, but an uncaptured hold is a
# standing state that stays on the rider's card until someone releases it or
# Stripe auto-cancels it. Booking holds never request extended authorization
# (utils/stripe_charge.authorize_ride), so Stripe expires them after 7 days;
# 8 days covers a hold's whole lifetime plus slack. That makes every daily run
# see the complete live population, so a missed tick cannot leave a hold
# unseen. Anything older has already been released by Stripe, and there is no
# rider money left to recover.
_ORPHAN_HOLD_LOOKBACK = timedelta(days=8)
# Holds younger than this are skipped. Between authorize and the ride INSERT,
# and during the SCA two-step (on-device confirm, then a re-book with
# preauthorized_payment_intent_id), a hold legitimately has no ride row yet.
_ORPHAN_HOLD_GRACE = timedelta(hours=1)
# Cancelled+captured detector: skip rows touched this recently. The live cancel
# path may still be mid-refund, and refund_amount lands after Stripe responds.
_CANCELLED_CAPTURED_GRACE = timedelta(hours=1)
# Only rides cancelled within this window are scanned. Without a bound, the
# candidate set only ever grows: every legitimate fee-bearing partial-capture
# cancel (cancellation.py / drivers/ride_cancel.py set auth_status='captured'
# and release the remainder, so refund_amount stays unset) matches forever and
# gets re-read on Stripe daily as `not_needed`. 14 days gives each new
# occurrence of the bug class 14 daily chances (a transient `unknown` retries
# the next day). The PRE-EXISTING backlog is sized and cleared by the
# human-run script, which is what it exists for, not by this loop.
_CANCELLED_CAPTURED_LOOKBACK = timedelta(days=14)
_CANCELLED_CAPTURED_PAGE = 500
_CANCELLED_CAPTURED_MAX_PAGES = 10
_CANCELLED_CAPTURED_CONCURRENCY = 5
# Dry-run outcomes that mean money is unaccounted for. Mirrors the script's
# non-zero exit set (reconcile_cancelled_captured_refunds._main) plus
# would_refund. pending_refund_on_stripe / not_needed are not alert-worthy.
_CC_ALERT_OUTCOMES = ("would_refund", "already_refunded_on_stripe", "captured_nothing", "unknown")
_CC_METRIC = "spinr_payment_cancelled_captured_unrefunded_total"
_ORPHAN_HOLD_METRIC = "spinr_payment_stripe_orphan_hold_total"
# A detection check that could not complete (Stripe/DB error, or a scan that
# hit its row ceiling). Without this, a check failing every day would look
# identical to a clean one to an alert rule.
_CHECK_FAILED_METRIC = "spinr_payment_reconcile_check_failed_total"
_CHECK_NAMES = ("orphan_hold", "cancelled_captured", "stuck_reserved_payout")
# payouts rows still 'reserved' past this age. The reserve INSERT and the
# terminal write straddle one Stripe Transfer call (routes/drivers/payouts.py),
# so a live request resolves in seconds; an hour is unambiguously stranded.
_STUCK_RESERVED_PAYOUT_AFTER = timedelta(hours=1)
_STUCK_RESERVED_PAYOUT_LIMIT = 500
_STUCK_RESERVED_METRIC = "spinr_payment_stuck_reserved_payouts_total"
_PAYOUT_TYPES = ("standard", "instant", "auto")

# Pre-register every series at 0 in EVERY process at import. The counters are
# in-process and render with a worker_pid label, so without this a series
# first appears already holding N, and PromQL increase() never counts that
# first sample. A real finding would then never fire its alert.
for _o in _CC_ALERT_OUTCOMES:
    metrics.inc(_CC_METRIC, {"outcome": _o}, by=0)
metrics.inc(_ORPHAN_HOLD_METRIC, by=0)
for _c in _CHECK_NAMES:
    metrics.inc(_CHECK_FAILED_METRIC, {"check": _c}, by=0)
for _t in _PAYOUT_TYPES:
    metrics.inc(_STUCK_RESERVED_METRIC, {"payout_type": _t}, by=0)


def _pod_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _seconds_until(target_hour_utc: int) -> float:
    now = datetime.now(timezone.utc)
    target = datetime.combine(now.date(), time(target_hour_utc, 0), tzinfo=timezone.utc)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _run_reconciliation_tick(target_date: Optional[date] = None) -> None:
    """One reconciliation pass for yesterday's transactions.

    ``target_date`` (UTC calendar day) re-runs the pass for a specific past day
    — the backfill path for days the loop missed (e.g. the stripe v15
    ``pi.get`` AttributeError that crashed every tick). Detection only: it
    writes one audit_logs summary row and never moves money. Default None =
    yesterday, the loop's normal behaviour.
    """
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
    yesterday = target_date or (date.today() - timedelta(days=_WINDOW_DAYS))
    window_start = int(datetime.combine(yesterday, time(0, 0), tzinfo=timezone.utc).timestamp())
    window_end = int(datetime.combine(yesterday, time(23, 59, 59), tzinfo=timezone.utc).timestamp())

    logger.info(
        "stripe_reconcile: reconciling %s (epoch %d–%d)",
        yesterday.isoformat(),
        window_start,
        window_end,
    )

    # ── 1. Fetch Stripe PaymentIntents created yesterday ─────────────────
    def _list_stripe_pis() -> Dict[str, Any]:
        # Off the event loop (C86): auto_paging_iter() can issue one blocking
        # Stripe HTTP call per page of yesterday's PaymentIntents -- run the
        # whole pagination loop in a thread, not just one call.
        out: Dict[str, Any] = {}
        for pi in _stripe.PaymentIntent.list(
            created={"gte": window_start, "lte": window_end},
            limit=100,
        ).auto_paging_iter():
            out[pi["id"]] = pi
        return out

    try:
        stripe_pis: Dict[str, Any] = await asyncio.to_thread(_list_stripe_pis)
    except Exception:
        logger.error("stripe_reconcile: Stripe API list failed", exc_info=True, extra={"domain": "payments"})
        return

    # ── 2. Fetch DB rides completed yesterday with a PI id ─────────────
    db_rides: List[Dict[str, Any]] = []
    try:
        db_rides = (
            await db_supabase.get_rows(
                "rides",
                {
                    "payment_status": "paid",
                    # Server-side day window: without it limit=2000 returns an
                    # arbitrary slice of ALL paid rides, so a backfill of an
                    # older day (or a busy table) silently misses rides. The
                    # _in_window pass below stays as the exact inclusive check.
                    "ride_completed_at": {
                        "$gte": datetime.fromtimestamp(window_start, tz=timezone.utc).isoformat(),
                        "$lte": datetime.fromtimestamp(window_end, tz=timezone.utc).isoformat(),
                    },
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
        logger.error("stripe_reconcile: DB rides query failed", exc_info=True, extra={"domain": "payments"})
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
                extra={"domain": "payments", "ride_id": _attr["ride_id"]},
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
                extra={"domain": "payments", "ride_id": ride["id"]},
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
                extra={"domain": "payments", "ride_id": ride["id"]},
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
            # v15: PaymentIntent is not a dict — .get() raised AttributeError
            # and killed every tick. stripe_get works for dicts + StripeObjects.
            actual_cents = stripe_get(pi, "amount_received", 0) or 0
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
                    extra={"domain": "payments", "ride_id": ride["id"]},
                )

    # ── 3b. Check for Stripe succeeded PIs with no DB ride ───────────────
    for pi_id, pi in stripe_pis.items():
        if pi["status"] != "succeeded":
            continue
        # Spinr Pass charges (one-off subscription Checkout, corporate/wallet
        # top-ups) are not rides — skip them so they aren't flagged as orphans.
        _scope = stripe_get(stripe_get(pi, "metadata"), "scope")
        if _scope in ("driver_subscription", "corporate_topup", "wallet_topup"):
            continue
        if pi_id not in db_pi_to_ride:
            _orphan_cents = stripe_get(pi, "amount_received", 0) or 0
            discrepancies.append(
                {
                    "type": "STRIPE_ORPHAN",
                    "payment_intent_id": pi_id,
                    "stripe_amount": _orphan_cents,
                }
            )
            logger.error(
                "stripe_reconcile: STRIPE_ORPHAN pi=%s amount_cents=%d — no ride in DB",
                pi_id,
                _orphan_cents,
                extra={"domain": "payments"},
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

    # ── 3f. Stuck stripe_events backstop (ACTION_ITEMS.md C10) ───────────
    # stripe_events rows left at processed_at=NULL: either an unhandled
    # event type (routes/webhooks.py's `else` branch — deliberate, and
    # already 2xx'd, so Stripe will never retry it) or a handler that
    # crashed/failed the final processed_at stamp after finishing its
    # business logic (the mark_stripe_event_processed bug fixed 2026-08-01
    # — also already 2xx'd). Detection only, same as 3c/3d — this never
    # re-runs webhook business logic (that would risk double-processing a
    # row where the side effects already happened), it only surfaces the
    # row for manual review. See _reconcile_stuck_stripe_events.
    stuck_stripe_events = await _reconcile_stuck_stripe_events(settings)
    discrepancies.extend(stuck_stripe_events)

    # ── 3g. Uncaptured holds with no ride row (STRIPE_ORPHAN_HOLD) ───────
    # 3b only sees SUCCEEDED PIs from yesterday. A booking hold placed before
    # the ride INSERT (routes/rides/booking.py) that never got a ride row is
    # requires_capture, has nothing in `rides` to find it, and so is invisible
    # to utils/orphaned_hold_reconciler too. Detection only — never releases.
    #
    # 3g/3h are CURRENT-STATE checks that feed P1 alert counters. They are
    # skipped on a target_date backfill so re-running N past days neither
    # stamps today's findings onto old audit rows nor re-fires the alert N
    # times.
    orphan_holds: Any = "skipped_backfill"
    cc_counts: Any = "skipped_backfill"
    stuck_reserved: Any = "skipped_backfill"
    if target_date is None:
        orphan_holds = await _reconcile_orphan_holds(_stripe)
        if orphan_holds is not None:
            discrepancies.extend(orphan_holds)

        # ── 3h. Cancelled rides with a captured, unrefunded hold ────────
        # Read-only scheduled counterpart of
        # scripts/reconcile_cancelled_captured_refunds.py's DRY RUN. Never
        # refunds: the --apply path stays human-triggered. See
        # _detect_cancelled_captured.
        cc_counts, cc_flagged = await _detect_cancelled_captured()
        discrepancies.extend(cc_flagged)

        # ── 3i. Payout rows stranded in 'reserved' ──────────────────────
        # Read-only: whether the Stripe Transfer went through is exactly
        # what a human has to confirm. See _reconcile_stuck_reserved_payouts.
        stuck_reserved = await _reconcile_stuck_reserved_payouts()
        if stuck_reserved is not None:
            discrepancies.extend(stuck_reserved)

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
        "stripe_events_stuck_unprocessed": len(stuck_stripe_events),
        # None = the check itself failed (logged), NOT "zero orphans".
        "stripe_orphan_holds": orphan_holds if not isinstance(orphan_holds, list) else len(orphan_holds),
        # None = the scan failed (logged); otherwise per-outcome ride counts.
        "cancelled_captured_unrefunded": cc_counts,
        # None = the check itself failed (logged), NOT "zero stuck rows".
        "payouts_stuck_reserved": stuck_reserved if not isinstance(stuck_reserved, list) else len(stuck_reserved),
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
        other_count = len(discrepancies) - len(stuck_stripe_events)
        logger.error(
            "stripe_reconcile: COMPLETE with %d discrepancies (%d stuck events, %d other) — see audit_logs for detail",
            len(discrepancies),
            len(stuck_stripe_events),
            other_count,
            extra={"domain": "payments"},
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


def _payout_transfer_idempotency_key(payout_id: str, payout_type: Optional[str]) -> Optional[str]:
    """The Stripe idempotency key the reserve-then-transfer path used.

    Not stored on the row; it is deterministic in routes/drivers/payouts.py.
    Auto payouts use attempt-scoped keys (utils/auto_payout.py) and tag the
    Transfer with metadata.payout_id instead, so no single key is returned.
    """
    if payout_type == "instant":
        return f"instant-payout-transfer-{payout_id}"
    if payout_type in (None, "standard"):
        return f"payout-transfer-{payout_id}"
    return None


async def _reconcile_stuck_reserved_payouts() -> Optional[List[Dict[str, Any]]]:
    """Detect payouts rows stranded in status='reserved'.

    routes/drivers/payouts.py INSERTs the row as 'reserved' before calling
    stripe.Transfer and only then writes a terminal status. A process death
    in between (crash, deploy, timeout) leaves the row 'reserved' forever: it
    keeps deducting from the driver's payable balance, and migration 250's
    one-in-flight index blocks every new payout for that driver.

    Read-only, never transitions a row: only Stripe can say whether the
    Transfer happened, and guessing wrong either double-pays (re-open) or
    loses the driver's money (fail). Surfaced for manual review with the
    idempotency key to search in Stripe. Returns None when the check itself
    failed (logged + counted), never an empty list that looks clean.
    """
    cutoff = datetime.now(timezone.utc) - _STUCK_RESERVED_PAYOUT_AFTER
    try:
        rows = (
            await db_supabase.get_rows(
                "payouts",
                {"status": "reserved", "created_at": {"$lt": cutoff.isoformat()}},
                columns="id,driver_id,payout_type,status,created_at",
                order="created_at",
                limit=_STUCK_RESERVED_PAYOUT_LIMIT,
            )
            or []
        )
    except Exception:
        logger.error(
            "stripe_reconcile: stuck-reserved payouts query failed", exc_info=True, extra={"domain": "payments"}
        )
        metrics.inc(_CHECK_FAILED_METRIC, {"check": "stuck_reserved_payout"})
        return None

    if len(rows) >= _STUCK_RESERVED_PAYOUT_LIMIT:
        # Oldest-first, so the most urgent rows are the ones surfaced.
        logger.error(
            "stripe_reconcile: stuck-reserved payouts scan hit its %d-row ceiling — more may exist",
            _STUCK_RESERVED_PAYOUT_LIMIT,
            extra={"domain": "payments"},
        )
        metrics.inc(_CHECK_FAILED_METRIC, {"check": "stuck_reserved_payout"})

    discrepancies: List[Dict[str, Any]] = []
    for row in rows:
        # Defensive: re-assert the state, never trust the query filter alone.
        if row.get("status") != "reserved" or not row.get("id"):
            continue
        created = row.get("created_at")
        if created and not _is_older_than(created, cutoff):
            continue  # still in flight
        payout_type = row.get("payout_type")
        idem = _payout_transfer_idempotency_key(row["id"], payout_type)
        discrepancies.append(
            {
                "type": "PAYOUT_STUCK_RESERVED",
                "payout_id": row["id"],
                "driver_id": row.get("driver_id"),
                "payout_type": payout_type,
                "created_at": created,
                "stripe_idempotency_key": idem,
            }
        )
        logger.error(
            "stripe_reconcile: PAYOUT_STUCK_RESERVED payout=%s driver=%s payout_type=%s created_at=%s "
            "stripe_idempotency_key=%s — confirm in Stripe whether the Transfer happened before touching the row",
            row["id"],
            row.get("driver_id"),
            payout_type,
            created,
            idem or "n/a (auto: search Transfers by metadata.payout_id)",
            extra={"domain": "payments", "driver_id": row.get("driver_id")},
        )
        label = payout_type if payout_type in _PAYOUT_TYPES else "other"
        metrics.inc(_STUCK_RESERVED_METRIC, {"payout_type": label})
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


async def _reconcile_stuck_stripe_events(
    settings: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Detect stripe_events rows left at processed_at=NULL past the grace
    window (ACTION_ITEMS.md C10).

    migrations/22_stripe_events.sql's own top comment described this exact
    scan ("a nightly reconciliation job should replay them") when the table
    was created but no job ever implemented it — this closes that gap.

    Two ways a row ends up here, both already 2xx'd to Stripe (so Stripe's
    own retry mechanism will never revisit them):
      - an unhandled event type (routes/webhooks.py's `else` branch
        deliberately leaves processed_at NULL "so [this job] can replay
        them if they later become actionable")
      - the handler finished its business logic but the final
        mark_stripe_event_processed stamp write itself failed
        (repositories/wallet_repo.py, fixed 2026-08-01 to log loudly
        instead of swallowing silently)

    A third case — a handler that raised mid-processing — is excluded by
    design: that path returns 5xx (not 2xx), so Stripe's own retry
    mechanism is still live for it and it is not "stuck" in the same
    permanent sense; the grace window below still catches it eventually if
    Stripe's own retries also don't succeed within it, which is
    conservative rather than a gap.

    Detection only — deliberately does NOT attempt to replay/re-run the
    stored event through the webhook business logic. For the
    already-succeeded-but-unstamped case, replay would risk re-running side
    effects that already happened (double wallet credit, double
    notification). Distinguishing that case from the never-ran case would
    require trusting the payload's own claims, which this job does not do.
    Surfaced for manual review instead — see the row's event_type / event_id
    in the audit_logs detail to decide the right remediation by hand.
    """
    discrepancies: List[Dict[str, Any]] = []
    cutoff = datetime.now(timezone.utc) - _STUCK_STRIPE_EVENT_AFTER

    # Only scan events received within the lookback window. Events older
    # than this are historical noise — either deliberately-ignored event
    # types whose processed_at was left NULL before the webhook handler
    # started stamping them (CRIMSON-SMOKE-7445-HC), or long-resolved
    # handler failures. Adjustable via app_settings.
    lookback_days = 30
    if settings:
        try:
            lookback_days = int(settings.get("stripe_reconcile_stuck_event_lookback_days", 30))
        except (ValueError, TypeError):
            pass
    lookback_cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    try:
        rows = (
            await db_supabase.get_rows(
                "stripe_events",
                {"processed_at": None, "received_at": {"$gte": lookback_cutoff}},
                columns="event_id,event_type,received_at,processed_at",
                limit=500,
            )
            or []
        )
    except Exception:
        logger.error("stripe_reconcile: stuck-stripe-events query failed", exc_info=True)
        return discrepancies

    for row in rows:
        # Defensive: re-assert the state, never trust the query filter alone.
        # event_id presence also guards against a shared/blanket-mocked
        # get_rows in tests returning an unrelated table's rows here.
        if not row.get("event_id") or row.get("processed_at") is not None:
            continue
        ts = row.get("received_at")
        if ts and not _is_older_than(ts, cutoff):
            continue  # still within the grace window — may still be in flight
        discrepancies.append(
            {
                "type": "STRIPE_EVENT_STUCK_UNPROCESSED",
                "event_id": row.get("event_id"),
                "event_type": row.get("event_type"),
                "received_at": row.get("received_at"),
            }
        )
        logger.error(
            "stripe_reconcile: STRIPE_EVENT_STUCK_UNPROCESSED event_id=%s event_type=%s received_at=%s",
            row.get("event_id"),
            row.get("event_type"),
            row.get("received_at"),
            extra={"domain": "payments", "event_id": row.get("event_id")},
        )
    return discrepancies


async def _reconcile_orphan_holds(stripe_mod: Any) -> Optional[List[Dict[str, Any]]]:
    """Detect uncaptured booking holds (``requires_capture``) with no ride row.

    ``routes/rides/booking.py`` authorizes the card BEFORE ``_insert_ride_with_code``.
    Two cases leave a real hold with nothing in ``rides`` pointing at it: (1) a
    crash or failed/timed-out INSERT after the authorize, and (2) an SCA
    two-step the rider confirmed on-device but never re-booked. Neither
    ``orphaned_hold_reconciler`` (it scans ``rides``) nor 3b (``succeeded``
    PIs only) can see either one.

    Its own type, not ``STRIPE_ORPHAN``, because the remediation is different:
    an orphaned CAPTURE is money taken with no ride (investigate, maybe refund).
    An orphaned HOLD has moved no money yet, and the fix is to cancel the
    authorization so the rider's funds free up before Stripe's 7-day expiry.
    Sharing one type would give an operator the wrong runbook for half the rows.

    Scans ``_ORPHAN_HOLD_LOOKBACK`` rather than yesterday. See that constant.

    Returns ``None`` when the Stripe list or the ride lookup fails, so the
    caller never reports "0 orphans" for a check that did not actually run.
    Detection only: it never cancels a hold.
    """
    now = datetime.now(timezone.utc)
    created_gte = int((now - _ORPHAN_HOLD_LOOKBACK).timestamp())
    created_lte = int((now - _ORPHAN_HOLD_GRACE).timestamp())

    def _list_holds() -> Dict[str, Any]:
        # Off the event loop (C86): one blocking Stripe call per page.
        out: Dict[str, Any] = {}
        for pi in stripe_mod.PaymentIntent.list(
            created={"gte": created_gte, "lte": created_lte},
            limit=100,
        ).auto_paging_iter():
            if stripe_get(pi, "status") != "requires_capture":
                continue
            # Same non-ride scopes 3b excludes (none use manual capture today,
            # but a future one must not be flagged as a ride hold).
            if stripe_get(stripe_get(pi, "metadata"), "scope") in (
                "driver_subscription",
                "corporate_topup",
                "wallet_topup",
            ):
                continue
            out[stripe_get(pi, "id")] = pi
        return out

    try:
        holds: Dict[str, Any] = await asyncio.to_thread(_list_holds)
    except Exception:
        logger.error("stripe_reconcile: orphan-hold Stripe list failed", exc_info=True, extra={"domain": "payments"})
        metrics.inc(_CHECK_FAILED_METRIC, {"check": "orphan_hold"})
        return None

    pi_ids = [p for p in holds if p]
    try:
        # Batched $in: the hold count grows with booking volume (URL-length cap).
        rows = (
            await db_supabase.get_rows_batched_in("rides", "payment_intent_id", pi_ids, columns="id,payment_intent_id")
            or []
        )
    except Exception:
        # Unknown is not "unlinked": flagging every hold on a DB blip would
        # send an operator to cancel holds on live rides.
        logger.error("stripe_reconcile: orphan-hold ride lookup failed", exc_info=True, extra={"domain": "payments"})
        metrics.inc(_CHECK_FAILED_METRIC, {"check": "orphan_hold"})
        return None
    linked = {r.get("payment_intent_id") for r in rows if r.get("payment_intent_id")}

    discrepancies: List[Dict[str, Any]] = []
    for pi_id in pi_ids:
        if pi_id in linked:
            continue
        pi = holds[pi_id]
        held_cents = stripe_get(pi, "amount_capturable", 0) or 0
        meta_ride_id = stripe_get(stripe_get(pi, "metadata"), "ride_id")
        discrepancies.append(
            {
                "type": "STRIPE_ORPHAN_HOLD",
                "payment_intent_id": pi_id,
                "amount_capturable": held_cents,
                "metadata_ride_id": meta_ride_id,
                "created": stripe_get(pi, "created"),
            }
        )
        logger.error(
            "stripe_reconcile: STRIPE_ORPHAN_HOLD pi=%s held_cents=%d metadata_ride_id=%s — "
            "uncaptured hold with no ride row; cancel the authorization to release the rider's funds",
            pi_id,
            held_cents,
            meta_ride_id,
            extra={"domain": "payments"},
        )
    metrics.inc(_ORPHAN_HOLD_METRIC, by=len(discrepancies))
    return discrepancies


def _cc_fee_owed_cents(ride: Dict[str, Any]) -> int:
    """Mirror of scripts/reconcile_cancelled_captured_refunds._fee_still_owed_from_capture, in cents.

    Duplicated rather than imported: importing that script into the server
    process would run its import-time ``logging.basicConfig`` / ``sys.path``
    mutation and load a second copy of ``db_supabase``. The
    ``test_cc_fee_rule_matches_script`` parity test fails if the two drift.
    """
    fee = to_decimal(ride.get("cancellation_fee_admin") or 0) + to_decimal(ride.get("cancellation_fee_driver") or 0)
    fee_pi = ride.get("cancel_fee_payment_intent_id")
    if bool(fee_pi) and fee_pi != ride.get("payment_intent_id") and (ride.get("payment_status") or "") == "paid":
        return 0
    return dollars_to_cents(fee)


async def _read_capture_state(*, ride_id: str, payment_intent_id: str) -> Optional[Dict[str, Any]]:
    """Lazy wrapper over utils.stripe_charge.read_capture_state (read-only, never raises)."""
    try:
        from ..utils.stripe_charge import read_capture_state  # type: ignore
    except ImportError:
        from utils.stripe_charge import read_capture_state  # type: ignore
    return await read_capture_state(ride_id=ride_id, payment_intent_id=payment_intent_id)


def _classify_cc(ride: Dict[str, Any], state: Optional[Dict[str, Any]]) -> str:
    """The DRY-RUN branch of the script's ``reconcile_one``: same outcome keys, no writes."""
    if state is None:
        return "unknown"
    if state.get("pending_refund_cents", 0) > 0:
        return "pending_refund_on_stripe"
    if state["refunded_cents"] > 0:
        return "already_refunded_on_stripe"
    if state["captured_cents"] <= 0:
        return "captured_nothing"
    if state["captured_cents"] - _cc_fee_owed_cents(ride) <= 0:
        return "not_needed"
    return "would_refund"


async def _detect_cancelled_captured() -> tuple[Optional[Dict[str, int]], List[Dict[str, Any]]]:
    """Scheduled, READ-ONLY run of the cancelled+captured refund backfill's dry run.

    Same candidate shape as ``scripts/reconcile_cancelled_captured_refunds.find_candidates``
    (``status='cancelled'``, ``auth_status='captured'``, a PI, no ``refund_amount``)
    and the same outcome classification as its ``reconcile_one(apply_changes=False)``.
    Stripe is only read, through ``read_capture_state``. This path never calls
    ``refund_excess_capture`` and never writes a row. Issuing the refund stays
    with the human-run ``--apply``.

    Kept out of ``orphaned_hold_reconciler``: that loop's ``OPEN_AUTH_STATES``
    deliberately excludes ``captured`` (it releases holds and must never touch
    captured money), and it MUTATES. This check is detect-only.

    Scope: rides cancelled in the last ``_CANCELLED_CAPTURED_LOOKBACK`` (see
    that constant for why the scan must be bounded). Pages through the window,
    because rides the script has already refunded keep matching the query (it
    never changes status/auth_status) and are only dropped afterwards on
    ``refund_amount``. Returns ``(outcome_counts | None, alert_discrepancies)``.
    ``None`` means the DB scan failed.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - _CANCELLED_CAPTURED_GRACE
    filters = {
        "status": "cancelled",
        "auth_status": "captured",
        "cancelled_at": {"$gte": (now - _CANCELLED_CAPTURED_LOOKBACK).isoformat()},
    }
    cols = (
        "id,status,auth_status,payment_intent_id,refund_amount,cancellation_fee_admin,"
        "cancellation_fee_driver,cancel_fee_payment_intent_id,payment_status,updated_at"
    )
    candidates: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    truncated = False
    try:
        for page in range(_CANCELLED_CAPTURED_MAX_PAGES):
            rows = (
                await db_supabase.get_rows(
                    "rides",
                    filters,
                    columns=cols,
                    order="id",
                    limit=_CANCELLED_CAPTURED_PAGE,
                    offset=page * _CANCELLED_CAPTURED_PAGE,
                )
                or []
            )
            for r in rows:
                # Defensive: re-assert the state, never trust the query filter alone.
                if r.get("status") != "cancelled" or r.get("auth_status") != "captured":
                    continue
                if not r.get("payment_intent_id"):
                    continue
                try:
                    if dollars_to_cents(to_decimal(r.get("refund_amount") or 0)) > 0:
                        continue
                except Exception:
                    # Malformed refund_amount: keep it as a candidate so Stripe
                    # decides, rather than silently dropping a possibly-owed ride.
                    logger.error(
                        "stripe_reconcile: cancelled-captured ride=%s has malformed refund_amount=%r",
                        r.get("id"),
                        r.get("refund_amount"),
                        extra={"domain": "payments", "ride_id": r.get("id")},
                    )
                ts = r.get("updated_at")
                if ts and not _is_older_than(ts, cutoff):
                    continue  # live cancel path may still be mid-refund
                candidates.append(r)
            if len(rows) < _CANCELLED_CAPTURED_PAGE:
                break
        else:
            # Every page was full. Probe one row past the ceiling so exactly
            # N*PAGE rows is not misreported as truncated.
            probe = await db_supabase.get_rows(
                "rides",
                filters,
                columns="id",
                order="id",
                limit=1,
                offset=_CANCELLED_CAPTURED_MAX_PAGES * _CANCELLED_CAPTURED_PAGE,
            )
            truncated = bool(probe)
    except Exception:
        logger.error("stripe_reconcile: cancelled-captured scan failed", exc_info=True, extra={"domain": "payments"})
        metrics.inc(_CHECK_FAILED_METRIC, {"check": "cancelled_captured"})
        return None, []
    if truncated:
        counts["scan_truncated"] = 1
        logger.error(
            "stripe_reconcile: cancelled-captured scan hit its %d-row ceiling; later rows were NOT checked",
            _CANCELLED_CAPTURED_PAGE * _CANCELLED_CAPTURED_MAX_PAGES,
            extra={"domain": "payments"},
        )
        metrics.inc(_CHECK_FAILED_METRIC, {"check": "cancelled_captured"})

    sem = asyncio.Semaphore(_CANCELLED_CAPTURED_CONCURRENCY)

    async def _one(ride: Dict[str, Any]) -> str:
        # One bad row (malformed fee, odd Stripe payload) must neither stop the
        # others nor lose the day's whole audit summary. Report it as unknown.
        try:
            async with sem:
                state = await _read_capture_state(ride_id=ride["id"], payment_intent_id=ride["payment_intent_id"])
            return _classify_cc(ride, state)
        except Exception:
            logger.error(
                "stripe_reconcile: cancelled-captured classify raised ride=%s",
                ride.get("id"),
                exc_info=True,
                extra={"domain": "payments", "ride_id": ride.get("id")},
            )
            return "unknown"

    outcomes = await asyncio.gather(*(_one(r) for r in candidates))

    flagged: List[Dict[str, Any]] = []
    for ride, outcome in zip(candidates, outcomes, strict=True):
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome not in _CC_ALERT_OUTCOMES:
            continue
        flagged.append(
            {
                "type": "CANCELLED_CAPTURED_UNREFUNDED",
                "ride_id": ride["id"],
                "payment_intent_id": ride["payment_intent_id"],
                "outcome": outcome,
            }
        )
        logger.error(
            "stripe_reconcile: CANCELLED_CAPTURED_UNREFUNDED ride=%s pi=%s outcome=%s — run "
            "scripts/reconcile_cancelled_captured_refunds.py --ride-id <id> (dry run first)",
            ride["id"],
            ride["payment_intent_id"],
            outcome,
            extra={"domain": "payments", "ride_id": ride["id"]},
        )
    for outcome in _CC_ALERT_OUTCOMES:
        metrics.inc(_CC_METRIC, {"outcome": outcome}, by=counts.get(outcome, 0))
    return counts, flagged


def _truthy(v: Any) -> bool:
    """Interpret an app-setting flag that may be stored as bool / str / int."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return False


def _expected_capture_cents(ride: Dict[str, Any], frozen_tip: Any) -> int | None:
    """Expected captured cents using the tip snapshot in the durable ledger.

    The ride's ``tip_amount`` may still be stale if settlement crashed before
    its final ride update, so it is never a recovery authority.
    """
    grand = ride.get("grand_total")
    if grand is None:
        grand = ride.get("total_fare")
    if grand is None:
        return None
    expected = _q2(grand) + _q2(frozen_tip)
    return int((expected * 100).to_integral_value())


async def _heal_one_processing_ride(ride_id: str, stripe_mod: Any) -> bool:
    """Atomically finalise ONE stuck ride using existing ledger + Stripe proof.

    Mark-paid + (idempotent, delta-based) tip credit only — never a re-charge
    and never a duplicate ledger write. settle_card's capture-but-DB-write-
    failed branch already wrote the financial_events row, and the driver-
    earnings base is recorded at completion, so this only lands the mark-paid
    the failed DB write missed. Safety rests on these guards:

      * A ride- and rider-owned aggregate ledger row must match the frozen
        tip-inclusive obligation; split components must have a unique exact
        manifest and each PI must be succeeded for exactly its recorded cents.
      * Missing, partial, malformed, or inconsistent proof stays unpaid for
        manual review; a succeeded primary PI alone never proves settlement.
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
        ledger_rows = await db_supabase.get_rows(
            "financial_events",
            {"ride_id": ride_id, "event_type": "stripe_charge", "ref": pi_id},
            columns="ride_id,user_id,event_type,ref,delta_cents,metadata",
            limit=1,
        )
    except Exception:
        logger.error("stripe_reconcile: heal ledger lookup failed ride=%s pi=%s", ride_id, pi_id, exc_info=True)
        return False
    ledger = next(
        (
            row
            for row in ledger_rows or []
            if row.get("ride_id") == ride_id
            and row.get("user_id") == ride.get("rider_id")
            and row.get("event_type") == "stripe_charge"
            and row.get("ref") == pi_id
        ),
        None,
    )
    if ledger is None:
        logger.error(
            "stripe_reconcile: heal SKIP missing/misowned aggregate ledger proof ride=%s pi=%s; manual review required",
            ride_id,
            pi_id,
        )
        return False
    metadata = ledger.get("metadata") or {}
    frozen_tip = metadata.get("tip_amount") if isinstance(metadata, dict) else None
    delta_cents = ledger.get("delta_cents")
    if isinstance(delta_cents, bool) or not isinstance(delta_cents, int) or frozen_tip is None:
        logger.error("stripe_reconcile: heal SKIP malformed aggregate ledger proof ride=%s pi=%s", ride_id, pi_id)
        return False
    try:
        expected_cents = _expected_capture_cents(ride, frozen_tip)
    except Exception:
        logger.error("stripe_reconcile: heal SKIP invalid frozen obligation ride=%s pi=%s", ride_id, pi_id)
        return False
    if expected_cents is None or delta_cents != expected_cents:
        logger.error(
            "stripe_reconcile: heal SKIP ledger obligation mismatch ride=%s pi=%s ledger_cents=%s expected_cents=%s",
            ride_id,
            pi_id,
            delta_cents,
            expected_cents,
        )
        return False

    components = metadata.get("component_payment_intents")
    if components is None:
        component_amounts = {pi_id: delta_cents}
    else:
        items = components.get("items") if isinstance(components, dict) and components.get("version") == 1 else None
        if not isinstance(items, list) or len(items) < 2:
            logger.error("stripe_reconcile: heal SKIP malformed split manifest ride=%s pi=%s", ride_id, pi_id)
            return False
        component_amounts: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                component_amounts = {}
                break
            component_pi = item.get("payment_intent_id")
            component_cents = item.get("amount_cents")
            if (
                not isinstance(component_pi, str)
                or not component_pi
                or isinstance(component_cents, bool)
                or not isinstance(component_cents, int)
                or component_cents <= 0
                or component_pi in component_amounts
            ):
                component_amounts = {}
                break
            component_amounts[component_pi] = component_cents
        if (
            not component_amounts
            or component_amounts.get(pi_id) is None
            or sum(component_amounts.values()) != delta_cents
        ):
            logger.error("stripe_reconcile: heal SKIP inconsistent split manifest ride=%s pi=%s", ride_id, pi_id)
            return False

    # Verify every exact component against Stripe. A succeeded primary PI
    # (fare-only hold) cannot stand in for an unverified overflow PI.
    try:
        for component_pi, component_cents in component_amounts.items():
            pi = await asyncio.to_thread(stripe_mod.PaymentIntent.retrieve, component_pi)
            status = stripe_get(pi, "status")
            amount_received = stripe_get(pi, "amount_received", 0) or 0
            if status != "succeeded" or amount_received != component_cents:
                logger.error(
                    "stripe_reconcile: heal SKIP component mismatch ride=%s pi=%s status=%s received=%s expected=%s",
                    ride_id,
                    component_pi,
                    status,
                    amount_received,
                    component_cents,
                )
                return False
    except Exception:
        logger.error(
            "stripe_reconcile: heal component PI retrieve failed ride=%s primary_pi=%s",
            ride_id,
            pi_id,
            exc_info=True,
        )
        return False

    try:
        from ..services.outbox_receipts import maybe_send_auto_receipt  # type: ignore
        from ..services.payment_service import (
            _tip_ride_update,  # type: ignore
            send_ride_receipt,  # type: ignore
        )
    except ImportError:
        from services.outbox_receipts import maybe_send_auto_receipt  # type: ignore
        from services.payment_service import (
            _tip_ride_update,  # type: ignore
            send_ride_receipt,  # type: ignore
        )

    tip = _q2(frozen_tip)
    settled_at = datetime.now(timezone.utc).isoformat()
    claimed = await db_supabase.update_one(
        "rides",
        {"id": ride_id, "payment_status": "processing"},
        {
            "payment_status": "paid",
            "paid_at": settled_at,
            "updated_at": settled_at,
            **_tip_ride_update(ride, tip),
        },
    )
    if not claimed:
        return False  # another writer finalised it first — idempotent no-op
    if ride.get("rider_id"):
        try:
            recovered_ride = {**ride, "payment_status": "paid", "paid_at": settled_at, "tip_amount": str(tip)}
            await maybe_send_auto_receipt(recovered_ride, ride["rider_id"], tip, send=send_ride_receipt)
        except Exception:
            logger.error("stripe_reconcile: recovered ride receipt failed ride=%s", ride_id, exc_info=True)
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

    Shipped dark on purpose: the mark-paid path moves money-state (marks a ride
    paid, restores its frozen tip, and routes a receipt), so it must be reviewed
    and validated in staging before an operator enables it in production. With
    the flag off this is a no-op and the rides remain detection-only.
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
