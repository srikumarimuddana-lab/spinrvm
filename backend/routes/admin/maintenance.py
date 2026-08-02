import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

try:
    from ...dependencies import get_admin_user, require_module
except ImportError:
    from dependencies import get_admin_user, require_module  # type: ignore

try:
    from ... import db_supabase
    from ...db_supabase import run_sync as _run_sync
    from ...supabase_client import supabase as _supabase_client
except ImportError:
    import db_supabase

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)


async def log_audit(
    action: str,
    entity_type: str,
    entity_id: str,
    actor: str,
    details: str = "",
) -> None:
    """Write a single row to the audit_logs table.

    Non-raising: callers wrap this in try/except so a DB hiccup never
    blocks the parent operation. Failures are logged at WARNING level.
    """
    try:
        await db_supabase.insert_one(
            "audit_logs",
            {
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "actor_id": actor,
                "details": details,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        logger.error(
            f"[AUDIT] log_audit({action}, {entity_type}, {entity_id}) failed: {exc}",
            exc_info=True,
            extra={"domain": "admin"},
        )


router = APIRouter()

# ── GPS Location History Cleanup ──


@router.post("/maintenance/cleanup-location-history")
async def admin_cleanup_location_history(days: int = Query(30, ge=7, le=1095)):
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

    deleted_historical = -1
    deleted_idle = -1
    try:
        await db_supabase.delete_many("driver_location_history", {"timestamp": {"$lt": cutoff_historical}})
    except Exception as e:
        logger.error(f"Cleanup historical GPS failed: {e}", exc_info=True)

    try:
        await db_supabase.delete_many(
            "driver_location_history",
            {"timestamp": {"$lt": cutoff_idle}, "tracking_phase": "online_idle"},
        )
    except Exception as e:
        logger.error(f"Cleanup idle GPS failed: {e}", exc_info=True)

    logger.info("[CLEANUP] Deleted historical + idle GPS points (counts not tracked — direct delete)")
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
    from collections import defaultdict

    # Default to yesterday (UTC)
    if target_date:
        stat_date = datetime.fromisoformat(target_date).date()
        # Completed days only: a partial-day rollup would make MAX(stat_date)
        # claim today is covered, so the leaderboard's freshness top-up
        # (routes/drivers.py::get_driver_leaderboard, keyed off day-after
        # MAX(stat_date)) would silently drop every ride completed after the
        # rollup ran until the next nightly pass.
        if stat_date >= datetime.now(timezone.utc).date():
            raise HTTPException(
                status_code=422,
                detail="target_date must be a completed UTC day (yesterday or earlier)",
            )
    else:
        stat_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    day_start = datetime.combine(stat_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    day_start_iso = day_start.isoformat()
    day_end_iso = day_end.isoformat()

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
        {
            "action": "ride_declined",
            "created_at": {"$gte": day_start_iso, "$lt": day_end_iso},
        },
        limit=10000,  # capped from 100k — decline events are sparse; 10k covers any realistic day
    )
    declines_by_driver: Dict[str, int] = defaultdict(int)
    for entry in decline_logs or []:
        # actor_id stores driver_id for decline entries (see decline_ride endpoint)
        did = entry.get("actor_id") or entry.get("user_email")
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
        columns="driver_id",
        limit=10000,  # enough to identify all active drivers; per-driver aggregation is done in SQL
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
        rides = rides_by_driver.get(driver_id, [])

        # Call the server-side SQL function instead of fetching up to 100 k GPS
        # rows into Python and running haversine in a loop. Returns 7 scalars.
        def _rpc(did=driver_id):
            return _supabase_client.rpc(
                "compute_driver_phase_distances",
                {
                    "p_driver_id": did,
                    "p_day_start": day_start_iso,
                    "p_day_end": day_end_iso,
                },
            ).execute()

        gps_stats: Dict[str, Any] = {}
        try:
            resp = await _run_sync(_rpc)
            rows = getattr(resp, "data", None) or []
            gps_stats = rows[0] if rows else {}
        except Exception as e:
            logger.error(
                f"[ROLLUP] compute_driver_phase_distances failed driver={driver_id}: {e}",
                exc_info=True,
            )

        idle_km = float(gps_stats.get("idle_km") or 0)
        navigating_km = float(gps_stats.get("navigating_km") or 0)
        trip_km = float(gps_stats.get("trip_km") or 0)
        online_minutes = int(gps_stats.get("online_minutes") or 0)
        first_online_at = gps_stats.get("first_online_at")
        last_online_at = gps_stats.get("last_online_at")

        # Ride counts and earnings
        rides_completed = sum(1 for r in rides if r.get("status") == "completed")
        rides_cancelled = sum(1 for r in rides if r.get("status") == "cancelled")
        total_earnings = float(
            sum(Decimal(str(r.get("driver_earnings") or 0)) for r in rides if r.get("status") == "completed")
        )
        total_tips = float(sum(Decimal(str(r.get("tip_amount") or 0)) for r in rides if r.get("status") == "completed"))

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
    _admin: dict = Depends(require_module("audit")),
):
    """Get audit log entries with optional filters and pagination."""
    filters: Dict[str, Any] = {}
    if action:
        filters["action"] = action
    if entity_type:
        filters["entity_type"] = entity_type
    if search:
        term = search.strip()
        if term:
            # entity_id is the column every current writer (log_admin_action,
            # log_user_action, the PII-reveal endpoint below) actually
            # populates — resource_id is the pre-migration-57 legacy column
            # that nothing writes anymore. Searching resource_id silently
            # matched zero modern rows instead of erroring, so this was a
            # quiet SOC search gap, not a crash.
            filters["$or"] = [
                {"actor_id": {"$regex": term, "$options": "i"}},
                {"entity_id": {"$regex": term, "$options": "i"}},
                {"details": {"$regex": term, "$options": "i"}},
            ]
    logs = await db_supabase.get_rows("audit_logs", filters, order="created_at", desc=True, limit=limit, offset=offset)
    return logs


class PiiRevealRequest(BaseModel):
    entity_type: str
    entity_id: str


@router.post("/audit/pii-reveal")
async def admin_log_pii_reveal(
    body: PiiRevealRequest,
    admin: dict = Depends(get_admin_user),
):
    """Log when an admin reveals PII for a specific entity (PIPEDA audit trail)."""
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "pii_revealed",
            "entity_type": body.entity_type,
            "entity_id": body.entity_id,
            "details": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"ok": True}
