"""Stale intent reconciler — flips long-unreachable drivers' intent offline.

``drivers.is_online`` is pure driver intent (see utils/driver_online.py);
nothing on the hot path derives it from reachability. The gap that leaves:
a driver who force-kills the app (or whose phone dies) stays intent-online
in the DB forever. Dispatch is unaffected — Redis presence already excludes
them — but admin views, analytics, and above all the *insurance period log*
stay wrong: a Period 1 (TNC contingent liability) window that never closes
is a regulatory audit problem.

This loop closes that gap on an hours scale, deliberately unlike the
retired presence_sweeper (which acted on 30-second Redis presence and
mass-flipped drivers whenever Redis fell back to per-replica in-process
state). Safeguards against that failure class:

  * **Durable staleness signal.** Candidates are selected on
    ``drivers.updated_at`` — a Postgres timestamp refreshed by every
    location batch (~30 s cadence while the app is alive). A Redis outage
    cannot make a live driver look stale.
  * **Redis-healthy gate.** The tick is skipped entirely when Redis is in
    in-process-fallback mode, so presence can never be misread as absent
    merely because Redis is down.
  * **Presence double-check.** A driver whose presence key is alive is
    never flipped, even with a stale row (e.g. location permission revoked
    while the WebSocket still heartbeats).
  * **Active-ride guard.** Drivers linked to an active ride are skipped —
    a Period 0 write while a ride is open would corrupt the insurance log.
    A stuck `searching` ride is the stuck-ride sweeper's job
    (`utils/stuck_ride_sweeper.py`); it does NOT cover any other status —
    a stuck `in_progress` ride has no automated recovery today, only alerting
    via `utils/stale_in_progress_ride_alerter.py`.
  * **Atomic claim.** The flip filters on ``is_online = true``; zero rows
    back means another replica (or the driver) won the race.

Interval: 15 min. Threshold: ``stale_intent_offline_hours`` app setting
(default 4 h).
"""

from __future__ import annotations

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
    from .. import db_supabase as db
    from ..features import send_push_notification
    from ..settings_loader import get_app_settings
    from .driver_presence import present_driver_ids_checked
    from .insurance_periods import record_period_transition
    from .redis_client import get_redis_stats
except ImportError:
    import db_supabase as db  # type: ignore
    from features import send_push_notification  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.driver_presence import present_driver_ids_checked  # type: ignore
    from utils.insurance_periods import record_period_transition  # type: ignore
    from utils.redis_client import get_redis_stats  # type: ignore

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 15 * 60
DEFAULT_STALE_HOURS = 4
CANDIDATE_LIMIT = 200
# Hard cap on rows examined per tick so a pathological backlog can't pin
# the loop; anything beyond it is picked up by later ticks.
MAX_CANDIDATES_PER_TICK = 2000

ACTIVE_RIDE_STATUSES = [
    "searching",
    "driver_assigned",
    "driver_accepted",
    "driver_arrived",
    "in_progress",
]


async def _stale_hours() -> float | None:
    """Configured threshold, or None when settings can't be read.

    A failed settings read must NOT silently fall back to the default: an
    operator may have raised the threshold (e.g. to 24h), and flipping
    drivers at 4h during a settings outage would override that. The caller
    skips the tick on None. The default only applies when settings load
    fine but the key is absent/invalid.
    """
    try:
        settings = await get_app_settings()
    except Exception as exc:
        logger.error(f"stale_intent: settings read failed — tick skipped: {exc}")
        return None
    try:
        hours = float(settings.get("stale_intent_offline_hours", DEFAULT_STALE_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_STALE_HOURS
    return hours if hours > 0 else DEFAULT_STALE_HOURS


async def reconcile_stale_intent(now_utc: datetime | None = None) -> dict[str, int]:
    """One tick. Returns counters for logging/tests."""
    stats = {"candidates": 0, "skipped_present": 0, "skipped_active_ride": 0, "flipped": 0}

    redis_stats = await get_redis_stats()
    if not redis_stats.get("connected"):
        # In-process fallback: presence is per-replica and cannot be trusted
        # as evidence of absence. Never flip intent in this mode.
        logger.debug("stale_intent: Redis not connected — tick skipped")
        return stats

    hours = await _stale_hours()
    if hours is None:
        return stats

    now = now_utc or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=hours)).isoformat()
    now_iso = now.isoformat()

    # Page through the stale set. Flipped rows leave the predicate, so the
    # next page starts after only the rows we *skipped* (still matching);
    # without this, >CANDIDATE_LIMIT stale drivers whose first page is all
    # skips would shadow the rest forever.
    offset = 0
    while stats["candidates"] < MAX_CANDIDATES_PER_TICK:
        candidates = await db.get_rows(
            "drivers",
            {"is_online": True, "updated_at": {"$lt": cutoff}},
            order="updated_at",
            limit=CANDIDATE_LIMIT,
            offset=offset,
            columns="id,user_id,updated_at",
        )
        stats["candidates"] += len(candidates)
        if not candidates:
            break

        ids = [str(d["id"]) for d in candidates if d.get("id")]

        try:
            present, reachable = await present_driver_ids_checked(ids)
        except Exception as exc:
            logger.warning(f"stale_intent: presence lookup failed — tick aborted: {exc}")
            return stats
        if not reachable:
            # Redis configured but unavailable mid-tick: an empty presence
            # set means "unknowable", not "everyone offline". Flipping on it
            # would recreate the retired presence_sweeper's mass-offline bug.
            logger.warning("stale_intent: presence store unreachable — tick aborted")
            return stats

        active_rides = await db.get_rows(
            "rides",
            {"driver_id": {"$in": ids}, "status": {"$in": ACTIVE_RIDE_STATUSES}},
            limit=CANDIDATE_LIMIT,
            columns="id,driver_id",
        )
        drivers_on_ride = {str(r["driver_id"]) for r in active_rides if r.get("driver_id")}

        page_skips = 0
        for driver in candidates:
            driver_id = str(driver["id"])
            if driver_id in present:
                stats["skipped_present"] += 1
                page_skips += 1
                continue
            if driver_id in drivers_on_ride:
                stats["skipped_active_ride"] += 1
                page_skips += 1
                logger.warning(
                    f"stale_intent: driver {driver_id} is stale but linked to an active ride — "
                    "left online; investigate the ride"
                )
                continue

            # Presence can have refreshed since the batched check above (a WS
            # pong updates Redis but not drivers.updated_at, so the claim
            # predicate alone can't see a reconnect). Re-check this one
            # driver immediately before the claim to shrink that window to
            # milliseconds.
            try:
                present_now, reachable_now = await present_driver_ids_checked([driver_id])
            except Exception as exc:
                logger.warning(f"stale_intent: presence re-check failed — tick aborted: {exc}")
                return stats
            if not reachable_now:
                logger.warning("stale_intent: presence store unreachable — tick aborted")
                return stats
            if present_now:
                stats["skipped_present"] += 1
                page_skips += 1
                continue

            # Atomic claim. Re-asserting the staleness predicate closes the
            # race where the app came back (location batch bumped
            # updated_at) between the candidate query and this write.
            claimed = await db.update_one(
                "drivers",
                {"id": driver_id, "is_online": True, "updated_at": {"$lt": cutoff}},
                {
                    "is_online": False,
                    "is_available": False,
                    "went_offline_at": now_iso,
                    "updated_at": now_iso,
                },
            )
            if not claimed:
                continue  # another replica won, driver toggled, or app woke up

            stats["flipped"] += 1
            logger.info(
                f"stale_intent: driver {driver_id} intent flipped offline (last activity {driver.get('updated_at')})"
            )
            # Period 0: fully offline, personal insurance only.
            await record_period_transition(driver_id, 0)

            user_id = driver.get("user_id")
            if user_id:
                try:
                    await send_push_notification(
                        str(user_id),
                        "You're now offline",
                        "We couldn't reach your app for a while, so you were taken "
                        "offline. Tap 'Go Online' when you're ready to drive again.",
                        data={"type": "auto_offline", "reason": "unreachable"},
                        target_app="driver",
                    )
                except Exception as exc:
                    logger.warning(f"stale_intent: offline push failed for driver {driver_id}: {exc}")

        if len(candidates) < CANDIDATE_LIMIT:
            break
        offset += page_skips

    if stats["flipped"]:
        logger.info(f"stale_intent: {stats}")
    return stats


async def stale_intent_reconciler_loop() -> None:
    """Every 15 min: close intent-online windows for drivers unreachable for hours."""
    logger.info("Stale intent reconciler started")
    while True:
        try:
            await reconcile_stale_intent()
        except Exception:
            logger.error("stale_intent_reconciler tick failed", exc_info=True)
        _record_heartbeat("stale_intent_reconciler (15min)")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS + random.uniform(-60, 60))
