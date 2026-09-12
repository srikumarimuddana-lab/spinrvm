"""Live route-deviation safety alert (Uber "RideCheck"-style detection).

For rides that are ``in_progress``, periodically compare the driver's
current position (``drivers.lat/lng``) against the ride's planned route
(``rides.planned_route_polyline``, captured at booking time — see
``routes/rides/booking.py``). If the driver is sustained more than
``_DEVIATION_THRESHOLD_METERS`` off that route for more than
``_SUSTAIN_SECONDS``, an open safety incident is created for the
trust-and-safety team, the same escalation path ``safety_checkin_loop.py``
uses (incident row + ``notify_safety_team`` + audit log).

Gap this closes
----------------
``.claude/context/domain-safety.md``'s "Night ride protections" section
lists a ">500m/60s live deviation ping" as "Intended, not built."
``utils/route_validation.py`` computes a similarly-named ``deviation_pct``,
but that is GPS-spoofing fraud detection run once, after the trip
completes, against the road network generally (is this point near ANY
road) — it does not run live and says nothing about whether the driver is
still heading toward the booked destination. This module is a genuinely
new, live, destination-aware check, not a duplicate of that one.

Design
------
- Alert-only: never mutates ``rides``/``drivers``/insurance-period rows.
  Mirrors ``stale_in_progress_ride_alerter.py``'s reasoning for why an
  automated ride-state fix is out of scope here too — a live deviation can
  be a legitimate detour (road closure, traffic, rider-requested stop), so
  a human on the safety team decides what to do next; this loop only gets
  them there.
- Deviation is measured as the driver's current position's minimum
  haversine distance to any point on ``planned_route_polyline`` — cheap,
  no external API call (unlike ``route_validation.py``'s OSRM/Google Roads
  calls), so this can run every tick for every in_progress ride without new
  third-party cost, latency, or an extra point of failure.
- "Sustained" tracking uses two Redis keys per ride (the same NX-claim
  idiom ``safety_checkin_loop.py`` uses for its own two-phase send/escalate
  state):
    ``route_deviation:first_seen:{ride_id}`` — claimed (SET NX) the first
      tick a ride is observed off-route; holds the ISO timestamp of that
      first observation.
    ``route_deviation:escalated:{ride_id}`` — claimed (SET NX) once a ride
      crosses the sustain threshold, so only one replica creates the
      incident for a given deviation episode.
  Both keys are deleted the moment a ride is observed back on-route, so a
  second, later deviation episode on the same (long) ride can be detected
  and escalated again rather than being permanently suppressed by the
  first episode's keys.
- Driver position freshness guard: a ``drivers`` row whose ``updated_at``
  is older than ``_MAX_POSITION_AGE_SECONDS`` is skipped for that tick
  (not treated as on-route) — a stale position is not evidence of
  anything, and ``stale_in_progress_ride_alerter.py`` already separately
  covers "driver hasn't reported in a long time" at its own (10-minute)
  threshold.

Feature flag: ``route_deviation_alert_enabled`` (``app_settings``, default
``False`` — dark-launched per CLAUDE.md's rollout rule for new,
non-trivial, team-facing behavior: ship dark, verify, then flip on).
Unlike ``stale_in_progress_ride_alert_enabled`` (an established alert that
fails OPEN on a settings-read error), this is a brand-new alert path that
does not exist in production today — a settings-read error is treated as
disabled (fails CLOSED), so an infra hiccup cannot silently turn on a new
safety-team-paging behavior nobody has switched on yet.

Interval: 30 seconds (matches ``safety_checkin_loop.py``, the closest
existing analog in both domain and cadence).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

try:
    from .. import db_supabase as db
    from ..features import notify_safety_team
    from ..geo_utils import calculate_distance
    from ..settings_loader import get_app_settings
    from .audit_logger import log_admin_action as _log_audit
    from .datetime_utils import parse_iso_utc
    from .metrics import inc as _metric_inc
    from .redis_client import redis_delete, redis_get, redis_set_nx
except ImportError:
    import db_supabase as db  # type: ignore
    from features import notify_safety_team  # type: ignore
    from geo_utils import calculate_distance  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.audit_logger import log_admin_action as _log_audit  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import redis_delete, redis_get, redis_set_nx  # type: ignore

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = 30
_DEVIATION_THRESHOLD_METERS = 500.0
_SUSTAIN_SECONDS = 60
_MAX_POSITION_AGE_SECONDS = 3 * 60  # a stale drivers.lat/lng row is not evidence either way
_CANDIDATE_LIMIT = 200
_FIRST_SEEN_TTL_SECONDS = 30 * 60  # generous vs. the 60s sustain window; just a garbage-collection net
_ESCALATED_TTL_SECONDS = 4 * 60 * 60  # matches safety_checkin_loop.py's escalated-key TTL
# Must match the exact name lifespan.py passes to _spawn() for this loop —
# the watchdog matches on this string.
_LOOP_NAME = "route_deviation_alerter (30s)"


def _first_seen_key(ride_id: str) -> str:
    return f"route_deviation:first_seen:{ride_id}"


def _escalated_key(ride_id: str) -> str:
    return f"route_deviation:escalated:{ride_id}"


async def _deviation_alert_enabled() -> bool:
    """Kill-switch read. Fails CLOSED (disabled) on a settings-read error —
    unlike an established always-on alert, this is a brand-new paging path
    that defaults off; an infra hiccup must not be the thing that turns it
    on for the first time."""
    try:
        settings = await get_app_settings() or {}
    except Exception as exc:
        logger.error(f"route_deviation_alerter: settings read failed, defaulting disabled: {exc}")
        return False
    flag = settings.get("route_deviation_alert_enabled")
    return bool(flag) if flag is not None else False


def _min_distance_to_route_meters(lat: float, lng: float, polyline: list) -> float | None:
    """Minimum haversine distance (meters) from (lat, lng) to any vertex of
    ``polyline`` (a list of [lat, lng] pairs). Vertex-distance rather than a
    true point-to-segment projection — simple and cheap, and a booked-route
    polyline is dense enough (Google/OSRM overview geometry) that this is a
    good enough approximation for a >500m alert threshold. Returns None if
    the polyline has no usable points."""
    best: float | None = None
    for point in polyline:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        p_lat, p_lng = point[0], point[1]
        if p_lat is None or p_lng is None:
            continue
        try:
            dist_km = calculate_distance(lat, lng, float(p_lat), float(p_lng))
        except (TypeError, ValueError):
            continue
        dist_m = dist_km * 1000.0
        if best is None or dist_m < best:
            best = dist_m
    return best


def _valid_position(lat, lng) -> bool:
    return lat is not None and lng is not None and (lat, lng) != (0, 0)


async def _clear_ride_state(ride_id: str) -> None:
    """Back on-route (or no longer checkable): clear both keys so a later,
    distinct deviation episode on the same ride can be detected and
    escalated again rather than being permanently suppressed."""
    await redis_delete(_first_seen_key(ride_id))
    await redis_delete(_escalated_key(ride_id))


async def _escalate(ride: dict, distance_m: float, now: datetime) -> None:
    ride_id = ride.get("id")
    rider_id = ride.get("rider_id")
    driver_id = ride.get("driver_id")

    try:
        incident = {
            "id": str(uuid.uuid4()),
            "reported_by_user_id": rider_id,
            "role": "rider",
            "category": "route_deviation",
            "description": (
                f"Driver has been {distance_m:.0f}m off the planned route for at least "
                f"{_SUSTAIN_SECONDS}s. Ride ID: {ride_id}, driver ID: {driver_id}. "
                "Please follow up via the safety dashboard."
            ),
            "status": "open",
            "ride_id": ride_id,
            "reported_at": now.isoformat(),
            "created_at": now.isoformat(),
        }
        await db.insert_one("safety_incidents", incident)
        logger.error(
            f"[ROUTE_DEVIATION] ride={ride_id} driver={driver_id} sustained {distance_m:.0f}m off-route "
            f">= {_SUSTAIN_SECONDS}s; safety incident opened."
        )

        try:
            await notify_safety_team(incident)
        except Exception as notify_exc:
            logger.error(
                f"[ROUTE_DEVIATION] notify_safety_team failed for incident {incident['id']}: {notify_exc}",
                exc_info=True,
            )

        try:
            await _log_audit(
                admin={"id": "system", "role": "system"},
                action="safety_incident_auto_escalated",
                resource="safety_incidents",
                resource_id=incident["id"],
                details={
                    "ride_id": ride_id,
                    "driver_id": driver_id,
                    "reason": "route_deviation",
                    "distance_m": round(distance_m, 1),
                    "sustain_seconds": _SUSTAIN_SECONDS,
                },
            )
        except Exception as audit_exc:
            logger.error(f"[ROUTE_DEVIATION] Audit log write failed for incident {incident['id']}: {audit_exc}")

        _metric_inc("spinr_safety_route_deviation_alert_total")
    except Exception:
        logger.error(
            f"[ROUTE_DEVIATION] Failed to escalate ride {ride_id} — will retry next tick",
            exc_info=True,
        )
        # Re-raise so the caller knows the escalation claim should not stick
        # (see _tick: on failure here the escalated key was never set, so a
        # later tick will retry).
        raise


async def _tick(now_utc: datetime | None = None) -> dict[str, int]:
    """One tick. Returns counters for logging/tests."""
    stats = {"candidates": 0, "off_route": 0, "escalated": 0}

    if not await _deviation_alert_enabled():
        return stats

    now = now_utc or datetime.now(timezone.utc)

    try:
        rides = await db.get_rows(
            "rides",
            {"status": "in_progress"},
            limit=_CANDIDATE_LIMIT,
            columns="id,driver_id,rider_id,planned_route_polyline",
        )
    except Exception as exc:
        logger.error(f"route_deviation_alerter: candidate ride query failed: {exc}", exc_info=True)
        return stats

    candidates = [
        r
        for r in (rides or [])
        if r.get("driver_id")
        and isinstance(r.get("planned_route_polyline"), list)
        and len(r["planned_route_polyline"]) >= 2
    ]
    stats["candidates"] = len(candidates)
    if not candidates:
        return stats

    driver_ids = sorted({str(r["driver_id"]) for r in candidates})
    drivers_by_id: dict[str, dict] = {}
    try:
        driver_rows = await db.get_rows(
            "drivers",
            {"id": {"$in": driver_ids}},
            limit=_CANDIDATE_LIMIT,
            columns="id,lat,lng,updated_at",
        )
        drivers_by_id = {str(d["id"]): d for d in (driver_rows or []) if d.get("id")}
    except Exception as exc:
        logger.error(f"route_deviation_alerter: driver lookup failed: {exc}", exc_info=True)
        return stats

    for ride in candidates:
        ride_id = str(ride["id"])
        driver = drivers_by_id.get(str(ride["driver_id"]))
        if not driver:
            continue

        lat, lng = driver.get("lat"), driver.get("lng")
        if not _valid_position(lat, lng):
            continue

        last_seen = parse_iso_utc(driver.get("updated_at"))
        if last_seen is None or (now - last_seen).total_seconds() > _MAX_POSITION_AGE_SECONDS:
            continue  # stale position — not evidence either way this tick

        distance_m = _min_distance_to_route_meters(lat, lng, ride["planned_route_polyline"])
        if distance_m is None:
            continue

        if distance_m < _DEVIATION_THRESHOLD_METERS:
            await _clear_ride_state(ride_id)
            continue

        stats["off_route"] += 1

        # Claim (or read) the first-observed-off-route timestamp for this
        # episode. Atomic SET NX so two concurrent ticks (this replica
        # racing itself, or two replicas) can't both start a fresh clock.
        claimed_start = await redis_set_nx(_first_seen_key(ride_id), now.isoformat(), ttl=_FIRST_SEEN_TTL_SECONDS)
        if claimed_start:
            continue  # this is the first tick to see it off-route; not sustained yet

        first_seen_str = await redis_get(_first_seen_key(ride_id))
        if not first_seen_str:
            continue  # key expired between the NX attempt and this read; treat as not-yet-sustained
        first_seen = parse_iso_utc(first_seen_str)
        if first_seen is None or (now - first_seen).total_seconds() < _SUSTAIN_SECONDS:
            continue  # off-route, but not sustained long enough yet

        already_escalated = await redis_get(_escalated_key(ride_id))
        if already_escalated:
            continue  # this episode already escalated

        claimed_escalation = await redis_set_nx(_escalated_key(ride_id), "1", ttl=_ESCALATED_TTL_SECONDS)
        if not claimed_escalation:
            continue  # another replica just won the escalation claim

        try:
            await _escalate(ride, distance_m, now)
            stats["escalated"] += 1
        except Exception:
            # _escalate already logged. Release the escalation claim so a
            # later tick can retry rather than silently never escalating.
            try:
                await redis_delete(_escalated_key(ride_id))
            except Exception:
                logger.error(
                    f"[ROUTE_DEVIATION] Failed to release escalation claim after failure ride_id={ride_id}",
                    exc_info=True,
                )

    if stats["escalated"]:
        logger.info(f"route_deviation_alerter: {stats}")
    return stats


async def route_deviation_alert_loop() -> None:
    """Every 30s: check in_progress rides for a sustained >500m route
    deviation and escalate a safety incident. See module docstring."""
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("route_deviation_alerter tick failed", exc_info=True)
            _metric_inc("spinr_bgloop_errors_total", {"loop": "route_deviation_alerter"})
        _record_heartbeat(_LOOP_NAME)
        await asyncio.sleep(_INTERVAL_SECONDS)
