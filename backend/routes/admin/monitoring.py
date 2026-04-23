# backend/routes/admin/monitoring.py
import os
import time as _time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ...db_supabase import _rows_from_res, run_sync
    from ...db_supabase import _breaker as _db_breaker
    from ...dependencies import get_admin_user
    from ...supabase_client import supabase
    from ...utils.driver_presence import PRESENCE_TTL, present_driver_ids
    from ...utils.metrics import snapshot as _metrics_snapshot
    from ...utils.redis_client import KNOWN_KEY_PREFIXES, count_keys_by_prefix, get_redis_stats, redis_delete_pattern
except ImportError:
    from db_supabase import _breaker as _db_breaker  # type: ignore
    from db_supabase import _rows_from_res, run_sync
    from dependencies import get_admin_user
    from supabase_client import supabase
    from utils.driver_presence import PRESENCE_TTL, present_driver_ids  # type: ignore
    from utils.metrics import snapshot as _metrics_snapshot  # type: ignore
    from utils.redis_client import KNOWN_KEY_PREFIXES, count_keys_by_prefix, get_redis_stats, redis_delete_pattern  # type: ignore

router = APIRouter(prefix="/admin/monitoring", tags=["Monitoring"])

ACTIVE_RIDE_STATUSES = ["searching", "driver_assigned", "driver_arrived", "in_progress"]
ON_RIDE_STATUSES = ["driver_assigned", "driver_arrived", "in_progress"]

# Process start time, captured once at import so uptime calc is O(1).
_PROCESS_START_TIME = _time.monotonic()

# Prefixes an admin is allowed to purge through the flush endpoint. Keeps
# dangerous resets (session:, otp:) gated behind code review — if an
# operator needs to flush those, they can do it via redis-cli, not a
# one-click dashboard button that would mass-logout every user.
_FLUSHABLE_PREFIXES = {
    "cache:user:",
    "cache:driver:",
    "cache:driver:by_user:",
    "idem:",
    "fares:",
    "spinr:retry_budget:",
}


@router.get("/drivers")
async def get_monitoring_drivers(current_admin: dict = Depends(get_admin_user)) -> List[Dict[str, Any]]:
    """Return all drivers with current location and status for the live map."""
    drivers_res = await run_sync(
        lambda: (
            supabase.table("drivers")
            .select(
                "id, user_id, is_online, is_available, lat, lng, "
                "vehicle_make, vehicle_model, vehicle_color, license_plate, "
                "vehicle_type_id, rating, total_rides, service_area_id"
            )
            .execute()
        )
    )
    drivers = _rows_from_res(drivers_res)
    if not drivers:
        return []

    user_ids = [d["user_id"] for d in drivers if d.get("user_id")]
    users_res = await run_sync(
        lambda: (
            supabase.table("users")
            .select("id, first_name, last_name, phone, profile_image")
            .in_("id", user_ids)
            .execute()
        )
    )
    users_by_id = {u["id"]: u for u in _rows_from_res(users_res)}

    driver_ids = [d["id"] for d in drivers]
    rides_res = await run_sync(
        lambda: (
            supabase.table("rides")
            .select("id, driver_id")
            .in_("status", ON_RIDE_STATUSES)
            .in_("driver_id", driver_ids)
            .execute()
        )
    )
    active_ride_by_driver = {r["driver_id"]: r["id"] for r in _rows_from_res(rides_res)}

    # Uber/Lyft-style presence: the DB `is_online` flag is "intent" (the
    # driver tapped Go Online), presence is "proof" (their app is still
    # talking to us). A driver is truly online iff both are true — this
    # is what fixes the ghost-online bug where `is_online=true` sticks
    # after the app crashes.
    present_ids = await present_driver_ids(driver_ids)

    result = []
    for d in drivers:
        user = users_by_id.get(d.get("user_id", ""), {})
        first = user.get("first_name") or ""
        last = user.get("last_name") or ""
        intent_online = bool(d.get("is_online"))
        is_present = d["id"] in present_ids
        result.append(
            {
                "id": d["id"],
                "name": f"{first} {last}".strip() or "Unknown Driver",
                "phone": user.get("phone", ""),
                "photo_url": user.get("profile_image"),
                "lat": d.get("lat"),
                "lng": d.get("lng"),
                # Effective online state (what dashboards should show as the
                # green badge). Intent AND proof — stops admins seeing ghost
                # drivers whose sockets died an hour ago.
                "is_online": intent_online and is_present,
                # Raw signals, kept on the payload so the dashboard can
                # tell an operator "intent says yes, but we haven't heard
                # from them in 90s" when the two disagree.
                "intent_online": intent_online,
                "is_present": is_present,
                "presence_ttl": PRESENCE_TTL,
                "is_available": bool(d.get("is_available")) and is_present,
                "vehicle_make": d.get("vehicle_make"),
                "vehicle_model": d.get("vehicle_model"),
                "vehicle_color": d.get("vehicle_color"),
                "license_plate": d.get("license_plate"),
                "vehicle_type_id": d.get("vehicle_type_id"),
                "rating": d.get("rating"),
                "total_rides": d.get("total_rides") or 0,
                "active_ride_id": active_ride_by_driver.get(d["id"]),
                "service_area_id": d.get("service_area_id"),
            }
        )
    return result


@router.get("/rides")
async def get_monitoring_rides(current_admin: dict = Depends(get_admin_user)) -> List[Dict[str, Any]]:
    """Return all active rides with rider/driver info for the live map."""
    rides_res = await run_sync(
        lambda: (
            supabase.table("rides")
            .select(
                "id, status, rider_id, driver_id, "
                "pickup_lat, pickup_lng, pickup_address, "
                "dropoff_lat, dropoff_lng, dropoff_address, "
                "total_fare, distance_km, created_at, corporate_account_id"
            )
            .in_("status", ACTIVE_RIDE_STATUSES)
            .execute()
        )
    )
    rides = _rows_from_res(rides_res)
    if not rides:
        return []

    rider_ids = list({r["rider_id"] for r in rides if r.get("rider_id")})
    driver_ids = list({r["driver_id"] for r in rides if r.get("driver_id")})

    riders_res = await run_sync(
        lambda: (
            supabase.table("users")
            .select("id, first_name, last_name, phone, profile_image")
            .in_("id", rider_ids)
            .execute()
        )
    )
    riders_by_id = {u["id"]: u for u in _rows_from_res(riders_res)}

    drivers_map_res = await run_sync(
        lambda: supabase.table("drivers").select("id, user_id, lat, lng").in_("id", driver_ids).execute()
    )
    drivers_rows = _rows_from_res(drivers_map_res)
    drivers_by_id = {d["id"]: d for d in drivers_rows}

    driver_user_ids = [d["user_id"] for d in drivers_rows if d.get("user_id")]
    driver_users_res = await run_sync(
        lambda: supabase.table("users").select("id, first_name, last_name, phone").in_("id", driver_user_ids).execute()
    )
    driver_users_by_id = {u["id"]: u for u in _rows_from_res(driver_users_res)}

    result = []
    for r in rides:
        rider = riders_by_id.get(r.get("rider_id", ""), {})
        drv_row = drivers_by_id.get(r.get("driver_id", ""), {})
        drv_user = driver_users_by_id.get(drv_row.get("user_id", ""), {})
        created = r.get("created_at", "")
        result.append(
            {
                "id": r["id"],
                "status": r["status"],
                "rider_id": r.get("rider_id"),
                "rider_name": f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() or "Unknown",
                "rider_phone": rider.get("phone"),
                "rider_photo": rider.get("profile_image"),
                "driver_id": r.get("driver_id"),
                "driver_name": f"{drv_user.get('first_name', '')} {drv_user.get('last_name', '')}".strip() or None,
                "driver_phone": drv_user.get("phone"),
                "pickup_lat": r.get("pickup_lat"),
                "pickup_lng": r.get("pickup_lng"),
                "pickup_address": r.get("pickup_address"),
                "dropoff_lat": r.get("dropoff_lat"),
                "dropoff_lng": r.get("dropoff_lng"),
                "dropoff_address": r.get("dropoff_address"),
                "driver_lat": r.get("driver_current_lat") or drv_row.get("lat"),
                "driver_lng": r.get("driver_current_lng") or drv_row.get("lng"),
                "total_fare": r.get("total_fare"),
                "distance_km": r.get("distance_km"),
                "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
                "is_corporate": bool(r.get("corporate_account_id")),
            }
        )
    return result


# ── Redis monitoring + actions ────────────────────────────────────────
#
# The rider/driver apps and the dispatch path all share one Redis
# instance. When it fills up Redis starts evicting keys (if
# maxmemory-policy is allkeys-lru, which is what we recommend) or
# rejects writes (noeviction). Either way the admin needs visibility
# plus a safe way to reclaim space during an incident.


@router.get("/redis")
async def get_redis_health(current_admin: dict = Depends(get_admin_user)) -> Dict[str, Any]:
    """Redis memory/usage snapshot + per-prefix key counts.

    Returns a two-part payload:
      - `stats`: O(1) INFO-derived counters (memory used, max, evictions,
        hit/miss, uptime, etc.). Safe to refresh every few seconds.
      - `prefix_counts`: O(total_keys) SCAN — heavier. Run on page
        load / user-triggered refresh, not as a 1s polling loop.

    `hit_rate_percent` is derived from Redis's own keyspace_hits and
    keyspace_misses (all keys, all ops) so it's a whole-Redis number —
    not our app-cache-only hit rate. Our app-specific hit rate comes
    from the `spinr_cache_hit_total` / `spinr_cache_miss_total`
    counters on /metrics.
    """
    stats = await get_redis_stats()
    prefix_counts = await count_keys_by_prefix(KNOWN_KEY_PREFIXES)

    hits = stats.get("keyspace_hits_total") or 0
    misses = stats.get("keyspace_misses_total") or 0
    total_ops = hits + misses
    stats["hit_rate_percent"] = round(hits / total_ops * 100, 2) if total_ops > 0 else None

    # Annotate each prefix so the dashboard can label rows without
    # hard-coding the mapping in the frontend. Add new prefixes here
    # when you add them to KNOWN_KEY_PREFIXES.
    prefix_descriptions = {
        "cache:user:":            "User row cache (30s TTL)",
        "cache:driver:":          "Driver row cache by id (30s TTL)",
        "cache:driver:by_user:":  "Driver row cache by user_id (30s TTL)",
        "idem:":                  "Idempotency-Key response cache (24h TTL)",
        "session:":               "Active login sessions",
        "otp:":                   "OTP records + lockout state",
        "ratelimit:":             "SlowAPI rate-limit counters",
        "spinr:retry_budget:":    "Per-second global retry budget counter",
        "spinr:ws:":              "WebSocket pub/sub state",
        "spinr:presence:driver:": "Driver presence heartbeat (90s TTL)",
        "fares:":                 "Per-area fare config cache (5min TTL)",
        "__other__":              "Keys outside known prefixes",
    }
    prefixes_with_meta = [
        {
            "prefix": p,
            "count": c,
            "description": prefix_descriptions.get(p, ""),
            "flushable": p in _FLUSHABLE_PREFIXES,
        }
        for p, c in prefix_counts.items()
    ]
    prefixes_with_meta.sort(key=lambda x: x["count"], reverse=True)

    return {
        "stats": stats,
        "prefix_counts": prefixes_with_meta,
        "flushable_prefixes": sorted(_FLUSHABLE_PREFIXES),
    }


class FlushPrefixRequest(BaseModel):
    prefix: str = Field(..., description="Key prefix ending with ':' — e.g. 'cache:user:'")
    confirm: str = Field(
        ...,
        description="Must be 'FLUSH' to proceed. Prevents accidental one-click wipes.",
    )


@router.post("/redis/flush-prefix")
async def flush_redis_prefix(
    body: FlushPrefixRequest,
    current_admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
    """Delete every key matching a prefix glob (prefix + '*').

    Restricted to the allowlist in `_FLUSHABLE_PREFIXES` so an operator
    can't one-click-log-out the entire user base via the OTP or
    session prefixes. If you legitimately need to flush those, use
    `redis-cli` with explicit confirmation — the audit trail there is
    better.
    """
    if body.confirm != "FLUSH":
        raise HTTPException(
            status_code=400,
            detail="Confirmation required: set `confirm` to 'FLUSH' to proceed.",
        )

    prefix = body.prefix.strip()
    if prefix not in _FLUSHABLE_PREFIXES:
        raise HTTPException(
            status_code=403,
            detail=f"Prefix '{prefix}' is not in the flushable allowlist. "
                   f"Allowed: {sorted(_FLUSHABLE_PREFIXES)}",
        )

    deleted = await redis_delete_pattern(f"{prefix}*")
    return {
        "prefix": prefix,
        "deleted_keys": deleted,
        "admin_id": current_admin.get("id"),
    }


# ── Infrastructure utilization ────────────────────────────────────────
#
# Process-level resource usage for the backend replica answering this
# request. For a per-replica fleet view, scrape /metrics from each
# replica with Prometheus instead — this endpoint answers "what is
# THIS replica doing right now" for an operator staring at a dashboard.


@router.get("/infrastructure")
async def get_infrastructure_stats(current_admin: dict = Depends(get_admin_user)) -> Dict[str, Any]:
    """System-level health for the current backend replica + summary
    of the DB/cache counters.

    - Uses the Python `resource` module (no psutil dependency) for RSS
      and CPU time so it works on every deployment we support.
    - Includes the DB circuit-breaker state so an operator can tell at
      a glance whether Supabase is flaking without tailing logs.
    - Includes Redis stats inline so the operator has one page instead
      of two.
    """
    uptime_seconds = int(_time.monotonic() - _PROCESS_START_TIME)

    # Process memory + CPU — `resource` is stdlib, no extra deps.
    try:
        import resource as _resource  # Unix-only; macOS + Linux OK, Windows doesn't have it
        ru = _resource.getrusage(_resource.RUSAGE_SELF)
        # ru_maxrss is bytes on Linux, kilobytes on macOS. Normalise to bytes.
        import sys as _sys
        if _sys.platform == "darwin":
            rss_bytes = ru.ru_maxrss  # already bytes on macOS (apparently)
        else:
            rss_bytes = ru.ru_maxrss * 1024  # Linux: KB → bytes
        cpu_user_seconds = ru.ru_utime
        cpu_system_seconds = ru.ru_stime
    except Exception:
        rss_bytes = None
        cpu_user_seconds = None
        cpu_system_seconds = None

    # Thread pool stats (the executor handling run_sync for Supabase).
    # The default executor doesn't expose active/idle counts — we can
    # at least report the configured max worker count so ops knows the
    # ceiling.
    import asyncio as _asyncio
    loop = _asyncio.get_running_loop()
    executor = loop._default_executor  # type: ignore[attr-defined]
    max_workers = None
    if executor is not None and hasattr(executor, "_max_workers"):
        max_workers = getattr(executor, "_max_workers", None)

    # DB circuit breaker snapshot
    db_circuit = {
        "state": _db_breaker._state,
        "recent_failures": len(_db_breaker._failure_times),
        "opened_at_monotonic": _db_breaker._opened_at,
    }

    # Redis quick numbers (cheap, already O(1))
    redis_stats = await get_redis_stats()

    # Metrics snapshot — sum each counter across all label sets so the
    # operator sees the top-line without drilling through Prometheus.
    metrics_sum: Dict[str, int] = {}
    snap = _metrics_snapshot()
    for name, bucket in snap["counters"].items():
        metrics_sum[name] = sum(bucket.values())

    return {
        "replica": {
            "hostname": os.environ.get("HOSTNAME") or os.uname().nodename,
            "pid": os.getpid(),
            "uptime_seconds": uptime_seconds,
            "python_version": os.environ.get("PYTHON_VERSION") or None,
        },
        "process": {
            "rss_bytes": rss_bytes,
            "rss_human": _humanize_bytes_local(rss_bytes) if rss_bytes else None,
            "cpu_user_seconds": cpu_user_seconds,
            "cpu_system_seconds": cpu_system_seconds,
        },
        "thread_pool": {
            "max_workers": max_workers,
            "note": "Active/idle counts not exposed by Python's default executor",
        },
        "db_circuit_breaker": db_circuit,
        "redis": {
            "connected": redis_stats.get("connected", False),
            "used_memory_bytes": redis_stats.get("used_memory_bytes"),
            "used_memory_human": redis_stats.get("used_memory_human"),
            "maxmemory_human": redis_stats.get("maxmemory_human"),
            "used_memory_percent": redis_stats.get("used_memory_percent"),
            "total_keys": redis_stats.get("total_keys"),
            "evicted_keys_total": redis_stats.get("evicted_keys_total"),
        },
        "metrics": metrics_sum,
    }


def _humanize_bytes_local(n: Optional[int]) -> Optional[str]:
    """Small duplicate of the helper in redis_client — keeps this route
    free of the circular import that would happen if we imported it
    from there. Five lines is cheaper than the import dance."""
    if n is None:
        return None
    units = ["B", "K", "M", "G", "T"]
    size = float(n)
    for u in units:
        if size < 1024:
            return f"{int(size)}{u}" if u == "B" else f"{size:.1f}{u}"
        size /= 1024
    return f"{size:.1f}T"
