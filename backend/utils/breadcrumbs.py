"""Persist driver GPS breadcrumbs from batched (background / REST) uploads.

Historically the WebSocket location handler (``routes/websocket.py``) was the
*only* writer of ``driver_location_history``. The background-task and REST
``POST /drivers/location-batch`` paths updated just the live driver marker and
dropped every point but the last, so any stretch a driver spent with the app
backgrounded (navigation in another app, screen locked) produced **zero**
breadcrumbs. Trip distance is computed from those breadcrumbs at settlement, and
the per-insurance-period trail is what SGI / the Saskatchewan Traffic Safety Act
audit relies on — so the gap corrupted both billing and the regulatory record.

This helper lets the batch endpoint persist breadcrumbs with the same
server-derived ``ride_id`` + ``tracking_phase`` the WS handler uses, making
foreground and background capture equal-class.

NOTE: ``RIDE_STATUS_TO_PHASE`` mirrors the inline map in ``routes/websocket.py``
(~line 607). Keep the two in sync until the WS handler is refactored to import
this constant.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from .. import db_supabase
except ImportError:
    import db_supabase  # type: ignore

try:
    from .datetime_utils import parse_iso_utc
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore

logger = logging.getLogger(__name__)

# Ride status → insurance/tracking phase. Mirror of routes/websocket.py.
RIDE_STATUS_TO_PHASE: Dict[str, str] = {
    "driver_assigned": "navigating_to_pickup",
    "driver_accepted": "navigating_to_pickup",
    "driver_arrived": "arrived_at_pickup",
    "in_progress": "trip_in_progress",
}
_ACTIVE_STATUSES = list(RIDE_STATUS_TO_PHASE.keys())


async def resolve_active_ride_phase(driver_id: str) -> Tuple[Optional[str], str]:
    """Return ``(ride_id, tracking_phase)`` for the driver's current active ride.

    No active ride → ``(None, "online_idle")``. Phase is derived from the ride
    status, never trusted from the client payload.
    """
    active_rides = await db_supabase.get_rows(
        "rides",
        {"driver_id": driver_id, "status": {"$in": _ACTIVE_STATUSES}},
        limit=10,
    )
    active_ride = active_rides[0] if active_rides else None
    if not active_ride:
        return None, "online_idle"
    phase = RIDE_STATUS_TO_PHASE.get(active_ride.get("status", ""), "online_idle")
    return active_ride.get("id"), phase


def _coord(point: Dict[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        v = point.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


async def persist_ride_breadcrumbs(driver_id: str, points: List[Dict[str, Any]]) -> int:
    """Persist a batch of GPS points as ``driver_location_history`` breadcrumbs.

    Only writes when the driver currently has an active ride — idle pings carry
    no ``ride_id`` and would only bloat the 90-day-retained table without
    feeding any trip's distance. Returns the number of breadcrumbs inserted.

    ``ride_id`` and ``tracking_phase`` are derived server-side so a background
    point the client tagged ``"background"`` still lands in the correct trip
    phase. Per-point client timestamps are preserved — the settlement-time
    distance filter relies on real capture times to reject gap/speed anomalies.
    """
    ride_id, phase = await resolve_active_ride_phase(driver_id)
    if not ride_id:
        return 0

    rows: List[Dict[str, Any]] = []
    for p in points:
        lat = _coord(p, "lat", "latitude")
        lng = _coord(p, "lng", "longitude")
        if lat is None or lng is None:
            continue
        # Drop obviously spoofed fixes; the heavy anomaly filter (speed /
        # distance / gap caps) runs once at settlement in routes/drivers.py.
        if p.get("mocked") is True:
            continue
        ts = parse_iso_utc(p.get("timestamp")) or datetime.now(timezone.utc)
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
                "tracking_phase": phase,
                "timestamp": ts,
            }
        )

    if not rows:
        return 0
    await db_supabase.insert_many("driver_location_history", rows)
    return len(rows)
