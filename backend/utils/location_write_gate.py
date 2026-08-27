"""Driver location marker write gate — coalesces ``drivers``-row position UPDATEs.

Driver GPS is the highest-frequency write path in the system. Every ingestion
route ends in an ``UPDATE drivers SET lat, lng, updated_at`` for the live map
marker, and until this module existed the two families of route throttled that
write differently — or not at all:

  * WebSocket (``routes/websocket.py``) throttled to one write per 3 s using
    per-connection in-process state, which reset on every reconnect.
  * REST ``POST /drivers/location-batch`` (``routes/drivers/location.py``, three
    handlers) had **no** write throttle, bounded only by the 60/min rate limit.

Because the two used independent state they could not coalesce with each other:
a driver flushing the REST outbox while pinging over WebSocket wrote the same
row from two uncoordinated paths. This module replaces both with one gate keyed
in Redis, so every ingestion route for a given driver shares a single window.

Mechanics: ``SET spinr:locwrite:{driver_id} 1 NX EX <interval>``. Acquiring the
key means no marker write landed within the interval, so this caller should do
it. Losing means another write already did.

**Fails open.** ``redis_set_nx`` deliberately raises on a real Redis error and
leaves the degradation choice to its caller (see its docstring). Here the write
must win: a Redis blip may never suppress a durable location write, matching the
Postgres-first / Redis-second ordering already documented at
``routes/websocket.py`` and ``socket_manager.update_driver_location``.

**Shadow mode.** Gated on the ``location_marker_write_gate_enabled`` app setting
(default OFF). While off, the gate still evaluates and counts what it *would*
have skipped as ``outcome="shadow_throttled"`` but every write still lands. That
yields a real production measurement of write volume and throttle hit-rate at
zero behavioural risk — the flag flips on from evidence, not from arithmetic.
Nothing on this path has ever been measured against live traffic (ACTION_ITEMS
E2 is built but blocked on E1).

**Period 1 is never skipped.** ``routes/drivers/location.py`` folds the
insurance-period deadhead accumulator (``period1_accum_km`` /
``period1_accum_since``) into the *same* ``update_data`` as lat/lng. Dropping
one of those writes would silently under-count a regulated SGI audit figure, so
callers carrying an accumulator delta must pass ``force=True``: the write lands
and the window restarts.

**Interval invariant.** ``utils/stale_intent_reconciler.py`` selects candidates
on ``drivers.updated_at < now - stale_intent_offline_hours`` (default 4 h) to
flip force-killed apps intent-offline. That remains correct only while this
interval stays orders of magnitude below that threshold — at 3 s a live driver's
row stays fresh to within seconds. Do **not** raise MARKER_WRITE_INTERVAL_S to
anything approaching hours without revisiting that loop.
"""

from __future__ import annotations

import logging

try:
    from .metrics import inc as _metric_inc
    from .redis_client import redis_set, redis_set_nx
except ImportError:  # pragma: no cover - top-level execution fallback
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import redis_set, redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

# Minimum seconds between durable ``drivers``-row marker UPDATEs per driver.
# Single source of truth: ``routes/websocket.py`` imports this rather than
# keeping its own copy. See the interval invariant in the module docstring
# before changing it.
MARKER_WRITE_INTERVAL_S = 3.0

# app_settings flag; default OFF so the gate ships in shadow mode.
GATE_FLAG = "location_marker_write_gate_enabled"

_PREFIX = "spinr:locwrite:"

_WRITE_METRIC = "spinr_drivers_location_write_total"
_GATE_FAILED_METRIC = "spinr_drivers_location_gate_failed_total"


def _key(driver_id: str) -> str:
    return f"{_PREFIX}{driver_id}"


async def _gate_enabled() -> bool:
    """Read the app_settings flag. Any failure means 'off' (never throttle)."""
    try:
        try:
            from ..settings_loader import get_app_settings
        except ImportError:  # pragma: no cover - top-level execution fallback
            from settings_loader import get_app_settings  # type: ignore

        settings = await get_app_settings() or {}
        return bool(settings.get(GATE_FLAG, False))
    except Exception:
        # Settings unreadable — degrade to today's behaviour (always write)
        # rather than throttling on an unknown flag state.
        logger.error("location write gate: settings read failed", exc_info=True)
        return False


async def should_write_marker(driver_id: str, *, path: str, force: bool = False) -> bool:
    """Return True iff this caller should issue the ``drivers`` marker UPDATE.

    ``path`` labels the ingestion route for metrics (``rest_v1``,
    ``rest_v2_trip``, ``rest_v2_idle``, ``ws_single``, ``ws_batch``).

    ``force=True`` bypasses the gate for a write that must not be dropped (a
    Period 1 accumulator delta). The write still restarts the window, since the
    row genuinely was written.
    """
    if not driver_id:
        return True

    key = _key(str(driver_id))
    interval = max(1, int(MARKER_WRITE_INTERVAL_S))

    if force:
        try:
            await redis_set(key, "1", ttl=interval)
        except Exception as exc:
            # Non-fatal: the write proceeds regardless, we just lose one
            # window's coalescing.
            logger.warning(f"location write gate: window refresh failed (driver={driver_id}): {exc}")
        _metric_inc(_WRITE_METRIC, {"path": path, "outcome": "period1_forced"})
        return True

    try:
        acquired = await redis_set_nx(key, "1", ttl=interval)
    except Exception as exc:
        # Fail open — a Redis outage must never suppress a durable write.
        logger.error(
            f"location write gate: Redis unavailable, failing open (driver={driver_id}): {exc}",
            exc_info=True,
        )
        _metric_inc(_GATE_FAILED_METRIC)
        _metric_inc(_WRITE_METRIC, {"path": path, "outcome": "written"})
        return True

    if acquired:
        _metric_inc(_WRITE_METRIC, {"path": path, "outcome": "written"})
        return True

    # A marker write for this driver already landed inside the window.
    if await _gate_enabled():
        _metric_inc(_WRITE_METRIC, {"path": path, "outcome": "throttled"})
        return False

    _metric_inc(_WRITE_METRIC, {"path": path, "outcome": "shadow_throttled"})
    return True
