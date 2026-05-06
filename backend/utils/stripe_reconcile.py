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
from decimal import Decimal
from typing import Any, Dict, List

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase  # type: ignore
    from ..settings_loader import get_app_settings  # type: ignore
    from ..utils.redis_client import redis_set_nx  # type: ignore
except ImportError:
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

_LOCK_KEY = "spinr:stripe:reconcile:lock"
_LOCK_TTL_SECONDS = 23 * 60 * 60
_RUN_HOUR_UTC = 2  # 02:00 UTC daily
_WINDOW_DAYS = 1  # reconcile the previous calendar day


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
                columns="id,payment_intent_id,fare,status,completed_at",
                limit=2000,
            )
            or []
        )
        # Filter to yesterday's completed rides in Python (avoids complex date filter)
        db_rides = [
            r
            for r in db_rides
            if r.get("payment_intent_id")
            and r.get("completed_at")
            and _in_window(r["completed_at"], window_start, window_end)
        ]
    except Exception:
        logger.error("stripe_reconcile: DB rides query failed", exc_info=True)
        return

    # Build lookup: pi_id → ride
    db_pi_to_ride: Dict[str, Dict] = {r["payment_intent_id"]: r for r in db_rides if r.get("payment_intent_id")}

    discrepancies: List[Dict[str, Any]] = []

    # ── 3a. Check each paid DB ride against Stripe ───────────────────────
    for ride in db_rides:
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
            # Skip amount check — amount_received is meaningless for non-succeeded PIs
            # and would generate a spurious second discrepancy for the same ride.
            continue

        # Amount check — Stripe amount_received is in cents; DB fare is CAD dollars
        if ride.get("fare") is not None:
            expected_cents = int(Decimal(str(ride["fare"])) * 100)
            actual_cents = pi.get("amount_received", 0)
            if expected_cents != actual_cents:
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

    # ── 4. Write summary to audit_logs ──────────────────────────────────
    summary = {
        "date": yesterday.isoformat(),
        "stripe_pis_checked": len(stripe_pis),
        "db_rides_checked": len(db_rides),
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
