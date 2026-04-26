import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

try:
    from ... import db_supabase
    from ...utils.datetime_utils import parse_iso_utc
except ImportError:
    import db_supabase
    from utils.datetime_utils import parse_iso_utc

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

router = APIRouter()

# ── GPS Location History Cleanup ──


@router.post("/maintenance/cleanup-location-history")
async def admin_cleanup_location_history(days: int = 30):
    """Delete old driver_location_history rows.

    By default deletes rows older than 30 days. On ride completion the
    aggregated data (phase distances, route polyline) is already stored on
    the ride row, so the raw GPS points are only needed for recent disputes.

    Also deletes online_idle points older than 24 hours regardless (they are
    never useful for historical analysis).
    """
    now = datetime.now(timezone.utc)
    cutoff_historical = (now - timedelta(days=days)).isoformat()
    cutoff_idle = (now - timedelta(hours=24)).isoformat()

    deleted_historical = 0
    deleted_idle = 0
    try:
        # Count rows to be deleted for reporting
        old_rows = await db_supabase.get_rows(
            "driver_location_history",
            {"timestamp": {"$lt": cutoff_historical}},
            limit=100000,
        )
        deleted_historical = len(old_rows or [])
        if deleted_historical > 0:
            await db_supabase.delete_many("driver_location_history", {"timestamp": {"$lt": cutoff_historical}})
    except Exception as e:
        logger.warning(f"Cleanup historical GPS failed: {e}")

    try:
        idle_rows = await db_supabase.get_rows(
            "driver_location_history",
            {"timestamp": {"$lt": cutoff_idle}, "tracking_phase": "online_idle"},
            limit=100000,
        )
        deleted_idle = len(idle_rows or [])
        if deleted_idle > 0:
            await db_supabase.delete_many(
                "driver_location_history", {"timestamp": {"$lt": cutoff_idle}, "tracking_phase": "online_idle"}
            )
    except Exception as e:
        logger.warning(f"Cleanup idle GPS failed: {e}")

    logger.info(f"[CLEANUP] Deleted {deleted_historical} historical + {deleted_idle} idle GPS points")
    return {
        "deleted_historical": deleted_historical,
        "deleted_idle": deleted_idle,
        "historical_cutoff": cutoff_historical,
        "idle_cutoff": cutoff_idle,
    }


@router.post("/maintenance/rollup-driver-daily")
async def admin_rollup_driver_daily(target_date: Optional[str] = None):
    """Roll up driver activity for a single day into driver_daily_stats.

    Captures:
    - Online minutes (first_online → last_online span that day)
    - Idle km (distance traveled in 'online_idle' phase — roaming)
    - Navigating km (driver → pickup)
    - Trip km (paid trips, from completed rides that day)
    - Rides completed/cancelled/declined counts
    - Earnings totals

    Run nightly via a cron job hitting this endpoint with yesterday's date.
    Idempotent — upserts by (driver_id, stat_date).
    """
    import math
    from collections import defaultdict

    # Default to yesterday (UTC)
    if target_date:
        stat_date = datetime.fromisoformat(target_date).date()
    else:
        stat_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    day_start = datetime.combine(stat_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    day_start_iso = day_start.isoformat()
    day_end_iso = day_end.isoformat()

    def _haversine(lat1, lng1, lat2, lng2):
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
        )
        return 2 * R * math.asin(math.sqrt(a))

    # Pull all rides from that day to determine the set of active driver IDs
    # and to count/sum earnings. 10 k rides/day is a safe upper bound for the
    # near term; revisit if Spinr expands to > 10 k daily rides per market.
    day_rides = await db_supabase.get_rows(
        "rides",
        {"created_at": {"$gte": day_start_iso, "$lt": day_end_iso}},
        limit=10000,
    )
    rides_by_driver: Dict[str, list] = defaultdict(list)
    for r in day_rides or []:
        did = r.get("driver_id")
        if did:
            rides_by_driver[did].append(r)

    # Pull decline audit log entries for the day to count per driver
    decline_logs = await db.get_rows(
        "audit_logs",
        {"action": "ride_declined", "created_at": {"$gte": day_start_iso, "$lt": day_end_iso}},
        limit=100000,
    )
    declines_by_driver: Dict[str, int] = defaultdict(int)
    for entry in decline_logs or []:
        # user_email column stores driver_id for decline entries (see decline_ride endpoint)
        did = entry.get("user_email")
        if did:
            declines_by_driver[did] += 1

    # Discover drivers who were online that day via a lightweight distinct-value
    # query (only driver_id + timestamp; no coordinates loaded yet). Fetching
    # 1 M rows across all drivers in one query OOMs the process; instead we
    # collect driver IDs here and fetch GPS points per driver below.
    presence_rows = await db_supabase.get_rows(
        "driver_location_history",
        {"timestamp": {"$gte": day_start_iso, "$lt": day_end_iso}},
        order="timestamp",
        limit=50000,  # enough to identify all active drivers; full points fetched per-driver
    )
    drivers_with_gps: set = set()
    for p in presence_rows or []:
        did = p.get("driver_id")
        if did:
            drivers_with_gps.add(did)

    all_driver_ids = drivers_with_gps | set(rides_by_driver.keys())

    created = 0
    updated = 0
    for driver_id in all_driver_ids:
        # Fetch this driver's GPS points for the day in a targeted query rather
        # than carrying all drivers' points in memory simultaneously.
        raw_points = await db_supabase.get_rows(
            "driver_location_history",
            {"driver_id": driver_id, "timestamp": {"$gte": day_start_iso, "$lt": day_end_iso}},
            order="timestamp",
            limit=100000,  # ~1 GPS point/sec × max 24 h online = 86 400 points; 100 k is a safe cap
        )
        points = [p for p in (raw_points or []) if p.get("lat") and p.get("lng")]
        rides = rides_by_driver.get(driver_id, [])

        # Online minutes (simple: span from first to last point)
        online_minutes = 0
        first_online_at = None
        last_online_at = None
        if points:
            first_online_at = points[0].get("timestamp")
            last_online_at = points[-1].get("timestamp")
            t_first = parse_iso_utc(first_online_at)
            t_last = parse_iso_utc(last_online_at)
            if t_first and t_last:
                online_minutes = max(0, int((t_last - t_first).total_seconds() / 60))

        # Per-phase distances
        idle_km = 0.0
        navigating_km = 0.0
        trip_km = 0.0
        for i in range(1, len(points)):
            prev, curr = points[i - 1], points[i]
            seg = _haversine(prev["lat"], prev["lng"], curr["lat"], curr["lng"])
            phase = curr.get("tracking_phase") or "unknown"
            if phase == "online_idle":
                idle_km += seg
            elif phase == "navigating_to_pickup":
                navigating_km += seg
            elif phase == "trip_in_progress":
                trip_km += seg

        # Ride counts and earnings
        rides_completed = sum(1 for r in rides if r.get("status") == "completed")
        rides_cancelled = sum(1 for r in rides if r.get("status") == "cancelled")
        total_earnings = sum(float(r.get("driver_earnings") or 0) for r in rides if r.get("status") == "completed")
        total_tips = sum(float(r.get("tip_amount") or 0) for r in rides if r.get("status") == "completed")

        # Determine service area from driver profile
        drv = await db_supabase.get_driver_by_id(driver_id)
        service_area_id = drv.get("service_area_id") if drv else None

        total_km = round(idle_km + navigating_km + trip_km, 2)

        stat_row = {
            "id": f"{driver_id}_{stat_date.isoformat()}",
            "driver_id": driver_id,
            "stat_date": stat_date.isoformat(),
            "service_area_id": service_area_id,
            "online_minutes": online_minutes,
            "idle_km": round(idle_km, 2),
            "navigating_km": round(navigating_km, 2),
            "trip_km": round(trip_km, 2),
            "total_km": total_km,
            "first_online_at": first_online_at,
            "last_online_at": last_online_at,
            "rides_completed": rides_completed,
            "rides_cancelled": rides_cancelled,
            "rides_declined": declines_by_driver.get(driver_id, 0),
            "total_earnings": round(total_earnings, 2),
            "total_tips": round(total_tips, 2),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Upsert
        existing = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("driver_daily_stats", {"id": stat_row["id"]}, limit=1)
        )
        if existing:
            await db_supabase.update_one("driver_daily_stats", {"id": stat_row["id"]}, stat_row)
            updated += 1
        else:
            stat_row["created_at"] = datetime.now(timezone.utc).isoformat()
            await db_supabase.insert_one("driver_daily_stats", stat_row)
            created += 1

    logger.info(f"[ROLLUP] driver_daily_stats for {stat_date}: created={created} updated={updated}")
    return {
        "stat_date": stat_date.isoformat(),
        "drivers_processed": len(all_driver_ids),
        "created": created,
        "updated": updated,
    }


# ============================================================
# Audit Logs
# ============================================================


@router.get("/audit-logs")
async def get_audit_logs(
    limit: int = Query(50),
    offset: int = Query(0),
    action: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    """Get audit log entries with optional filters and pagination."""
    filters: Dict[str, Any] = {}
    if action:
        filters["action"] = action
    if entity_type:
        filters["entity_type"] = entity_type
    if search:
        term = re.escape(search.strip())
        if term:
            filters["$or"] = [
                {"user_email": {"$regex": term, "$options": "i"}},
                {"entity_id": {"$regex": term, "$options": "i"}},
                {"details": {"$regex": term, "$options": "i"}},
            ]
    logs = await db_supabase.get_rows(
        "audit_logs", filters, order="created_at", desc=True, limit=limit, offset=offset
    )
    return logs


async def log_audit(action: str, entity_type: str, entity_id: str, user_email: str, details: str = ""):
    """Record an audit log entry. Call from admin endpoints."""
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "action": action,  # created, updated, deleted, login, status_change
            "entity_type": entity_type,  # driver, user, ride, promotion, service_area, staff, setting
            "entity_id": entity_id,
            "user_email": user_email,
            "details": details,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
