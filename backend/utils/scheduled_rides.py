"""Scheduled ride dispatcher — background task that dispatches scheduled rides
at the appropriate time and sends reminder notifications.

Flow:
1. Every 60 seconds, check for scheduled rides due in the next 10 minutes
2. Send a reminder notification to the rider 10 minutes before
3. When the scheduled time arrives, dispatch the ride (set status to 'searching')
4. Match a driver using the existing match_driver_to_ride() logic
"""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from ..db import db
    from ..features import send_push_notification
    from ..socket_manager import manager
    from .datetime_utils import parse_iso_utc
    from .redis_client import redis_set_nx
except ImportError:
    from db import db
    from features import send_push_notification
    from socket_manager import manager  # type: ignore[no-redef]
    from utils.datetime_utils import parse_iso_utc
    from utils.redis_client import redis_set_nx  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


async def _notify_schedule_delayed(ride_id: str, rider_id, ride: dict) -> None:
    """Tell the rider their scheduled ride is waiting on their current trip.

    De-duped via a Redis NX key (1h TTL) so a rider on a long trip isn't pinged
    every 60-second dispatcher tick. Best-effort — a notification failure must
    never break the retry loop.
    """
    if not rider_id:
        return
    try:
        # redis_set_nx returns True only for the first caller within the TTL.
        if not await redis_set_nx(f"spinr:sched_delay_notified:{ride_id}", "1", ttl=3600):
            return
    except Exception as dedup_err:
        # Redis unavailable — fall through and notify (worst case: a duplicate
        # push), rather than swallow the alert entirely.
        logger.debug(f"scheduled dispatch: delay-notice dedup check failed for {ride_id}: {dedup_err}")
    try:
        await send_push_notification(
            rider_id,
            "Your scheduled ride is waiting",
            "We'll start finding a driver as soon as your current trip ends.",
            data={"type": "scheduled_ride_delayed", "ride_id": ride_id},
        )
    except Exception as e:
        logger.warning(f"scheduled dispatch: delayed-notice push failed for {ride_id}: {e}")


async def _dispatch_scheduled_ride(ride: dict):
    """Transition a scheduled ride from 'scheduled' to 'searching' and start driver matching.

    The scheduled→searching transition is an atomic DB claim: the update filters
    on ``status='scheduled'`` so exactly one caller (across replicas and loop
    ticks) wins. A claim returning no row means the ride was already dispatched,
    cancelled, or otherwise moved on — we return without acting. This satisfies
    the Background Loop Recipe replay-safety contract (atomic DB claim) without
    relying on the Redis leader lock, which may be unavailable in dev fallback.
    """
    ride_id = ride["id"]
    rider_id = ride.get("rider_id")
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        # Atomic claim: only the caller that flips scheduled→searching proceeds.
        try:
            claimed = await db.update_one(
                "rides",
                {"id": ride_id, "status": "scheduled"},
                {
                    "$set": {
                        "status": "searching",
                        "scheduled_dispatched": True,
                        # Mark the moment dispatch actually began so downstream
                        # latency metrics measure from real request time, not booking.
                        "ride_requested_at": now_iso,
                        "updated_at": now_iso,
                    }
                },
            )
        except Exception as claim_exc:
            # rides_one_active_per_rider (migration 53) is a partial unique
            # index over the active statuses. 'scheduled' sits outside that set,
            # so if the rider already has a live trip when their scheduled
            # pickup time arrives, flipping to 'searching' collides with it and
            # the UPDATE raises. That is an expected, recoverable conflict — not
            # a dispatch failure: leave the ride in 'scheduled' so a later tick
            # retries once the rider is free, and tell the rider once (Redis-
            # deduped) so the delay isn't silent. Any other error bubbles up.
            msg = str(claim_exc).lower()
            if "rides_one_active_per_rider" in msg or "23505" in msg or "duplicate" in msg or "unique" in msg:
                logger.warning(
                    f"scheduled dispatch deferred: rider has an active ride; ride {ride_id} stays 'scheduled' for retry"
                )
                await _notify_schedule_delayed(ride_id, rider_id, ride)
                return
            raise
        if not claimed:
            # Another replica/tick won the claim, or the ride was cancelled or
            # already dispatched. Nothing to do.
            return

        logger.info(f"Dispatched scheduled ride {ride_id}: scheduled → searching")

        # Pre-authorize the card hold NOW (at dispatch), not at booking: a
        # scheduled ride may be booked days out, beyond Stripe's ~7-day auth
        # lifetime, so the hold is placed when the ride actually goes live.
        # Card source only, and only if no hold exists yet. The rider isn't
        # present, so a decline must NOT strand the ride (block_on_decline=False)
        # — it degrades to post-trip settlement like any un-held ride.
        try:
            if claimed.get("payment_method") == "card" and not claimed.get("auth_status"):
                from decimal import Decimal as _Decimal

                try:
                    from ..routes.rides import booking as _rides_booking
                except ImportError:
                    from routes.rides import booking as _rides_booking  # type: ignore

                _rider_row = await db.get_user_by_id(rider_id) if rider_id else None
                _grand = claimed.get("grand_total")
                if _grand is None:
                    _grand = claimed.get("total_fare") or 0
                _preauth = await _rides_booking._preauthorize_ride_card(
                    ride_id=ride_id,
                    rider_id=rider_id,
                    grand_total=_Decimal(str(_grand)),
                    stripe_customer_id=(_rider_row or {}).get("stripe_customer_id"),
                    payment_method_id=claimed.get("payment_method_id"),
                    block_on_decline=False,
                )
                if _preauth.fields:
                    await db.update_one("rides", {"id": ride_id}, {"$set": _preauth.fields})
        except Exception as _auth_err:
            # Never let a pre-auth hiccup block dispatch; post-trip settlement
            # remains the safety net. Surface loudly — it's a payment path.
            logger.error(
                "scheduled dispatch: pre-auth at dispatch failed for %s: %s",
                ride_id,
                _auth_err,
                exc_info=True,
            )

        # Mandatory state-change WS event (rider + admins). Drives the rider
        # app's status update and patches any admin dashboard that already has
        # this ride in its map.
        try:
            await manager.broadcast_ride_status(
                ride_id,
                "searching",
                rider_id=rider_id,
                is_scheduled=True,
            )
        except Exception as ws_err:
            logger.warning(f"scheduled dispatch: WS broadcast failed for {ride_id}: {ws_err}")

        # Surface the now-live ride to admin monitoring as a NEW row. The
        # dashboard's ride_status_changed handler only patches an existing
        # entry; ride_requested is the path that calls applyRide() to add a
        # row. The create_ride path skips ride_requested for deferred rides,
        # so without this a scheduled ride going live is invisible to an
        # already-open dashboard until the next snapshot refresh. Payload must
        # match the MonitoringRide contract (nested ``ride`` object).
        try:
            try:
                from ..routes.admin.monitoring import build_monitoring_ride
            except ImportError:
                from routes.admin.monitoring import build_monitoring_ride
            rider = None
            if rider_id:
                rider = await db.get_user_by_id(rider_id)
            await manager.broadcast_to_admins(
                {
                    "type": "ride_requested",
                    "ride": build_monitoring_ride(claimed, rider=rider),
                }
            )
        except Exception as admin_err:
            logger.warning(f"scheduled dispatch: admin ride_requested broadcast failed for {ride_id}: {admin_err}")

        # Import and run driver matching. We do NOT pass ride= so the dispatch
        # path re-fetches the freshly-claimed 'searching' row.
        try:
            from routes.rides import matching as _rides_matching
        except ImportError:
            from ..routes.rides import matching as _rides_matching

        await _rides_matching.match_driver_to_ride(ride_id)

        # Arm the no-drivers-found timeout exactly as the live booking path does,
        # so a scheduled ride that finds no driver auto-cancels instead of
        # hanging in 'searching' indefinitely.
        asyncio.create_task(_rides_matching.ride_search_timeout(ride_id))

        # Notify rider
        if rider_id:
            await send_push_notification(
                rider_id,
                "Your scheduled ride is starting!",
                f"We're finding a driver for your ride to {ride.get('dropoff_address', 'your destination')}.",
                data={"type": "scheduled_ride_dispatched", "ride_id": ride_id},
            )

    except Exception as e:
        logger.error(f"Failed to dispatch scheduled ride {ride_id}: {e}", exc_info=True)


async def _send_reminder(ride: dict):
    """Send a 10-minute reminder notification for an upcoming scheduled ride."""
    ride_id = ride["id"]
    try:
        # Check if reminder already sent
        if ride.get("reminder_sent"):
            return

        rider_id = ride.get("rider_id")

        if rider_id:
            await send_push_notification(
                rider_id,
                "Ride reminder - 10 minutes",
                f"Your ride to {ride.get('dropoff_address', 'your destination')} is scheduled soon. A driver will be assigned shortly.",
                data={"type": "scheduled_ride_reminder", "ride_id": ride_id},
            )

        await db.update_one(
            "rides",
            {"id": ride_id},
            {"$set": {"reminder_sent": True}},
        )
        logger.info(f"Sent reminder for scheduled ride {ride_id}")

    except Exception as e:
        logger.error(f"Failed to send reminder for ride {ride_id}: {e}", exc_info=True)


async def check_scheduled_rides():
    """Check for scheduled rides that need dispatching or reminders."""
    try:
        if not await redis_set_nx("spinr:scheduled_rides:lock", "1", ttl=90):
            return
    except Exception as _lock_err:
        logger.warning(f"scheduled_rides: Redis leader lock unavailable ({_lock_err}), proceeding without lock")

    now = datetime.now(timezone.utc)
    ten_min_from_now = now + timedelta(minutes=10)

    try:
        # Get all pending scheduled rides. These sit in status 'scheduled'
        # (set by create_ride for deferred bookings) until their scheduled_time
        # arrives — querying 'searching' here meant the loop never saw a
        # correctly-stored scheduled ride (CR-2).
        scheduled = await db.get_rows(
            "rides",
            {
                "is_scheduled": True,
                "status": "scheduled",
            },
            limit=100,
            order="scheduled_time",
            columns="id,rider_id,scheduled_time,scheduled_dispatched,reminder_sent,dropoff_address",
        )
    except Exception as e:
        original = getattr(e, "details", {}).get("original") if hasattr(e, "details") else None
        logger.error(f"Failed to fetch scheduled rides: {e} | original={original}", exc_info=True)
        return

    for ride in scheduled:
        scheduled_time_str = ride.get("scheduled_time")
        if not scheduled_time_str:
            continue

        scheduled_time = parse_iso_utc(scheduled_time_str)
        if scheduled_time is None:
            continue

        already_dispatched = ride.get("scheduled_dispatched", False)
        already_reminded = ride.get("reminder_sent", False)

        # Send reminder 10 minutes before (if not already sent)
        if not already_reminded and now <= scheduled_time and scheduled_time <= ten_min_from_now:
            await _send_reminder(ride)

        # Dispatch when it's time (or past time)
        if not already_dispatched and now >= scheduled_time:
            await _dispatch_scheduled_ride(ride)


async def scheduled_ride_dispatcher_loop():
    """Background loop that checks scheduled rides every 60 seconds."""
    logger.info("Scheduled ride dispatcher started")
    while True:
        try:
            await check_scheduled_rides()
        except Exception as e:
            logger.error(f"Scheduled ride dispatcher error: {e}", exc_info=True)
        _record_heartbeat("scheduled_dispatcher (60s)")
        # B-P3-2: ±6 s jitter on the 60 s interval so replicas don't
        # contend for the same scheduled-ride row on every minute boundary.
        await asyncio.sleep(60 + random.uniform(-6, 6))
