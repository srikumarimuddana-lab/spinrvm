"""B3.3 — in-process batching for per-ping breadcrumb writes.

The WS location handler used to run one driver_location_history insert per
GPS ping (~1/s per driver). Points are now buffered per driver and flushed
as a single insert_many when the buffer reaches _MAX_POINTS, is older than
_MAX_AGE_SECONDS, or the driver's active-ride context changes (so a trip's
points are always persisted under the ride row whose milestones phase them).

Replica-safety: this is connection-scoped state, like
ConnectionManager.active_connections — a driver's WebSocket is pinned to
one replica, so an in-process buffer cannot split a driver's trail across
replicas. The REST location-batch path bypasses the buffer entirely (it is
already batched on-device).

Loss semantics: a flush failure loses at most one buffer (≤ _MAX_POINTS
points) and raises — same contract as the old per-ping write, which dropped
the point and killed the socket; the driver app's on-device buffer re-uploads
via REST batch after reconnect. Callers in disconnect cleanup must wrap
flush_driver_breadcrumbs themselves so cleanup is never masked.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from .breadcrumbs import persist_ride_breadcrumbs
except ImportError:
    from utils.breadcrumbs import persist_ride_breadcrumbs  # type: ignore

logger = logging.getLogger(__name__)

_MAX_POINTS = 10
_MAX_AGE_SECONDS = 10.0


@dataclass
class _DriverBuffer:
    ride_id: Optional[str]
    ride: Optional[Dict[str, Any]]
    opened_at: float
    points: List[Dict[str, Any]] = field(default_factory=list)


_buffers: Dict[str, _DriverBuffer] = {}


async def buffer_ride_breadcrumb(
    driver_id: str,
    point: Dict[str, Any],
    *,
    active_ride: Optional[Dict[str, Any]] = None,
) -> int:
    """Queue one GPS point; flush when the batch is full, old, or the ride changed.

    Returns the number of rows persisted by any flush this call triggered
    (0 when the point was only buffered).
    """
    ride = active_ride if isinstance(active_ride, dict) else None
    ride_id = ride.get("id") if ride else None

    inserted = 0
    buf = _buffers.get(driver_id)
    if buf is not None and buf.ride_id != ride_id:
        # Ride context changed (assigned / completed): persist the old batch
        # under the ride it was captured on before starting a new one.
        inserted += await flush_driver_breadcrumbs(driver_id)
        buf = None

    if buf is None:
        buf = _DriverBuffer(ride_id=ride_id, ride=ride, opened_at=time.monotonic())
        _buffers[driver_id] = buf

    # Freshest row wins at flush — later pings carry newer ride milestones
    # (driver_arrived_at, ride_started_at) that phase earlier buffered points.
    if ride is not None:
        buf.ride = ride
    buf.points.append(point)

    if len(buf.points) >= _MAX_POINTS or time.monotonic() - buf.opened_at >= _MAX_AGE_SECONDS:
        inserted += await flush_driver_breadcrumbs(driver_id)
    return inserted


async def flush_driver_breadcrumbs(driver_id: str) -> int:
    """Persist and clear the driver's buffered points. No-op when empty.

    Call sites: threshold flushes (above), WS disconnect cleanup, and the
    ride-completion path — settlement computes trip distance from the trail,
    so the tail of the trip must be on disk before it runs.
    """
    buf = _buffers.pop(driver_id, None)
    if buf is None or not buf.points:
        return 0
    return await persist_ride_breadcrumbs(
        driver_id,
        buf.points,
        persist_idle=True,
        active_ride=buf.ride,
    )
