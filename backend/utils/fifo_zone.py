"""Airport FIFO queue zone detection (ADR-008).

A driver waiting in any *feeder-lot* venue of a FIFO-enabled *terminal* is put
into one shared, wait-ordered line keyed by the terminal id. This module turns a
raw driver GPS fix into a queue membership transition:

    enter a lot feeding terminal T  ->  stamp drivers.zone_venue_id = T, zone_entered_at = now
    leave all of T's lots           ->  clear both columns

It is called best-effort from every driver-location ingestion path (WS ping and
the REST /location-batch fallback) AFTER the authoritative location write, so it
must never raise into that path and must be cheap when no FIFO venue is
configured (the common case): the feeder-lot set is cached in-process and, with
zero feeder lots, `update_driver_zone` returns before any DB call.

Correctness notes:
  * The actual DB write is guarded in `db_supabase.set_driver_zone` so repeated
    pings inside a lot never reset the wait time (back-of-line bug). This module
    only decides *whether* a transition happened, to avoid a write per ping.
  * A driver's WebSocket connection is pinned to one replica, so the in-process
    `_zone_state` cache reliably tracks the WS path. The REST fallback may land
    on any replica, so it passes the authoritative `current_zone_id` from the
    driver row it already fetched, bypassing the cache for the `prev` value.
  * Exit uses a hysteresis band (radius * EXIT_HYSTERESIS) so a driver parked
    near the lot boundary doesn't flap in and out of the queue.
"""

import logging
import math
import time
from typing import Any, Dict, List, Optional

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover - top-level run
    import db_supabase  # type: ignore

try:
    from .metrics import inc as _metric_inc
except ImportError:  # pragma: no cover - top-level run
    from utils.metrics import inc as _metric_inc  # type: ignore

logger = logging.getLogger(__name__)

# Feeder-lot venue cache. FIFO venues are admin config that changes rarely, and
# this runs on the hot GPS path, so serve from a short in-process TTL cache.
_VENUE_CACHE_TTL_SECONDS = 30.0
_lots_cache: Optional[List[Dict[str, Any]]] = None
_lots_fetched_at = 0.0

# Re-entering within radius * this factor keeps a driver in their current queue,
# so jitter at the lot boundary doesn't reset their place in line.
EXIT_HYSTERESIS = 1.15

# driver_id -> terminal_id for drivers currently believed to be queued. Holds
# only queued drivers (popped on exit), so it stays as small as the live queue.
_zone_state: Dict[str, str] = {}

# Sentinel: "caller did not supply an authoritative zone, use the cache".
_USE_CACHE = object()


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def _feeder_lots() -> List[Dict[str, Any]]:
    """Active feeder-lot venues (those with a queue_for_venue_id), cached."""
    global _lots_cache, _lots_fetched_at
    now = time.monotonic()
    if _lots_cache is not None and now - _lots_fetched_at < _VENUE_CACHE_TTL_SECONDS:
        return _lots_cache
    try:
        venues = await db_supabase.get_rows("venues", {"is_active": True}, limit=2000)
    except Exception:
        # Best-effort enrichment that runs AFTER the authoritative location
        # write — never break GPS ingestion on a venue-cache refresh failure.
        # Logged at error (not warning) per the no-silent-swallow rule; serve
        # the last good snapshot (or empty) until the next refresh.
        logger.error("fifo_zone: feeder-lot venue refresh failed", exc_info=True)
        return _lots_cache or []
    lots: List[Dict[str, Any]] = []
    for v in venues or []:
        terminal = v.get("queue_for_venue_id")
        clat, clng = v.get("center_lat"), v.get("center_lng")
        if not terminal or clat is None or clng is None:
            continue
        lots.append(
            {
                "terminal_id": str(terminal),
                "lat": float(clat),
                "lng": float(clng),
                "radius_m": float(v.get("radius_m") or 150),
            }
        )
    _lots_cache, _lots_fetched_at = lots, now
    return lots


def _resolve_terminal(
    lat: float, lng: float, lots: List[Dict[str, Any]], current_terminal: Optional[str]
) -> Optional[str]:
    """Terminal id the point is queued for, or None. Nearest matching lot wins.

    Lots feeding the driver's *current* terminal get an enlarged (hysteresis)
    radius so a driver hovering at the boundary stays in their existing queue.
    """
    best_terminal: Optional[str] = None
    best_d: Optional[float] = None
    for lot in lots:
        d = _haversine_m(lat, lng, lot["lat"], lot["lng"])
        radius = lot["radius_m"]
        if current_terminal is not None and lot["terminal_id"] == current_terminal:
            radius *= EXIT_HYSTERESIS
        if d <= radius and (best_d is None or d < best_d):
            best_terminal, best_d = lot["terminal_id"], d
    return best_terminal


async def update_driver_zone(
    driver_id: str,
    lat: float,
    lng: float,
    current_zone_id: Any = _USE_CACHE,
) -> Optional[str]:
    """Reconcile a driver's FIFO queue membership against a fresh GPS fix.

    Best-effort: returns the resulting terminal id (or None), and never raises.

    `current_zone_id` is the authoritative drivers.zone_venue_id when the caller
    already has the row (REST /location-batch); WS callers omit it and rely on
    the connection-pinned in-process cache.
    """
    driver_id = str(driver_id)
    lots = await _feeder_lots()

    if current_zone_id is _USE_CACHE:
        prev: Optional[str] = _zone_state.get(driver_id)
    else:
        prev = str(current_zone_id) if current_zone_id else None
        # Keep the in-process view consistent with the authoritative value.
        if prev:
            _zone_state[driver_id] = prev
        else:
            _zone_state.pop(driver_id, None)

    # Fast path: no FIFO configured and this driver isn't tracked → nothing to do.
    if not lots and prev is None:
        return None

    target = _resolve_terminal(lat, lng, lots, prev)
    if target == prev:
        return prev  # no transition → no write

    try:
        changed = await db_supabase.set_driver_zone(driver_id, target)
    except Exception:
        # zone_venue_id is a regulatory-adjacent dispatch fairness signal, not a
        # money/auth write; surface loudly but don't fail location ingestion.
        logger.error("fifo_zone: set_driver_zone failed driver=%s", driver_id, exc_info=True)
        return prev

    if target is None:
        _zone_state.pop(driver_id, None)
    else:
        _zone_state[driver_id] = target

    if changed:
        _metric_inc(
            "spinr_dispatch_fifo_queue_entered_total"
            if target is not None
            else "spinr_dispatch_fifo_queue_exited_total"
        )
    return target


def _reset_for_tests() -> None:
    """Clear module caches between tests."""
    global _lots_cache, _lots_fetched_at
    _lots_cache = None
    _lots_fetched_at = 0.0
    _zone_state.clear()
