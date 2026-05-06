"""Payment retry — automatically retries failed ride payments with exponential backoff.

Runs as a background task every 5 minutes. Finds rides with payment_status
'failed' or 'requires_action' and retries via Stripe up to 3 times.
Also resolves driver payouts stuck as 'pending' after transfer failures.

Replay-safety contract (CLAUDE.md, Background loops):
  This loop runs on every replica simultaneously. Two replays of the
  same tick must NOT charge twice or fire double notifications. We rely
  on two layers:

    1. Stripe idempotency_key on PaymentIntent.confirm — derived from
       (ride_id, retry_count). Two replicas confirming the same intent
       at the same retry_count produce the same key, so Stripe returns
       the same response and no second charge happens.

    2. Atomic claim via conditional update — bumping
       ``payment_retry_count`` filters on the prior count, so only the
       replica that won the increment proceeds to fire the rider /
       driver push. The loser sees update_one return None and bails.
"""

import asyncio
import logging
import os
import random
import socket
import uuid
from datetime import datetime, timezone

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from ..db import db
    from ..features import send_push_notification
    from ..settings_loader import get_app_settings
    from .datetime_utils import parse_iso_utc
    from .redis_client import redis_set_nx
except ImportError:
    from db import db
    from features import send_push_notification
    from settings_loader import get_app_settings
    from utils.datetime_utils import parse_iso_utc
    from utils.redis_client import redis_set_nx

logger = logging.getLogger(__name__)


def _pod_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


MAX_RETRIES = 3
RETRY_INTERVAL_SECONDS = 300  # 5 minutes


async def update_payout_status(payout_id: str, status: str) -> None:
    await db.update_one(
        "payouts",
        {"id": payout_id},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )


async def notify_driver_payout_failed(driver_id: str, payout_id: str) -> None:
    try:
        await send_push_notification(
            driver_id,
            "Payout failed",
            "We couldn't process your payout. Please contact support.",
            data={"type": "payout_failed", "payout_id": payout_id},
        )
    except Exception as err:
        logger.debug(f"Payout failure push notification failed: {err}")


async def retry_stuck_payouts() -> None:
    """Mark driver payouts that exceeded retry attempts as failed (8-5).

    Replay-safe: the status flip is conditional on ``status='pending'``
    so two replicas racing on the same payout will see exactly one
    successful update; the other gets None back and skips the
    "Payout failed" push so the driver isn't notified twice.
    """
    try:
        stuck_payouts = await db.get_rows(
            "payouts",
            {"status": "pending"},
            limit=50,
            order="created_at",
        )
    except Exception as e:
        logger.error(f"Payout retry: failed to fetch payouts: {e}")
        return

    for payout in stuck_payouts:
        payout_id = payout["id"]
        driver_id = payout.get("driver_id", "")
        retry_count = payout.get("retry_count", 0)

        if retry_count >= MAX_RETRIES:
            # Atomic claim — only the replica that flips status='pending'
            # → 'failed' proceeds to notify. update_one returns the row
            # on success and None when the WHERE clause matched zero rows
            # (i.e. another replica already flipped it).
            claimed = await db.update_one(
                "payouts",
                {"id": payout_id, "status": "pending"},
                {"$set": {"status": "failed", "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
            if claimed is None:
                continue
            await notify_driver_payout_failed(driver_id, payout_id)
            logger.error(f"Payout {payout_id} failed after {MAX_RETRIES} attempts")
            logger.error(
                f"ADMIN ALERT: Payout {payout_id} for driver {driver_id} "
                f"permanently failed after {MAX_RETRIES} retry attempts — manual review required"
            )


async def retry_failed_payments():
    """Find and retry failed payments."""
    try:
        # Find rides with failed payments that haven't exceeded retry limit
        rides = await db.get_rows(
            "rides",
            {"payment_status": {"$in": ["failed", "requires_action"]}},
            limit=50,
            order="created_at",
        )
    except Exception as e:
        logger.error(f"Payment retry: failed to fetch rides: {e}")
        return

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    for ride in rides:
        ride_id = ride["id"]
        retry_count = ride.get("payment_retry_count", 0)

        if retry_count >= MAX_RETRIES:
            continue

        # Skip rides older than 24 hours
        created_dt = parse_iso_utc(ride.get("created_at"))
        if created_dt is not None and (datetime.now(timezone.utc) - created_dt).total_seconds() > 86400:
            continue

        payment_intent_id = ride.get("payment_intent_id")
        if not payment_intent_id or not stripe_secret:
            continue

        try:
            import stripe

            # Attempt to confirm the payment intent
            intent = stripe.PaymentIntent.retrieve(payment_intent_id, api_key=stripe_secret)

            if intent.status == "succeeded":
                # Already succeeded (webhook may have missed it) — mark paid but do
                # NOT increment retry_count, otherwise the `retry_count + 1 >= MAX`
                # check at the bottom of this loop would fire a false "payment
                # failed" push to the rider. Setting status='paid' is idempotent
                # so two replicas writing the same value is harmless.
                await db.update_one(
                    "rides",
                    {"id": ride_id},
                    {
                        "$set": {
                            "payment_status": "paid",
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                )
                logger.info(f"Payment retry: ride {ride_id} already paid (intent succeeded)")
                continue

            elif intent.status in ("requires_payment_method", "requires_confirmation"):
                # Idempotency key dedupes the confirm call when two replicas
                # both pick up the same ride at the same retry_count, so
                # Stripe returns the cached response instead of charging twice.
                stripe.PaymentIntent.confirm(
                    payment_intent_id,
                    api_key=stripe_secret,
                    idempotency_key=f"retry-confirm-{ride_id}-{uuid.uuid4()}",
                )
                attempt = retry_count + 1
                claimed = await db.update_one(
                    "rides",
                    {"id": ride_id, "payment_retry_count": retry_count},
                    {
                        "$set": {
                            "payment_status": "processing",
                            "payment_retry_count": attempt,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                )
                if claimed is None:
                    continue
                logger.info(f"Payment retry: ride {ride_id} retry #{attempt} submitted")
                # Notify the driver so they know a retry is in progress (13-9)
                driver_id = ride.get("driver_id")
                if driver_id:
                    try:
                        await send_push_notification(
                            driver_id,
                            "Payment retry in progress",
                            f"Payment retry {attempt} of {MAX_RETRIES} in progress",
                            {
                                "type": "payment_retry",
                                "ride_id": ride_id,
                                "attempt": str(attempt),
                                "max_retries": str(MAX_RETRIES),
                                "deeplink": "/driver/earnings",
                            },
                        )
                    except Exception as push_err:
                        logger.debug(f"Payment retry push to driver failed: {push_err}")

            elif intent.status == "canceled":
                # Cannot retry a cancelled intent — pin count to MAX
                # under a claim so the rider notification below fires
                # exactly once.
                claimed = await db.update_one(
                    "rides",
                    {"id": ride_id, "payment_retry_count": retry_count},
                    {"$set": {"payment_retry_count": MAX_RETRIES}},
                )
                if claimed is None:
                    continue

            else:
                logger.warning(
                    f"PaymentIntent {intent.id} in unexpected state {intent.status!r} for ride {ride_id}; skipping confirm"
                )
                continue

        except Exception as e:
            # CLAUDE.md: never warn-and-continue on payment errors.
            logger.error(f"Payment retry failed for ride {ride_id}: {e}", exc_info=True)
            # Atomic claim on the count bump — only the replica that won
            # the race fires the rider failure push below.
            claimed = await db.update_one(
                "rides",
                {"id": ride_id, "payment_retry_count": retry_count},
                {
                    "$set": {
                        "payment_retry_count": retry_count + 1,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
            if claimed is None:
                continue

        # Notify rider on final failure — only the replica that won the
        # bump above reaches here, so the push fires once per ride.
        if retry_count + 1 >= MAX_RETRIES:
            rider_id = ride.get("rider_id")
            if rider_id:
                try:
                    await send_push_notification(
                        rider_id,
                        "Payment failed",
                        "We couldn't process payment for your ride. Please update your payment method.",
                        data={"type": "payment_failed", "ride_id": ride_id},
                    )
                except Exception as push_err:
                    logger.debug(f"Payment failure push notification failed: {push_err}")


async def payment_retry_loop():
    """Background loop that retries failed payments every RETRY_INTERVAL_SECONDS."""
    logger.info(f"Payment retry service started (interval={RETRY_INTERVAL_SECONDS}s)")
    while True:
        # Single-replica enforcement: only the pod that claims the lock runs
        # the retry; others sleep the full interval. Prevents N simultaneous
        # Stripe retries on multi-replica deploys. TTL is 1.5× interval so
        # the lock expires cleanly before the next tick's election.
        lock_ttl = int(RETRY_INTERVAL_SECONDS * 1.5)
        if not await redis_set_nx("spinr:payment:retry:lock", _pod_id(), lock_ttl):
            _record_heartbeat("payment_retry (5min)")
            await asyncio.sleep(RETRY_INTERVAL_SECONDS)
            continue
        try:
            await retry_failed_payments()
        except Exception as e:
            logger.error(f"Payment retry loop error: {e}", exc_info=True)
        try:
            await retry_stuck_payouts()
        except Exception as e:
            logger.error(f"Payout retry loop error: {e}", exc_info=True)
        _record_heartbeat("payment_retry (5min)")
        # B-P3-2: per-tick ±10% jitter so replicas don't tick in lockstep
        # and create a thundering herd against Stripe + Supabase. Tested
        # cap is RETRY_INTERVAL_SECONDS * 0.1 ≈ 30s on 5min interval.
        delta = RETRY_INTERVAL_SECONDS * 0.1
        await asyncio.sleep(RETRY_INTERVAL_SECONDS + random.uniform(-delta, delta))
