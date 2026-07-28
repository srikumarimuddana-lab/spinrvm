"""Pre-authorization capture sweeper.

Captures booking-time card holds whose tip window has elapsed.

When a rider completes a trip but never finishes the in-app settlement (never
opens the rate/tip screen), the manual-capture hold placed at booking would sit
uncaptured until Stripe expires it (~7 days) — at which point the money is gone
and the 0%-commission driver eats the fare. This loop closes that gap: a short
tip window after completion, then a single capture of (fare + any recorded tip)
against the held PaymentIntent — the same single-fee path the rider would have
triggered themselves.

Replay-safety contract (CLAUDE.md, Background loops):
  Runs on every replica. Two replays must not double-capture. Three layers:
    1. Redis leader lock (SET NX) — normally one pod runs a tick; this is a
       best-effort throttle, not a guarantee. When Redis is unavailable the
       in-process fallback lets every replica run, so it is NOT the safety net.
    2. Atomic DB claim — flip payment_status 'pending' → 'processing' asserting
       auth_status unchanged; only the replica whose UPDATE returns a row acts.
       This is the real double-capture guard and holds even with Redis down.
    3. Stripe idempotency_key on capture (ride-capture-{ride_id}-{cents}) — even
       if two replicas both win a claim race, Stripe dedupes the capture.

Settlement itself is delegated to services.payment_service.settle_card, which
already knows how to capture an open hold (and how to fall back to a fresh
charge if the hold is unusable), so this loop holds no payment logic of its own.
"""

import asyncio
import logging
import os
import random
import socket
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

try:
    from ..db import db
    from ..services.payment_service import send_ride_receipt, settle_card
    from .datetime_utils import parse_iso_utc
    from .redis_client import redis_set_nx
except ImportError:
    from db import db  # type: ignore
    from services.payment_service import send_ride_receipt, settle_card  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:  # pragma: no cover

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


logger = logging.getLogger(__name__)

# Minutes to wait after completion before auto-capturing a hold. Gives the rider
# time to add a tip on the rating screen so it captures on the same PaymentIntent.
# Well inside Stripe's ~7-day authorization lifetime.
TIP_WINDOW_MINUTES = 20
CAPTURE_INTERVAL_SECONDS = 300  # 5 minutes
_LOOP_NAME = "preauth_capture (5min)"


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _round(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pod_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def _capture_one(ride: dict) -> None:
    """Settle a single claimed ride by capturing its hold. Never raises."""
    ride_id = ride["id"]
    rider_id = ride.get("rider_id")
    grand = ride.get("grand_total")
    if grand is None:
        grand = ride.get("total_fare") or 0
    tip = _round(_d(ride.get("tip_amount") or 0))
    total_charge = _round(_d(grand) + tip)

    try:
        result = await settle_card(ride, ride_id, rider_id, total_charge, tip)
    except Exception as e:
        # settle_card normally writes 'paid'/'failed' itself; if it raised before
        # any write the ride stays 'processing' and the payment_retry loop
        # reconciles it after 30 min. Surface loudly — payment path error.
        logger.error("[preauth-capture] settle_card raised for ride %s: %s", ride_id, e, exc_info=True)
        return

    if result.success:
        try:
            await send_ride_receipt(ride, rider_id, tip)
        except Exception as exc:
            logger.error("[preauth-capture] receipt send failed for ride %s: %s", ride_id, exc)
        # Meta Purchase/FirstRide. This path settles a card ride WITHOUT going
        # through rides/payments.py::process_payment, so without this hook an
        # auto-captured hold — a common completion path — would move money and
        # emit no conversion at all. Guarded on already_paid so an idempotent
        # replay does not emit a second one; FirstRide stays once-per-user via
        # its own atomic claim regardless of which path gets there first.
        if not result.already_paid:
            try:
                from ..services.meta_conversions_service import send_ride_purchase_for_ride
            except ImportError:
                from services.meta_conversions_service import send_ride_purchase_for_ride  # type: ignore
            await send_ride_purchase_for_ride(ride, rider_id, result.charged_amount)
        logger.info("[preauth-capture] auto-captured hold for ride %s ($%s)", ride_id, total_charge)
    else:
        # Capture declined / hold unusable → settle_card has set payment_status
        # to 'failed'; the payment_retry loop will re-drive it. Info, not error:
        # this is the expected handoff, not a swallowed failure.
        logger.info(
            "[preauth-capture] settlement for ride %s did not complete (%s) — handed to retry loop",
            ride_id,
            result.error_code or result.error,
        )


async def _capture_tick() -> None:
    """Find completed card rides with an open hold past the tip window and
    capture them. One atomic claim per ride guards against double-capture."""
    try:
        rides = await db.get_rows(
            "rides",
            {
                "status": "completed",
                "payment_status": "pending",
                "auth_status": {"$in": ["authorized", "fare_only"]},
            },
            limit=50,
            order="ride_completed_at",
            columns=(
                "id,rider_id,driver_id,payment_method,payment_method_id,payment_status,"
                "auth_status,authorized_amount,payment_intent_id,total_fare,grand_total,"
                "tip_amount,driver_earnings,ride_completed_at,updated_at"
            ),
        )
    except Exception as e:
        logger.error("[preauth-capture] failed to fetch candidate rides: %s", e, exc_info=True)
        return

    now = datetime.now(timezone.utc)
    window_seconds = TIP_WINDOW_MINUTES * 60

    for ride in rides:
        completed_at = parse_iso_utc(ride.get("ride_completed_at") or ride.get("updated_at"))
        if completed_at is not None and (now - completed_at).total_seconds() < window_seconds:
            continue  # tip window still open — let the rider tip first

        auth_status = ride.get("auth_status")
        # Atomic claim: only the replica that flips pending → processing (while
        # auth_status is still the value we read) proceeds; others get None.
        claimed = await db.update_one(
            "rides",
            {"id": ride["id"], "payment_status": "pending", "auth_status": auth_status},
            {"$set": {"payment_status": "processing", "updated_at": now.isoformat()}},
        )
        if claimed is None:
            continue

        await _capture_one(ride)


async def preauth_capture_loop() -> None:
    """Background loop: capture expiring pre-auth holds every interval."""
    logger.info(
        f"Pre-auth capture sweeper started (interval={CAPTURE_INTERVAL_SECONDS}s, window={TIP_WINDOW_MINUTES}m)"
    )
    while True:
        # TTL = 2× interval so the lock outlives a slow tick (50 rides × Stripe
        # latency) even after the +10% sleep jitter; the atomic DB claim is the
        # real double-capture guard if a second replica ever races in.
        lock_ttl = int(CAPTURE_INTERVAL_SECONDS * 2)
        if not await redis_set_nx("spinr:preauth:capture:lock", _pod_id(), lock_ttl):
            _record_heartbeat(_LOOP_NAME)
            await asyncio.sleep(CAPTURE_INTERVAL_SECONDS)
            continue
        try:
            await _capture_tick()
        except Exception as e:
            logger.error(f"Pre-auth capture loop error: {e}", exc_info=True)
        _record_heartbeat(_LOOP_NAME)
        # ±10% jitter so replicas don't tick in lockstep.
        delta = CAPTURE_INTERVAL_SECONDS * 0.1
        await asyncio.sleep(CAPTURE_INTERVAL_SECONDS + random.uniform(-delta, delta))
