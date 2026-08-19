# backend/routes/admin/monitoring.py
import asyncio
import logging
import os
import time as _time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...db_supabase import _DB_EXECUTOR as _db_executor_pool
    from ...db_supabase import _breaker as _db_breaker
    from ...db_supabase import _rows_from_res, count_documents, get_rows, run_sync
    from ...dependencies import get_admin_user
    from ...supabase_client import supabase
    from ...utils.audit_logger import log_admin_action
    from ...utils.driver_online import intent_online
    from ...utils.driver_presence import PRESENCE_TTL, present_driver_ids
    from ...utils.metrics import snapshot as _metrics_snapshot
    from ...utils.redis_client import (
        KNOWN_KEY_PREFIXES,
        count_keys_by_prefix,
        get_redis_stats,
        redis_delete_pattern,
    )
except ImportError:
    import db_supabase  # type: ignore
    from db_supabase import _DB_EXECUTOR as _db_executor_pool  # type: ignore
    from db_supabase import _breaker as _db_breaker  # type: ignore
    from db_supabase import _rows_from_res, count_documents, get_rows, run_sync
    from dependencies import get_admin_user
    from supabase_client import supabase
    from utils.audit_logger import log_admin_action  # type: ignore # noqa: F401
    from utils.driver_online import intent_online  # type: ignore
    from utils.driver_presence import PRESENCE_TTL, present_driver_ids  # type: ignore
    from utils.metrics import snapshot as _metrics_snapshot  # type: ignore
    from utils.redis_client import (  # type: ignore
        KNOWN_KEY_PREFIXES,
        count_keys_by_prefix,
        get_redis_stats,
        redis_delete_pattern,
    )

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/monitoring", tags=["Monitoring"])

ACTIVE_RIDE_STATUSES = ["searching", "driver_assigned", "driver_arrived", "in_progress"]
ON_RIDE_STATUSES = ["driver_assigned", "driver_arrived", "in_progress"]


def build_monitoring_ride(
    ride: Dict[str, Any],
    rider: Optional[Dict[str, Any]] = None,
    driver_user: Optional[Dict[str, Any]] = None,
    driver_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Shape a ride row into the MonitoringRide payload the admin dashboard expects.

    Single source of truth for the dashboard ride shape: the snapshot fetcher,
    the live ``ride_requested`` broadcast in create_ride, and the scheduled-ride
    dispatcher all build through here so the contract in
    admin-dashboard/.../monitoring/types.ts (``MonitoringRide``) cannot drift
    between them.
    """
    rider = rider or {}
    driver_user = driver_user or {}
    driver_row = driver_row or {}
    created = ride.get("created_at", "")
    return {
        "id": ride.get("id"),
        "status": ride.get("status"),
        "rider_id": ride.get("rider_id"),
        "rider_name": f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() or "Unknown",
        "rider_phone": rider.get("phone"),
        "rider_photo": rider.get("profile_image"),
        "driver_id": ride.get("driver_id"),
        "driver_name": f"{driver_user.get('first_name', '')} {driver_user.get('last_name', '')}".strip() or None,
        "driver_phone": driver_user.get("phone"),
        "pickup_lat": ride.get("pickup_lat"),
        "pickup_lng": ride.get("pickup_lng"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_lat": ride.get("dropoff_lat"),
        "dropoff_lng": ride.get("dropoff_lng"),
        "dropoff_address": ride.get("dropoff_address"),
        "driver_lat": ride.get("driver_current_lat") or driver_row.get("lat"),
        "driver_lng": ride.get("driver_current_lng") or driver_row.get("lng"),
        "total_fare": ride.get("total_fare"),
        "distance_km": ride.get("distance_km"),
        "created_at": (created.isoformat() if hasattr(created, "isoformat") else str(created)),
        "is_corporate": bool(ride.get("corporate_account_id")),
        # Finding #12 (scheduled-rides gap review): once dispatched, a
        # scheduled ride is indistinguishable from an on-demand one in the
        # monitoring feed — including when its driver cancels post-accept,
        # which is unconditionally terminal (no auto-requeue) for every ride
        # type. A rider who planned around a specific pickup time has less
        # slack to just re-hail than an on-demand rider does, so ops needs
        # to be able to tell these apart to prioritize follow-up.
        "is_scheduled": bool(ride.get("is_scheduled")),
    }


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


async def fetch_monitoring_drivers() -> List[Dict[str, Any]]:
    """Core fetcher for driver monitoring data. Called by REST and WS handlers."""
    drivers_res = await run_sync(
        lambda: (
            supabase.table("drivers")
            .select(
                "id, user_id, is_online, is_available, "
                "went_online_at, went_offline_at, "
                "lat, lng, "
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
    driver_ids = [d["id"] for d in drivers]

    users_res, rides_res, present_ids = await asyncio.gather(
        run_sync(
            lambda: (
                supabase.table("users")
                .select("id, first_name, last_name, phone, profile_image")
                .in_("id", user_ids)
                .execute()
            )
        ),
        run_sync(
            lambda: (
                supabase.table("rides")
                .select("id, driver_id")
                .in_("status", ON_RIDE_STATUSES)
                .in_("driver_id", driver_ids)
                .execute()
            )
        ),
        present_driver_ids(driver_ids),
    )
    users_by_id = {u["id"]: u for u in _rows_from_res(users_res)}
    active_ride_by_driver = {r["driver_id"]: r["id"] for r in _rows_from_res(rides_res)}

    result = []
    for d in drivers:
        user = users_by_id.get(d.get("user_id", ""), {})
        first = user.get("first_name") or ""
        last = user.get("last_name") or ""
        # Authoritative intent comes from the went_online_at / went_offline_at
        # timestamps (migration 97); intent_online() falls back to the legacy
        # is_online flag when both timestamps are NULL (unmigrated rows).
        driver_intent_online = intent_online(d)
        is_present = d["id"] in present_ids
        result.append(
            {
                "id": d["id"],
                "name": f"{first} {last}".strip() or "Unknown Driver",
                "phone": user.get("phone", ""),
                "photo_url": user.get("profile_image"),
                "lat": d.get("lat"),
                "lng": d.get("lng"),
                "is_online": driver_intent_online and is_present,
                "intent_online": driver_intent_online,
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


async def fetch_monitoring_rides() -> List[Dict[str, Any]]:
    """Core fetcher for ride monitoring data. Called by REST and WS handlers."""
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

    riders_res, drivers_map_res = await asyncio.gather(
        run_sync(
            lambda: (
                supabase.table("users")
                .select("id, first_name, last_name, phone, profile_image")
                .in_("id", rider_ids)
                .execute()
            )
        ),
        run_sync(lambda: supabase.table("drivers").select("id, user_id, lat, lng").in_("id", driver_ids).execute()),
    )
    riders_by_id = {u["id"]: u for u in _rows_from_res(riders_res)}
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
        result.append(build_monitoring_ride(r, rider=rider, driver_user=drv_user, driver_row=drv_row))
    return result


@router.get("/drivers")
async def get_monitoring_drivers(
    current_admin: dict = Depends(get_admin_user),
) -> List[Dict[str, Any]]:
    """Return all drivers with current location and status for the live map."""
    return await fetch_monitoring_drivers()


@router.post("/migrate-profile-images")
async def migrate_profile_images(
    limit: int = 25,
    current_admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
    """Backfill: move existing base64 profile photos to object storage.

    New uploads already store a URL; this migrates the legacy base64 blobs in
    users.profile_image to the `profile-photos` bucket in batches. Call
    repeatedly until ``remaining`` is 0. Idempotent (only touches data: URIs).
    """
    import asyncio
    import base64 as _b64
    import uuid as _uuid

    sb = getattr(db_supabase, "supabase", None)
    if not sb:
        raise HTTPException(status_code=503, detail="Object storage not configured")

    limit = max(1, min(int(limit or 25), 100))
    # ilike '%data:%' narrows to base64 blobs; the startswith guard below is the
    # source of truth (skips anything already migrated to a URL).
    rows = await get_rows("users", {"profile_image": {"$regex": "data:"}}, limit=limit)

    _ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
    migrated = 0
    failed = 0
    for u in rows:
        data_uri = u.get("profile_image") or ""
        if not data_uri.startswith("data:"):
            continue
        try:
            header, b64 = data_uri.split(",", 1)
            ctype = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else "image/jpeg"
            content = _b64.b64decode(b64)
            path = f"{u['id']}/{_uuid.uuid4()}.{_ext.get(ctype, 'jpg')}"

            def _up(c=content, p=path, ct=ctype):
                sb.storage.from_("profile-photos").upload(
                    file=c, path=p, file_options={"content-type": ct, "upsert": "true"}
                )
                res = sb.storage.from_("profile-photos").get_public_url(p)
                return res if isinstance(res, str) else getattr(res, "public_url", None)

            url = await asyncio.to_thread(_up)
            if url:
                await db_supabase.update_one("users", {"id": u["id"]}, {"profile_image": url})
                migrated += 1
            else:
                failed += 1
        except Exception:
            # PII-safe: user id only, never the image/address.
            logger.error("[migrate-profile-images] failed for user %s", u.get("id"), exc_info=True)
            failed += 1

    remaining = await count_documents("users", {"profile_image": {"$regex": "data:"}})
    return {"migrated": migrated, "failed": failed, "remaining": remaining}


@router.get("/email-deliverability")
async def get_email_deliverability(
    days: int = 7,
    current_admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
    """Transactional-email health from email_send_log + email_suppressions.

    PIPEDA: never returns recipient email addresses — send rows carry
    recipient_user_id only, and suppression rows expose reason/source, not the
    address.
    """
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    days = max(1, min(int(days or 7), 90))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows = await get_rows("email_send_log", {"created_at": {"$gte": since}}, order="created_at", desc=True, limit=5000)
    by_status = Counter((r.get("status") or "unknown") for r in rows)
    by_provider = Counter((r.get("provider") or "none") for r in rows)
    by_type = Counter((r.get("email_type") or "unknown") for r in rows)
    total = len(rows)
    failed = by_status.get("failed", 0)

    recent_failures = [
        {
            "email_type": r.get("email_type"),
            "provider": r.get("provider"),
            "status": r.get("status"),
            "recipient_user_id": r.get("recipient_user_id"),
            "created_at": r.get("created_at"),
        }
        for r in rows
        if r.get("status") == "failed"
    ][:25]

    suppression_list_size = await count_documents("email_suppressions", {})
    recent_supp = await get_rows("email_suppressions", {}, order="created_at", desc=True, limit=25)
    recent_suppressions = [
        {
            "reason": s.get("reason"),
            "detail": s.get("detail"),
            "source": s.get("source"),
            "message_id": s.get("message_id"),
            "created_at": s.get("created_at"),
        }
        for s in (recent_supp or [])
    ]

    return {
        "window_days": days,
        "total": total,
        "by_status": dict(by_status),
        "by_provider": dict(by_provider),
        "by_type": dict(by_type),
        "failure_rate": round(failed / total, 4) if total else 0.0,
        "suppressed_in_window": by_status.get("suppressed", 0),
        "suppression_list_size": suppression_list_size,
        "recent_failures": recent_failures,
        "recent_suppressions": recent_suppressions,
    }


@router.get("/rides")
async def get_monitoring_rides(
    current_admin: dict = Depends(get_admin_user),
) -> List[Dict[str, Any]]:
    """Return all active rides with rider/driver info for the live map."""
    return await fetch_monitoring_rides()


# ── Redis monitoring + actions ────────────────────────────────────────
#
# The rider/driver apps and the dispatch path all share one Redis
# instance. When it fills up Redis starts evicting keys (if
# maxmemory-policy is allkeys-lru, which is what we recommend) or
# rejects writes (noeviction). Either way the admin needs visibility
# plus a safe way to reclaim space during an incident.


@router.get("/redis")
async def get_redis_health(
    current_admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
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
        "cache:user:": "User row cache (30s TTL)",
        "cache:driver:": "Driver row cache by id (30s TTL)",
        "cache:driver:by_user:": "Driver row cache by user_id (30s TTL)",
        "idem:": "Idempotency-Key response cache (24h TTL)",
        "session:": "Active login sessions",
        "otp:": "OTP records + lockout state",
        "ratelimit:": "SlowAPI rate-limit counters",
        "spinr:retry_budget:": "Per-second global retry budget counter",
        "spinr:ws:": "WebSocket pub/sub state",
        "spinr:presence:driver:": "Driver presence heartbeat (90s TTL)",
        "fares:": "Per-area fare config cache (5min TTL)",
        "__other__": "Keys outside known prefixes",
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


@router.get("/redis/connectivity")
async def get_redis_connectivity(
    current_admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
    """Live connectivity probe — PING + pub/sub round-trip on every configured
    Redis URL. This is what tells you "Redis up but pub/sub broken" (the
    failure mode that breaks cross-replica admin live-monitoring on Upstash),
    which get_redis_stats's INFO snapshot can't see.

    Deliberately a SEPARATE endpoint from `/redis`: each probe opens Redis
    clients and runs SUBSCRIBE/PUBLISH/receive per URL, which costs
    connection/request quota (and can hang when pub/sub is slow — exactly the
    Upstash failure being debugged). The dashboard polls `/redis` every ~10s
    but only calls this on page load + manual refresh, so the expensive probe
    is never on the poll loop. Best-effort.
    """
    connectivity: List[Dict[str, Any]] = []
    try:
        try:
            from ...core.config import settings
            from ...utils.redis_diag import diagnose_redis
            from ...utils.ws_pubsub import resolve_ws_redis_url
        except ImportError:
            from core.config import settings  # type: ignore
            from utils.redis_diag import diagnose_redis  # type: ignore
            from utils.ws_pubsub import resolve_ws_redis_url  # type: ignore

        connectivity = await diagnose_redis(
            {
                "REDIS_URL": settings.REDIS_URL,
                "RATE_LIMIT_REDIS_URL": settings.RATE_LIMIT_REDIS_URL,
                "WS_REDIS_URL (effective)": resolve_ws_redis_url(settings.WS_REDIS_URL, settings.RATE_LIMIT_REDIS_URL),
            }
        )
    except Exception as exc:
        connectivity = [{"label": "probe", "status": "error", "error": str(exc)}]

    return {"connectivity": connectivity}


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
            detail=f"Prefix '{prefix}' is not in the flushable allowlist. Allowed: {sorted(_FLUSHABLE_PREFIXES)}",
        )

    # This is a destructive, irreversible production action (wipes every key
    # under the prefix) -- it must leave a forensic trail. `deleted` (the
    # count) is only known after the flush runs, so audit-log after acting,
    # same convention as other outcome-carrying admin audit rows (e.g.
    # stripe_import.py's account-redirect audit, rides.py's payout-period-close
    # audit). A failure of the flush itself is still audited (outcome=failure)
    # before re-raising -- silently swallowing it would violate CLAUDE.md's
    # "do not silently swallow errors" rule for a destructive admin action.
    try:
        deleted = await redis_delete_pattern(f"{prefix}*")
    except Exception as exc:
        logger.error(
            "redis flush-prefix failed",
            extra={"domain": "admin", "prefix": prefix, "admin_id": current_admin.get("id")},
            exc_info=True,
        )
        await log_admin_action(
            current_admin,
            "redis_flush_prefix",
            "redis",
            prefix,
            {"prefix": prefix, "outcome": "failure", "error": str(exc)},
        )
        raise HTTPException(status_code=503, detail="Redis flush failed; retry.") from exc

    logger.info(
        "redis flush-prefix succeeded",
        extra={
            "domain": "admin",
            "prefix": prefix,
            "deleted_keys": deleted,
            "admin_id": current_admin.get("id"),
        },
    )
    # log_admin_action never raises (see utils/audit_logger.py) and logs its
    # own error on write failure -- an audit-write failure must not block or
    # corrupt this endpoint's response, but it must not be hidden either.
    audit_id = await log_admin_action(
        current_admin,
        "redis_flush_prefix",
        "redis",
        prefix,
        {"prefix": prefix, "outcome": "success", "deleted_keys": deleted},
    )
    if audit_id is None:
        logger.error(
            "redis flush-prefix audit log write failed after a successful flush",
            extra={"domain": "admin", "prefix": prefix, "admin_id": current_admin.get("id")},
        )

    return {
        "prefix": prefix,
        "deleted_keys": deleted,
        "admin_id": current_admin.get("id"),
        "audit_id": audit_id,
    }


@router.get("/websockets")
async def get_websocket_health(
    current_admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
    """WebSocket health for the backend replica that served this request.

    Two parts:
      - ``fanout``: cross-replica pub/sub status (``ws_pubsub.status()``).
        When ``active`` is False the admin live-monitoring map only sees
        clients connected to the *same* uvicorn worker — the exact failure
        behind a frozen map when Redis is unreachable. ``last_error`` says
        why (e.g. ``connect: TimeoutError``).
      - ``connections``: active socket counts for the uvicorn worker that
        answered this request, bucketed by client type. Each worker process
        holds its own in-process registry, so these are **per-worker**, not
        per-host — with ``workers_hint`` workers on a host the true host
        total is spread across that many processes, and a given request only
        sees one. ``worker_pid`` identifies which process answered.
    """
    import platform

    try:
        from ...socket_manager import manager
        from ...utils.ws_pubsub import pubsub
    except ImportError:
        from socket_manager import manager  # type: ignore
        from utils.ws_pubsub import pubsub  # type: ignore

    workers_env = os.environ.get("UVICORN_WORKERS")
    workers_hint = int(workers_env) if workers_env and workers_env.isdigit() else None

    return {
        "fanout": pubsub.status(),
        "connections": manager.connection_stats(),
        "replica_hostname": platform.node(),
        "worker_pid": os.getpid(),
        "workers_hint": workers_hint,
        "per_worker": True,
    }


# ── Infrastructure utilization ────────────────────────────────────────
#
# Process-level resource usage for the backend replica answering this
# request. For a per-replica fleet view, scrape /metrics from each
# replica with Prometheus instead — this endpoint answers "what is
# THIS replica doing right now" for an operator staring at a dashboard.


@router.get("/infrastructure")
async def get_infrastructure_stats(
    current_admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
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

    # Thread pool stats for the spinr-db executor that actually handles
    # run_sync Supabase calls. This previously inspected the asyncio loop's
    # *default* executor, which never runs DB work — ops saw the wrong pool.
    max_workers = getattr(_db_executor_pool, "_max_workers", None)
    spawned_threads = len(getattr(_db_executor_pool, "_threads", ()) or ())
    queue = getattr(_db_executor_pool, "_work_queue", None)
    queued_calls = queue.qsize() if queue is not None else None

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
            "spawned_threads": spawned_threads,
            "queued_calls": queued_calls,
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


@router.get("/health")
async def get_dashboard_health(
    current_admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
    """Concise operational health for uptime checkers and ops dashboards.

    Returns status 'ok' | 'degraded' | 'down' based on DB circuit breaker
    state and Redis connectivity.  Active ride / online driver counts are
    informational — they never affect the overall status.

    Responds 503 when status == 'down' so external monitors can alert
    without parsing the body.  Unlike /infrastructure, this endpoint is
    intentionally terse: one widget, one answer.
    """
    checks: Dict[str, Any] = {}
    overall = "ok"

    # DB health — circuit breaker is the fastest signal we have
    db_state = _db_breaker._state  # "closed" | "open" | "half-open"
    db_failures = len(_db_breaker._failure_times)
    if db_state == "open":
        checks["db"] = {
            "status": "down",
            "circuit": db_state,
            "recent_failures": db_failures,
        }
        overall = "down"
    elif db_state == "half-open" or db_failures > 0:
        checks["db"] = {
            "status": "degraded",
            "circuit": db_state,
            "recent_failures": db_failures,
        }
        if overall == "ok":
            overall = "degraded"
    else:
        checks["db"] = {"status": "ok", "circuit": "closed", "recent_failures": 0}

    # Redis health
    redis_stats = await get_redis_stats()
    redis_connected = redis_stats.get("connected", False)
    checks["redis"] = {
        "status": "ok" if redis_connected else "degraded",
        "connected": redis_connected,
    }
    if not redis_connected and overall == "ok":
        overall = "degraded"

    # Operational counts — best-effort; query failures don't change overall status
    try:
        active_res = await run_sync(
            lambda: supabase.table("rides").select("id", count="exact").in_("status", ACTIVE_RIDE_STATUSES).execute()
        )
        active_rides: Optional[int] = getattr(active_res, "count", None) or len(_rows_from_res(active_res))
    except Exception:
        active_rides = None

    try:
        online_res = await run_sync(
            lambda: supabase.table("drivers").select("id", count="exact").eq("is_online", True).execute()
        )
        online_drivers: Optional[int] = getattr(online_res, "count", None) or len(_rows_from_res(online_res))
    except Exception:
        online_drivers = None

    checks["operations"] = {
        "active_rides": active_rides,
        "online_drivers": online_drivers,
    }

    payload: Dict[str, Any] = {
        "status": overall,
        "checks": checks,
        "uptime_seconds": int(_time.monotonic() - _PROCESS_START_TIME),
    }

    if overall == "down":
        raise HTTPException(status_code=503, detail=payload)

    return payload


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
