"""Sweeps rides stuck in 'searching' after the in-process timeout was lost (e.g., pod restart)."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase
    from ..features import send_push_notification
    from ..socket_manager import manager
    from .card_hold_release import release_open_hold
    from .metrics import inc as _metric_inc
except ImportError:
    import db_supabase  # type: ignore
    from features import send_push_notification  # type: ignore
    from socket_manager import manager  # type: ignore
    from utils.card_hold_release import release_open_hold  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore

try:
    from ..supabase_client import supabase
except ImportError:
    from supabase_client import supabase  # type: ignore

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 60
_SEARCHING_TIMEOUT_MINUTES = 5

# The release itself lives in utils/card_hold_release so the sweeper, the
# orphaned-hold reconciler and (conceptually) the interactive cancel path share one
# definition of the failure semantics. Duplicating those semantics is exactly how the
# bug this fixes came about: the release existed in routes/rides/cancellation.py and
# was never carried over to this loop.


async def _sweep() -> None:
    if not supabase:
        return

    cutoff_iso = (datetime.now(timezone.utc) - timedelta(minutes=_SEARCHING_TIMEOUT_MINUTES)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    update_payload = {
        "status": "cancelled",
        "cancelled_at": now_iso,
        "cancellation_reason": "No nearby drivers found. Please try again.",
        "cancelled_by": "system",
        "cancellation_type": "no_drivers_found",
        "updated_at": now_iso,
    }

    def _claim():
        res = (
            supabase.table("rides")
            .update(update_payload)
            .eq("status", "searching")
            .lt("ride_requested_at", cutoff_iso)
            .execute()
        )
        return db_supabase._rows_from_res(res)

    try:
        claimed_rides = await db_supabase.run_sync(_claim, retry_policy="write")
    except Exception as exc:
        logger.error(f"[stuck_ride_sweeper] DB claim failed: {exc}", exc_info=True)
        return

    if not claimed_rides:
        return

    logger.info(f"[stuck_ride_sweeper] cancelling {len(claimed_rides)} stuck ride(s)")

    for ride in claimed_rides:
        ride_id = ride.get("id")
        rider_id = ride.get("rider_id")
        driver_id = ride.get("driver_id")

        # Release the rider's card hold FIRST. The WS and push calls below are
        # network round-trips that can block for seconds (push especially), and
        # money integrity should not queue behind a notification — CLAUDE.md's
        # anti-patterns list calls out awaiting Twilio/Stripe inline for this
        # reason. Both notify calls already tolerate failure independently, so
        # nothing downstream depends on this ordering.
        await release_open_hold(ride, source="sweeper")

        if rider_id:
            try:
                await manager.send_personal_message(
                    {
                        "type": "ride_cancelled",
                        "ride_id": ride_id,
                        "reason": "no_drivers_found",
                        "message": "No nearby drivers found. Please try again.",
                    },
                    f"rider_{rider_id}",
                )
            except Exception as exc:
                logger.error(
                    f"[stuck_ride_sweeper] WS notify failed for ride {ride_id}: {exc}",
                    exc_info=True,
                )

            try:
                await send_push_notification(
                    rider_id,
                    "No drivers available",
                    "We couldn't find a driver nearby. Please try again.",
                    {"ride_id": str(ride_id), "type": "ride_cancelled"},
                )
            except Exception as exc:
                logger.error(
                    f"[stuck_ride_sweeper] push notify failed for ride {ride_id}: {exc}",
                    exc_info=True,
                )

        if driver_id:
            try:
                await db_supabase.set_driver_available(driver_id, True)
            except Exception as exc:
                logger.error(
                    f"[stuck_ride_sweeper] driver release failed for {driver_id}: {exc}",
                    exc_info=True,
                )

    _metric_inc("spinr_stuck_ride_sweeper_cancelled_total", {"count": str(len(claimed_rides))})


async def stuck_ride_sweeper_loop() -> None:
    await asyncio.sleep(random.uniform(0, _SWEEP_INTERVAL_SECONDS))
    while True:
        _t0 = time.monotonic()
        _had_error = False
        try:
            await _sweep()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[stuck_ride_sweeper] tick failed: {exc}", exc_info=True)
            _had_error = True
        if _had_error:
            _metric_inc("spinr_bgloop_errors_total", {"loop": "stuck_ride_sweeper"})
        _record_heartbeat("stuck_ride_sweeper (60s)")
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS * (0.9 + random.random() * 0.2))
