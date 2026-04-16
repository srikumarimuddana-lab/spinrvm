import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...features import send_push_notification
except ImportError:
    import db_supabase
    from features import send_push_notification

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Shared helpers (used by rides.py too via import) ----------


def _user_display_name(user: Optional[Dict]) -> str:
    if not user:
        return ""
    fn = user.get("first_name") or ""
    ln = user.get("last_name") or ""
    return f"{fn} {ln}".strip() or user.get("email") or user.get("phone") or ""


async def _batch_fetch_drivers_and_users(rider_ids: List[str], driver_ids: List[str]) -> tuple:
    """Batch-fetch drivers and users in 2-3 queries instead of N+1 loops."""
    drivers_list = (
        await db_supabase.get_rows("drivers", {"id": {"$in": driver_ids}}, limit=max(len(driver_ids), 1))
        if driver_ids
        else []
    )
    drivers_map = {d["id"]: d for d in drivers_list if d.get("id")}

    all_user_ids = list(
        {
            *rider_ids,
            *(d.get("user_id") for d in drivers_list if d.get("user_id")),
        }
    )
    users_list = (
        await db_supabase.get_rows("users", {"id": {"$in": all_user_ids}}, limit=max(len(all_user_ids), 1))
        if all_user_ids
        else []
    )
    users_map = {u["id"]: u for u in users_list if u.get("id")}

    return drivers_map, users_map


# ---------- Driver helper: activity log ----------


async def _log_driver_activity(
    driver_id: str,
    event_type: str,
    title: str,
    description: str = "",
    metadata: dict = None,
    actor: str = "admin",
):
    """Helper to record a driver lifecycle event."""
    try:
        await db_supabase.insert_one(
            "driver_activity_log",
            {
                "id": str(uuid.uuid4()),
                "driver_id": driver_id,
                "event_type": event_type,
                "title": title,
                "description": description,
                "metadata": metadata or {},
                "actor": actor,
                "created_at": datetime.utcnow().isoformat(),
            },
        )
    except Exception as e:
        logger.warning(f"Failed to log driver activity: {e}")


# ---------- Pydantic models ----------


class DriverVerifyRequest(BaseModel):
    verified: bool


class DriverActionRequest(BaseModel):
    action: str  # approve, reject, suspend, ban, unban, reactivate
    reason: Optional[str] = None


class DriverStatusOverride(BaseModel):
    status: str  # pending, active, rejected, suspended, banned
    is_verified: Optional[bool] = None
    reason: Optional[str] = None


class DriverNoteCreate(BaseModel):
    note: str
    category: str = "general"


# ---------- Drivers list ----------


@router.get("/drivers")
async def admin_get_drivers(
    limit: int = 50,
    offset: int = 0,
    is_verified: Optional[bool] = None,
    is_online: Optional[bool] = None,
):
    """Get all drivers with filters, enriched with user name/email/phone."""
    filters = {}
    if is_verified is not None:
        filters["is_verified"] = is_verified
    if is_online is not None:
        filters["is_online"] = is_online
    drivers = await db_supabase.get_rows("drivers", filters, order="created_at", desc=True, limit=limit, offset=offset)
    user_ids = list({d.get("user_id") for d in drivers if d.get("user_id")})
    users_list = (
        await db_supabase.get_rows("users", {"id": {"$in": user_ids}}, limit=max(len(user_ids), 1)) if user_ids else []
    )
    users_map = {u["id"]: u for u in users_list if u.get("id")}
    out = []
    for d in drivers:
        u = users_map.get(d.get("user_id"))
        out.append(
            {
                **d,
                "name": _user_display_name(u) or d.get("name"),
                "email": u.get("email") if u else None,
                "phone": u.get("phone") if u else d.get("phone"),
            }
        )
    return out


@router.get("/drivers/stats")
async def admin_get_driver_stats(
    service_area_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Get driver statistics, optionally filtered by service area and date range.

    Returns overall + per-service-area stats, plus daily chart data for
    driver joins, rides, and earnings.
    """
    from collections import defaultdict

    now = datetime.utcnow()
    # Default date range: last 30 days
    if start_date:
        range_start = datetime.fromisoformat(start_date.replace("Z", "+00:00").replace("+00:00", ""))
    else:
        range_start = now - timedelta(days=30)
    range_start = range_start.replace(hour=0, minute=0, second=0, microsecond=0)

    if end_date:
        range_end = datetime.fromisoformat(end_date.replace("Z", "+00:00").replace("+00:00", ""))
        range_end = range_end.replace(hour=23, minute=59, second=59, microsecond=0)
    else:
        range_end = now

    # Fetch all service areas for lookups
    service_areas = await db_supabase.get_rows("service_areas", order="name", limit=200)
    area_map = {a["id"]: a.get("name", "Unknown") for a in service_areas}

    # ── Fetch drivers ──
    driver_filters: Dict[str, Any] = {}
    if service_area_id:
        driver_filters["service_area_id"] = service_area_id
    all_drivers = await db_supabase.get_rows("drivers", driver_filters, order="created_at", desc=True, limit=5000)

    # Enrich with user info (batch)
    user_ids = list({d.get("user_id") for d in all_drivers if d.get("user_id")})
    users_list = (
        await db_supabase.get_rows("users", {"id": {"$in": user_ids}}, limit=max(len(user_ids), 1)) if user_ids else []
    )
    users_map: Dict[str, Any] = {u["id"]: u for u in users_list if u.get("id")}

    # Auto-detect needs_review: active drivers with expired docs or pending re-uploads
    all_docs = await db_supabase.get_rows("driver_documents", {"status": "pending"}, limit=10000)
    pending_doc_driver_ids = {d.get("driver_id") for d in all_docs if d.get("driver_id")}

    now_iso = datetime.utcnow().isoformat()
    expiry_fields = [
        "license_expiry_date",
        "insurance_expiry_date",
        "vehicle_inspection_expiry_date",
        "background_check_expiry_date",
    ]

    enriched_drivers = []
    for d in all_drivers:
        u = users_map.get(d.get("user_id"))
        driver_status = d.get("status", "pending")

        # Auto-detect needs_review for active drivers
        if driver_status == "active":
            for ef in expiry_fields:
                exp = d.get(ef)
                if exp and str(exp) < now_iso:
                    driver_status = "needs_review"
                    break
            if driver_status == "active" and d.get("id") in pending_doc_driver_ids:
                driver_status = "needs_review"

        enriched_drivers.append(
            {
                **d,
                "status": driver_status,
                "first_name": u.get("first_name") if u else d.get("first_name"),
                "last_name": u.get("last_name") if u else d.get("last_name"),
                "name": _user_display_name(u) or d.get("name"),
                "email": u.get("email") if u else None,
                "phone": u.get("phone") if u else d.get("phone"),
            }
        )

    # ── Compute overall driver stats ──
    total = len(enriched_drivers)
    online = sum(1 for d in enriched_drivers if d.get("is_online"))
    active_count = sum(1 for d in enriched_drivers if d.get("status") == "active")
    pending_count = sum(1 for d in enriched_drivers if d.get("status") == "pending")
    needs_review_count = sum(1 for d in enriched_drivers if d.get("status") == "needs_review")
    suspended_count = sum(1 for d in enriched_drivers if d.get("status") == "suspended")
    banned_count = sum(1 for d in enriched_drivers if d.get("status") == "banned")
    total_rides_sum = sum(int(d.get("total_rides") or 0) for d in enriched_drivers)
    total_earnings_sum = sum(float(d.get("total_earnings") or 0) for d in enriched_drivers)
    avg_rating = 0.0
    rated = [d for d in enriched_drivers if d.get("rating") and float(d.get("rating", 0)) > 0]
    if rated:
        avg_rating = round(sum(float(d["rating"]) for d in rated) / len(rated), 2)

    # ── Per-service-area breakdown ──
    area_stats: Dict[str, Dict[str, Any]] = {}
    for d in enriched_drivers:
        aid = d.get("service_area_id") or "unassigned"
        if aid not in area_stats:
            area_stats[aid] = {
                "service_area_id": aid,
                "service_area_name": area_map.get(aid, "Unassigned"),
                "total": 0,
                "online": 0,
                "verified": 0,
                "unverified": 0,
                "total_rides": 0,
                "total_earnings": 0.0,
            }
        area_stats[aid]["total"] += 1
        if d.get("is_online"):
            area_stats[aid]["online"] += 1
        if d.get("is_verified"):
            area_stats[aid]["verified"] += 1
        else:
            area_stats[aid]["unverified"] += 1
        area_stats[aid]["total_rides"] += int(d.get("total_rides") or 0)
        area_stats[aid]["total_earnings"] += float(d.get("total_earnings") or 0)

    # ── Daily charts (within date range) ──
    num_days = (range_end - range_start).days + 1
    if num_days > 365:
        num_days = 365

    # Driver joins per day
    daily_joins: Dict[str, int] = defaultdict(int)
    for d in enriched_drivers:
        ca = d.get("created_at")
        if not ca:
            continue
        try:
            dt = datetime.fromisoformat(str(ca).replace("Z", "+00:00").replace("+00:00", ""))
        except Exception:  # noqa: S112
            continue
        if range_start <= dt <= range_end:
            day_key = dt.strftime("%Y-%m-%d")
            daily_joins[day_key] += 1

    # Rides + earnings per day (for drivers matching the service_area filter)
    driver_ids_set = {d["id"] for d in enriched_drivers}
    ride_filters: Dict[str, Any] = {"created_at": {"$gte": range_start.isoformat()}}
    all_rides = await db_supabase.get_rows("rides", ride_filters, order="created_at", desc=True, limit=50000)

    # Filter rides to only those belonging to our driver set
    relevant_rides = [r for r in all_rides if r.get("driver_id") in driver_ids_set] if service_area_id else all_rides

    daily_rides: Dict[str, int] = defaultdict(int)
    daily_earnings: Dict[str, float] = defaultdict(float)
    for r in relevant_rides:
        ca = r.get("created_at")
        if not ca:
            continue
        try:
            dt = datetime.fromisoformat(str(ca).replace("Z", "+00:00").replace("+00:00", ""))
        except Exception:  # noqa: S112
            continue
        if range_start <= dt <= range_end:
            day_key = dt.strftime("%Y-%m-%d")
            daily_rides[day_key] += 1
            if r.get("status") == "completed":
                daily_earnings[day_key] += float(r.get("driver_earnings") or 0)

    # Build chart arrays
    joins_chart = []
    rides_chart = []
    earnings_chart = []
    for i in range(num_days):
        day = range_start + timedelta(days=i)
        day_key = day.strftime("%Y-%m-%d")
        day_label = day.strftime("%b %d")
        joins_chart.append({"date": day_label, "date_raw": day_key, "count": daily_joins.get(day_key, 0)})
        rides_chart.append({"date": day_label, "date_raw": day_key, "count": daily_rides.get(day_key, 0)})
        earnings_chart.append(
            {"date": day_label, "date_raw": day_key, "amount": round(daily_earnings.get(day_key, 0), 2)}
        )

    return {
        "stats": {
            "total": total,
            "online": online,
            "active": active_count,
            "pending": pending_count,
            "needs_review": needs_review_count,
            "suspended": suspended_count,
            "banned": banned_count,
            "total_rides": total_rides_sum,
            "total_earnings": total_earnings_sum,
            "avg_rating": avg_rating,
        },
        "area_stats": list(area_stats.values()),
        "charts": {
            "daily_joins": joins_chart,
            "daily_rides": rides_chart,
            "daily_earnings": earnings_chart,
        },
        "drivers": enriched_drivers,
        "service_areas": [{"id": a["id"], "name": a.get("name", "Unknown")} for a in service_areas],
    }


@router.put("/drivers/{driver_id}")
async def admin_update_driver(driver_id: str, updates: Dict[str, Any]):
    """Update driver details from admin dashboard."""
    allowed = {
        "first_name",
        "last_name",
        "email",
        "phone",
        "gender",
        "city",
        "service_area_id",
        "vehicle_type_id",
        "vehicle_make",
        "vehicle_model",
        "vehicle_color",
        "vehicle_year",
        "license_plate",
        "vehicle_vin",
        "license_number",
        "license_expiry_date",
        "insurance_expiry_date",
        "vehicle_inspection_expiry_date",
        "background_check_expiry_date",
        "work_eligibility_expiry_date",
    }
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    existing = await db_supabase.get_driver_by_id(driver_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Driver {driver_id} not found")

    try:
        await db_supabase.update_one("drivers", {"id": driver_id}, filtered)
    except Exception as e:
        logger.error(f"Failed to update driver {driver_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update driver: {e}") from e
    return {"message": "Driver updated", "updated_fields": list(filtered.keys())}


@router.post("/drivers/{driver_id}/verify")
async def admin_verify_driver(driver_id: str, req: DriverVerifyRequest):
    """Verify or unverify a driver.

    NOTE: the Supabase `drivers` table in production was created from
    supabase_schema.sql, which has no `updated_at` (and no `verified_at`)
    column on `drivers`. Writing either triggers PGRST204 -> 500 (which
    previously escaped CORSMiddleware and surfaced in the browser as a CORS
    error). Only set columns that actually exist on the table.
    """
    try:
        # First check if driver exists
        existing_driver = await db_supabase.get_driver_by_id(driver_id)
        if not existing_driver:
            raise HTTPException(status_code=404, detail=f"Driver {driver_id} not found")

        update_fields: Dict[str, Any] = {"is_verified": req.verified}
        # Clear needs_review when admin verifies (re-approves)
        if req.verified:
            update_fields["needs_review"] = False
        await db_supabase.update_one("drivers", {"id": driver_id}, update_fields)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update driver {driver_id} verify flag: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update driver: {e}") from e
    # G4: Notify the driver via push so they know their verification status
    # changed without having to manually check the Documents screen.
    try:
        if existing_driver.get("user_id"):
            if req.verified:
                await send_push_notification(
                    existing_driver["user_id"],
                    "Account Verified! ✅",
                    "Your driver account has been verified. You can now go online and start accepting rides!",
                    {"type": "driver_verified"},
                )
            else:
                await send_push_notification(
                    existing_driver["user_id"],
                    "Verification Update ⚠️",
                    "Your driver verification status has been updated. Please check your documents.",
                    {"type": "driver_unverified"},
                )
    except Exception as e:
        logger.warning(f"[ADMIN] Push notification failed for driver {driver_id}: {e}")

    return {"message": f"Driver {'verified' if req.verified else 'unverified'}"}


@router.post("/drivers/{driver_id}/action")
async def admin_driver_action(driver_id: str, req: DriverActionRequest):
    """Perform a lifecycle action on a driver.

    Actions: approve, reject, suspend, ban, unban, reactivate.
    Each action transitions the driver to the appropriate state and
    records the reason + timestamp for audit trail.
    """
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    current_status = driver.get("status", "pending")
    now = datetime.utcnow().isoformat()
    updates: Dict[str, Any] = {"updated_at": now}

    if req.action == "approve":
        # Approve → Active: driver can go online
        updates["status"] = "active"
        updates["is_verified"] = True
        updates["rejection_reason"] = None
        updates["verified_at"] = now

    elif req.action == "suspend":
        # Suspend: temporarily disable, store reason
        if not req.reason:
            raise HTTPException(status_code=400, detail="Reason is required when suspending")
        updates["status"] = "suspended"
        updates["suspension_reason"] = req.reason
        updates["suspended_at"] = now
        updates["is_online"] = False
        updates["is_available"] = False

    elif req.action == "ban":
        # Ban: permanently block, store reason
        if not req.reason:
            raise HTTPException(status_code=400, detail="Reason is required when banning")
        updates["status"] = "banned"
        updates["is_verified"] = False
        updates["ban_reason"] = req.reason
        updates["banned_at"] = now
        updates["is_online"] = False
        updates["is_available"] = False

    elif req.action == "unban":
        # Unban → Active
        updates["status"] = "active"
        updates["is_verified"] = True
        updates["ban_reason"] = None
        updates["banned_at"] = None
        updates["unban_reason"] = req.reason
        updates["unbanned_at"] = now

    elif req.action == "reactivate":
        # Reactivate from suspended → Active
        updates["status"] = "active"
        updates["is_verified"] = True
        updates["suspension_reason"] = None
        updates["suspended_at"] = None

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

    try:
        await db_supabase.update_one("drivers", {"id": driver_id}, updates)
    except Exception as e:
        logger.error(f"Failed driver action {req.action} on {driver_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

    logger.info(f"[ADMIN] Driver {driver_id} action={req.action} reason={req.reason}")

    # Auto-log to activity timeline
    action_titles = {
        "approve": "Driver Approved",
        "reject": "Application Rejected",
        "suspend": "Driver Suspended",
        "ban": "Driver Banned",
        "unban": "Driver Unbanned",
        "reactivate": "Driver Reactivated",
    }
    await _log_driver_activity(
        driver_id,
        req.action,
        action_titles.get(req.action, f"Action: {req.action}"),
        req.reason or "",
        {"old_status": current_status, "new_status": updates.get("status"), "reason": req.reason},
    )

    # G4: Notify the driver about their status change. Critical for
    # approve/reject/suspend — without this, drivers wait days not knowing
    # their application was processed.
    action_push_map = {
        "approve": (
            "You're Approved! 🎉",
            "Your driver application has been approved. You can now go online and start earning!",
        ),
        "reject": ("Application Update", "Your driver application needs attention. Please check your documents."),
        "suspend": (
            "Account Suspended ⚠️",
            f"Your account has been suspended. Reason: {req.reason or 'Contact support for details.'}",
        ),
        "ban": (
            "Account Deactivated",
            "Your driver account has been deactivated. Contact support for more information.",
        ),
        "unban": ("Account Restored! ✅", "Your driver account has been restored. You can now go online again."),
        "reactivate": (
            "Account Reactivated! ✅",
            "Your account has been reactivated. You can now go online and accept rides!",
        ),
    }
    push_info = action_push_map.get(req.action)
    if push_info and driver.get("user_id"):
        try:
            await send_push_notification(
                driver["user_id"],
                push_info[0],
                push_info[1],
                {"type": f"driver_{req.action}", "new_status": updates.get("status", "")},
            )
        except Exception as e:
            logger.warning(f"[ADMIN] Push notification failed for driver action {req.action}: {e}")

    return {
        "message": f"Driver {req.action}d successfully",
        "new_status": updates.get("status", current_status),
    }


@router.put("/drivers/{driver_id}/status-override")
async def admin_override_driver_status(driver_id: str, req: DriverStatusOverride):
    """Manually move a driver to any status. Use with caution."""
    valid = {"pending", "active", "needs_review", "suspended", "banned"}
    if req.status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid)}")

    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    now = datetime.utcnow().isoformat()
    updates: Dict[str, Any] = {"status": req.status, "updated_at": now}

    # Sync is_verified with status
    updates["is_verified"] = req.status == "active"

    # Take offline if not active
    if req.status != "active":
        updates["is_online"] = False
        updates["is_available"] = False

    if req.reason:
        if req.status == "suspended":
            updates["suspension_reason"] = req.reason
        elif req.status == "banned":
            updates["ban_reason"] = req.reason

    await db_supabase.update_one("drivers", {"id": driver_id}, updates)
    logger.info(f"[ADMIN] Driver {driver_id} status overridden to {req.status} reason={req.reason}")
    await _log_driver_activity(
        driver_id,
        "status_override",
        f"Status changed to {req.status}",
        req.reason or "Manual admin override",
        {"old_status": driver.get("status"), "new_status": req.status, "reason": req.reason},
    )
    return {"message": f"Driver status set to {req.status}"}


# ── Driver Notes ──


@router.get("/drivers/{driver_id}/notes")
async def admin_get_driver_notes(driver_id: str):
    """Get all notes for a driver, newest first."""
    notes = await db_supabase.get_rows(
        "driver_notes", {"driver_id": driver_id}, order="created_at", desc=True, limit=200
    )
    return notes or []


@router.post("/drivers/{driver_id}/notes")
async def admin_add_driver_note(driver_id: str, req: DriverNoteCreate):
    """Add a note to a driver's record."""
    if not req.note.strip():
        raise HTTPException(status_code=400, detail="Note cannot be empty")
    doc = {
        "id": str(uuid.uuid4()),
        "driver_id": driver_id,
        "note": req.note.strip(),
        "category": req.category,
        "created_at": datetime.utcnow().isoformat(),
    }
    await db_supabase.insert_one("driver_notes", doc)
    await _log_driver_activity(
        driver_id,
        "note_added",
        f"Note added ({req.category})",
        req.note[:100],
        {"category": req.category},
    )
    return doc


@router.delete("/drivers/notes/{note_id}")
async def admin_delete_driver_note(note_id: str):
    """Delete a note."""
    await db_supabase.delete_many("driver_notes", {"id": note_id})
    return {"message": "Note deleted"}


# ── Driver Activity Log ──


@router.get("/drivers/{driver_id}/activity")
async def admin_get_driver_activity(driver_id: str, limit: int = 100):
    """Get full activity timeline for a driver, newest first."""
    activities = await db_supabase.get_rows(
        "driver_activity_log",
        {"driver_id": driver_id},
        order="created_at",
        desc=True,
        limit=limit,
    )
    return activities or []


@router.get("/drivers/{driver_id}/rides")
async def admin_get_driver_rides(driver_id: str):
    """Get all rides for a specific driver."""
    rides = await db_supabase.get_rows("rides", {"driver_id": driver_id}, order="created_at", desc=True, limit=500)
    return rides


@router.get("/drivers/{driver_id}/daily-stats")
async def admin_get_driver_daily_stats(
    driver_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Get aggregated daily stats for a driver. Default: last 30 days."""
    if not end_date:
        end_date = datetime.utcnow().date().isoformat()
    if not start_date:
        start_date = (datetime.utcnow().date() - timedelta(days=30)).isoformat()

    stats = await db_supabase.get_rows(
        "driver_daily_stats",
        {
            "driver_id": driver_id,
            "stat_date": {"$gte": start_date, "$lte": end_date},
        },
        order="stat_date",
        desc=True,
        limit=400,
    )
    return stats or []


# ---------- Driver Area Assignment ----------


@router.put("/drivers/{driver_id}/area")
async def admin_assign_driver_area(driver_id: str, service_area_id: str):
    """Assign a driver to a specific service area."""
    await db_supabase.update_one(
        "drivers",
        {"id": driver_id},
        {
            "service_area_id": service_area_id,
            "updated_at": datetime.utcnow().isoformat(),
        },
    )
    return {"message": f"Driver assigned to area {service_area_id}"}


@router.get("/drivers/{driver_id}/location-trail")
async def admin_get_driver_location_trail(
    driver_id: str,
    hours: int = Query(24),
):
    """Get driver's location history (table: driver_location_history)."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    locations = await db_supabase.get_rows(
        "driver_location_history",
        {"driver_id": driver_id, "timestamp": {"$gte": cutoff}},
        order="timestamp",
        limit=5000,
    )
    return [
        {
            "lat": loc.get("lat"),
            "lng": loc.get("lng"),
            "timestamp": loc.get("timestamp"),
        }
        for loc in locations
    ]
