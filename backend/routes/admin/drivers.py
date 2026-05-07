import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...features import send_push_notification
    from ...utils.audit_logger import log_admin_action
    from ...utils.datetime_utils import parse_iso_utc
except ImportError:
    import db_supabase
    from dependencies import get_admin_user  # noqa: F401
    from features import send_push_notification
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.datetime_utils import parse_iso_utc

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
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        logger.error(f"Failed to log driver activity: {e}", exc_info=True)


# ---------- Pydantic models ----------


class DriverVerifyRequest(BaseModel):
    verified: bool


class DriverActionRequest(BaseModel):
    action: Literal["approve", "reject", "suspend", "ban", "unban", "reactivate"]
    reason: Optional[str] = None


class DriverStatusOverride(BaseModel):
    status: Literal["pending", "active", "rejected", "suspended", "banned"]
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
    search: Optional[str] = None,
    is_verified: Optional[bool] = None,
    is_online: Optional[bool] = None,
    is_available: Optional[bool] = None,
    status: Optional[str] = None,
    service_area_id: Optional[str] = None,
):
    """Get drivers with filters, enriched with user name/email/phone.

    Defense-in-depth dedup: migration 31 adds UNIQUE(drivers.phone) and
    UNIQUE(drivers.user_id) so duplicates can't exist at the DB level.
    We still collapse by phone/user_id here so that if a legacy snapshot
    ever restores old state, the admin UI won't show duplicate rows.
    """
    import re

    filters = {}
    if is_verified is not None:
        filters["is_verified"] = is_verified
    if is_online is not None:
        filters["is_online"] = is_online
    if is_available is not None:
        filters["is_available"] = is_available
    if status:
        filters["status"] = status
    if service_area_id:
        filters["service_area_id"] = service_area_id

    # Find matching user IDs first if search is provided
    if search:
        term = search.strip()
        if term:
            user_filters = {
                "$or": [
                    {"phone": {"$regex": re.escape(term), "$options": "i"}},
                    {"email": {"$regex": re.escape(term), "$options": "i"}},
                    {"first_name": {"$regex": re.escape(term), "$options": "i"}},
                    {"last_name": {"$regex": re.escape(term), "$options": "i"}},
                ]
            }
            matching_users = await db_supabase.get_rows("users", user_filters, limit=100)
            matching_uids = [u["id"] for u in matching_users if u.get("id")]

            # Match driver rows by phone/plate directly OR by user_id from user search above.
            # `name` is not a column on drivers — it's derived from the joined users row.
            filters["$or"] = [
                {"phone": {"$regex": re.escape(term), "$options": "i"}},
                {"license_plate": {"$regex": re.escape(term), "$options": "i"}},
            ]
            if matching_uids:
                filters["$or"].append({"user_id": {"$in": matching_uids}})

    drivers = await db_supabase.get_rows("drivers", filters, order="created_at", desc=True, limit=limit, offset=offset)

    # Defensive dedup — keep the earliest-created row per (user_id, phone).
    seen_user_ids: set = set()
    seen_phones: set = set()
    deduped = []
    for d in sorted(drivers, key=lambda r: r.get("created_at") or ""):
        uid = d.get("user_id")
        phone = d.get("phone")
        if (uid and uid in seen_user_ids) or (phone and phone in seen_phones):
            continue
        if uid:
            seen_user_ids.add(uid)
        if phone:
            seen_phones.add(phone)
        deduped.append(d)
    # Restore the original newest-first order expected by the UI.
    deduped.sort(key=lambda r: r.get("created_at") or "", reverse=True)

    user_ids = list({d.get("user_id") for d in deduped if d.get("user_id")})
    users_list = (
        await db_supabase.get_rows("users", {"id": {"$in": user_ids}}, limit=max(len(user_ids), 1)) if user_ids else []
    )
    users_map = {u["id"]: u for u in users_list if u.get("id")}
    out = []
    for d in deduped:
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


class DriverSearchRequest(BaseModel):
    search: str
    limit: int = 5
    is_online: Optional[bool] = None
    is_available: Optional[bool] = None


@router.post("/drivers/search")
async def admin_search_drivers(
    body: DriverSearchRequest,
    admin_user: dict = Depends(get_admin_user),
):
    """Typeahead search for drivers via POST body to keep search terms out of server logs."""
    return await admin_get_drivers(
        limit=body.limit,
        search=body.search,
        is_online=body.is_online,
        is_available=body.is_available,
    )


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

    now = datetime.now(timezone.utc)
    # Default date range: last 30 days. parse_iso_utc always returns a
    # UTC-aware datetime (or None) so comparisons below match `now`.
    range_start = parse_iso_utc(start_date) if start_date else None
    if range_start is None:
        range_start = now - timedelta(days=30)
    range_start = range_start.replace(hour=0, minute=0, second=0, microsecond=0)

    range_end = parse_iso_utc(end_date) if end_date else None
    if range_end is None:
        range_end = now
    else:
        range_end = range_end.replace(hour=23, minute=59, second=59, microsecond=0)

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

    # Auto-detect needs_review: active drivers with expired docs or pending re-uploads.
    # Capped at 500 for the inline needs_review flag; full paginated list via
    # GET /documents/pending (A-P4-4).
    all_docs = await db_supabase.get_rows("driver_documents", {"status": "pending"}, limit=500)
    pending_doc_driver_ids = {d.get("driver_id") for d in all_docs if d.get("driver_id")}

    now_iso = datetime.now(timezone.utc).isoformat()
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
    total_earnings_sum = float(sum(Decimal(str(d.get("total_earnings") or 0)) for d in enriched_drivers))
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
        area_stats[aid]["total_earnings"] = float(
            Decimal(str(area_stats[aid]["total_earnings"])) + Decimal(str(d.get("total_earnings") or 0))
        )

    # ── Daily charts (within date range) ──
    num_days = (range_end - range_start).days + 1
    if num_days > 365:
        num_days = 365

    # Driver joins per day
    daily_joins: Dict[str, int] = defaultdict(int)
    for d in enriched_drivers:
        dt = parse_iso_utc(d.get("created_at"))
        if dt is None:
            continue
        if range_start <= dt <= range_end:
            day_key = dt.strftime("%Y-%m-%d")
            daily_joins[day_key] += 1

    # Rides + earnings per day (for drivers matching the service_area filter)
    driver_ids_set = {d["id"] for d in enriched_drivers}
    ride_filters: Dict[str, Any] = {"created_at": {"$gte": range_start.isoformat()}}
    all_rides = await db_supabase.get_rows("rides", ride_filters, order="created_at", desc=True, limit=5000)

    # Filter rides to only those belonging to our driver set
    relevant_rides = [r for r in all_rides if r.get("driver_id") in driver_ids_set] if service_area_id else all_rides

    daily_rides: Dict[str, int] = defaultdict(int)
    daily_earnings: Dict[str, float] = defaultdict(float)
    for r in relevant_rides:
        dt = parse_iso_utc(r.get("created_at"))
        if dt is None:
            continue
        if range_start <= dt <= range_end:
            day_key = dt.strftime("%Y-%m-%d")
            daily_rides[day_key] += 1
            if r.get("status") == "completed":
                daily_earnings[day_key] = float(
                    Decimal(str(daily_earnings[day_key])) + Decimal(str(r.get("driver_earnings") or 0))
                )

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
async def admin_update_driver(driver_id: str, updates: Dict[str, Any], admin: dict = Depends(get_admin_user)):
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
        # B-P3-leak-cleanup: full traceback to logs, generic detail
        # to client. Supabase / postgrest errors carry table internals.
        logger.exception(f"Failed to update driver {driver_id}")
        raise HTTPException(
            status_code=500,
            detail="Failed to update driver.",
        ) from e
    await log_admin_action(admin, "driver_updated", "drivers", driver_id, {"updated_fields": list(filtered.keys())})
    return {"message": "Driver updated", "updated_fields": list(filtered.keys())}


@router.post("/drivers/{driver_id}/verify")
async def admin_verify_driver(driver_id: str, req: DriverVerifyRequest, admin: dict = Depends(get_admin_user)):
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
        # B-P3-leak-cleanup: full traceback to logs, generic detail
        # to client.
        logger.exception(f"Failed to update driver {driver_id} verify flag")
        raise HTTPException(
            status_code=500,
            detail="Failed to update driver.",
        ) from e
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

    await log_admin_action(admin, "driver_verified", "drivers", driver_id, {"verified": req.verified})
    return {"message": f"Driver {'verified' if req.verified else 'unverified'}"}


@router.post("/drivers/{driver_id}/action")
async def admin_driver_action(driver_id: str, req: DriverActionRequest, admin: dict = Depends(get_admin_user)):
    """Perform a lifecycle action on a driver.

    Actions: approve, reject, suspend, ban, unban, reactivate.
    Each action transitions the driver to the appropriate state and
    records the reason + timestamp for audit trail.
    """
    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    current_status = driver.get("status", "pending")
    now = datetime.now(timezone.utc).isoformat()
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
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.") from e

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
    await log_admin_action(
        admin,
        f"driver_{req.action}",
        "drivers",
        driver_id,
        {"action": req.action, "reason": req.reason, "old_status": current_status, "new_status": updates.get("status")},
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
async def admin_override_driver_status(
    driver_id: str, req: DriverStatusOverride, admin: dict = Depends(get_admin_user)
):
    """Manually move a driver to any status. Use with caution."""
    valid = {"pending", "active", "needs_review", "suspended", "banned"}
    if req.status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid)}")

    driver = await db_supabase.get_driver_by_id(driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    now = datetime.now(timezone.utc).isoformat()
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
    await log_admin_action(
        admin,
        "driver_status_override",
        "drivers",
        driver_id,
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
        "created_at": datetime.now(timezone.utc).isoformat(),
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
async def admin_get_driver_rides(
    driver_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get rides for a driver with pagination (max 500 per page)."""
    rides = await db_supabase.get_rows("rides", {"driver_id": driver_id}, order="created_at", desc=True, limit=limit)
    # Apply offset in-process (Supabase helper doesn't expose OFFSET natively)
    page = rides[offset : offset + limit]
    return {"rides": page, "total": len(rides), "offset": offset, "limit": limit}


@router.get("/drivers/{driver_id}/daily-stats")
async def admin_get_driver_daily_stats(
    driver_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Get aggregated daily stats for a driver. Default: last 30 days."""
    if not end_date:
        end_date = datetime.now(timezone.utc).date().isoformat()
    if not start_date:
        start_date = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()

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
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"message": f"Driver assigned to area {service_area_id}"}


@router.get("/drivers/{driver_id}/location-trail")
async def admin_get_driver_location_trail(
    driver_id: str,
    hours: int = Query(24),
):
    """Get driver's location history (table: driver_location_history)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
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
