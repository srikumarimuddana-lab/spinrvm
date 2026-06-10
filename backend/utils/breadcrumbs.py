"""Persist driver GPS breadcrumbs from batched (background / REST) uploads.

Historically the WebSocket location handler (``routes/websocket.py``) was the
*only* writer of ``driver_location_history``. The background-task and REST
``POST /drivers/location-batch`` paths updated just the live driver marker and
dropped every point but the last, so any stretch a driver spent with the app
backgrounded produced **zero** breadcrumbs. Trip distance is computed from those
breadcrumbs at settlement, and the per-insurance-period trail is what SGI / the
Saskatchewan Traffic Safety Act audit relies on.

This helper persists the full batch, but it must NOT blindly stamp every point
with the driver's *current* ride/phase: a REST/background batch can carry
buffered points from a previous ride or from an earlier phase (e.g. a crash
reload of the on-device buffer, or a batch spanning the pickup→trip
transition). Restamping those would attach stale navigation/previous-ride
breadcrumbs to the current trip and inflate billable ``trip_in_progress`` and
the audit trail. So we:

  * discard points whose embedded ``ride_id`` belongs to a different ride;
  * discard points captured before the current ride's window (stale);
  * attribute each surviving point's phase from ITS OWN capture timestamp
    against the ride's server-recorded milestones (``driver_accepted_at`` /
    ``driver_arrived_at`` / ``ride_started_at``) — never the client's phase tag,
    which a driver could spoof to inflate billable distance;
  * cap the batch (a driver client must not force an unbounded Supabase insert).

NOTE: ``RIDE_STATUS_TO_PHASE`` mirrors the inline map in ``routes/websocket.py``
(~line 607). Keep the two in sync until the WS handler is refactored to import
this constant.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from .. import db_supabase
except ImportError:
    import db_supabase  # type: ignore

try:
    from .datetime_utils import parse_iso_utc
    from .redis_client import redis_get, redis_set
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.redis_client import redis_get, redis_set  # type: ignore

logger = logging.getLogger(__name__)

# B3.1: the WS location handler used to run a rides query on EVERY GPS ping
# (~1/s per online driver) just to resolve the active ride. 5 s of staleness
# is acceptable here — phase attribution is re-derived per point from server
# milestones, and the settlement-time anomaly filter is the billing authority.
_ACTIVE_RIDES_CACHE_TTL = 5

# Ride status → insurance/tracking phase. Mirror of routes/websocket.py.
RIDE_STATUS_TO_PHASE: Dict[str, str] = {
    "driver_assigned": "navigating_to_pickup",
    "driver_accepted": "navigating_to_pickup",
    "driver_arrived": "arrived_at_pickup",
    "in_progress": "trip_in_progress",
}
_ACTIVE_STATUSES = list(RIDE_STATUS_TO_PHASE.keys())

# Mirror the WebSocket batch path's bound so the REST path can't force an
# arbitrarily large single insert (worker stall + breadcrumb-table bloat).
MAX_BREADCRUMB_BATCH = 500
_MAX_CLIENT_CLOCK_SKEW = timedelta(minutes=5)
_ACTIVE_RIDE_NOT_PROVIDED = object()


def _active_rides_cache_key(driver_id: str) -> str:
    return f"driver:{driver_id}:active_rides"


async def resolve_active_rides_cached(driver_id: str) -> List[Dict[str, Any]]:
    """Driver's active ride rows, via a 5 s Redis cache (B3.1, <150ms SLA).

    Empty results are cached too — idle online drivers ping constantly and
    are exactly the case that must not hit the rides table per ping. Redis
    being down degrades to the original per-ping query, never to an error.
    """
    key = _active_rides_cache_key(driver_id)
    try:
        raw = await redis_get(key)
        if raw is not None:
            return json.loads(raw)
    except Exception:
        logger.debug("active-rides cache read failed for %s; querying DB", driver_id, exc_info=True)

    active_rides = await db_supabase.get_rows(
        "rides",
        {"driver_id": driver_id, "status": {"$in": _ACTIVE_STATUSES}},
        order="created_at",
        desc=True,
        limit=10,
    )
    try:
        await redis_set(key, json.dumps(active_rides, default=str), ttl=_ACTIVE_RIDES_CACHE_TTL)
    except Exception:
        logger.debug("active-rides cache write failed for %s", driver_id, exc_info=True)
    return active_rides


async def resolve_active_ride(driver_id: str) -> Optional[Dict[str, Any]]:
    """Return the driver's current active ride row, or None."""
    active_rides = await resolve_active_rides_cached(driver_id)
    return active_rides[0] if active_rides else None


def _phase_for_timestamp(ride: Dict[str, Any], ts: Optional[datetime], current_phase: str) -> str:
    """Server-authoritative phase for a point captured at ``ts``.

    Derived from the ride's recorded milestones so a point buffered during
    navigation and uploaded mid-trip is billed as navigation, not trip. Falls
    back to the ride's current phase when the point carries no usable timestamp.
    """
    if ts is None:
        return current_phase
    started = parse_iso_utc(ride.get("ride_started_at"))
    if started and ts >= started:
        return "trip_in_progress"
    arrived = parse_iso_utc(ride.get("driver_arrived_at"))
    if arrived and ts >= arrived:
        return "arrived_at_pickup"
    return "navigating_to_pickup"


def _coord(point: Dict[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        v = point.get(k)
        if v is not None:
            try:
                value = float(v)
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) else None
    return None


def _valid_lat_lng(lat: float, lng: float) -> bool:
    # Reject invalid ranges and the common no-fix/default coordinate.
    return -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0)


def _bounded_capture_time(captured_at: Optional[datetime], received_at: datetime) -> datetime:
    """Use device capture time only when it is close enough to server receipt time.

    Future device clocks otherwise create future ``driver_location_history.timestamp``
    values that evade timestamp-based cleanup jobs until that future date.
    """
    if captured_at is None:
        return received_at
    if captured_at > received_at + _MAX_CLIENT_CLOCK_SKEW:
        logger.warning(
            "breadcrumb capture time is in the future; using received_at captured_at=%s received_at=%s",
            captured_at.isoformat(),
            received_at.isoformat(),
        )
        return received_at
    return captured_at


async def persist_ride_breadcrumbs(
    driver_id: str,
    points: List[Dict[str, Any]],
    *,
    persist_idle: bool = False,
    active_ride: Optional[Dict[str, Any]] | object = _ACTIVE_RIDE_NOT_PROVIDED,
) -> int:
    """Persist GPS points as ``driver_location_history`` breadcrumbs.

    When the driver has an active ride, ride_id + phase are derived/validated
    server-side per point (see module docstring): stale or other-ride points
    are discarded, phase comes from each point's own capture timestamp, and
    the batch is capped at ``MAX_BREADCRUMB_BATCH``. Single live WebSocket
    pings can pass ``persist_idle=True`` to keep the historical online-idle
    breadcrumb behavior when no active ride exists. Returns inserted rows.
    """
    if not isinstance(points, list):
        logger.warning("breadcrumb persist received non-list points for driver_id=%s", driver_id)
        return 0
    points = [p for p in points if isinstance(p, dict)]
    if not points:
        return 0

    ride = await resolve_active_ride(driver_id) if active_ride is _ACTIVE_RIDE_NOT_PROVIDED else active_ride

    ride_id = ride.get("id") if isinstance(ride, dict) else None
    current_phase = (
        RIDE_STATUS_TO_PHASE.get(ride.get("status", ""), "online_idle") if isinstance(ride, dict) else "online_idle"
    )
    # Points captured before the ride was assigned to this driver are stale
    # (previous ride / idle) and must not attach to this trip.
    window_start = (
        parse_iso_utc(ride.get("driver_accepted_at")) or parse_iso_utc(ride.get("created_at"))
        if isinstance(ride, dict)
        else None
    )
    if not isinstance(ride, dict) and not persist_idle:
        return 0

    # Cap before building rows — keep the most recent points if over the bound.
    if len(points) > MAX_BREADCRUMB_BATCH:
        logger.info(
            "location-batch over cap for driver_id=%s ride_id=%s: %d points -> keeping last %d",
            driver_id,
            ride_id,
            len(points),
            MAX_BREADCRUMB_BATCH,
        )
        points = points[-MAX_BREADCRUMB_BATCH:]

    rows: List[Dict[str, Any]] = []
    for p in points:
        lat = _coord(p, "lat", "latitude")
        lng = _coord(p, "lng", "longitude")
        if lat is None or lng is None or not _valid_lat_lng(lat, lng):
            continue
        # Drop obviously spoofed fixes; the heavy anomaly filter (speed /
        # distance / gap caps) runs once at settlement in routes/drivers.py.
        if p.get("mocked") is True:
            continue
        # Discard points that belong to a different ride (stale buffer / prior trip).
        point_ride = p.get("ride_id")
        if point_ride and point_ride != ride_id:
            continue
        captured_at = parse_iso_utc(
            p.get("captured_at") or p.get("device_timestamp") or p.get("recorded_at") or p.get("timestamp")
        )
        received_at = datetime.now(timezone.utc)
        bounded_captured_at = _bounded_capture_time(captured_at, received_at)
        # Discard points captured before this ride's window (pre-ride / stale).
        if captured_at is not None and window_start is not None and bounded_captured_at < window_start:
            continue
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "driver_id": driver_id,
                "ride_id": ride_id,
                "lat": lat,
                "lng": lng,
                "speed": p.get("speed"),
                "heading": p.get("heading"),
                "accuracy": p.get("accuracy"),
                "altitude": p.get("altitude"),
                # Server-authoritative phase from the point's own capture time.
                "tracking_phase": _phase_for_timestamp(ride, bounded_captured_at, current_phase)
                if isinstance(ride, dict)
                else "online_idle",
                "timestamp": bounded_captured_at,
                "received_at": received_at,
            }
        )

    if not rows:
        return 0
    await db_supabase.insert_many("driver_location_history", rows)
    return len(rows)
