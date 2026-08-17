"""Tip batch-charge loop.

Collects tips that could not ride on the booking hold (see
``services/pending_tip_service``) and credits the driver once the money has
actually arrived.

Why batch rather than charge each tip on arrival: Stripe's fixed fee is $0.30
regardless of amount, so a lone $2 tip loses 18% to fees. Waiting until a rider
owes enough — or until a tip has been outstanding too long — collects several
tips on one PaymentIntent and one fixed fee.

Two triggers, whichever fires first:
  * BATCH_THRESHOLD_CAD — enough owed to make the fee ratio sane.
  * MAX_AGE_DAYS        — a ceiling so a driver never waits indefinitely on a
                          rider who tipped once and never rode again. Without
                          this, a $2 debt from a lapsed rider is never collected.

Replay-safety contract (CLAUDE.md, Background loops):
  Runs on every replica. Two replays must not double-charge. Three layers:
    1. Redis leader lock (SET NX) — best-effort throttle so replicas don't all
       wake together. NOT the safety net; with Redis down every replica runs.
    2. Atomic DB claim — flip each row 'owed'/'failed' → 'charging' asserting
       the status we read. Only rows whose UPDATE returned a row are charged.
       This is the real double-charge guard and holds with Redis down.
    3. Stripe idempotency key — charge_ancillary_fee keys on
       (fee_type, ride_id, cents, payment_method), so even if two replicas both
       win a claim race, Stripe dedupes the charge itself.

MONEY RULE: a driver is credited ONLY on the transition to 'charged'. A failed
collection must never leave a driver credited with money we did not take — that
is precisely the bug this whole subsystem exists to fix.
"""

import asyncio
import logging
import os
import random
import socket
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List

try:
    from .. import db_supabase
    from ..utils.earnings_snapshot import build_earnings_snapshot
    from ..utils.metrics import inc as _metric_inc
    from ..utils.stripe_charge import charge_ancillary_fee
    from .redis_client import redis_set_nx
except ImportError:  # pragma: no cover — dual import pattern, see CLAUDE.md
    import db_supabase  # type: ignore
    from utils.earnings_snapshot import build_earnings_snapshot  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore
    from utils.stripe_charge import charge_ancillary_fee  # type: ignore

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:  # pragma: no cover

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


logger = logging.getLogger(__name__)

BATCH_THRESHOLD_CAD = Decimal("10.00")
MAX_AGE_DAYS = 7
BATCH_INTERVAL_SECONDS = 1800  # 30 minutes — tips are not time-critical
MAX_ATTEMPTS = 6
_LOOP_NAME = "tip_batch_charge (30min)"


def _d(v: Any) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _round(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pod_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _is_due(rows: List[Dict[str, Any]], now: datetime) -> bool:
    """Whether this rider's outstanding tips should be collected now."""
    total = _round(sum((_d(r.get("amount")) for r in rows), Decimal("0")))
    if total >= BATCH_THRESHOLD_CAD:
        return True
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    for r in rows:
        created = r.get("created_at")
        if not created:
            # No timestamp to age against — collect rather than strand it.
            return True
        try:
            ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return True
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts <= cutoff:
            return True
    return False


async def _credit_driver_for_tip(row: Dict[str, Any], pi_id: str) -> None:
    """Apply a COLLECTED tip to the ride and the driver's earnings.

    Only ever called after a successful charge. Writes tip_amount alongside
    driver_earnings and rebuilds driver_earnings_snapshot through the Decimal
    builder, so the frozen total stays an exact component sum (it feeds T4A).
    """
    ride_id = row.get("ride_id")
    tip = _round(_d(row.get("amount")))
    try:
        ride = await db_supabase.get_ride(ride_id)
        if not ride:
            logger.error("[tip-batch] ride %s vanished after charging its tip pi=%s", ride_id, pi_id)
            return

        payload: Dict[str, Any] = {
            "tip_amount": float(_round(_d(ride.get("tip_amount")) + tip)),
            "driver_earnings": float(_round(_d(ride.get("driver_earnings")) + tip)),
        }
        des = ride.get("driver_earnings_snapshot")
        if des and isinstance(des, dict):
            des.update(
                build_earnings_snapshot(
                    fare=des.get("fare") or 0,
                    tip=_round(_d(des.get("tip")) + tip),
                    incentive=des.get("incentive") or 0,
                    tax=des.get("tax") or 0,
                    cancel_fee=des.get("cancel_fee") or 0,
                )
            )
            payload["driver_earnings_snapshot"] = des
        await db_supabase.update_ride(ride_id, payload)
    except Exception as e:
        # The money IS collected — the row is already 'charged'. Failing to
        # credit here means a driver is short, which needs a human, so this is
        # an error with the ride id, not a warning.
        logger.error(
            "[tip-batch] COLLECTED tip for ride %s (pi=%s) but failed to credit driver: %s",
            ride_id,
            pi_id,
            e,
            exc_info=True,
        )


async def _charge_rider_batch(rider_id: str, rows: List[Dict[str, Any]]) -> None:
    """Claim, charge, and settle one rider's outstanding tips. Never raises."""
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Atomic claim. Each row is claimed on the status we actually read, so a
    #    replica that lost the race gets None and skips it. Claim BEFORE
    #    touching Stripe: a claimed-but-uncharged row is recoverable (it reverts
    #    to 'failed' and retries), whereas charging first could double-charge.
    claimed: List[Dict[str, Any]] = []
    for row in rows:
        try:
            got = await db_supabase.update_one(
                "pending_tips",
                {"id": row["id"], "status": row.get("status")},
                {"$set": {"status": "charging", "updated_at": now_iso}},
            )
        except Exception as e:
            logger.error("[tip-batch] claim failed for pending_tip %s: %s", row.get("id"), e, exc_info=True)
            continue
        if got is not None:
            claimed.append(row)

    if not claimed:
        return

    total = _round(sum((_d(r.get("amount")) for r in claimed), Decimal("0")))
    # Oldest claimed row anchors the charge: it gives charge_ancillary_fee a
    # concrete ride for metadata, and makes the Stripe idempotency key stable
    # across replays of the same batch.
    anchor = min(claimed, key=lambda r: str(r.get("created_at") or ""))
    anchor_ride_id = anchor.get("ride_id")

    async def _release(status: str, error: str | None = None) -> None:
        for r in claimed:
            try:
                await db_supabase.update_one(
                    "pending_tips",
                    {"id": r["id"]},
                    {
                        "$set": {
                            "status": status,
                            "attempts": int(r.get("attempts") or 0) + 1,
                            "last_error": (error or "")[:500],
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                )
            except Exception as e:
                logger.error("[tip-batch] could not release pending_tip %s: %s", r.get("id"), e, exc_info=True)

    try:
        rider = await db_supabase.get_user_by_id(rider_id)
        ride = await db_supabase.get_ride(anchor_ride_id)
    except Exception as e:
        logger.error("[tip-batch] lookup failed for rider %s: %s", rider_id, e, exc_info=True)
        await _release("failed", str(e))
        return

    stripe_customer_id = (rider or {}).get("stripe_customer_id")
    payment_method_id = (ride or {}).get("payment_method_id") or (rider or {}).get("default_payment_method")
    if not stripe_customer_id or not payment_method_id:
        # Card removed since the ride. Not retryable by this loop — the rider has
        # to add a card — so leave it 'failed' and let it age; support can see it.
        logger.error(
            "[tip-batch] no usable card for rider %s (%s owed across %d tips)",
            rider_id,
            total,
            len(claimed),
        )
        await _release("failed", "no payment method on file")
        _metric_inc("spinr_payment_tip_batch_total", {"outcome": "no_card"})
        return

    outcome = await charge_ancillary_fee(
        ride=ride or {"id": anchor_ride_id},
        rider_id=rider_id,
        amount=total,
        payment_method_id=payment_method_id,
        stripe_customer_id=stripe_customer_id,
        fee_type="tip_batch",
    )

    if outcome.status != "succeeded":
        # requires_action lands here too: an off-session tip charge that needs
        # SCA cannot be completed by a background loop. It stays owed, and the
        # rider is prompted next time they open the app.
        logger.error(
            "[tip-batch] charge failed for rider %s amount=%s status=%s error=%s",
            rider_id,
            total,
            outcome.status,
            outcome.error_message,
        )
        await _release("failed", outcome.error_message or outcome.status)
        _metric_inc("spinr_payment_tip_batch_total", {"outcome": "failed"})
        return

    # 2. Money is in. Mark charged FIRST so a crash here cannot re-charge, then
    #    credit drivers. Crediting is recoverable by hand; double-charging a
    #    rider is not.
    pi_id = outcome.payment_intent_id
    charged_at = datetime.now(timezone.utc).isoformat()
    for r in claimed:
        try:
            await db_supabase.update_one(
                "pending_tips",
                {"id": r["id"]},
                {
                    "$set": {
                        "status": "charged",
                        "batch_payment_intent_id": pi_id,
                        "charged_at": charged_at,
                        "updated_at": charged_at,
                    }
                },
            )
        except Exception as e:
            logger.error(
                "[tip-batch] CHARGED rider %s (pi=%s) but could not mark tip %s charged: %s",
                rider_id,
                pi_id,
                r.get("id"),
                e,
                exc_info=True,
            )

    for r in claimed:
        await _credit_driver_for_tip(r, pi_id)

    logger.info(
        "[tip-batch] collected %s across %d tips for rider %s (pi=%s)",
        total,
        len(claimed),
        rider_id,
        pi_id,
    )
    _metric_inc("spinr_payment_tip_batch_total", {"outcome": "success"})


async def _batch_tick() -> None:
    """Group outstanding tips by rider and collect the ones that are due."""
    try:
        rows = await db_supabase.get_rows(
            "pending_tips",
            {"status": {"$in": ["owed", "failed"]}, "attempts": {"$lt": MAX_ATTEMPTS}},
            limit=500,
            order="created_at",
        )
    except Exception as e:
        logger.error("[tip-batch] failed to fetch outstanding tips: %s", e, exc_info=True)
        return

    by_rider: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows or []:
        rider_id = row.get("rider_id")
        if rider_id:
            by_rider.setdefault(rider_id, []).append(row)

    now = datetime.now(timezone.utc)
    for rider_id, rider_rows in by_rider.items():
        if not _is_due(rider_rows, now):
            continue
        await _charge_rider_batch(rider_id, rider_rows)


async def tip_batch_charge_loop() -> None:
    """Background loop: collect outstanding tips on a batched charge."""
    logger.info(
        f"Tip batch-charge loop started (interval={BATCH_INTERVAL_SECONDS}s, "
        f"threshold=${BATCH_THRESHOLD_CAD}, max_age={MAX_AGE_DAYS}d)"
    )
    while True:
        try:
            lock_ttl = int(BATCH_INTERVAL_SECONDS * 2)
            try:
                got_lock = await redis_set_nx("spinr:tips:batch:lock", _pod_id(), lock_ttl)
            except Exception as lock_err:
                # Redis down — proceed anyway. The per-row DB claim is the real
                # double-charge guard, so losing the throttle costs wasted work,
                # never a duplicate charge.
                logger.error(f"tip_batch_charge: leader lock unavailable ({lock_err}), proceeding without it")
                got_lock = True
            if got_lock:
                await _batch_tick()
        except Exception:
            logger.error("tip_batch_charge tick failed", exc_info=True)

        _record_heartbeat(_LOOP_NAME)
        delta = BATCH_INTERVAL_SECONDS * 0.1
        await asyncio.sleep(BATCH_INTERVAL_SECONDS + random.uniform(-delta, delta))
