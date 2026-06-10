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

Loss semantics: a failed persist NEVER drops points silently — the batch is
stashed under the ride context it was captured on and retried at the next
flush for that driver, before newer points, so trail order is preserved.
The stash is capped at _MAX_PENDING_BATCHES per driver; beyond that the
oldest batch is dropped with an ERROR log (the driver app's on-device
buffer + REST re-upload is the backstop for prolonged DB outages). Flush
failures still raise so callers see them; disconnect cleanup must wrap
flush_driver_breadcrumbs itself so cleanup is never masked.
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

# Batches whose persist failed, retried oldest-first at the next flush, each
# under the ride context it was captured on (a batch from ride X must never
# be re-attributed to a later context).
_pending_retry: Dict[str, List[_DriverBuffer]] = {}
_MAX_PENDING_BATCHES = 5

# Drivers with a flush in flight. A concurrent flush no-ops — same outcome
# as the previous pop-first design, without the double-persist it allowed.
_flushing: set = set()


def _stash_failed_batch(driver_id: str, buf: _DriverBuffer) -> None:
    stash = _pending_retry.setdefault(driver_id, [])
    stash.append(buf)
    overflow = len(stash) - _MAX_PENDING_BATCHES
    if overflow > 0:
        lost = stash[:overflow]
        del stash[:overflow]
        logger.error(
            "breadcrumb retry stash overflow for driver %s: dropped %d batch(es) / %d point(s); "
            "on-device REST re-upload is the remaining backstop",
            driver_id,
            overflow,
            sum(len(b.points) for b in lost),
        )


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

    Retries previously failed batches first (oldest-first, own ride context),
    then the live buffer. On persist failure the points are stashed for the
    next flush and the exception propagates. Call sites: threshold flushes
    (above), WS disconnect cleanup, and the ride-completion path —
    settlement computes trip distance from the trail, so the tail of the
    trip must be on disk before it runs.
    """
    if driver_id in _flushing:
        return 0
    _flushing.add(driver_id)
    try:
        inserted = 0
        pending = _pending_retry.get(driver_id)
        while pending:
            batch = pending[0]
            inserted += await persist_ride_breadcrumbs(
                driver_id, batch.points, persist_idle=True, active_ride=batch.ride
            )
            pending.pop(0)
        _pending_retry.pop(driver_id, None)

        buf = _buffers.pop(driver_id, None)
        if buf is None or not buf.points:
            return inserted
        try:
            inserted += await persist_ride_breadcrumbs(driver_id, buf.points, persist_idle=True, active_ride=buf.ride)
        except Exception:
            _stash_failed_batch(driver_id, buf)
            raise
        return inserted
    finally:
        _flushing.discard(driver_id)
