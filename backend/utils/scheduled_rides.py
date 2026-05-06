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
    from .datetime_utils import parse_iso_utc
    from .metrics import inc as _metric_inc
    from .metrics import set_gauge as _metric_gauge
except ImportError:
    from db import db
    from features import send_push_notification
    from utils.datetime_utils import parse_iso_utc

logger = logging.getLogger(__name__)


async def _dispatch_scheduled_ride(ride: dict):
    """Transition a scheduled ride from 'scheduled' to 'searching' and start driver matching."""
    ride_id = ride["id"]
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        # Atomic claim: only proceed if the ride is still in 'scheduled' state.
        # Using a conditional update guards against duplicate dispatch on
        # multi-replica deployments (both replicas reach this point at the same
        # tick; only the one whose UPDATE matches a row proceeds).
        updated = await db.update_one(
            "rides",
            {"id": ride_id, "status": "scheduled"},
            {
                "status": "searching",
                "scheduled_dispatched": True,
                "updated_at": now_iso,
            },
        )
        if not updated:
            # Already dispatched or cancelled by another replica / admin.
            return

        # Import and run driver matching
        try:
            from routes.rides import match_driver_to_ride
        except ImportError:
            from ..routes.rides import match_driver_to_ride  # type: ignore

        await match_driver_to_ride(ride_id)
        logger.info(f"Dispatched scheduled ride {ride_id}")

        # Notify rider — best effort; failure must not roll back the dispatch.
        rider_id = ride.get("rider_id")
        if rider_id:
            try:
                dropoff = (ride.get("dropoff_address") or "your destination")[:60]
                await send_push_notification(
                    rider_id,
                    "Your scheduled ride is starting!",
                    f"We're finding a driver for your ride to {dropoff}.",
                    data={"type": "scheduled_ride_dispatched", "ride_id": ride_id},
                )
            except Exception as push_err:
                logger.warning(f"Scheduled ride push failed for ride {ride_id}: {push_err}")

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
            {"reminder_sent": True},
        )
        logger.info(f"Sent reminder for scheduled ride {ride_id}")

    except Exception as e:
        logger.error(f"Failed to send reminder for ride {ride_id}: {e}")


async def check_scheduled_rides():
    """Check for scheduled rides that need dispatching or reminders."""
    now = datetime.now(timezone.utc)
    ten_min_from_now = now + timedelta(minutes=10)

    try:
        # Get all pending scheduled rides (still in 'scheduled' state, not yet
        # dispatched to 'searching').  The dispatcher transitions them to
        # 'searching' atomically inside _dispatch_scheduled_ride.
        scheduled = await db.get_rows(
            "rides",
            {
                "is_scheduled": True,
                "status": "scheduled",
            },
            limit=100,
            order="scheduled_time",
        )
    except Exception as e:
        original = getattr(e, "details", {}).get("original") if hasattr(e, "details") else None
        logger.error(f"Failed to fetch scheduled rides: {e} | original={original}")
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
            logger.error(f"Scheduled ride dispatcher error: {e}")
        _record_heartbeat("scheduled_dispatcher (60s)")
        # B-P3-2: ±6 s jitter on the 60 s interval so replicas don't
        # contend for the same scheduled-ride row on every minute boundary.
        await asyncio.sleep(60 + random.uniform(-6, 6))
