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
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from .. import db_supabase
    from ..services import ledger_service
    from .redis_client import redis_set_nx
except ImportError:  # python -m backend.server vs top-level
    import db_supabase  # type: ignore
    from services import ledger_service  # type: ignore
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

# Ride columns needed to decompose a process_payment charge. Explicit list —
# never select * in a loop (payment_retry idiom).
_RIDE_COLUMNS = "id,total_fare,grand_total,tax_amount,driver_earnings,tip_amount"

# Set after the first "function does not exist" error so a partial deploy
# (code live, migration 287 not yet applied) logs once, not every 15 minutes.
_rpc_missing_logged = False


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
    legs = ledger_service.build_charge_legs(
        total_cents=amount,
        driver_cents=to_cents(ride.get("driver_earnings")),
        tax_cents=to_cents(ride.get("tax_amount")),
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
            logger.error("[LEDGER-PROJ] work-queue fetch failed: {}", err)
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
            logger.error("[LEDGER-PROJ] ride batch fetch failed, deferring tick: {}", err)
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
                ledger_service._escalate(
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
            logger.error("[LEDGER-PROJ] projection raised for event {}", event.get("id"), exc_info=True)

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
            acquired = await redis_set_nx(_LOCK_KEY, _pod_id(), int(LEDGER_PROJECTION_INTERVAL_SECONDS * 1.5))
            if acquired:
                await project_pending_legs()
        except Exception:
            logger.error("ledger_projection_loop: tick raised", exc_info=True)
        # Heartbeat on both the acquired and lock-skipped paths so follower
        # replicas still look alive to the watchdog.
        _record_heartbeat(_LOOP_NAME)
        delta = LEDGER_PROJECTION_INTERVAL_SECONDS * 0.1
        await asyncio.sleep(LEDGER_PROJECTION_INTERVAL_SECONDS + random.uniform(-delta, delta))
