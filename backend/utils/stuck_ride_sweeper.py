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
    from .redis_client import try_acquire_leader_lock
except ImportError:
    import db_supabase  # type: ignore
    from features import send_push_notification  # type: ignore
    from socket_manager import manager  # type: ignore
    from utils.card_hold_release import release_open_hold  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import try_acquire_leader_lock  # type: ignore

try:
    from ..supabase_client import supabase
except ImportError:
    from supabase_client import supabase  # type: ignore

try:
    from ..settings_loader import get_app_settings
except ImportError:
    from settings_loader import get_app_settings  # type: ignore

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 60
# Scheduled rides: cancelled 5 min past pickup (and past ride_requested_at),
# unchanged by the on-demand setting below.
_SEARCHING_TIMEOUT_MINUTES = 5

# On-demand rides: settings.ride_search_timeout_seconds (migration 468). Same
# default and clamp as routes/rides/matching.py's _ride_search_timeout_seconds,
# so this backstop cancels at the same time as the in-process timer.
_DEFAULT_SEARCH_TIMEOUT_SECONDS = 300
_MIN_SEARCH_TIMEOUT_SECONDS = 90
# Max 300: the offer-skip key lasts 300 s and ride_offers is UNIQUE(ride_id, driver_id) (migration 100).
_MAX_SEARCH_TIMEOUT_SECONDS = 300


async def _search_timeout_seconds() -> int:
    """Configured on-demand search window, clamped to 90..300. Missing → 300;
    read error or non-integer → 300, logged at error level. Never raises."""
    try:
        settings = await get_app_settings()
        raw = settings.get("ride_search_timeout_seconds")
    except Exception as exc:
        logger.error(
            f"[stuck_ride_sweeper] could not read ride_search_timeout_seconds, "
            f"using {_DEFAULT_SEARCH_TIMEOUT_SECONDS}s: {exc}",
            exc_info=True,
        )
        return _DEFAULT_SEARCH_TIMEOUT_SECONDS
    if raw is None:
        return _DEFAULT_SEARCH_TIMEOUT_SECONDS
    try:
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise TypeError(f"unsupported type {type(raw).__name__}")
        seconds = int(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        logger.error(
            f"[stuck_ride_sweeper] invalid ride_search_timeout_seconds={raw!r}, "
            f"using {_DEFAULT_SEARCH_TIMEOUT_SECONDS}s: {exc}"
        )
        return _DEFAULT_SEARCH_TIMEOUT_SECONDS
    return max(_MIN_SEARCH_TIMEOUT_SECONDS, min(_MAX_SEARCH_TIMEOUT_SECONDS, seconds))


# The release itself lives in utils/card_hold_release so the sweeper, the
# orphaned-hold reconciler and (conceptually) the interactive cancel path share one
# definition of the failure semantics. Duplicating those semantics is exactly how the
# bug this fixes came about: the release existed in routes/rides/cancellation.py and
# was never carried over to this loop.


async def _sweep() -> None:
    if not supabase:
        return

    search_timeout_seconds = await _search_timeout_seconds()
    now = datetime.now(timezone.utc)
    on_demand_cutoff_iso = (now - timedelta(seconds=search_timeout_seconds)).isoformat()
    scheduled_cutoff_iso = (now - timedelta(minutes=_SEARCHING_TIMEOUT_MINUTES)).isoformat()
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
            # On-demand (no scheduled_time): the configured window. Scheduled:
            # both timestamps past the fixed 5-minute cutoff, as before. At the
            # default 300 s both cutoffs are equal and this matches the old
            # .lt(ride_requested_at).or_(scheduled_time null/lt) filter exactly.
            .or_(
                f"and(scheduled_time.is.null,ride_requested_at.lt.{on_demand_cutoff_iso}),"
                f"and(scheduled_time.lt.{scheduled_cutoff_iso},ride_requested_at.lt.{scheduled_cutoff_iso})"
            )
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
                    target_app="rider",
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
        # Leader lock: LOAD shedding only, not correctness. This loop is
        # already replay-safe, so N replicas running it is correct — just N×
        # the DB work at a 60s cadence, which the 2026-08-26 audit
        # measured among the top query sources. Same rationale
        # dispute_evidence_reminder.py already states for its own lock.
        # Fails OPEN, so a Redis blip restores exactly today's behaviour.
        # TTL < the minimum sleep below, or a pod finds its own key alive and
        # halves the cadence.
        # Jittered 0.9-1.1x, so the floor is 54s and 51s clears it.
        if not await try_acquire_leader_lock("stuck_ride_sweeper", int(_SWEEP_INTERVAL_SECONDS * 0.85)):
            _record_heartbeat("stuck_ride_sweeper (60s)")
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS * (0.9 + random.random() * 0.2))
            continue
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
