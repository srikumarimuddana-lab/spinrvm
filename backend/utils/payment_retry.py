"""Payment retry — automatically retries failed ride payments with exponential backoff.

Runs as a background task every 5 minutes. Finds rides with payment_status
'failed' or 'requires_action' and retries via Stripe up to 3 times.
Also resolves driver payouts stuck as 'pending' after transfer failures.

Replay-safety contract (CLAUDE.md, Background loops):
  This loop runs on every replica simultaneously. Two replays of the
  same tick must NOT charge twice or fire double notifications. We rely
  on two layers:

    1. Atomic claim via conditional update — each ride is claimed by
       flipping ``payment_status`` from its current value to 'retrying'
       while also asserting ``payment_retry_count`` equals the value
       read. Only the replica whose UPDATE returns a row proceeds to
       call Stripe; the other gets None back and skips the ride entirely.

    2. Stripe idempotency_key on PaymentIntent.confirm — derived from
       (ride_id, retry_count). In the unlikely event two replicas both
       win a claim race (e.g. concurrent reads before either write
       commits), Stripe deduplicates the confirm call so no second
       charge happens.
"""

import asyncio
import logging
import os
import random
import socket
from datetime import datetime, timezone

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from ..db import db
    from ..features import send_push_notification
    from ..models.ride_status import RideStatus
    from ..settings_loader import get_app_settings
    from ..socket_manager import manager
    from .datetime_utils import parse_iso_utc
    from .redis_client import redis_set_nx
except ImportError:
    from db import db
    from features import send_push_notification
    from models.ride_status import RideStatus
    from settings_loader import get_app_settings
    from socket_manager import manager
    from utils.datetime_utils import parse_iso_utc
    from utils.redis_client import redis_set_nx

logger = logging.getLogger(__name__)

# Mirrors admin/rides.py::_INVOICE_CLAIM_STALE_SECONDS. A `pending:<epoch>:<uuid>`
# claim older than this is an abandoned invoice-creation attempt (the request
# crashed before Stripe created the invoice), so an in-app retry is safe.
_INVOICE_CLAIM_STALE_SECONDS = 300


def _invoice_claim_is_stale(sentinel: str) -> bool:
    """True if a `pending:<epoch>:<uuid>` invoice claim is old enough to reclaim.

    Legacy `pending:<uuid>` sentinels (no embedded timestamp) return False — they
    are treated conservatively and left for manual ops cleanup."""
    try:
        parts = str(sentinel).split(":")
        if len(parts) < 3:
            return False
        age = (datetime.now(timezone.utc) - datetime.fromtimestamp(float(parts[1]), tz=timezone.utc)).total_seconds()
        return age >= _INVOICE_CLAIM_STALE_SECONDS
    except (ValueError, OverflowError, OSError):
        return False


async def _alert_admins_payment_exhausted(ride: dict) -> None:
    """Notify admins when a ride's payment retries are exhausted."""
    ride_id = ride.get("id", "")
    rider_id = ride.get("rider_id", "")
    total_fare = ride.get("total_fare", 0)

    try:
        await manager.broadcast_to_admins(
            {
                "type": "payment_retries_exhausted",
                "ride_id": ride_id,
                "rider_id": rider_id,
                "total_fare": total_fare,
                "payment_retry_count": ride.get("payment_retry_count", MAX_RETRIES),
            }
        )
    except Exception as exc:
        logger.error(f"Admin WS broadcast failed for exhausted payment ride {ride_id}: {exc}")

    try:
        # Only the admin id is needed to target the push — project it.
        admin_users = await db.get_rows("users", {"role": "admin"}, columns="id", limit=50)
        for admin in admin_users:
            try:
                await send_push_notification(
                    admin["id"],
                    "Payment retries exhausted",
                    f"Ride {ride_id[:8]}… — all {MAX_RETRIES} retries failed. Rider is blocked from booking.",
                    data={"type": "payment_retries_exhausted", "ride_id": ride_id},
                )
            except Exception as _push_exc:
                logger.debug(f"Payment alert push to admin {admin.get('id')} failed: {_push_exc}")
    except Exception as exc:
        logger.error(f"Failed to fetch admin users for payment alert: {exc}")

    logger.error(
        f"ADMIN ALERT: Ride {ride_id} payment retries exhausted ({MAX_RETRIES} attempts). "
        f"Rider {rider_id} is now blocked from booking.",
        extra={"domain": "payments", "ride_id": ride_id, "rider_id": rider_id},
    )


def _pod_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


MAX_RETRIES = 3
RETRY_INTERVAL_SECONDS = 300  # 5 minutes


async def update_payout_status(payout_id: str, status: str) -> None:
    await db.update_one(
        "payouts",
        {"id": payout_id},
        {
            "$set": {
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
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
            columns="id,driver_id,retry_count,status",
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
                {
                    "$set": {
                        "status": "failed",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
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
    """Find and retry failed/stuck payments."""
    try:
        rides = await db.get_rows(
            "rides",
            {
                "payment_status": {"$in": ["failed", "requires_action", "processing"]},
                # Cancelled rides never had a fare charge to retry — their
                # payment_status here reflects a cancellation-fee charge
                # (see cancel_ride_rider), which has its own PaymentIntent
                # namespace. Retrying it here would confirm/create a fare
                # PaymentIntent against a stale booking-time hold instead,
                # and the ride would sit in this scan forever since none of
                # this loop's success paths ever apply to a cancelled ride.
                "status": {"$ne": RideStatus.CANCELLED},
            },
            limit=50,
            order="created_at",
            columns=(
                "id,rider_id,driver_id,payment_status,payment_retry_count,"
                "payment_intent_id,admin_alerted_payment_exhausted,total_fare,"
                "stripe_invoice_id,created_at,updated_at"
            ),
        )
    except Exception as e:
        logger.error(f"Payment retry: failed to fetch rides: {e}")
        return

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    for ride in rides:
        ride_id = ride["id"]
        retry_count = ride.get("payment_retry_count", 0)
        current_status = ride.get("payment_status", "failed")

        # An admin sent (or is creating) a payable Stripe invoice for this ride —
        # collection has moved to the hosted invoice (settled by invoice.paid).
        # Retrying the stored PaymentIntent on the old card here would collect a
        # second time alongside the invoice, so skip ANY non-null claim (a
        # finalized in_* id or a 'pending:' creation sentinel alike). We never
        # re-open retries by age — a stuck claim is recovered admin-side (which
        # creates invoices crash-safely), not by silently re-charging here.
        _invoice_sid = ride.get("stripe_invoice_id")
        if _invoice_sid:
            if str(_invoice_sid).startswith("pending:") and _invoice_claim_is_stale(_invoice_sid):
                # Surface a long-lived bare sentinel so ops can re-send admin-side.
                logger.error(
                    "payment_retry: ride %s has a stale pending invoice sentinel '%s' — "
                    "in-app retry is blocked; admin must re-send the invoice to recover",
                    ride_id,
                    _invoice_sid,
                    extra={"domain": "payments", "ride_id": ride_id},
                )
            continue

        if retry_count >= MAX_RETRIES:
            if not ride.get("admin_alerted_payment_exhausted"):
                claimed = await db.update_one(
                    "rides",
                    {"id": ride_id, "admin_alerted_payment_exhausted": False},
                    {
                        "$set": {
                            "admin_alerted_payment_exhausted": True,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                )
                if claimed is not None:
                    await _alert_admins_payment_exhausted(ride)
            continue

        # Skip rides older than 24 hours
        created_dt = parse_iso_utc(ride.get("created_at"))
        now_utc = datetime.now(timezone.utc)
        if created_dt is not None and (now_utc - created_dt).total_seconds() > 86400:
            continue

        # "processing" means Stripe is mid-flight; only intervene after 30 min
        # (the webhook should have arrived by then).
        if current_status == "processing":
            updated_dt = parse_iso_utc(ride.get("updated_at"))
            if updated_dt is not None and (now_utc - updated_dt).total_seconds() < 1800:
                continue

        payment_intent_id = ride.get("payment_intent_id")
        if not payment_intent_id or not stripe_secret:
            continue

        # Atomic claim: only the replica that transitions payment_status →
        # 'retrying' (while it is still the fetched status and retry_count
        # is unchanged) proceeds. The other replica gets None back and skips,
        # preventing duplicate Stripe charges and double push notifications.
        claimed = await db.update_one(
            "rides",
            {
                "id": ride_id,
                "payment_status": current_status,
                "payment_retry_count": retry_count,
                # Mirror /process-payment: assert no invoice claim landed between
                # the read above and this claim. Without it, an admin send-invoice
                # that wins the row right here would leave the retry loop confirming
                # the old PI while the hosted invoice is also collectible.
                "stripe_invoice_id": None,
            },
            {
                "$set": {
                    "payment_status": "retrying",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        if claimed is None:
            continue

        try:
            import stripe

            # Attempt to confirm the payment intent
            intent = stripe.PaymentIntent.retrieve(payment_intent_id, api_key=stripe_secret)

            if intent.status == "succeeded":
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
                attempt = retry_count + 1
                # Scheduled retries use a per-attempt suffix so each retry
                # gets a fresh Stripe call. The in-flight race (two replicas
                # both confirming during trip-end) is handled by charge_ride's
                # key (ride-confirm-{ride_id}-{amount}); by the time the retry
                # loop fires, that initial window has closed and we need a new
                # idempotency key to avoid Stripe replaying a cached transient
                # error from attempt 1 on attempts 2+.
                stripe.PaymentIntent.confirm(
                    payment_intent_id,
                    api_key=stripe_secret,
                    idempotency_key=f"ride-confirm-{ride_id}-{intent.amount}-retry-{attempt}",
                )
                await db.update_one(
                    "rides",
                    {"id": ride_id},
                    {
                        "$set": {
                            "payment_status": "processing",
                            "payment_retry_count": attempt,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                )
                logger.info(f"Payment retry: ride {ride_id} retry #{attempt} submitted")
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
                await db.update_one(
                    "rides",
                    {"id": ride_id},
                    {
                        "$set": {
                            "payment_status": "failed",
                            "payment_retry_count": MAX_RETRIES,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                )

            else:
                logger.warning(
                    f"PaymentIntent {intent.id} in unexpected state {intent.status!r} for ride {ride_id}; skipping confirm"
                )
                continue

        except Exception as e:
            # CLAUDE.md: never warn-and-continue on payment errors.
            logger.error(f"Payment retry failed for ride {ride_id}: {e}", exc_info=True)
            new_count = retry_count + 1
            await db.update_one(
                "rides",
                {"id": ride_id},
                {
                    "$set": {
                        "payment_status": "failed",
                        "payment_retry_count": new_count,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
            if new_count >= MAX_RETRIES:
                await _alert_admins_payment_exhausted(ride)
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
        # Best-effort throttle (C5): when a real Redis backs redis_set_nx, only
        # one pod runs a tick, avoiding N simultaneous Stripe-retry scans. But
        # the in-process fallback (REDIS_URL unset) gives every replica its own
        # dict, so SET NX returns True everywhere and this is NOT mutual
        # exclusion. That is acceptable because it is NOT the correctness guard:
        # double-charge is prevented by the atomic DB claim (payment_status →
        # 'retrying') + the Stripe idempotency key (see module docstring). The
        # lock only reduces redundant work; it is never relied on for safety.
        # TTL is 1.5× interval so a real lock expires before the next election.
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
