"""Point-in-time gauges refreshed at metrics-scrape time.

Counters and histograms accumulate as the app runs, but gauges describe *now* —
Redis memory, dependency health, background-loop liveness. They are refreshed
when a scrape arrives rather than on a timer, so there is no background work
when nobody is looking and no staleness when someone is.

Extracted from ``server.py``'s ``/metrics`` handler so the public (auth-gated,
port 8000) and private (unauthenticated, Fly-scraped, separate port) metrics
listeners share one implementation. Two hand-copied refresh blocks would drift,
and a gauge that is fresh on one endpoint and stale on the other is worse than
one that is consistently stale — it makes dashboards disagree with probes.

**Never raises.** Each group is wrapped independently, so a failure in one
degrades that group only and the rest of the exposition is still served. An
empty scrape is far worse than a partial one: Prometheus reads absent series as
"no data", which silently disables alerts rather than firing them.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

try:
    from .dependency_health import gauge_value, probe_dependencies
    from .loop_monitor import get_heartbeat_epochs
    from .metrics import set_gauge
    from .redis_client import get_redis_stats
except ImportError:  # pragma: no cover - exercised by `python -m` vs top-level
    from utils.dependency_health import gauge_value, probe_dependencies  # type: ignore
    from utils.loop_monitor import get_heartbeat_epochs  # type: ignore
    from utils.metrics import set_gauge  # type: ignore
    from utils.redis_client import get_redis_stats  # type: ignore

logger = logging.getLogger("spinr.metrics")


async def _refresh_dependency_gauges() -> None:
    """1 = serving, 0.5 = degraded, 0 = down or unconfigured.

    Uses dependency_health's 30 s cache, so a 15 s scrape interval does not
    double the probe rate. probe_dependencies() never raises by contract.
    """
    result: Dict[str, Any] = await probe_dependencies()
    for name, dep in (result.get("dependencies") or {}).items():
        set_gauge("spinr_dependency_up", gauge_value(dep.get("status", "")), {"dependency": name})


def _refresh_loop_heartbeat_gauges() -> None:
    """Background-loop liveness, per ADR-010 §3.

    A second, independent stall-detection path alongside the in-app
    ``loop_watchdog`` — the watchdog posts to ALERT_WEBHOOK_URL and does not
    depend on the metrics pipeline, while this gives dashboard visibility and
    survives the watchdog itself dying. Alert as:

        time() - spinr_loop_heartbeat_timestamp_seconds > 2 * expected_interval

    Evaluate per provider, never summed — every loop runs on BOTH Fly and
    Railway by design (ADR-010 §4), so a healthy Fly loop would otherwise mask
    a dead Railway one.
    """
    for loop_name, epoch in get_heartbeat_epochs().items():
        set_gauge("spinr_loop_heartbeat_timestamp_seconds", epoch, {"loop": loop_name})


async def _refresh_redis_gauges() -> None:
    """Redis INFO is O(1), so refreshing per scrape is cheap.

    Exposed as gauges (not counters) even for the counter-like INFO values:
    Prometheus can rate() either, and keeping them gauges avoids lying about
    reset semantics for numbers we do not control.
    """
    try:
        rs = await get_redis_stats()
    except Exception:
        set_gauge("spinr_redis_connected", 0)
        raise

    if not rs.get("connected"):
        set_gauge("spinr_redis_connected", 0)
        return

    set_gauge("spinr_redis_used_memory_bytes", rs.get("used_memory_bytes") or 0)
    set_gauge("spinr_redis_maxmemory_bytes", rs.get("maxmemory_bytes") or 0)
    if rs.get("used_memory_percent") is not None:
        set_gauge("spinr_redis_used_memory_percent", rs["used_memory_percent"])
    set_gauge("spinr_redis_total_keys", rs.get("total_keys") or 0)
    set_gauge("spinr_redis_connected_clients", rs.get("connected_clients") or 0)
    set_gauge("spinr_redis_uptime_seconds", rs.get("uptime_seconds") or 0)
    set_gauge("spinr_redis_keyspace_hits", rs.get("keyspace_hits_total") or 0)
    set_gauge("spinr_redis_keyspace_misses", rs.get("keyspace_misses_total") or 0)
    set_gauge("spinr_redis_evicted_keys", rs.get("evicted_keys_total") or 0)
    set_gauge("spinr_redis_expired_keys", rs.get("expired_keys_total") or 0)
    set_gauge("spinr_redis_connected", 1)


async def refresh_all() -> None:
    """Refresh every scrape-time gauge group. Never raises.

    Each group is isolated: a failing dependency probe must not cost us the
    Redis gauges, and vice versa. Failures are logged at error (not swallowed)
    per CLAUDE.md — the scrape surviving is not a reason for the cause to be
    invisible.
    """
    try:
        await _refresh_dependency_gauges()
    except Exception:
        logger.error("Failed to export dependency gauges; serving remaining metrics", exc_info=True)

    try:
        _refresh_loop_heartbeat_gauges()
    except Exception:
        logger.error("Failed to export loop heartbeat gauges; serving remaining metrics", exc_info=True)

    try:
        await _refresh_redis_gauges()
    except Exception:
        logger.error("Failed to export Redis gauges; serving remaining metrics", exc_info=True)
