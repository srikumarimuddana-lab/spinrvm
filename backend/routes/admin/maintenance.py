import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Query

try:
    from ...db import db
except ImportError:
    from db import db

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
    now = datetime.utcnow()
    cutoff_historical = (now - timedelta(days=days)).isoformat()
    cutoff_idle = (now - timedelta(hours=24)).isoformat()

    deleted_historical = 0
    deleted_idle = 0
    try:
        # Count rows to be deleted for reporting
        old_rows = await db.get_rows(
            "driver_location_history",
            {"timestamp": {"$lt": cutoff_historical}},
            limit=100000,
        )
        deleted_historical = len(old_rows or [])
        if deleted_historical > 0:
            await db.driver_location_history.delete_many({"timestamp": {"$lt": cutoff_historical}})
    except Exception as e:
        logger.warning(f"Cleanup historical GPS failed: {e}")

    try:
        idle_rows = await db.get_rows(
            "driver_location_history",
            {"timestamp": {"$lt": cutoff_idle}, "tracking_phase": "online_idle"},
            limit=100000,
        )
        deleted_idle = len(idle_rows or [])
        if deleted_idle > 0:
            await db.driver_location_history.delete_many(
                {
                    "timestamp": {"$lt": cutoff_idle},
                    "tracking_phase": "online_idle",
                }
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
        stat_date = (datetime.utcnow() - timedelta(days=1)).date()

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

    # Pull all GPS points from that day
    all_points = await db.get_rows(
        "driver_location_history",
        {"timestamp": {"$gte": day_start_iso, "$lt": day_end_iso}},
        order="timestamp",
        limit=1000000,
    )

    # Group by driver
    by_driver: Dict[str, list] = defaultdict(list)
    for p in all_points or []:
        did = p.get("driver_id")
        if did and p.get("lat") and p.get("lng"):
            by_driver[did].append(p)

    # Pull all rides from that day to count + sum earnings
    day_rides = await db.get_rows(
        "rides",
        {"created_at": {"$gte": day_start_iso, "$lt": day_end_iso}},
        limit=10000,
    )
    rides_by_driver: Dict[str, list] = defaultdict(list)
    for r in day_rides or []:
        did = r.get("driver_id")
        if did:
            rides_by_driver[did].append(r)

    all_driver_ids = set(by_driver.keys()) | set(rides_by_driver.keys())

    created = 0
    updated = 0
    for driver_id in all_driver_ids:
        points = sorted(by_driver.get(driver_id, []), key=lambda x: str(x.get("timestamp", "")))
        rides = rides_by_driver.get(driver_id, [])

        # Online minutes (simple: span from first to last point)
        online_minutes = 0
        first_online_at = None
        last_online_at = None
        if points:
            try:
                first_online_at = points[0].get("timestamp")
                last_online_at = points[-1].get("timestamp")
                t_first = datetime.fromisoformat(str(first_online_at).replace("Z", "+00:00").replace("+00:00", ""))
                t_last = datetime.fromisoformat(str(last_online_at).replace("Z", "+00:00").replace("+00:00", ""))
                online_minutes = max(0, int((t_last - t_first).total_seconds() / 60))
            except Exception as _exc:
                logger.debug(f"Could not compute online_minutes for driver {driver_id}: {_exc}")

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
        drv = await db.drivers.find_one({"id": driver_id})
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
            "rides_declined": 0,  # TODO: wire up if we track declines
            "total_earnings": round(total_earnings, 2),
            "total_tips": round(total_tips, 2),
            "updated_at": datetime.utcnow().isoformat(),
        }

        # Upsert
        existing = await db.driver_daily_stats.find_one({"id": stat_row["id"]})
        if existing:
            await db.driver_daily_stats.update_one({"id": stat_row["id"]}, {"$set": stat_row})
            updated += 1
        else:
            stat_row["created_at"] = datetime.utcnow().isoformat()
            await db.driver_daily_stats.insert_one(stat_row)
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
async def get_audit_logs(limit: int = Query(50), offset: int = Query(0)):
    """Get audit log entries."""
    logs = await db.get_rows("audit_logs", order="created_at", desc=True, limit=limit)
    return logs


async def log_audit(action: str, entity_type: str, entity_id: str, user_email: str, details: str = ""):
    """Record an audit log entry. Call from admin endpoints."""
    await db.audit_logs.insert_one(
        {
            "id": str(uuid.uuid4()),
            "action": action,  # created, updated, deleted, login, status_change
            "entity_type": entity_type,  # driver, user, ride, promotion, service_area, staff, setting
            "entity_id": entity_id,
            "user_email": user_email,
            "details": details,
            "created_at": datetime.utcnow().isoformat(),
        }
    )
