"""Double-entry leg projection — derives financial_event_entries from headers.

One-line purpose: walk `financial_events` headers that have no legs yet
(oldest first, via the `financial_events_missing_legs` RPC, migration 287),
decompose each into balanced debit/credit legs, and batch-insert them.
Interval: 15 minutes. Reads `financial_events` + `rides`; writes only
`financial_event_entries`.

Why a projection instead of writing legs in the request path: the ride row
already carries everything the legs need (`grand_total`, `tax_amount`,
`driver_earnings`, fee splits in event metadata), so legs are a *derived
view* — recomputable, backfillable over all history, and never able to slow
down or fail a settlement. This is the low-infra equivalent of deriving
accounting records from an event stream: the durable state row is the
source of truth, the projection materializes the accounting overlay.

Replay safety (every replica runs this loop):
- The UNIQUE(event_id, account, side) constraint on financial_event_entries
  plus the single batched ``insert_many`` per event mean a concurrent
  duplicate projection fails whole-statement with 23505 — which the ledger
  writer already treats as "written". Two pods projecting the same batch is
  wasted work, never wrong data.
- The Redis lock below is therefore purely a throttle (same doctrine as
  payment_retry.py): it is NEVER relied on for correctness, and the
  in-process Redis fallback making it a non-lock in dev is acceptable.

Degraded decomposition: when an event cannot be split (ride row gone,
metadata missing on historical rows, amounts that do not reconcile), the
projection books the full amount to platform_revenue rather than skipping.
Skipping would wedge the queue: the RPC returns the oldest N leg-less
headers, so a page of permanently-unprojectable rows would sit at the head
forever and newer events would never project. A degraded entry is balanced
and truthful at the money-in level — platform_revenue is already the
documented plug account — and it is flagged loudly (ALERT_LEGS_DEGRADED)
so finance can re-derive the split later if needed.
"""

import asyncio
import random
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from .. import db_supabase
    from ..services import ledger_service
    from .datetime_utils import parse_iso_utc
    from .redis_client import redis_set_nx
except ImportError:  # python -m backend.server vs top-level
    import db_supabase  # type: ignore
    from services import ledger_service  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:
    try:
        from utils.loop_monitor import record_heartbeat as _record_heartbeat  # type: ignore
    except ImportError:

        def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
            pass


LEDGER_PROJECTION_INTERVAL_SECONDS = 900  # 15 min
_LOCK_KEY = "spinr:ledger:projection:lock"
_LOOP_NAME = "ledger_projection (15min)"
_BATCH_LIMIT = 200

# Per-tick sleep jitter, as a fraction of the interval, so replicas don't tick
# in lockstep. Minimum sleep is therefore interval * (1 - _JITTER_FRACTION).
_JITTER_FRACTION = 0.1

# The throttle lock's TTL must be SHORTER than that minimum sleep. Longer, and
# the pod that ran the last tick wakes to find its OWN key still alive, fails
# SET NX, and sleeps another full interval — so the loop ticks every ~2
# intervals and the documented 15-minute cadence silently becomes ~30, halving
# backfill throughput.
#
# Deliberate divergence from payment_retry.py, whose `interval * 1.5` (and the
# comment claiming it "expires before the next election") has exactly that bug.
# Filed as ACTION_ITEMS B21 rather than changed here — those loops have their
# own tuning and blast radius.
#
# The cost of the shorter TTL is a brief window each cycle where no pod holds
# the lock, so two replicas can occasionally run the same tick. That is
# harmless by construction: correctness comes from UNIQUE(event_id, account,
# side) plus the whole-batch insert, and this lock is only ever a throttle
# (module docstring). The extra 0.05 leaves headroom under the 0.9 floor.
_LOCK_TTL_SECONDS = int(LEDGER_PROJECTION_INTERVAL_SECONDS * (1 - _JITTER_FRACTION - 0.05))

# Ride columns needed to decompose a process_payment charge. Explicit list —
# never select * in a loop (payment_retry idiom). discount_amount is required,
# not optional: driver_earnings is computed pre-discount while the rider is
# charged post-discount, so without it every promo ride looks like a fare whose
# parts exceed the whole. See build_charge_legs. payment_status is required by
# the B20 settlement-confirmed gate below (see _fare_ready_to_decompose).
_RIDE_COLUMNS = "id,total_fare,grand_total,tax_amount,driver_earnings,tip_amount,discount_amount,payment_status"

# Set after the first "function does not exist" error so a partial deploy
# (code live, migration 287 not yet applied) logs once, not every 15 minutes.
_rpc_missing_logged = False

# B20: how long a fare/tip event may wait, past migration 287's 30-minute
# work-queue gate, for its ride's payment_status to reach 'paid' before
# decomposition gives up waiting and falls back to degraded.
#
# Why the 30-minute RPC gate isn't enough on its own: it covers the normal
# "header written before update_ride lands" gap in services/payment_service.py
# (_finalize_card_settlement's legacy two-write path — record_payment_event,
# THEN update_ride applies the tip delta to rides.driver_earnings /
# rides.tax_amount and flips payment_status to 'paid'). But if that second
# write fails outright — the "Charge {} succeeded but ride {} DB update
# failed" branch in _finalize_card_settlement, which returns 503 and leaves
# the ride stuck at whatever payment_status it had (typically 'processing')
# — the ride can sit stuck for far longer than 30 minutes while still being
# the oldest row in the queue, and driver_earnings/tax_amount stay at their
# stale pre-tip values.
#
# 6h, not 24h, and deliberately shorter than payment_retry.py's own 24h
# ceiling on the same ride:
#   - payment_retry.retry_failed_payments only waits 30 min before touching a
#     'processing' ride, then (for exactly this "Stripe already succeeded"
#     case) fixes payment_status on that very tick — so the legitimate
#     recovery path this gate is waiting for normally lands within
#     ~30-60 minutes, not hours. 6h is a generous multiple of that, not a
#     tight timeout.
#   - utils/reconciliation.py's daily leg-completeness check pages on-call
#     when the projection work-queue HEAD has not advanced in 24h (a stuck
#     event sitting un-projected pins the head — see that module's
#     _check_leg_completeness). Keeping this fallback well under 24h means a
#     genuinely stuck row degrades and clears the head long before that
#     separate alarm could fire on it, instead of the two racing.
#   - A ride payment_retry could not fix (MAX_RETRIES exhausted, or the
#     stale-invoice-sentinel path) already pages an admin well inside 6h, so
#     nothing here waits past the point where a human has already been
#     notified. Continuing to wait past that would only let one permanently
#     -stuck row sit at the head of the oldest-first queue (the exact
#     starvation the degraded-legs design exists to avoid — see module
#     docstring). Falling back to degraded at 6h books the correct TOTAL
#     (still balanced, still auditable) instead of a wrong tip/platform
#     split, matching every other degraded reason above.
_SETTLEMENT_FALLBACK_SECONDS = 6 * 60 * 60


def _fare_ready_to_decompose(ride: Dict[str, Any], event: Dict[str, Any]) -> tuple:
    """Return (ready: bool, timed_out: bool) for a process_payment fare event.

    ``ready`` — rides.driver_earnings / rides.tax_amount are trustworthy
    (payment_status has reached 'paid', so any tip delta has landed).

    ``timed_out`` — only meaningful when ``ready`` is False: the event has
    been waiting longer than _SETTLEMENT_FALLBACK_SECONDS with no sign the
    ride will ever settle, so the caller should degrade instead of retrying
    forever. See _SETTLEMENT_FALLBACK_SECONDS for why 'paid' — not merely
    'not failed/not cancelled' — is the bar: any other value (including
    'processing') is exactly the state a stuck post-charge update leaves
    behind, which is the B20 bug this gates against.
    """
    if ride.get("payment_status") == "paid":
        return True, False
    created_at = parse_iso_utc(event.get("created_at"))
    if created_at is None:
        # Can't age an event with no parseable created_at — never block it
        # forever on a check we can't evaluate; fall through to the existing
        # (pre-B20) behavior of decomposing straight from the ride row.
        return True, False
    age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
    return False, age_seconds >= _SETTLEMENT_FALLBACK_SECONDS


def _pod_id() -> str:
    import os

    return f"{socket.gethostname()}:{os.getpid()}"


def _is_missing_function_error(exc: Exception) -> bool:
    text = str(exc)
    return "PGRST202" in text or "does not exist" in text or "Could not find the function" in text


def _degraded_legs(event_type: str, amount_cents: int) -> List["ledger_service.Leg"]:
    """Whole-amount fallback: money in/out against platform_revenue only."""
    Leg = ledger_service.Leg
    if event_type == "stripe_refund":
        return [
            Leg(ledger_service.ACCT_PLATFORM_REVENUE, ledger_service.DEBIT, amount_cents),
            Leg(ledger_service.ACCT_STRIPE_RECEIVABLE, ledger_service.CREDIT, amount_cents),
        ]
    return [
        Leg(ledger_service.ACCT_STRIPE_RECEIVABLE, ledger_service.DEBIT, amount_cents),
        Leg(ledger_service.ACCT_PLATFORM_REVENUE, ledger_service.CREDIT, amount_cents),
    ]


def _decompose(event: Dict[str, Any], ride: Optional[Dict[str, Any]]) -> tuple:
    """Return (legs, degraded: bool, reason: str | None) for one header.

    Routing is by event_type first, then metadata["source"] — the source
    string is what the writers stamp (process_payment, cancellation_fee,
    scheduled_cancel_notice_fee, charge.refunded).
    """
    to_cents = ledger_service.to_cents
    event_type = event.get("event_type") or ""
    meta = event.get("metadata") or {}
    source = meta.get("source") or ""
    amount = abs(int(event.get("delta_cents") or 0))

    if amount <= 0:
        # Defensive only: the RPC filters delta_cents <> 0 at the source
        # (migration 287), because a $0 header has no money movement to
        # decompose and would otherwise occupy the oldest-first queue forever.
        return [], False, "zero_amount"

    if event_type == "stripe_refund":
        tax_reversed = to_cents(meta.get("tax_reversed") or 0)
        legs = ledger_service.build_refund_legs(refund_cents=amount, tax_reversed_cents=tax_reversed)
        if legs:
            return legs, False, None
        return _degraded_legs(event_type, amount), True, "refund_build_failed"

    if source == "cancellation_fee":
        fee_driver = meta.get("fee_driver")
        if fee_driver is None:
            # Historical rows predate the fee-split metadata (added 2026-08-07).
            return _degraded_legs(event_type, amount), True, "no_fee_split_metadata"
        legs = ledger_service.build_charge_legs(total_cents=amount, driver_cents=to_cents(fee_driver), tax_cents=0)
        if legs:
            return legs, False, None
        return _degraded_legs(event_type, amount), True, "fee_split_inconsistent"

    if source == "scheduled_cancel_notice_fee":
        # Rider-only pre-dispatch fee: no driver, no tax — all platform.
        return _degraded_legs(event_type, amount), False, None

    # Default: a ride fare settlement (source process_payment, or webhook
    # settles that reuse it). Decompose from the ride row.
    if not ride:
        return _degraded_legs(event_type, amount), True, "ride_missing"

    # B20: rides.driver_earnings / rides.tax_amount only reflect the tip
    # split once payment_status has actually reached 'paid'. Cancellation-fee
    # and notice-fee events never reach this branch (both return above,
    # unconditionally, regardless of ride.payment_status) — this gate is
    # scoped to fare/tip events only, by construction.
    ready, timed_out = _fare_ready_to_decompose(ride, event)
    if not ready:
        if timed_out:
            return _degraded_legs(event_type, amount), True, "payment_not_settled_timeout"
        # Not stuck (yet) — just skip this tick and let the RPC hand it back
        # next time, unbooked. Deliberately NOT a degraded entry: booking now
        # would risk exactly the tip-misclassification this gate exists to
        # prevent, and this event keeps its slot in the oldest-first queue
        # only up to _SETTLEMENT_FALLBACK_SECONDS (see that constant) before
        # falling through to the degraded branch above instead of blocking
        # newer events forever.
        return [], False, "awaiting_payment_settlement"

    legs = ledger_service.build_charge_legs(
        total_cents=amount,
        driver_cents=to_cents(ride.get("driver_earnings")),
        tax_cents=to_cents(ride.get("tax_amount")),
        # The rider paid `amount` (post-discount); the driver is owed a share
        # computed pre-discount. promo_expense carries the difference, which is
        # what keeps the entry balanced instead of degrading.
        promo_cents=to_cents(ride.get("discount_amount")),
    )
    if legs:
        return legs, False, None
    return _degraded_legs(event_type, amount), True, "amounts_inconsistent"


async def project_pending_legs(limit: int = _BATCH_LIMIT) -> Dict[str, int]:
    """One projection tick. Returns counters; never raises.

    Flag is checked ONCE here (not per event) — write_legs is called with
    check_flag=False for that reason.
    """
    global _rpc_missing_logged
    stats = {"fetched": 0, "projected": 0, "degraded": 0, "skipped": 0, "failed": 0}

    if not await ledger_service.double_entry_enabled():
        return stats

    try:
        events = await db_supabase.rpc("financial_events_missing_legs", {"p_limit": limit}) or []
    except Exception as err:
        if _is_missing_function_error(err):
            if not _rpc_missing_logged:
                logger.warning(
                    "[LEDGER-PROJ] financial_events_missing_legs RPC absent — "
                    "migration 287 not applied yet; projection idle until it is"
                )
                _rpc_missing_logged = True
        else:
            # opt(exception=...) + details["original"]: run_sync's DatabaseError
            # stringifies to a constant, so a bare {} loses the Postgres error.
            logger.opt(exception=err).error(
                "[LEDGER-PROJ] work-queue fetch failed: {} (original={})",
                err,
                (getattr(err, "details", None) or {}).get("original", "n/a"),
            )
        return stats

    stats["fetched"] = len(events)
    if not events:
        return stats

    # Batch-fetch the referenced rides in one query (no N+1).
    ride_ids = sorted({e["ride_id"] for e in events if e.get("ride_id")})
    rides_by_id: Dict[str, Dict[str, Any]] = {}
    if ride_ids:
        try:
            rows = await db_supabase.get_rows(
                "rides", {"id": {"$in": ride_ids}}, limit=len(ride_ids), columns=_RIDE_COLUMNS
            )
            rides_by_id = {r["id"]: r for r in (rows or [])}
        except Exception as err:
            # Without rides every fare event would project degraded — skip the
            # tick instead and let the next one retry with a healthy DB.
            logger.opt(exception=err).error(
                "[LEDGER-PROJ] ride batch fetch failed, deferring tick: {} (original={})",
                err,
                (getattr(err, "details", None) or {}).get("original", "n/a"),
            )
            return stats

    for event in events:
        try:
            legs, degraded, reason = _decompose(event, rides_by_id.get(event.get("ride_id")))
            if not legs:
                stats["skipped"] += 1
                continue
            if degraded:
                stats["degraded"] += 1
                logger.error(
                    "[LEDGER-PROJ] degraded decomposition for event {} ride {} ({}): {}",
                    event.get("id"),
                    event.get("ride_id"),
                    event.get("event_type"),
                    reason,
                )
                ledger_service.escalate(
                    "LEDGER LEGS DEGRADED — booked whole to platform_revenue",
                    {
                        "event_id": event.get("id"),
                        "ride_id": event.get("ride_id"),
                        "event_type": event.get("event_type"),
                        "reason": reason,
                    },
                    alert=ledger_service.ALERT_LEGS_DEGRADED,
                )
            ok = await ledger_service.write_legs(
                event["id"],
                legs,
                ride_id=event.get("ride_id"),
                event_type=event.get("event_type") or "",
                check_flag=False,
            )
            if ok:
                stats["projected"] += 1
            else:
                stats["failed"] += 1
        except Exception:
            # Per-item isolation: one bad row never stops the batch.
            stats["failed"] += 1
            logger.opt(exception=True).error("[LEDGER-PROJ] projection raised for event {}", event.get("id"))

    if stats["projected"] or stats["degraded"] or stats["failed"]:
        logger.info(
            "[LEDGER-PROJ] tick: fetched={fetched} projected={projected} "
            "degraded={degraded} skipped={skipped} failed={failed}",
            **stats,
        )
    return stats


async def ledger_projection_loop() -> None:
    """Background loop shell. Lock is a throttle, not the correctness guard."""
    logger.info("Ledger projection loop started (interval={}s)", LEDGER_PROJECTION_INTERVAL_SECONDS)
    while True:
        try:
            acquired = await redis_set_nx(_LOCK_KEY, _pod_id(), _LOCK_TTL_SECONDS)
            if acquired:
                await project_pending_legs()
        except Exception:
            logger.opt(exception=True).error("ledger_projection_loop: tick raised")
        # Heartbeat on both the acquired and lock-skipped paths so follower
        # replicas still look alive to the watchdog.
        _record_heartbeat(_LOOP_NAME)
        delta = LEDGER_PROJECTION_INTERVAL_SECONDS * _JITTER_FRACTION
        await asyncio.sleep(LEDGER_PROJECTION_INTERVAL_SECONDS + random.uniform(-delta, delta))
