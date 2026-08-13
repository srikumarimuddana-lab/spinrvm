"""Alert-only detector for ``in_progress`` rides with no recent driver location.

Gap (P2 task #16): ``stuck_ride_sweeper`` only recovers rides stuck in
``searching`` (see its own module docstring/constants — ``_SEARCHING_TIMEOUT_MINUTES``
and a ``.eq("status", "searching")`` claim). An ``in_progress`` ride abandoned
by a force-killed driver app has NO automated recovery today: the rider can't
book a new ride (``in_progress`` is in ``active_statuses``) and the driver's
Period-3 insurance audit row (``driver_insurance_periods``) stays open
indefinitely. ``driver_claim_reaper.py`` and ``stale_intent_reconciler.py``
previously had comments implying the sweeper covered this case — corrected in
the same change that added this module to point here instead.

This loop is intentionally ALERT-ONLY. It never mutates ``rides.status`` or
the driver's insurance-period row. CLAUDE.md's ride-state-machine invariant —
"Transitions from in_progress are completed only. Never cancelled after trip
start" — is exactly why an automated fix is out of scope: force-completing a
ride nobody has looked at risks charging (or failing to charge) a trip that
may still be legitimately in progress (e.g. driver's phone died but the trip
is real and ongoing). ``admin_complete_ride``
(``routes/admin/rides.py``) already does the correct thing — closes the ride
AND calls ``record_period_transition`` to close Period 3 — once a human
confirms abandonment from the admin live-monitoring page. Today that only
happens on a rider complaint; this loop's entire job is to get a human there
proactively via Sentry + a structured error log, matching the
"detect but don't auto-fix" pattern ``stale_intent_reconciler`` already uses
for its own related-but-distinct gap (stale driver *intent*, not stale rides).

Staleness threshold — why 10 minutes
-------------------------------------
``routes/drivers/location.py``'s ``update_location_batch`` docstring documents
the driver app's outbox flush cadence as 5-15s (~4-12 requests/minute) under
normal operation, and ``drivers.updated_at`` is refreshed by every location
batch write (both the legacy marker path and the v2 durable-outbox path) —
the same durable per-driver staleness signal ``stale_intent_reconciler``
already relies on (there, at an hours-scale threshold for a different,
coarser question: is this driver's *app* reachable at all).

``STALE_MINUTES = 10`` is ~40-120x the normal ping cadence: comfortably past
a transient gap (elevator, tunnel, brief OS-level background throttling)
while still surfacing an abandoned trip within the same shift rather than
letting the rider sit locked out of booking, and the insurance Period-3 row
stay open, for hours. We additionally require the ride to have been
``in_progress`` for at least ``STALE_MINUTES`` (via ``ride_started_at``)
before considering it a candidate at all, so a ride that started seconds ago
— whose driver's last DB write predates pickup — can't false-positive on the
very first tick after the trip began.

Replay-safety (CLAUDE.md, Background loops / ``spinr-background-loop`` skill)
-------------------------------------------------------------------------------
This is a pure read + Sentry/log side effect — no DB write — so there is
nothing for two replicas to race on; both alerting is harmless (deliberately
so, see below). Repeat-alert suppression uses a Redis ``SET NX`` dedupe key
per ride (``_ALERT_DEDUPE_TTL_SECONDS``), the same idiom other loops in this
package use for leader locks. ``redis_set_nx`` now *raises* on a real
(Redis-configured-but-unavailable) error rather than silently degrading
(2026-08-11 fix, see ``driver_claim_reaper.py`` for the same consideration) —
this loop explicitly fails OPEN on that error: a dedupe failure means we
proceed to alert (possibly duplicating a page) rather than skip the ride or
crash the tick, because a missed multi-hour alerting gap is worse than a
duplicate PagerDuty notification.

Feature flag: ``stale_in_progress_ride_alert_enabled`` (``app_settings``,
default ``True``). This is a pure kill switch — flipping it off stops the
alert, nothing else; it never touches ride state or insurance periods either
way. Per CLAUDE.md's flag-without-redeploy convention, this lets ops silence
the loop instantly (e.g. alert-noise incident) without a deploy.
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
    from ..settings_loader import get_app_settings
    from .datetime_utils import parse_iso_utc
    from .metrics import inc as _metric_inc
    from .redis_client import redis_set_nx
except ImportError:
    import db_supabase as db  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 5 * 60
STALE_MINUTES = 10
CANDIDATE_LIMIT = 200
# Re-alert cadence for a still-unresolved ride: loud enough that an ignored
# page nags again within the hour, quiet enough not to re-fire every 5 min tick.
_ALERT_DEDUPE_TTL_SECONDS = 60 * 60
_LOOP_NAME = "stale_in_progress_ride_alerter (5min)"


async def _alert_enabled() -> bool:
    """Kill-switch read. Fails open (enabled) on a settings-read error — a
    missing alert is a worse failure mode than one extra Sentry event during
    a settings outage."""
    try:
        settings = await get_app_settings() or {}
    except Exception as exc:
        logger.error(f"stale_in_progress_ride_alerter: settings read failed, defaulting enabled: {exc}")
        return True
    flag = settings.get("stale_in_progress_ride_alert_enabled")
    return True if flag is None else bool(flag)


async def _already_alerted_recently(ride_id: str) -> bool:
    """Redis dedupe. True iff an alert already fired for this ride within
    ``_ALERT_DEDUPE_TTL_SECONDS``. Fails OPEN (returns False -> caller
    alerts) on a Redis error — see module docstring."""
    key = f"spinr:alert:stale_in_progress_ride:{ride_id}"
    try:
        acquired = await redis_set_nx(key, "1", _ALERT_DEDUPE_TTL_SECONDS)
    except Exception as exc:
        logger.warning(
            f"stale_in_progress_ride_alerter: dedupe check unavailable for ride {ride_id}, "
            f"alerting anyway (fail-open): {exc}"
        )
        return False
    return not acquired


def _escalate(ride_id: str, driver_id: str | None, minutes_stale: float | None) -> None:
    """Sentry + structured error log. Pure side effect — caller guarantees no
    ride/driver row is touched here or anywhere else in this module."""
    logger.error(
        "STALE IN_PROGRESS RIDE — no driver location update within threshold: "
        f"ride_id={ride_id} driver_id={driver_id} "
        f"minutes_since_last_location={minutes_stale if minutes_stale is not None else 'unknown'} "
        f"threshold_minutes={STALE_MINUTES}. No automated recovery exists for this state "
        "(stuck_ride_sweeper only covers 'searching') — review via admin live "
        f"monitoring; POST /admin/rides/{ride_id}/complete if confirmed abandoned."
    )
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.capture_message(
            "STALE IN_PROGRESS RIDE — no driver location update",
            level="error",
            tags={
                "spinr_alert": "stale_in_progress_ride",
                "domain": "dispatch",
                "surface": "backend",
                "ride_id": str(ride_id) if ride_id else "unknown",
                "driver_id": str(driver_id) if driver_id else "unknown",
            },
            contexts={
                "stale_in_progress_ride": {
                    "ride_id": ride_id,
                    "driver_id": driver_id,
                    "minutes_since_last_location": minutes_stale,
                    "threshold_minutes": STALE_MINUTES,
                }
            },
        )
    except Exception as sentry_err:  # pragma: no cover - telemetry must never break the loop
        logger.debug(f"stale_in_progress_ride_alerter: Sentry escalation unavailable: {sentry_err}")
    _metric_inc("spinr_dispatch_stale_in_progress_ride_alert_total")


async def _check(now_utc: datetime | None = None) -> dict[str, int]:
    """One tick. PURE DETECTION — must never write to ``rides`` or
    ``drivers`` (or any insurance-period table). Returns counters for
    logging/tests. ``now_utc`` is injectable for deterministic tests, same
    convention as ``stale_intent_reconciler.reconcile_stale_intent``."""
    stats = {"candidates": 0, "alerted": 0, "deduped": 0}

    if not await _alert_enabled():
        return stats

    now = now_utc or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=STALE_MINUTES)
    cutoff_iso = cutoff.isoformat()

    try:
        rides = await db.get_rows(
            "rides",
            {"status": "in_progress", "ride_started_at": {"$lt": cutoff_iso}},
            limit=CANDIDATE_LIMIT,
            columns="id,driver_id,ride_started_at",
        )
    except Exception as exc:
        logger.error(f"stale_in_progress_ride_alerter: candidate ride query failed: {exc}", exc_info=True)
        return stats

    stats["candidates"] = len(rides or [])
    if not rides:
        return stats

    driver_ids = sorted({str(r["driver_id"]) for r in rides if r.get("driver_id")})
    drivers_by_id: dict[str, dict] = {}
    if driver_ids:
        try:
            driver_rows = await db.get_rows(
                "drivers",
                {"id": {"$in": driver_ids}},
                limit=CANDIDATE_LIMIT,
                columns="id,updated_at",
            )
            drivers_by_id = {str(d["id"]): d for d in (driver_rows or []) if d.get("id")}
        except Exception as exc:
            # A failed driver lookup must not abort the tick: a missing
            # signal for these candidates is itself worth alerting on below
            # (treated as unknown/stale), not a reason to skip the whole batch.
            logger.error(f"stale_in_progress_ride_alerter: driver lookup failed: {exc}", exc_info=True)

    for ride in rides:
        ride_id = ride.get("id")
        driver_id = ride.get("driver_id")
        if not ride_id or not driver_id:
            continue

        driver = drivers_by_id.get(str(driver_id))
        driver_last_seen = parse_iso_utc(driver.get("updated_at")) if driver else None
        if driver_last_seen is not None and driver_last_seen >= cutoff:
            continue  # driver has a recent-enough location write; not stale

        minutes_stale = (now - driver_last_seen).total_seconds() / 60.0 if driver_last_seen else None

        if await _already_alerted_recently(str(ride_id)):
            stats["deduped"] += 1
            continue

        _escalate(str(ride_id), str(driver_id), minutes_stale)
        stats["alerted"] += 1

    if stats["alerted"] or stats["deduped"]:
        logger.info(f"stale_in_progress_ride_alerter: {stats}")
    return stats


async def stale_in_progress_ride_alert_loop() -> None:
    """Every 5 min: alert (never mutate) on ``in_progress`` rides with no
    recent driver location update. See module docstring for the full gap
    this closes and why it stays alert-only."""
    await asyncio.sleep(random.uniform(0, CHECK_INTERVAL_SECONDS))
    while True:
        try:
            await _check()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"stale_in_progress_ride_alerter: tick failed: {exc}", exc_info=True)
            _metric_inc("spinr_bgloop_errors_total", {"loop": "stale_in_progress_ride_alerter"})
        _record_heartbeat(_LOOP_NAME)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS + random.uniform(-30, 30))
