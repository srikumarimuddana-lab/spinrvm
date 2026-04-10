from fastapi import APIRouter, Depends, Query, HTTPException, Header  # type: ignore
from typing import Dict, Any, Optional, List
from pydantic import BaseModel  # type: ignore
from datetime import datetime, timedelta
import jwt
import uuid
import logging

try:
    from ..dependencies import get_current_user, get_admin_user  # type: ignore
    from ..db import db  # type: ignore
    from ..settings_loader import get_app_settings  # type: ignore
    from ..core.config import settings
    from .. import db_supabase  # type: ignore
except ImportError:
    from dependencies import get_current_user, get_admin_user  # type: ignore
    from db import db  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from core.config import settings
    import db_supabase  # type: ignore

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["Admin"])

# Admin authentication sub-router
admin_auth_router = APIRouter(prefix="/admin/auth", tags=["Admin Auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class SessionResponse(BaseModel):
    user: Optional[Dict[str, Any]] = None
    authenticated: bool = False


@admin_auth_router.get("/session", response_model=SessionResponse)
async def get_session(authorization: Optional[str] = Header(None)):
    """Get current admin session - returns user if authenticated"""
    if not authorization:
        return SessionResponse(user=None, authenticated=False)

    # Extract token from "Bearer <token>" format
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            return SessionResponse(user=None, authenticated=False)
    except ValueError:
        return SessionResponse(user=None, authenticated=False)

    # Verify the JWT token
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.ALGORITHM]
        )
        user_id = payload.get("user_id")
        role = payload.get("role")
        email = payload.get("email")
        phone = payload.get("phone")

        if not user_id:
            return SessionResponse(user=None, authenticated=False)

        # Return authenticated user info
        modules = payload.get("modules", [])
        return SessionResponse(
            user={
                "id": user_id,
                "email": email,
                "phone": phone,
                "role": role or "admin",
                "modules": modules,
            },
            authenticated=True,
        )
    except jwt.ExpiredSignatureError:
        return SessionResponse(user=None, authenticated=False)
    except jwt.InvalidTokenError:
        return SessionResponse(user=None, authenticated=False)


@admin_auth_router.post("/login")
async def admin_login(request: LoginRequest):
    """Admin login — supports super admin + staff members with module access."""
    import hashlib

    ALL_MODULES = [
        "dashboard",
        "users",
        "drivers",
        "rides",
        "earnings",
        "promotions",
        "surge",
        "service_areas",
        "vehicle_types",
        "pricing",
        "support",
        "disputes",
        "notifications",
        "settings",
        "corporate_accounts",
        "documents",
        "heatmap",
        "staff",
    ]

    # 1. Super admin from env
    if (
        request.email == settings.ADMIN_EMAIL
        and request.password == settings.ADMIN_PASSWORD
    ):
        token = jwt.encode(
            {
                "user_id": "admin-001",
                "email": request.email,
                "role": "super_admin",
                "modules": ALL_MODULES,
                "phone": request.email,
            },
            settings.JWT_SECRET,
            algorithm=settings.ALGORITHM,
        )
        return {
            "user": {
                "id": "admin-001",
                "email": request.email,
                "role": "super_admin",
                "first_name": "Super",
                "last_name": "Admin",
                "modules": ALL_MODULES,
            },
            "token": token,
        }

    # 2. Staff member
    staff = await db.admin_staff.find_one({"email": request.email.lower()})
    if staff:
        pw_hash = hashlib.sha256(request.password.encode()).hexdigest()
        if staff.get("password_hash") == pw_hash:
            if not staff.get("is_active", True):
                raise HTTPException(status_code=403, detail="Account is deactivated")
            await db.admin_staff.update_one(
                {"id": staff["id"]},
                {"$set": {"last_login": datetime.utcnow().isoformat()}},
            )
            modules = staff.get("modules", ["dashboard"])
            token = jwt.encode(
                {
                    "user_id": staff["id"],
                    "email": staff["email"],
                    "role": staff.get("role", "custom"),
                    "modules": modules,
                    "phone": staff["email"],
                },
                settings.JWT_SECRET,
                algorithm=settings.ALGORITHM,
            )
            return {
                "user": {
                    "id": staff["id"],
                    "email": staff["email"],
                    "role": staff.get("role", "custom"),
                    "first_name": staff.get("first_name", ""),
                    "last_name": staff.get("last_name", ""),
                    "modules": modules,
                },
                "token": token,
            }

    raise HTTPException(status_code=401, detail="Invalid credentials")


@admin_auth_router.post("/logout")
async def admin_logout():
    """Admin logout endpoint"""
    return {"message": "Logged out successfully"}


class DriverVerifyRequest(BaseModel):
    verified: bool


# ---------- Settings (single row id='app_settings', flat keys) ----------


@admin_router.get("/settings")
async def admin_get_settings():
    """Get all settings (normalized single app_settings row as dict)."""
    return await get_app_settings()


@admin_router.put("/settings")
async def admin_update_settings(settings: Dict[str, Any]):
    """Update settings (upsert single app_settings row)."""
    # First check if settings row exists
    existing = await db.settings.find_one({"id": "app_settings"})

    payload = {
        "id": "app_settings",
        **settings,
        "updated_at": datetime.utcnow().isoformat(),
    }

    if existing:
        # Update existing row - build update dict without 'id'
        update_payload = {k: v for k, v in payload.items() if k != "id"}
        await db.settings.update_one({"id": "app_settings"}, {"$set": update_payload})
    else:
        # Insert new row
        await db.settings.insert_one(payload)

    return {"message": "Settings updated"}


# ---------- Service areas (table: service_areas) ----------


@admin_router.get("/service-areas")
async def admin_get_service_areas():
    """Get all service areas. Sub-regions are nested under their parent as 'sub_regions'."""
    areas = await db.get_rows("service_areas", order="name", limit=500)
    # Build parent -> children mapping
    parent_map: Dict[str, list] = {}
    parents = []
    for a in areas:
        pid = a.get("parent_service_area_id")
        if pid:
            parent_map.setdefault(pid, []).append(a)
        else:
            parents.append(a)
    # Attach sub_regions to each parent
    for p in parents:
        p["sub_regions"] = parent_map.get(p["id"], [])
    return parents


@admin_router.post("/service-areas")
async def admin_create_service_area(area: Dict[str, Any]):
    """Create service area with full configuration."""
    doc = {
        "id": str(uuid.uuid4()),
        "name": area.get("name"),
        "city": area.get("city", ""),
        "polygon": area.get("geojson", area.get("polygon", [])),
        "is_active": area.get("is_active", True),
        # Sub-region support (e.g. airport zone inside a parent area)
        "parent_service_area_id": area.get("parent_service_area_id"),
        "is_airport": area.get("is_airport", False),
        "airport_fee": area.get("airport_fee", 0),
        "surge_active": area.get("surge_enabled", area.get("surge_active", False)),
        "surge_multiplier": area.get("surge_multiplier", 1.0),
        "gst_enabled": area.get("gst_enabled", True),
        "gst_rate": area.get("gst_rate", 5.0),
        "pst_enabled": area.get("pst_enabled", False),
        "pst_rate": area.get("pst_rate", 0.0),
        "hst_enabled": area.get("hst_enabled", False),
        "hst_rate": area.get("hst_rate", 0.0),
        # Spinr Pass kill switch
        "spinr_pass_enabled": area.get("spinr_pass_enabled", True),
        "subscription_plan_ids": area.get("subscription_plan_ids", []),
        # Driver matching settings (per-area)
        "driver_matching_algorithm": area.get("driver_matching_algorithm", "nearest"),
        "search_radius_km": area.get("search_radius_km", 10.0),
        "min_driver_rating": area.get("min_driver_rating", 4.0),
        # Demand heatmap — when true, drivers in this area see ride demand overlay
        "show_demand_heatmap": area.get("show_demand_heatmap", False),
        "created_at": datetime.utcnow().isoformat(),
    }
    row = await db.service_areas.insert_one(doc)
    return {"area_id": doc["id"]}


@admin_router.put("/service-areas/{area_id}")
async def admin_update_service_area(area_id: str, area: Dict[str, Any]):
    """Update service area — accepts any field."""
    allowed = [
        "name",
        "city",
        "polygon", # previously geojson mapped to polygon below
        "is_active",
        "parent_service_area_id",
        "is_airport",
        "airport_fee",
        "surge_active",
        "surge_multiplier",
        "gst_enabled",
        "gst_rate",
        "pst_enabled",
        "pst_rate",
        "hst_enabled",
        "hst_rate",
        "required_documents",
        "spinr_pass_enabled",
        "subscription_plan_ids",
        "driver_matching_algorithm",
        "search_radius_km",
        "min_driver_rating",
        "show_demand_heatmap",
    ]
    
    # Map geojson from frontend to polygon in DB schema if present
    if "geojson" in area:
        area["polygon"] = area["geojson"]
        
    # Map surge_enabled to surge_active
    if "surge_enabled" in area:
        area["surge_active"] = area["surge_enabled"]
        
    update_payload = {k: v for k, v in area.items() if k in allowed and v is not None}

    if update_payload:
        # NOTE: service_areas table does not have an updated_at column in Supabase schema.
        # Adding it causes PGRST204 -> 500 error.
        await db.service_areas.update_one({"id": area_id}, {"$set": update_payload})
    return {"message": "Service area updated"}


@admin_router.delete("/service-areas/{area_id}")
async def admin_delete_service_area(area_id: str):
    """Delete service area."""
    await db.service_areas.delete_many({"id": area_id})
    return {"message": "Service area deleted"}


# ---------- Vehicle types (table: vehicle_types) ----------


@admin_router.get("/vehicle-types")
async def admin_get_vehicle_types():
    """Get all vehicle types."""
    types = await db.get_rows("vehicle_types", order="created_at", limit=100)
    return types


@admin_router.post("/vehicle-types")
async def admin_create_vehicle_type(vtype: Dict[str, Any]):
    """Create vehicle type."""
    doc = {
        "name": vtype.get("name"),
        "description": vtype.get("description", ""),
        "icon": vtype.get("icon", ""),
        "base_fare": vtype.get("base_fare"),
        "price_per_km": vtype.get("price_per_km"),
        "price_per_minute": vtype.get("price_per_minute"),
        "is_active": vtype.get("is_active", True),
        "created_at": datetime.utcnow().isoformat(),
    }
    row = await db.vehicle_types.insert_one(doc)
    return {"type_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@admin_router.put("/vehicle-types/{type_id}")
async def admin_update_vehicle_type(type_id: str, vtype: Dict[str, Any]):
    """Update vehicle type."""
    update_payload = {}
    if vtype.get("name") is not None:
        update_payload["name"] = vtype.get("name")
    if vtype.get("description") is not None:
        update_payload["description"] = vtype.get("description")
    if vtype.get("icon") is not None:
        update_payload["icon"] = vtype.get("icon")
    if vtype.get("base_fare") is not None:
        update_payload["base_fare"] = vtype.get("base_fare")
    if vtype.get("price_per_km") is not None:
        update_payload["price_per_km"] = vtype.get("price_per_km")
    if vtype.get("price_per_minute") is not None:
        update_payload["price_per_minute"] = vtype.get("price_per_minute")
    if vtype.get("is_active") is not None:
        update_payload["is_active"] = vtype.get("is_active")

    if update_payload:
        await db.vehicle_types.update_one({"id": type_id}, {"$set": update_payload})
    return {"message": "Vehicle type updated"}


@admin_router.delete("/vehicle-types/{type_id}")
async def admin_delete_vehicle_type(type_id: str):
    """Delete vehicle type."""
    await db.vehicle_types.delete_many({"id": type_id})
    return {"message": "Vehicle type deleted"}


# ---------- Fare configs (table: fare_configs; schema column names) ----------


@admin_router.get("/fare-configs")
async def admin_get_fare_configs():
    """Get all fare configurations."""
    configs = await db.get_rows(
        "fare_configs", order="created_at", desc=True, limit=200
    )
    return configs


@admin_router.post("/fare-configs")
async def admin_create_fare_config(config: Dict[str, Any]):
    """Create fare configuration."""
    doc = {
        "name": config.get("name", ""),
        "service_area_id": config.get("service_area_id", ""),
        "vehicle_type_id": config.get("vehicle_type_id", ""),
        "base_fare": config.get("base_fare", 0),
        "per_km_rate": config.get("price_per_km", config.get("per_km_rate", 0)),
        "per_minute_rate": config.get(
            "price_per_minute", config.get("per_minute_rate", 0)
        ),
        "minimum_fare": config.get("minimum_fare", 0),
        "booking_fee": config.get("booking_fee", 2.0),
        "is_active": config.get("is_active", True),
        "created_at": datetime.utcnow().isoformat(),
    }
    row = await db.fare_configs.insert_one(doc)
    return {"config_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@admin_router.put("/fare-configs/{config_id}")
async def admin_update_fare_config(config_id: str, config: Dict[str, Any]):
    """Update fare configuration."""
    updates = {
        "name": config.get("name"),
        "base_fare": config.get("base_fare"),
        "per_km_rate": config.get("price_per_km", config.get("per_km_rate")),
        "per_minute_rate": config.get(
            "price_per_minute", config.get("per_minute_rate")
        ),
        "area_geojson": config.get("area_geojson"),
        "is_active": config.get("is_active"),
    }
    updates = {k: v for k, v in updates.items() if v is not None}
    if updates:
        await db.fare_configs.update_one({"id": config_id}, {"$set": updates})
    return {"message": "Fare configuration updated"}


@admin_router.delete("/fare-configs/{config_id}")
async def admin_delete_fare_config(config_id: str):
    """Delete fare configuration."""
    await db.fare_configs.delete_many({"id": config_id})
    return {"message": "Fare configuration deleted"}


# ---------- Drivers list (paginated, enriched with user) ----------


def _user_display_name(user: Optional[Dict]) -> str:
    if not user:
        return ""
    fn = user.get("first_name") or ""
    ln = user.get("last_name") or ""
    return f"{fn} {ln}".strip() or user.get("email") or user.get("phone") or ""


async def _batch_fetch_drivers_and_users(
    rider_ids: List[str], driver_ids: List[str]
) -> tuple:
    """Batch-fetch drivers and users in 2-3 queries instead of N+1 loops."""
    drivers_list = (
        await db.get_rows("drivers", {"id": {"$in": driver_ids}}, limit=max(len(driver_ids), 1))
        if driver_ids else []
    )
    drivers_map = {d["id"]: d for d in drivers_list if d.get("id")}

    all_user_ids = list({
        *rider_ids,
        *(d.get("user_id") for d in drivers_list if d.get("user_id")),
    })
    users_list = (
        await db.get_rows("users", {"id": {"$in": all_user_ids}}, limit=max(len(all_user_ids), 1))
        if all_user_ids else []
    )
    users_map = {u["id"]: u for u in users_list if u.get("id")}

    return drivers_map, users_map


@admin_router.get("/drivers")
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
    drivers = await db.get_rows(
        "drivers", filters, order="created_at", desc=True, limit=limit, offset=offset
    )
    user_ids = list({d.get("user_id") for d in drivers if d.get("user_id")})
    users_list = await db.get_rows("users", {"id": {"$in": user_ids}}, limit=max(len(user_ids), 1)) if user_ids else []
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


@admin_router.get("/drivers/stats")
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
    service_areas = await db.get_rows("service_areas", order="name", limit=200)
    area_map = {a["id"]: a.get("name", "Unknown") for a in service_areas}

    # ── Fetch drivers ──
    driver_filters: Dict[str, Any] = {}
    if service_area_id:
        driver_filters["service_area_id"] = service_area_id
    all_drivers = await db.get_rows("drivers", driver_filters, order="created_at", desc=True, limit=5000)

    # Enrich with user info (batch)
    user_ids = list({d.get("user_id") for d in all_drivers if d.get("user_id")})
    users_list = await db.get_rows("users", {"id": {"$in": user_ids}}, limit=max(len(user_ids), 1)) if user_ids else []
    users_map: Dict[str, Any] = {u["id"]: u for u in users_list if u.get("id")}

    # Auto-detect needs_review: active drivers with expired docs or pending re-uploads
    all_docs = await db.get_rows("driver_documents", {"status": "pending"}, limit=10000)
    pending_doc_driver_ids = {d.get("driver_id") for d in all_docs if d.get("driver_id")}

    now_iso = datetime.utcnow().isoformat()
    expiry_fields = [
        "license_expiry_date", "insurance_expiry_date",
        "vehicle_inspection_expiry_date", "background_check_expiry_date",
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

        enriched_drivers.append({
            **d,
            "status": driver_status,
            "first_name": u.get("first_name") if u else d.get("first_name"),
            "last_name": u.get("last_name") if u else d.get("last_name"),
            "name": _user_display_name(u) or d.get("name"),
            "email": u.get("email") if u else None,
            "phone": u.get("phone") if u else d.get("phone"),
        })

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
                "total": 0, "online": 0, "verified": 0, "unverified": 0,
                "total_rides": 0, "total_earnings": 0.0,
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
        except Exception:
            continue
        if range_start <= dt <= range_end:
            day_key = dt.strftime("%Y-%m-%d")
            daily_joins[day_key] += 1

    # Rides + earnings per day (for drivers matching the service_area filter)
    driver_ids_set = {d["id"] for d in enriched_drivers}
    ride_filters: Dict[str, Any] = {"created_at": {"$gte": range_start.isoformat()}}
    all_rides = await db.get_rows("rides", ride_filters, order="created_at", desc=True, limit=50000)

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
        except Exception:
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
        earnings_chart.append({"date": day_label, "date_raw": day_key, "amount": round(daily_earnings.get(day_key, 0), 2)})

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


@admin_router.get("/rides")
async def admin_get_rides(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
):
    """Get all rides with filters, enriched with rider_name and driver_name. Returns paginated."""
    filters = {}
    if status:
        filters["status"] = status

    # Get total count for pagination
    total_count = await db.rides.count_documents(filters)

    rides = await db.get_rows(
        "rides", filters, order="created_at", desc=True, limit=limit, offset=offset
    )
    rider_ids = list({r.get("rider_id") for r in rides if r.get("rider_id")})
    driver_ids = list({r.get("driver_id") for r in rides if r.get("driver_id")})
    drivers_map, users_map = await _batch_fetch_drivers_and_users(rider_ids, driver_ids)
    out = []
    for r in rides:
        rider = users_map.get(r.get("rider_id"))
        driver = drivers_map.get(r.get("driver_id"))
        driver_user = users_map.get(driver.get("user_id")) if driver else None
        out.append(
            {
                **r,
                "rider_name": _user_display_name(rider),
                "driver_name": _user_display_name(driver_user)
                if driver_user
                else (driver.get("name") if driver else None),
            }
        )
    return {"rides": out, "total_count": total_count, "limit": limit, "offset": offset}


@admin_router.put("/drivers/{driver_id}")
async def admin_update_driver(driver_id: str, updates: Dict[str, Any]):
    """Update driver details from admin dashboard."""
    allowed = {
        "first_name", "last_name", "email", "phone", "gender", "city",
        "service_area_id", "vehicle_type_id",
        "vehicle_make", "vehicle_model", "vehicle_color", "vehicle_year",
        "license_plate", "vehicle_vin",
        "license_number", "license_expiry_date", "insurance_expiry_date",
        "vehicle_inspection_expiry_date", "background_check_expiry_date",
        "work_eligibility_expiry_date",
    }
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    existing = await db.drivers.find_one({"id": driver_id})
    if not existing:
        raise HTTPException(status_code=404, detail=f"Driver {driver_id} not found")

    try:
        await db.drivers.update_one({"id": driver_id}, {"$set": filtered})
    except Exception as e:
        logger.error(f"Failed to update driver {driver_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update driver: {e}")
    return {"message": "Driver updated", "updated_fields": list(filtered.keys())}


@admin_router.post("/drivers/{driver_id}/verify")
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
        existing_driver = await db.drivers.find_one({"id": driver_id})
        if not existing_driver:
            raise HTTPException(status_code=404, detail=f"Driver {driver_id} not found")

        update_fields: Dict[str, Any] = {"is_verified": req.verified}
        # Clear needs_review when admin verifies (re-approves)
        if req.verified:
            update_fields["needs_review"] = False
        await db.drivers.update_one(
            {"id": driver_id},
            {"$set": update_fields},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update driver {driver_id} verify flag: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update driver: {e}")
    return {"message": f"Driver {'verified' if req.verified else 'unverified'}"}


class DriverActionRequest(BaseModel):
    action: str  # approve, reject, suspend, ban, unban, reactivate
    reason: Optional[str] = None


@admin_router.post("/drivers/{driver_id}/action")
async def admin_driver_action(driver_id: str, req: DriverActionRequest):
    """Perform a lifecycle action on a driver.

    Actions: approve, reject, suspend, ban, unban, reactivate.
    Each action transitions the driver to the appropriate state and
    records the reason + timestamp for audit trail.
    """
    driver = await db.drivers.find_one({"id": driver_id})
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
        await db.drivers.update_one({"id": driver_id}, {"$set": updates})
    except Exception as e:
        logger.error(f"Failed driver action {req.action} on {driver_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
        driver_id, req.action,
        action_titles.get(req.action, f"Action: {req.action}"),
        req.reason or "",
        {"old_status": current_status, "new_status": updates.get("status"), "reason": req.reason},
    )

    return {
        "message": f"Driver {req.action}d successfully",
        "new_status": updates.get("status", current_status),
    }


class DriverStatusOverride(BaseModel):
    status: str  # pending, active, rejected, suspended, banned
    is_verified: Optional[bool] = None
    reason: Optional[str] = None


@admin_router.put("/drivers/{driver_id}/status-override")
async def admin_override_driver_status(driver_id: str, req: DriverStatusOverride):
    """Manually move a driver to any status. Use with caution."""
    valid = {"pending", "active", "needs_review", "suspended", "banned"}
    if req.status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid)}")

    driver = await db.drivers.find_one({"id": driver_id})
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

    await db.drivers.update_one({"id": driver_id}, {"$set": updates})
    logger.info(f"[ADMIN] Driver {driver_id} status overridden to {req.status} reason={req.reason}")
    await _log_driver_activity(
        driver_id, "status_override", f"Status changed to {req.status}",
        req.reason or "Manual admin override",
        {"old_status": driver.get("status"), "new_status": req.status, "reason": req.reason},
    )
    return {"message": f"Driver status set to {req.status}"}


# ── Driver Notes ──


@admin_router.get("/drivers/{driver_id}/notes")
async def admin_get_driver_notes(driver_id: str):
    """Get all notes for a driver, newest first."""
    notes = await db.get_rows(
        "driver_notes", {"driver_id": driver_id}, order="created_at", desc=True, limit=200
    )
    return notes or []


class DriverNoteCreate(BaseModel):
    note: str
    category: str = "general"


@admin_router.post("/drivers/{driver_id}/notes")
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
    await db.driver_notes.insert_one(doc)
    await _log_driver_activity(
        driver_id, "note_added", f"Note added ({req.category})",
        req.note[:100], {"category": req.category},
    )
    return doc


@admin_router.delete("/drivers/notes/{note_id}")
async def admin_delete_driver_note(note_id: str):
    """Delete a note."""
    await db.driver_notes.delete_many({"id": note_id})
    return {"message": "Note deleted"}


# ── Driver Activity Log ──


async def _log_driver_activity(
    driver_id: str, event_type: str, title: str,
    description: str = "", metadata: dict = None, actor: str = "admin",
):
    """Helper to record a driver lifecycle event."""
    try:
        await db.driver_activity_log.insert_one({
            "id": str(uuid.uuid4()),
            "driver_id": driver_id,
            "event_type": event_type,
            "title": title,
            "description": description,
            "metadata": metadata or {},
            "actor": actor,
            "created_at": datetime.utcnow().isoformat(),
        })
    except Exception as e:
        logger.warning(f"Failed to log driver activity: {e}")


@admin_router.get("/drivers/{driver_id}/activity")
async def admin_get_driver_activity(driver_id: str, limit: int = 100):
    """Get full activity timeline for a driver, newest first."""
    activities = await db.get_rows(
        "driver_activity_log", {"driver_id": driver_id},
        order="created_at", desc=True, limit=limit,
    )
    return activities or []



# ---------- Stats (count_documents + sum from rides) ----------


@admin_router.get("/stats")
async def admin_get_stats():
    """Get admin dashboard statistics."""
    total_drivers = await db.drivers.count_documents({})
    active_drivers = await db.drivers.count_documents({"is_online": True})
    total_rides = await db.rides.count_documents({})
    today_start = (
        datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    )
    rides_today = await db.rides.count_documents({"created_at": {"$gte": today_start}})
    completed_today = await db.get_rows(
        "rides",
        {"status": "completed", "ride_completed_at": {"$gte": today_start}},
        limit=10000,
    )
    revenue_today = sum(float(r.get("total_fare") or 0) for r in completed_today)
    month_start = (
        datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    ).isoformat()
    completed_month = await db.get_rows(
        "rides",
        {"status": "completed", "ride_completed_at": {"$gte": month_start}},
        limit=10000,
    )
    revenue_month = sum(float(r.get("total_fare") or 0) for r in completed_month)
    pending_applications = await db.drivers.count_documents({"is_verified": False})
    return {
        "total_drivers": total_drivers,
        "active_drivers": active_drivers,
        "total_rides": total_rides,
        "rides_today": rides_today,
        "revenue_today": revenue_today,
        "revenue_month": revenue_month,
        "pending_applications": pending_applications,
    }


@admin_router.get("/rides/stats")
async def admin_get_ride_stats():
    """Get ride count/revenue stats for today, yesterday, this week, this month, plus daily chart data."""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    # This week (Monday start)
    week_start = today_start - timedelta(days=today_start.weekday())
    week_end = week_start + timedelta(days=7)

    # This month
    month_start = today_start.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)

    today_count = await db_supabase.get_ride_count_by_date_range(
        today_start.isoformat(), now.isoformat()
    )
    yesterday_count = await db_supabase.get_ride_count_by_date_range(
        yesterday_start.isoformat(), today_start.isoformat()
    )
    this_week_count = await db_supabase.get_ride_count_by_date_range(
        week_start.isoformat(), week_end.isoformat()
    )
    this_month_count = await db_supabase.get_ride_count_by_date_range(
        month_start.isoformat(), next_month.isoformat()
    )

    # Revenue stats from completed rides
    completed_today = await db_supabase.get_rows(
        "rides", {"status": "completed", "ride_completed_at": {"$gte": today_start.isoformat()}}, limit=10000
    )
    total_revenue = sum(float(r.get("total_fare") or 0) for r in completed_today)
    total_tips = sum(float(r.get("tip_amount") or 0) for r in completed_today)
    completed_count = len(completed_today)

    # Monthly completed rides for revenue
    completed_month = await db_supabase.get_rows(
        "rides", {"status": "completed", "ride_completed_at": {"$gte": month_start.isoformat()}}, limit=10000
    )
    month_revenue = sum(float(r.get("total_fare") or 0) for r in completed_month)

    # Daily chart data for last 14 days
    daily_chart = []
    for i in range(13, -1, -1):
        day_start = today_start - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        count = await db_supabase.get_ride_count_by_date_range(
            day_start.isoformat(), day_end.isoformat()
        )
        daily_chart.append({
            "date": day_start.strftime("%b %d"),
            "rides": count,
        })

    return {
        "today_count": today_count,
        "yesterday_count": yesterday_count,
        "this_week_count": this_week_count,
        "this_month_count": this_month_count,
        "week_start": week_start.strftime("%b %d"),
        "week_end": (week_end - timedelta(days=1)).strftime("%b %d"),
        "month_start": month_start.strftime("%b %d"),
        "month_end": (next_month - timedelta(days=1)).strftime("%b %d"),
        "today_revenue": round(total_revenue, 2),
        "today_tips": round(total_tips, 2),
        "today_completed": completed_count,
        "month_revenue": round(month_revenue, 2),
        "daily_chart": daily_chart,
    }


@admin_router.get("/rides/{ride_id}/details")
async def admin_get_ride_details(ride_id: str):
    """Get detailed ride information with rider, driver, flags, complaints, lost items, location trail."""
    ride = await db_supabase.get_ride_details_enriched(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    return ride


@admin_router.get("/rides/{ride_id}/location-trail")
async def admin_get_ride_location_trail(ride_id: str):
    """Get driver location trail for a specific ride."""
    trail = await db_supabase.get_ride_location_trail(ride_id)
    return trail


@admin_router.get("/rides/{ride_id}/live")
async def admin_get_live_ride(ride_id: str):
    """Get live ride data including current driver location."""
    data = await db_supabase.get_live_ride_data(ride_id)
    if not data:
        raise HTTPException(status_code=404, detail="Ride not found")
    return data


@admin_router.get("/rides/{ride_id}/invoice")
async def admin_get_ride_invoice(ride_id: str):
    """Get structured invoice data for a ride (used for client-side PDF generation)."""
    ride = await db_supabase.get_ride_details_enriched(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    return {
        "ride_id": ride.get("id"),
        "status": ride.get("status"),
        "created_at": ride.get("created_at"),
        "ride_completed_at": ride.get("ride_completed_at"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "distance_km": ride.get("distance_km", 0),
        "duration_minutes": ride.get("duration_minutes", 0),
        "base_fare": ride.get("base_fare", 0),
        "distance_fare": ride.get("distance_fare", 0),
        "time_fare": ride.get("time_fare", 0),
        "booking_fee": ride.get("booking_fee", 0),
        "airport_fee": ride.get("airport_fee", 0),
        "total_fare": ride.get("total_fare", 0),
        "tip_amount": ride.get("tip_amount", 0),
        "surge_multiplier": ride.get("surge_multiplier", 1.0),
        "payment_method": ride.get("payment_method", "card"),
        "payment_status": ride.get("payment_status", "pending"),
        "rider_name": ride.get("rider_name", ""),
        "rider_phone": ride.get("rider_phone", ""),
        "rider_email": ride.get("rider_email", ""),
        "driver_name": ride.get("driver_name", ""),
        "driver_phone": ride.get("driver_phone", ""),
        "driver_vehicle": ride.get("driver_vehicle", ""),
        "driver_license_plate": ride.get("driver_license_plate", ""),
        "pickup_lat": ride.get("pickup_lat"),
        "pickup_lng": ride.get("pickup_lng"),
        "dropoff_lat": ride.get("dropoff_lat"),
        "dropoff_lng": ride.get("dropoff_lng"),
    }


class FlagRequest(BaseModel):
    target_type: str  # 'rider' or 'driver'
    reason: str
    description: Optional[str] = None


@admin_router.post("/rides/{ride_id}/flag")
async def admin_flag_ride_participant(ride_id: str, req: FlagRequest):
    """Flag a rider or driver from a ride. 3 active flags = auto-ban."""
    ride = await db.rides.find_one({"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if req.target_type not in ("rider", "driver"):
        raise HTTPException(status_code=400, detail="target_type must be 'rider' or 'driver'")

    target_id = ride.get("rider_id") if req.target_type == "rider" else ride.get("driver_id")
    if not target_id:
        raise HTTPException(status_code=400, detail=f"No {req.target_type} assigned to this ride")

    flag_data = {
        "id": str(uuid.uuid4()),
        "target_type": req.target_type,
        "target_id": target_id,
        "ride_id": ride_id,
        "reason": req.reason,
        "description": req.description,
        "flagged_by": "admin",
        "is_active": True,
    }

    result = await db_supabase.create_flag(flag_data)
    return result


class ComplaintRequest(BaseModel):
    against_type: str  # 'rider' or 'driver'
    category: str  # safety, behavior, fraud, damage, other
    description: str


@admin_router.post("/rides/{ride_id}/complaint")
async def admin_create_complaint(ride_id: str, req: ComplaintRequest):
    """Create a complaint against a rider or driver from a ride."""
    ride = await db.rides.find_one({"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if req.against_type not in ("rider", "driver"):
        raise HTTPException(status_code=400, detail="against_type must be 'rider' or 'driver'")

    against_id = ride.get("rider_id") if req.against_type == "rider" else ride.get("driver_id")
    if not against_id:
        raise HTTPException(status_code=400, detail=f"No {req.against_type} assigned to this ride")

    complaint_data = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "against_type": req.against_type,
        "against_id": against_id,
        "category": req.category,
        "description": req.description,
        "status": "open",
        "created_by": "admin",
    }

    complaint = await db_supabase.create_complaint(complaint_data)
    return complaint


class ComplaintResolveRequest(BaseModel):
    status: str  # resolved or dismissed
    resolution: str


@admin_router.put("/complaints/{complaint_id}/resolve")
async def admin_resolve_complaint(complaint_id: str, req: ComplaintResolveRequest):
    """Resolve or dismiss a complaint."""
    result = await db_supabase.resolve_complaint(complaint_id, {
        "status": req.status,
        "resolution": req.resolution,
        "resolved_by": "admin",
        "updated_at": datetime.utcnow().isoformat(),
    })
    if not result:
        raise HTTPException(status_code=404, detail="Complaint not found")
    return result


class LostAndFoundRequest(BaseModel):
    item_description: str


@admin_router.post("/rides/{ride_id}/lost-and-found")
async def admin_report_lost_item(ride_id: str, req: LostAndFoundRequest):
    """Report a lost item from a ride and notify the driver."""
    ride = await db.rides.find_one({"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    driver_id = ride.get("driver_id")
    rider_id = ride.get("rider_id")
    if not driver_id:
        raise HTTPException(status_code=400, detail="No driver assigned to this ride")

    item_data = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "rider_id": rider_id or "",
        "driver_id": driver_id,
        "item_description": req.item_description,
        "status": "reported",
        "created_by": "admin",
    }

    item = await db_supabase.create_lost_and_found(item_data)

    # Send push notification to driver
    try:
        driver = await db.drivers.find_one({"id": driver_id})
        if driver and driver.get("user_id"):
            driver_user = await db.users.find_one({"id": driver["user_id"]})
            if driver_user and driver_user.get("fcm_token"):
                try:
                    from ..features import send_push_notification
                except ImportError:
                    from features import send_push_notification
                await send_push_notification(
                    driver_user["fcm_token"],
                    "Lost Item Report",
                    f"A rider reported a lost item: {req.item_description}. Please check your vehicle.",
                    {"type": "lost_and_found", "ride_id": ride_id},
                )
                # Update status to driver_notified
                await db_supabase.update_lost_and_found(item["id"], {
                    "status": "driver_notified",
                    "notified_at": datetime.utcnow().isoformat(),
                })
    except Exception as e:
        logger.warning(f"Failed to send lost item notification: {e}")

    return item


class LostAndFoundResolveRequest(BaseModel):
    status: str  # resolved or unresolved
    admin_notes: Optional[str] = None


@admin_router.put("/lost-and-found/{item_id}/resolve")
async def admin_resolve_lost_item(item_id: str, req: LostAndFoundResolveRequest):
    """Resolve or mark a lost and found item as unresolved."""
    update_data = {
        "status": req.status,
        "updated_at": datetime.utcnow().isoformat(),
    }
    if req.admin_notes:
        update_data["admin_notes"] = req.admin_notes
    if req.status == "resolved":
        update_data["resolved_at"] = datetime.utcnow().isoformat()

    result = await db_supabase.update_lost_and_found(item_id, update_data)
    if not result:
        raise HTTPException(status_code=404, detail="Item not found")
    return result


@admin_router.get("/flags")
async def admin_list_flags(
    limit: int = 100,
    offset: int = 0,
):
    """List all flags with optional pagination."""
    flags = await db_supabase.get_rows(
        "flags", order="created_at", desc=True, limit=limit, offset=offset
    )
    return flags


@admin_router.get("/lost-and-found")
async def admin_list_lost_and_found(
    limit: int = 100,
    offset: int = 0,
):
    """List all lost and found items."""
    items = await db_supabase.get_rows(
        "lost_and_found", order="created_at", desc=True, limit=limit, offset=offset
    )
    return items


@admin_router.put("/flags/{flag_id}/deactivate")
async def admin_deactivate_flag(flag_id: str):
    """Deactivate a flag (soft delete)."""
    result = await db_supabase.update_one("flags", {"id": flag_id}, {"$set": {"is_active": False}})
    if not result:
        raise HTTPException(status_code=404, detail="Flag not found")
    return {"message": "Flag deactivated"}


@admin_router.delete("/flags/{flag_id}")
async def admin_delete_flag(flag_id: str):
    """Permanently delete a flag."""
    await db_supabase.delete_one("flags", {"id": flag_id})
    return {"message": "Flag deleted"}


@admin_router.put("/lost-and-found/{item_id}")
async def admin_update_lost_item(item_id: str, req: dict):
    """Update a lost and found item."""
    update = {k: v for k, v in req.items() if k in ("item_description", "status", "admin_notes")}
    update["updated_at"] = datetime.utcnow().isoformat()
    result = await db_supabase.update_lost_and_found(item_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Item not found")
    return result


@admin_router.delete("/lost-and-found/{item_id}")
async def admin_delete_lost_item(item_id: str):
    """Delete a lost and found item."""
    await db_supabase.delete_one("lost_and_found", {"id": item_id})
    return {"message": "Item deleted"}


@admin_router.delete("/disputes/{dispute_id}")
async def admin_delete_dispute(dispute_id: str):
    """Delete a dispute."""
    await db_supabase.delete_one("disputes", {"id": dispute_id})
    return {"message": "Dispute deleted"}


@admin_router.get("/complaints")
async def admin_list_complaints(limit: int = 100, offset: int = 0):
    """List all complaints."""
    return await db_supabase.get_rows(
        "complaints", order="created_at", desc=True, limit=limit, offset=offset
    )


@admin_router.delete("/complaints/{complaint_id}")
async def admin_delete_complaint(complaint_id: str):
    """Delete a complaint."""
    await db_supabase.delete_one("complaints", {"id": complaint_id})
    return {"message": "Complaint deleted"}


@admin_router.get("/drivers/{driver_id}/rides")
async def admin_get_driver_rides(driver_id: str):
    """Get all rides for a specific driver."""
    rides = await db.get_rows(
        "rides", {"driver_id": driver_id}, order="created_at", desc=True, limit=500
    )
    return rides


@admin_router.get("/earnings")
async def admin_get_earnings(period: str = Query("month")):
    """Get earnings statistics from completed rides.

    Uses MongoDB aggregation to calculate totals from ride data.
    """
    # Calculate date range
    now = datetime.utcnow()
    if period == "day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = now - timedelta(days=7)
    else:  # month
        start_date = now - timedelta(days=30)

    start_date_str = start_date.isoformat()

    # Get completed rides since start_date
    completed_rides = await db.get_rows(
        "rides",
        {"status": "completed", "ride_completed_at": {"$gte": start_date_str}},
        limit=10000,
    )

    # Calculate totals
    total_revenue = sum(float(r.get("total_fare") or 0) for r in completed_rides)
    driver_earnings = sum(float(r.get("driver_earnings") or 0) for r in completed_rides)
    platform_fees = sum(float(r.get("admin_earnings") or 0) for r in completed_rides)

    return {
        "period": period,
        "total_revenue": total_revenue,
        "total_rides": len(completed_rides),
        "driver_earnings": driver_earnings,
        "platform_fees": platform_fees,
    }


@admin_router.get("/export/rides")
async def admin_export_rides(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Export rides data (schema: total_fare)."""
    rides = await db.get_rows("rides", order="created_at", desc=True, limit=1000)
    rider_ids = list({r.get("rider_id") for r in rides if r.get("rider_id")})
    driver_ids = list({r.get("driver_id") for r in rides if r.get("driver_id")})
    drivers_map, users_map = await _batch_fetch_drivers_and_users(rider_ids, driver_ids)
    out = []
    for r in rides:
        rider = users_map.get(r.get("rider_id"))
        driver = drivers_map.get(r.get("driver_id"))
        driver_user = users_map.get(driver.get("user_id")) if driver else None
        out.append(
            {
                "id": r.get("id"),
                "pickup_address": r.get("pickup_address"),
                "dropoff_address": r.get("dropoff_address"),
                "fare": r.get("total_fare"),
                "status": r.get("status"),
                "created_at": r.get("created_at"),
                "rider_name": _user_display_name(rider),
                "driver_name": _user_display_name(driver_user)
                if driver_user
                else (driver.get("name") if driver else None),
            }
        )
    return {"rides": out, "count": len(out)}


@admin_router.get("/export/drivers")
async def admin_export_drivers():
    """Export drivers data."""
    drivers = await db.get_rows("drivers", order="created_at", desc=True, limit=1000)
    user_ids = list({d.get("user_id") for d in drivers if d.get("user_id")})
    users_list = await db.get_rows("users", {"id": {"$in": user_ids}}, limit=max(len(user_ids), 1)) if user_ids else []
    users_map = {u["id"]: u for u in users_list if u.get("id")}
    out = []
    for d in drivers:
        u = users_map.get(d.get("user_id"))
        out.append(
            {
                "id": d.get("id"),
                "name": _user_display_name(u),
                "email": u.get("email") if isinstance(u, dict) else None,
                "phone": u.get("phone") if isinstance(u, dict) else d.get("phone"),
                "vehicle_make": d.get("vehicle_make"),
                "vehicle_model": d.get("vehicle_model"),
                "license_plate": d.get("license_plate"),
                "is_verified": d.get("is_verified"),
                "is_online": d.get("is_online"),
                "total_rides": d.get("total_rides"),
                "created_at": d.get("created_at"),
            }
        )
    return {"drivers": out, "count": len(out)}


# ---------- Users (riders) ----------


@admin_router.get("/users")
async def admin_get_users(
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
):
    """Get all users (riders) with optional search and pagination."""
    filters = {}
    if search:
        # Search across name, email, phone
        filters["$or"] = [
            {"first_name": {"$regex": search, "$options": "i"}},
            {"last_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}},
        ]

    users = await db.get_rows(
        "users", filters, order="created_at", desc=True, limit=limit, offset=offset
    )
    return users


@admin_router.get("/users/{user_id}")
async def admin_get_user_details(user_id: str):
    """Get detailed user information."""
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user's recent rides
    rides = await db.get_rows(
        "rides", {"rider_id": user_id}, order="created_at", desc=True, limit=10
    )

    return {
        **user,
        "total_rides": await db.rides.count_documents({"rider_id": user_id}),
        "recent_rides": rides,
    }


@admin_router.put("/users/{user_id}/status")
async def admin_update_user_status(user_id: str, status_data: Dict[str, Any]):
    """Update user status (e.g., suspend, activate)."""
    valid_status = ["active", "suspended", "banned"]
    new_status = status_data.get("status")

    if new_status not in valid_status:
        raise HTTPException(
            status_code=400, detail=f"Invalid status. Must be one of: {valid_status}"
        )

    await db.users.update_one(
        {"id": user_id},
        {"$set": {"status": new_status, "updated_at": datetime.utcnow().isoformat()}},
    )
    return {"message": f"User status updated to {new_status}"}


# ---------- Promotions (Discount Codes) ----------


@admin_router.get("/promotions")
async def admin_get_promotions():
    """Get all promotions/discount codes."""
    promotions = await db.get_rows(
        "promotions", order="created_at", desc=True, limit=500
    )
    return promotions


@admin_router.post("/promotions")
async def admin_create_promotion(promotion: Dict[str, Any]):
    """Create a new promotion/discount code."""
    # Only include fields that exist in the Supabase promotions table schema
    doc: Dict[str, Any] = {
        "code": (promotion.get("code") or "").strip().upper(),
        "description": promotion.get("description", ""),
        "promo_type": promotion.get("promo_type", "discount"),
        "discount_type": promotion.get("discount_type", "flat"),
        "discount_value": promotion.get("discount_value", 0),
        "max_discount": promotion.get("max_discount"),
        "max_uses": promotion.get("max_uses", 0),
        "max_uses_per_user": promotion.get("max_uses_per_user", 1),
        "uses": 0,
        "valid_from": promotion.get("valid_from", datetime.utcnow().isoformat()),
        "expiry_date": promotion.get("expiry_date"),
        "min_ride_fare": promotion.get("min_ride_fare", 0),
        "first_ride_only": promotion.get("first_ride_only", False),
        "new_user_days": promotion.get("new_user_days", 0),
        "is_active": promotion.get("is_active", True),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    # Optional JSONB fields that exist in the schema
    if promotion.get("applicable_areas"):
        doc["applicable_areas"] = promotion["applicable_areas"]
    if promotion.get("applicable_vehicles"):
        doc["applicable_vehicles"] = promotion["applicable_vehicles"]
    if promotion.get("user_segments"):
        doc["user_segments"] = promotion["user_segments"]
    if promotion.get("valid_days"):
        doc["valid_days"] = promotion["valid_days"]
    if promotion.get("valid_hours_start") is not None:
        doc["valid_hours_start"] = promotion["valid_hours_start"]
    if promotion.get("valid_hours_end") is not None:
        doc["valid_hours_end"] = promotion["valid_hours_end"]
    if promotion.get("total_budget"):
        doc["total_budget"] = promotion["total_budget"]
    if promotion.get("referrer_user_id"):
        doc["referrer_user_id"] = promotion["referrer_user_id"]
    if promotion.get("referrer_reward"):
        doc["referrer_reward"] = promotion["referrer_reward"]

    # Fields that require migration (may not exist yet in DB)
    # Insert safely - skip if column doesn't exist
    optional_fields = {
        "assigned_user_ids": promotion.get("assigned_user_ids", []),
        "inactive_days": promotion.get("inactive_days", 0),
        "min_total_rides": promotion.get("min_total_rides", 0),
        "max_total_rides": promotion.get("max_total_rides", 0),
    }

    try:
        # Try inserting with all fields first
        full_doc = {**doc, **optional_fields}
        row = await db.promotions.insert_one(full_doc)
    except Exception:
        # Fallback: insert without optional fields that may not exist in schema
        logger.warning("Promotions insert failed with optional fields, retrying without them")
        row = await db.promotions.insert_one(doc)

    return {"promotion_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@admin_router.get("/promotions/usage")
async def admin_get_promo_usage(
    promo_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(100),
    offset: int = Query(0),
):
    """Get promo code usage/redemption history."""
    filters: Dict[str, Any] = {}
    if promo_id:
        filters["promo_id"] = promo_id

    try:
        applications = await db.get_rows(
            "promo_applications",
            filters,
            order="created_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception:
        logger.warning("promo_applications table may not exist yet")
        return []

    # Filter by date range in Python (supabase may not support complex date filters)
    if date_from or date_to:
        filtered = []
        for app in applications:
            created = app.get("created_at", "")
            if date_from and created < date_from:
                continue
            if date_to and created > date_to + "T23:59:59Z":
                continue
            filtered.append(app)
        applications = filtered

    return applications


@admin_router.get("/promotions/stats")
async def admin_get_promo_stats(date_range: Optional[str] = Query(None, alias="range")):
    """Get promotion statistics with daily usage data."""
    all_promos = await db.get_rows("promotions", {}, limit=10000)
    try:
        all_usage = await db.get_rows("promo_applications", {}, order="created_at", desc=True, limit=10000)
    except Exception:
        logger.warning("promo_applications table may not exist yet")
        all_usage = []

    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")

    # Date range filtering
    range_start = None
    if date_range == "today":
        range_start = today
    elif date_range == "yesterday":
        range_start = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    elif date_range == "week":
        range_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    elif date_range == "last_week":
        range_start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    elif date_range == "month":
        range_start = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    # Filter usage by range
    filtered_usage = all_usage
    if range_start:
        filtered_usage = [u for u in all_usage if u.get("created_at", "") >= range_start]

    # Promo counts
    total_codes = len([p for p in all_promos if p.get("promo_type") != "private"])
    active_codes = len([p for p in all_promos if p.get("promo_type") != "private" and p.get("is_active")])
    expired_codes = len([p for p in all_promos if p.get("promo_type") != "private" and not p.get("is_active") and p.get("expiry_date") and p.get("expiry_date", "") < now.isoformat()])
    total_private = len([p for p in all_promos if p.get("promo_type") == "private"])
    active_private = len([p for p in all_promos if p.get("promo_type") == "private" and p.get("is_active")])

    # Usage stats
    total_redemptions = len(filtered_usage)
    total_discount = sum(float(u.get("discount_applied", 0)) for u in filtered_usage)

    # Daily usage for charts (last 30 days)
    daily: Dict[str, Dict[str, Any]] = {}
    for i in range(30):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = {"date": d, "count": 0, "amount": 0.0}

    for u in all_usage:
        d = u.get("created_at", "")[:10]
        if d in daily:
            daily[d]["count"] += 1
            daily[d]["amount"] += float(u.get("discount_applied", 0))

    daily_usage = sorted(daily.values(), key=lambda x: x["date"])

    return {
        "total_codes": total_codes,
        "active_codes": active_codes,
        "expired_codes": expired_codes,
        "total_private": total_private,
        "active_private": active_private,
        "total_redemptions": total_redemptions,
        "total_discount_given": round(total_discount, 2),
        "daily_usage": daily_usage,
    }


@admin_router.put("/promotions/{promotion_id}")
async def admin_update_promotion(promotion_id: str, promotion: Dict[str, Any]):
    """Update a promotion."""
    allowed_fields = [
        "code",
        "description",
        "promo_type",
        "discount_type",
        "discount_value",
        "max_discount",
        "max_uses",
        "max_uses_per_user",
        "valid_from",
        "expiry_date",
        "min_ride_fare",
        "first_ride_only",
        "new_user_days",
        "applicable_areas",
        "applicable_vehicles",
        "user_segments",
        "total_budget",
        "valid_days",
        "valid_hours_start",
        "valid_hours_end",
        "referrer_reward",
        "is_active",
        "assigned_user_ids",
        "inactive_days",
        "min_total_rides",
        "max_total_rides",
    ]
    updates = {
        k: v for k, v in promotion.items() if k in allowed_fields and v is not None
    }

    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        try:
            await db.promotions.update_one({"id": promotion_id}, {"$set": updates})
        except Exception:
            # If update fails (e.g. column doesn't exist yet), remove optional fields and retry
            for f in ["assigned_user_ids", "inactive_days", "min_total_rides", "max_total_rides"]:
                updates.pop(f, None)
            if updates:
                await db.promotions.update_one({"id": promotion_id}, {"$set": updates})
    return {"message": "Promotion updated"}


@admin_router.delete("/promotions/{promotion_id}")
async def admin_delete_promotion(promotion_id: str):
    """Delete a promotion."""
    await db.promotions.delete_many({"id": promotion_id})
    return {"message": "Promotion deleted"}


# ---------- Disputes ----------


@admin_router.get("/disputes")
async def admin_get_disputes():
    """Get all disputes."""
    try:
        disputes = await db.get_rows("disputes", order="created_at", desc=True, limit=500)
    except Exception:
        logger.warning("disputes table may not exist yet")
        return []
    return disputes


@admin_router.post("/disputes")
async def admin_create_dispute(dispute: Dict[str, Any]):
    """Create a dispute manually from admin."""
    doc = {
        "id": str(uuid.uuid4()),
        "ride_id": dispute.get("ride_id"),
        "user_id": dispute.get("user_id"),
        "user_name": dispute.get("user_name", ""),
        "user_type": dispute.get("user_type", "rider"),
        "reason": dispute.get("reason", ""),
        "description": dispute.get("description", ""),
        "status": "pending",
        "refund_amount": dispute.get("refund_amount", 0),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db.disputes.insert_one(doc)
    return {"success": True, "dispute": doc}


@admin_router.get("/disputes/{dispute_id}")
async def admin_get_dispute_details(dispute_id: str):
    """Get detailed dispute information."""
    dispute = await db.disputes.find_one({"id": dispute_id})
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    # Get related ride information
    ride = await db.rides.find_one({"id": dispute.get("ride_id")})

    return {**dispute, "ride_details": ride}


@admin_router.put("/disputes/{dispute_id}")
async def admin_update_dispute(dispute_id: str, dispute: Dict[str, Any]):
    """Update a dispute."""
    allowed = ["reason", "description", "status", "refund_amount", "user_type"]
    updates = {k: v for k, v in dispute.items() if k in allowed and v is not None}
    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db.disputes.update_one({"id": dispute_id}, {"$set": updates})
    return {"message": "Dispute updated"}


@admin_router.put("/disputes/{dispute_id}/resolve")
async def admin_resolve_dispute(dispute_id: str, resolution: Dict[str, Any]):
    """Resolve a dispute."""
    resolution_data = {
        "resolution_status": resolution.get("status"),  # resolved, rejected, pending
        "resolution_notes": resolution.get("notes", ""),
        "resolved_at": datetime.utcnow().isoformat(),
        "resolved_by": resolution.get("resolved_by", "admin"),
    }

    await db.disputes.update_one({"id": dispute_id}, {"$set": resolution_data})
    return {"message": "Dispute resolved"}


@admin_router.delete("/disputes/{dispute_id}")
async def admin_delete_dispute(dispute_id: str):
    """Delete a dispute."""
    await db.disputes.delete_many({"id": dispute_id})
    return {"message": "Dispute deleted"}


# ---------- Support Tickets ----------


@admin_router.get("/tickets")
async def admin_get_tickets():
    """Get all support tickets."""
    tickets = await db.get_rows(
        "support_tickets", order="created_at", desc=True, limit=500
    )
    return tickets


@admin_router.post("/tickets")
async def admin_create_ticket(ticket: Dict[str, Any]):
    """Create a support ticket manually from admin."""
    doc = {
        "id": str(uuid.uuid4()),
        "subject": ticket.get("subject", ""),
        "category": ticket.get("category", "general"),
        "message": ticket.get("message", ""),
        "priority": ticket.get("priority", "medium"),
        "user_id": ticket.get("user_id"),
        "user_name": ticket.get("user_name", "Admin"),
        "user_email": ticket.get("user_email", ""),
        "status": "open",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db.support_tickets.insert_one(doc)
    return {"success": True, "ticket": doc}


@admin_router.get("/tickets/{ticket_id}")
async def admin_get_ticket_details(ticket_id: str):
    """Get detailed ticket information."""
    ticket = await db.support_tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Get ticket messages
    messages = await db.get_rows(
        "support_messages", {"ticket_id": ticket_id}, order="created_at", limit=100
    )

    return {**ticket, "messages": messages}


@admin_router.post("/tickets/{ticket_id}/reply")
async def admin_reply_to_ticket(ticket_id: str, reply: Dict[str, Any]):
    """Reply to a support ticket."""
    message_doc = {
        "ticket_id": ticket_id,
        "sender_type": "admin",
        "sender_id": "admin-001",  # Could be dynamic based on current admin
        "message": reply.get("message", ""),
        "created_at": datetime.utcnow().isoformat(),
    }

    # Insert message
    await db.support_messages.insert_one(message_doc)

    # Update ticket status if needed
    if reply.get("status"):
        await db.support_tickets.update_one(
            {"id": ticket_id},
            {
                "$set": {
                    "status": reply.get("status"),
                    "updated_at": datetime.utcnow().isoformat(),
                }
            },
        )

    return {"message": "Reply sent"}


@admin_router.post("/tickets/{ticket_id}/close")
async def admin_close_ticket(ticket_id: str):
    """Close a support ticket."""
    await db.support_tickets.update_one(
        {"id": ticket_id},
        {"$set": {"status": "closed", "closed_at": datetime.utcnow().isoformat()}},
    )
    return {"message": "Ticket closed"}


@admin_router.put("/tickets/{ticket_id}")
async def admin_update_ticket(ticket_id: str, ticket: Dict[str, Any]):
    """Update a support ticket."""
    allowed = ["subject", "category", "priority", "status"]
    updates = {k: v for k, v in ticket.items() if k in allowed and v is not None}
    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db.support_tickets.update_one({"id": ticket_id}, {"$set": updates})
    return {"message": "Ticket updated"}


@admin_router.delete("/tickets/{ticket_id}")
async def admin_delete_ticket(ticket_id: str):
    """Delete a support ticket."""
    await db.support_tickets.delete_many({"id": ticket_id})
    return {"message": "Ticket deleted"}


# ---------- FAQs ----------


@admin_router.get("/faqs")
async def admin_get_faqs():
    """Get all FAQ entries."""
    faqs = await db.get_rows("faqs", order="created_at", desc=True, limit=500)
    return faqs


@admin_router.post("/faqs")
async def admin_create_faq(faq: Dict[str, Any]):
    """Create a new FAQ entry."""
    doc = {
        "question": faq.get("question"),
        "answer": faq.get("answer"),
        "category": faq.get("category", "general"),
        "is_active": faq.get("is_active", True),
        "created_at": datetime.utcnow().isoformat(),
    }
    row = await db.faqs.insert_one(doc)
    return {"faq_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@admin_router.put("/faqs/{faq_id}")
async def admin_update_faq(faq_id: str, faq: Dict[str, Any]):
    """Update an FAQ entry."""
    updates = {}
    if faq.get("question") is not None:
        updates["question"] = faq.get("question")
    if faq.get("answer") is not None:
        updates["answer"] = faq.get("answer")
    if faq.get("category") is not None:
        updates["category"] = faq.get("category")
    if faq.get("is_active") is not None:
        updates["is_active"] = faq.get("is_active")

    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db.faqs.update_one({"id": faq_id}, {"$set": updates})
    return {"message": "FAQ updated"}


@admin_router.delete("/faqs/{faq_id}")
async def admin_delete_faq(faq_id: str):
    """Delete an FAQ entry."""
    await db.faqs.delete_many({"id": faq_id})
    return {"message": "FAQ deleted"}


# ---------- Notifications ----------


@admin_router.post("/notifications/send")
async def admin_send_notification(notification: Dict[str, Any]):
    """Send a notification to a specific user or audience."""
    user_id = notification.get("user_id")
    title = notification.get("title", "")
    body = notification.get("body", "")
    notification_type = notification.get("type", "general")
    audience = notification.get("audience", "user")  # user, all, riders, drivers

    # Create notification document
    notification_doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "title": title,
        "body": body,
        "type": notification_type,
        "audience": audience,
        "sent_at": datetime.utcnow().isoformat(),
        "status": "sent",
        "sent_count": 1 if user_id else 0,
    }

    # If targeting specific user, insert and send
    if user_id:
        await db.notifications.insert_one(notification_doc)
        # TODO: Integrate with push notification service (FCM)
        logger.info(f"Notification sent to user {user_id}: {title}")
    elif audience == "all":
        # Broadcast to all users - just log for now
        logger.info(f"Broadcast notification to all users: {title}")
    elif audience == "riders":
        # Broadcast to all riders
        logger.info(f"Broadcast notification to all riders: {title}")
    elif audience == "drivers":
        # Broadcast to all drivers
        logger.info(f"Broadcast notification to all drivers: {title}")

    return {"success": True, "notification": notification_doc}


@admin_router.get("/notifications")
async def admin_get_notifications(
    limit: int = Query(50),
    offset: int = Query(0),
    status: Optional[str] = None,
    notification_type: Optional[str] = None,
):
    """Get all sent notifications with optional filters."""
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    if notification_type:
        filters["type"] = notification_type

    notifications = await db.get_rows(
        "notifications",
        filters,
        order="created_at"
        if "created_at" in (await db.notifications.find_one({}) or {})
        else "sent_at",
        desc=True,
        limit=limit,
        offset=offset,
    )
    return notifications


# ---------- Area Management (Pricing, Tax, Vehicle Pricing) ----------


@admin_router.get("/areas/{area_id}/fees")
async def admin_get_area_fees(area_id: str):
    """Get all fees for a service area."""
    fees = await db.get_rows(
        "area_fees", {"service_area_id": area_id}, order="created_at", limit=100
    )
    return fees


@admin_router.post("/areas/{area_id}/fees")
async def admin_create_area_fee(area_id: str, fee: Dict[str, Any]):
    """Create a new fee for a service area."""
    doc = {
        "id": str(uuid.uuid4()),
        "service_area_id": area_id,
        "fee_name": fee.get("fee_name", ""),
        "fee_type": fee.get("fee_type", "custom"),
        "calc_mode": fee.get("calc_mode", "flat"),
        "amount": float(fee.get("amount", 0)),
        "description": fee.get("description", ""),
        "conditions": fee.get("conditions", {}),
        "is_active": fee.get("is_active", True),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db.area_fees.insert_one(doc)
    return doc


@admin_router.put("/areas/{area_id}/fees/{fee_id}")
async def admin_update_area_fee(area_id: str, fee_id: str, fee: Dict[str, Any]):
    """Update an area fee."""
    allowed = ["fee_name", "fee_type", "calc_mode", "amount", "description", "conditions", "is_active"]
    updates = {k: fee[k] for k in allowed if k in fee}
    if "amount" in updates:
        updates["amount"] = float(updates["amount"])
    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db.area_fees.update_one({"id": fee_id}, {"$set": updates})
    return {"message": "Area fee updated"}


@admin_router.delete("/areas/{area_id}/fees/{fee_id}")
async def admin_delete_area_fee(area_id: str, fee_id: str):
    """Delete an area fee."""
    await db.area_fees.delete_many({"id": fee_id})
    return {"message": "Area fee deleted"}


@admin_router.get("/areas/{area_id}/tax")
async def admin_get_area_tax(area_id: str):
    """Get tax configuration for a service area."""
    area = await db.service_areas.find_one({"id": area_id})
    if not area:
        return {"service_area_id": area_id, "gst_enabled": True, "gst_rate": 5.0, "pst_enabled": False, "pst_rate": 0, "hst_enabled": False, "hst_rate": 0}
    return {
        "service_area_id": area_id,
        "gst_enabled": area.get("gst_enabled", True),
        "gst_rate": area.get("gst_rate", 5.0),
        "pst_enabled": area.get("pst_enabled", False),
        "pst_rate": area.get("pst_rate", 0),
        "hst_enabled": area.get("hst_enabled", False),
        "hst_rate": area.get("hst_rate", 0),
    }


@admin_router.put("/areas/{area_id}/tax")
async def admin_update_area_tax(area_id: str, tax: Dict[str, Any]):
    """Update tax configuration for a service area."""
    allowed = ["gst_enabled", "gst_rate", "pst_enabled", "pst_rate", "hst_enabled", "hst_rate"]
    updates = {k: tax[k] for k in allowed if k in tax}
    if updates:
        await db.service_areas.update_one({"id": area_id}, {"$set": updates})
    area = await db.service_areas.find_one({"id": area_id})
    return {k: area.get(k) for k in allowed}


@admin_router.get("/areas/{area_id}/vehicle-pricing")
async def admin_get_vehicle_pricing(area_id: str):
    """Get vehicle pricing configuration for a service area.

    Returns {vehicle_types, fare_configs} so the fare-config editor can
    display a row per vehicle type with the area's specific rates.
    """
    vehicle_types = await db.get_rows("vehicle_types", {"is_active": True}, order="name", limit=50)
    fare_configs = await db.get_rows("fare_configs", {"service_area_id": area_id}, limit=100)
    return {
        "vehicle_types": vehicle_types or [],
        "fare_configs": fare_configs or [],
    }


# ---------- Driver Area Assignment ----------


@admin_router.put("/drivers/{driver_id}/area")
async def admin_assign_driver_area(driver_id: str, service_area_id: str):
    """Assign a driver to a specific service area."""
    await db.drivers.update_one(
        {"id": driver_id},
        {
            "$set": {
                "service_area_id": service_area_id,
                "updated_at": datetime.utcnow().isoformat(),
            }
        },
    )
    return {"message": f"Driver assigned to area {service_area_id}"}


# ---------- Surge Pricing ----------


@admin_router.put("/service-areas/{area_id}/surge")
async def admin_update_surge_pricing(area_id: str, surge: Dict[str, Any]):
    """Update surge pricing for a service area."""
    surge_doc = {
        "service_area_id": area_id,
        "multiplier": surge.get("multiplier", 1.0),
        "is_active": surge.get("is_active", False),
        "updated_at": datetime.utcnow().isoformat(),
    }

    existing = await db.surge_pricing.find_one({"service_area_id": area_id})
    if existing:
        await db.surge_pricing.update_one(
            {"service_area_id": area_id}, {"$set": surge_doc}
        )
    else:
        await db.surge_pricing.insert_one(surge_doc)

    return {"message": "Surge pricing updated"}


@admin_router.get("/drivers/{driver_id}/location-trail")
async def admin_get_driver_location_trail(
    driver_id: str,
    hours: int = Query(24),
):
    """Get driver's location history (table: driver_location_history)."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    locations = await db.get_rows(
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


# ---------- Document Requirements ----------


@admin_router.get("/documents/requirements")
async def admin_get_document_requirements():
    """Get all document requirements."""
    requirements = await db.get_rows(
        "document_requirements", order="created_at", limit=100
    )
    return requirements or []


@admin_router.post("/documents/requirements")
async def admin_create_document_requirement(requirement: Dict[str, Any]):
    """Create a new document requirement."""
    doc = {
        "name": requirement.get("name"),
        "description": requirement.get("description", ""),
        "document_type": requirement.get("document_type"),
        "is_required": requirement.get("is_required", True),
        "applicable_to": requirement.get(
            "applicable_to", "driver"
        ),  # driver, rider, vehicle
        "created_at": datetime.utcnow().isoformat(),
    }
    row = await db.document_requirements.insert_one(doc)
    return {
        "requirement_id": str(row.get("id") if row and isinstance(row, dict) else "")
    }


@admin_router.put("/documents/requirements/{requirement_id}")
async def admin_update_document_requirement(
    requirement_id: str, requirement: Dict[str, Any]
):
    """Update a document requirement."""
    updates = {}
    if requirement.get("name") is not None:
        updates["name"] = requirement.get("name")
    if requirement.get("description") is not None:
        updates["description"] = requirement.get("description")
    if requirement.get("document_type") is not None:
        updates["document_type"] = requirement.get("document_type")
    if requirement.get("is_required") is not None:
        updates["is_required"] = requirement.get("is_required")
    if requirement.get("applicable_to") is not None:
        updates["applicable_to"] = requirement.get("applicable_to")

    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db.document_requirements.update_one(
            {"id": requirement_id}, {"$set": updates}
        )
    return {"message": "Document requirement updated"}


@admin_router.delete("/documents/requirements/{requirement_id}")
async def admin_delete_document_requirement(requirement_id: str):
    """Delete a document requirement."""
    await db.document_requirements.delete_one({"id": requirement_id})
    return {"message": "Document requirement deleted"}


# ---------- Driver Documents ----------


@admin_router.get("/documents/drivers/{driver_id}")
async def admin_get_driver_documents(driver_id: str):
    """Get all documents for a specific driver."""
    documents = await db.get_rows(
        "driver_documents",
        {"driver_id": driver_id},
        order="uploaded_at",
        desc=True,
        limit=100,
    )
    return documents or []


# Map keywords in a requirement name to the legacy top-level expiry column
# on the `drivers` row. Used when approving a re-uploaded document so that
# the go-online expiry check in routes/drivers.py update_driver_status stops
# rejecting the driver based on the stale onboarding-time value.
_REQUIREMENT_EXPIRY_FIELD_KEYWORDS = (
    ("license", "license_expiry_date"),
    ("driving", "license_expiry_date"),
    ("permit", "license_expiry_date"),
    ("insurance", "insurance_expiry_date"),
    ("inspection", "vehicle_inspection_expiry_date"),
    ("background", "background_check_expiry_date"),
    ("work", "work_eligibility_expiry_date"),
    ("eligibility", "work_eligibility_expiry_date"),
)


def _legacy_expiry_field_for_requirement(req_name: Optional[str]) -> Optional[str]:
    if not req_name:
        return None
    name = req_name.lower()
    for kw, field in _REQUIREMENT_EXPIRY_FIELD_KEYWORDS:
        if kw in name:
            return field
    return None


@admin_router.post("/documents/{document_id}/review")
async def admin_review_driver_document(document_id: str, review_data: Dict[str, Any]):
    """Review and approve/reject a driver document.

    On approval, if an ``expiry_date`` is provided (or already stored on the
    doc), we also refresh the corresponding legacy top-level expiry column on
    the ``drivers`` row so that the go-online check in
    ``update_driver_status`` sees the new date instead of the stale
    onboarding-time value (which used to leave drivers blocked offline).
    """
    status = review_data.get("status")
    rejection_reason = review_data.get("rejection_reason")
    expiry_raw = review_data.get("expiry_date")

    if status not in ["approved", "rejected", "pending"]:
        raise HTTPException(status_code=400, detail="Invalid status")

    # Load existing doc so we know which driver + requirement this is.
    existing = await db.driver_documents.find_one({"id": document_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")

    # Parse incoming expiry (accept ISO string or None).
    new_expiry_iso: Optional[str] = None
    if expiry_raw:
        try:
            new_expiry_iso = datetime.fromisoformat(
                str(expiry_raw).replace("Z", "+00:00")
            ).isoformat()
        except ValueError:
            new_expiry_iso = None

    # NOTE: driver_documents schema only guarantees these columns:
    #   id, driver_id, document_type, document_url, status,
    #   rejection_reason, uploaded_at, updated_at, requirement_id, side
    # Writing `reviewed_at` or `expiry_date` here would cause PGRST204
    # ("Could not find the X column") -> 500 response with no CORS headers,
    # which is why this endpoint has been silently failing in production.
    updates: Dict[str, Any] = {
        "status": status,
        "updated_at": datetime.utcnow().isoformat(),
    }
    if rejection_reason:
        updates["rejection_reason"] = rejection_reason

    try:
        await db.driver_documents.update_one(
            {"id": document_id},
            {"$set": updates},
        )
    except Exception as e:
        logger.error(f"Failed to update driver_document {document_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update document: {e}")

    # On approval, propagate the expiry to the legacy drivers.* column so the
    # go-online check stops blocking based on stale onboarding-time values.
    if status == "approved":
        effective_expiry_iso = new_expiry_iso

        req_row = None
        try:
            req_row = await db.document_requirements.find_one(
                {"id": existing.get("requirement_id")}
            )
        except Exception:
            req_row = None

        legacy_field = _legacy_expiry_field_for_requirement(
            req_row.get("name") if req_row else None
        )
        if legacy_field:
            # If admin did not supply a new expiry, clear the stale legacy
            # value (None) so the go-online check skips it instead of
            # rejecting on a past date from original onboarding.
            try:
                await db.drivers.update_one(
                    {"id": existing.get("driver_id")},
                    {
                        "$set": {
                            legacy_field: effective_expiry_iso,
                            "updated_at": datetime.utcnow().isoformat(),
                        }
                    },
                )
            except Exception as e:
                logger.warning(
                    f"Could not update legacy expiry field {legacy_field} "
                    f"for driver {existing.get('driver_id')}: {e}"
                )

    # After approving, check if this driver has no more pending docs → clear needs_review
    if status == "approved":
        driver_id = existing.get("driver_id")
        if driver_id:
            remaining_pending = await db.get_rows(
                "driver_documents",
                {"driver_id": driver_id, "status": "pending"},
                limit=1,
            )
            if not remaining_pending:
                # All pending docs approved → set driver back to active
                try:
                    drv = await db.drivers.find_one({"id": driver_id})
                    if drv and drv.get("status") == "needs_review":
                        await db.drivers.update_one(
                            {"id": driver_id},
                            {"$set": {"status": "active", "is_verified": True}},
                        )
                except Exception:
                    pass

    # Log to activity timeline
    doc_type = existing.get("document_type", "Document")
    await _log_driver_activity(
        existing.get("driver_id", ""), f"document_{status}",
        f"Document {status}: {doc_type}",
        rejection_reason or "",
        {"document_id": document_id, "document_type": doc_type, "status": status},
    )

    return {"message": f"Document {status}"}


# ---------- Heat Map Data ----------


@admin_router.get("/rides/heatmap-data")
async def admin_get_heatmap_data(
    filter: str = Query("all"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    service_area_id: Optional[str] = None,
    group_by: str = Query("both"),
):
    """Get ride location data for heat map visualisation.

    Query params:
        filter: 'all' | 'corporate' | 'regular'
        start_date / end_date: ISO date strings (YYYY-MM-DD)
        service_area_id: optional area filter
        group_by: 'pickup' | 'dropoff' | 'both'
    """
    query_filters: Dict[str, Any] = {}

    # Date range filter
    if start_date:
        query_filters.setdefault("created_at", {})["$gte"] = start_date
    if end_date:
        query_filters.setdefault("created_at", {})["$lte"] = end_date + "T23:59:59"

    # Corporate vs regular filter
    if filter == "corporate":
        query_filters["corporate_account_id"] = {"$ne": None}
    elif filter == "regular":
        query_filters["corporate_account_id"] = None

    # Service area filter
    if service_area_id:
        query_filters["service_area_id"] = service_area_id

    rides = await db.get_rows(
        "rides", query_filters, order="created_at", desc=True, limit=10000
    )

    pickup_points = []
    dropoff_points = []
    corporate_count = 0
    regular_count = 0

    for r in rides:
        p_lat = r.get("pickup_lat")
        p_lng = r.get("pickup_lng")
        d_lat = r.get("dropoff_lat")
        d_lng = r.get("dropoff_lng")

        if p_lat is not None and p_lng is not None:
            pickup_points.append([float(p_lat), float(p_lng), 1])
        if d_lat is not None and d_lng is not None:
            dropoff_points.append([float(d_lat), float(d_lng), 1])

        if r.get("corporate_account_id"):
            corporate_count += 1
        else:
            regular_count += 1

    return {
        "pickup_points": pickup_points,
        "dropoff_points": dropoff_points,
        "stats": {
            "total_rides": len(rides),
            "corporate_rides": corporate_count,
            "regular_rides": regular_count,
        },
    }


# ---------- Heat Map Settings ----------

_HEATMAP_SETTINGS_ID = "heatmap_settings"

_DEFAULT_HEATMAP_SETTINGS = {
    "heat_map_enabled": True,
    "heat_map_default_range": "month",
    "heat_map_intensity": "medium",
    "heat_map_radius": 25,
    "heat_map_blur": 15,
    "heat_map_gradient_start": "#00ff00",
    "heat_map_gradient_mid": "#ffff00",
    "heat_map_gradient_end": "#ff0000",
    "heat_map_show_pickups": True,
    "heat_map_show_dropoffs": True,
    "corporate_heat_map_enabled": True,
    "regular_rider_heat_map_enabled": True,
}


@admin_router.get("/settings/heatmap")
async def admin_get_heatmap_settings():
    """Return heat-map display settings (single settings row)."""
    row = await db.settings.find_one({"id": _HEATMAP_SETTINGS_ID})
    if row:
        # Merge defaults with stored values so new keys always appear
        merged = {**_DEFAULT_HEATMAP_SETTINGS, **row}
        merged.pop("_id", None)
        return merged
    return {**_DEFAULT_HEATMAP_SETTINGS, "id": _HEATMAP_SETTINGS_ID}


@admin_router.put("/settings/heatmap")
async def admin_update_heatmap_settings(data: Dict[str, Any]):
    """Update heat-map display settings."""
    payload = {
        "id": _HEATMAP_SETTINGS_ID,
        **{k: v for k, v in data.items() if k in _DEFAULT_HEATMAP_SETTINGS},
        "updated_at": datetime.utcnow().isoformat(),
    }

    existing = await db.settings.find_one({"id": _HEATMAP_SETTINGS_ID})
    if existing:
        update_fields = {k: v for k, v in payload.items() if k != "id"}
        await db.settings.update_one(
            {"id": _HEATMAP_SETTINGS_ID}, {"$set": update_fields}
        )
    else:
        await db.settings.insert_one(payload)

    return {"message": "Heat map settings updated"}


# ---------- Corporate Accounts (moved to dedicated routes) ----------

# Note: Corporate accounts functionality has been moved to dedicated routes
# in /api/admin/corporate-accounts to ensure consistency and proper validation
# See routes/corporate_accounts.py for implementation


# ============================================================
# Staff Management — Multi-admin with role-based module access
# ============================================================

AVAILABLE_MODULES = [
    "dashboard",
    "users",
    "drivers",
    "rides",
    "earnings",
    "promotions",
    "surge",
    "service_areas",
    "vehicle_types",
    "pricing",
    "support",
    "disputes",
    "notifications",
    "settings",
    "corporate_accounts",
    "documents",
    "heatmap",
    "staff",  # Only super_admin can access this
]

ROLE_PRESETS = {
    "super_admin": AVAILABLE_MODULES,
    "operations": [
        "dashboard",
        "rides",
        "drivers",
        "surge",
        "service_areas",
        "vehicle_types",
        "heatmap",
    ],
    "support": ["dashboard", "support", "disputes", "notifications", "users"],
    "finance": ["dashboard", "earnings", "promotions", "corporate_accounts", "pricing"],
}


class StaffCreateRequest(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str
    role: str = "custom"  # super_admin, operations, support, finance, custom
    modules: Optional[List[str]] = None  # Only used if role=custom


class StaffUpdateRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: Optional[str] = None
    modules: Optional[List[str]] = None
    is_active: Optional[bool] = None


@admin_router.get("/staff")
async def list_staff(authorization: Optional[str] = Header(None)):
    """List all staff members."""
    staff = await db.get_rows("admin_staff", limit=100)
    # Remove passwords from response
    for s in staff:
        s.pop("password_hash", None)
        s.pop("password", None)
    return staff


@admin_router.post("/staff")
async def create_staff(
    req: StaffCreateRequest, authorization: Optional[str] = Header(None)
):
    """Create a new staff member with role-based module access."""
    import hashlib

    # Check if email already exists
    existing = await db.admin_staff.find_one({"email": req.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered as staff")

    # Determine modules based on role
    if req.role in ROLE_PRESETS:
        modules = ROLE_PRESETS[req.role]
    elif req.role == "custom" and req.modules:
        modules = [m for m in req.modules if m in AVAILABLE_MODULES]
    else:
        modules = ["dashboard"]

    staff = {
        "id": str(uuid.uuid4()),
        "email": req.email.lower(),
        "password_hash": hashlib.sha256(req.password.encode()).hexdigest(),
        "first_name": req.first_name,
        "last_name": req.last_name,
        "role": req.role,
        "modules": modules,
        "is_active": True,
        "created_at": datetime.utcnow().isoformat(),
        "last_login": None,
    }

    await db.admin_staff.insert_one(staff)
    staff.pop("password_hash")
    return staff


@admin_router.get("/staff/{staff_id}")
async def get_staff(staff_id: str):
    """Get a single staff member."""
    s = await db.admin_staff.find_one({"id": staff_id})
    if not s:
        raise HTTPException(status_code=404, detail="Staff member not found")
    s.pop("password_hash", None)
    s.pop("password", None)
    return s


@admin_router.put("/staff/{staff_id}")
async def update_staff(staff_id: str, req: StaffUpdateRequest):
    """Update staff member role/modules/status."""
    s = await db.admin_staff.find_one({"id": staff_id})
    if not s:
        raise HTTPException(status_code=404, detail="Staff member not found")

    updates = {}
    if req.first_name is not None:
        updates["first_name"] = req.first_name
    if req.last_name is not None:
        updates["last_name"] = req.last_name
    if req.is_active is not None:
        updates["is_active"] = req.is_active
    if req.role is not None:
        updates["role"] = req.role
        if req.role in ROLE_PRESETS:
            updates["modules"] = ROLE_PRESETS[req.role]
    if req.modules is not None:
        updates["modules"] = [m for m in req.modules if m in AVAILABLE_MODULES]

    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db.admin_staff.update_one({"id": staff_id}, {"$set": updates})

    return {"success": True}


@admin_router.delete("/staff/{staff_id}")
async def delete_staff(staff_id: str):
    """Delete a staff member."""
    await db.admin_staff.delete_many({"id": staff_id})
    return {"success": True}


@admin_router.get("/staff/modules/list")
async def list_modules():
    """List available modules and role presets."""
    return {
        "modules": AVAILABLE_MODULES,
        "role_presets": {k: v for k, v in ROLE_PRESETS.items()},
    }


# ============================================================
# Spinr Pass — Driver Subscription Plans
# ============================================================


class SubscriptionPlanCreate(BaseModel):
    name: str  # "Basic", "Pro", "Unlimited"
    price: float  # 19.99, 49.99
    duration_days: int = 30  # 1=daily, 7=weekly, 30=monthly
    rides_per_day: int = -1  # -1 = unlimited, or 4, 8, etc.
    description: Optional[str] = None
    features: Optional[List[str]] = None  # ["Priority support", "Surge protection"]
    vehicle_types: Optional[List[str]] = None  # restrict to vehicle type IDs, null=all
    service_areas: Optional[List[str]] = None  # restrict to area IDs, null=all
    is_active: bool = True


class SubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    duration_days: Optional[int] = None
    rides_per_day: Optional[int] = None
    description: Optional[str] = None
    features: Optional[List[str]] = None
    vehicle_types: Optional[List[str]] = None
    service_areas: Optional[List[str]] = None
    is_active: Optional[bool] = None


@admin_router.get("/subscription-plans")
async def list_subscription_plans():
    """List all Spinr Pass subscription plans."""
    plans = await db.get_rows("subscription_plans", limit=50)
    return plans


@admin_router.post("/subscription-plans")
async def create_subscription_plan(req: SubscriptionPlanCreate):
    """Create a new driver subscription plan."""
    plan = {
        "id": str(uuid.uuid4()),
        "name": req.name,
        "price": req.price,
        "duration_days": req.duration_days,
        "rides_per_day": req.rides_per_day,
        "description": req.description or "",
        "features": req.features or [],
        "vehicle_types": req.vehicle_types,
        "service_areas": req.service_areas,
        "is_active": req.is_active,
        "subscriber_count": 0,
        "created_at": datetime.utcnow().isoformat(),
    }
    await db.subscription_plans.insert_one(plan)
    return plan


@admin_router.put("/subscription-plans/{plan_id}")
async def update_subscription_plan(plan_id: str, req: SubscriptionPlanUpdate):
    """Update a subscription plan."""
    updates = {k: v for k, v in req.dict().items() if v is not None}
    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db.subscription_plans.update_one({"id": plan_id}, {"$set": updates})
    return {"success": True}


@admin_router.delete("/subscription-plans/{plan_id}")
async def delete_subscription_plan(plan_id: str):
    """Delete a subscription plan."""
    await db.subscription_plans.delete_many({"id": plan_id})
    return {"success": True}


# ─── Driver Subscription Management ───


@admin_router.get("/driver-subscriptions")
async def list_driver_subscriptions(status: Optional[str] = Query(None)):
    """List all driver subscriptions, optionally filtered by status."""
    subs = await db.driver_subscriptions.find({}).to_list(200)
    if status:
        subs = [s for s in subs if s.get("status") == status]
    return subs


@admin_router.get("/subscription-stats")
async def admin_get_subscription_stats(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    service_area_ids: Optional[str] = None,
):
    """Get Spinr Pass subscription revenue stats, transaction list, and chart data.

    service_area_ids: comma-separated list of area IDs to filter by driver's area.
    """
    from collections import defaultdict

    area_filter = set(service_area_ids.split(",")) if service_area_ids else None

    now = datetime.utcnow()
    if start_date:
        range_start = datetime.fromisoformat(start_date.replace("Z", "").replace("+00:00", ""))
    else:
        range_start = now - timedelta(days=30)
    range_start = range_start.replace(hour=0, minute=0, second=0, microsecond=0)

    if end_date:
        range_end = datetime.fromisoformat(end_date.replace("Z", "").replace("+00:00", ""))
        range_end = range_end.replace(hour=23, minute=59, second=59)
    else:
        range_end = now

    # Fetch all subscriptions
    all_subs = await db.driver_subscriptions.find({}).to_list(10000)

    # Fetch all plans for lookup
    all_plans = await db.get_rows("subscription_plans", limit=100)
    plan_map = {p["id"]: p for p in all_plans}

    # Fetch drivers for name + area lookup (batch)
    driver_ids = list({s.get("driver_id") for s in all_subs if s.get("driver_id")})
    raw_drivers_map, raw_users_map = await _batch_fetch_drivers_and_users([], driver_ids)
    drivers_map: Dict[str, str] = {}
    driver_area_map: Dict[str, str] = {}
    for did, d in raw_drivers_map.items():
        u = raw_users_map.get(d.get("user_id")) if d.get("user_id") else None
        name = _user_display_name(u) if u else ""
        drivers_map[did] = name or d.get("name") or did[:8]
        if d.get("service_area_id"):
            driver_area_map[did] = d["service_area_id"]

    # Filter by service area if requested
    if area_filter:
        all_subs = [s for s in all_subs if driver_area_map.get(s.get("driver_id", "")) in area_filter]

    # Overall stats
    active = [s for s in all_subs if s.get("status") == "active"]
    expired = [s for s in all_subs if s.get("status") == "expired"]
    cancelled = [s for s in all_subs if s.get("status") == "cancelled"]
    total_revenue = sum(float(s.get("price") or 0) for s in all_subs)
    active_revenue = sum(float(s.get("price") or 0) for s in active)

    # Filter to date range for transactions and charts
    def parse_dt(s):
        try:
            return datetime.fromisoformat(str(s).replace("Z", "").replace("+00:00", ""))
        except Exception:
            return None

    in_range = []
    for s in all_subs:
        dt = parse_dt(s.get("created_at") or s.get("started_at"))
        if dt and range_start <= dt <= range_end:
            in_range.append(s)

    range_revenue = sum(float(s.get("price") or 0) for s in in_range)

    # Per-plan breakdown
    plan_stats = defaultdict(lambda: {"name": "", "count": 0, "revenue": 0.0, "active": 0})
    for s in all_subs:
        pid = s.get("plan_id") or "unknown"
        plan_stats[pid]["name"] = s.get("plan_name") or plan_map.get(pid, {}).get("name", "Unknown")
        plan_stats[pid]["count"] += 1
        plan_stats[pid]["revenue"] += float(s.get("price") or 0)
        if s.get("status") == "active":
            plan_stats[pid]["active"] += 1

    # Daily charts (within date range)
    num_days = min((range_end - range_start).days + 1, 365)
    daily_revenue = defaultdict(float)
    daily_new_subs = defaultdict(int)
    for s in in_range:
        dt = parse_dt(s.get("created_at") or s.get("started_at"))
        if dt:
            day_key = dt.strftime("%Y-%m-%d")
            daily_revenue[day_key] += float(s.get("price") or 0)
            daily_new_subs[day_key] += 1

    revenue_chart = []
    subscribers_chart = []
    for i in range(num_days):
        day = range_start + timedelta(days=i)
        day_key = day.strftime("%Y-%m-%d")
        day_label = day.strftime("%b %d")
        revenue_chart.append({"date": day_label, "date_raw": day_key, "amount": round(daily_revenue.get(day_key, 0), 2)})
        subscribers_chart.append({"date": day_label, "date_raw": day_key, "count": daily_new_subs.get(day_key, 0)})

    # Transaction list (in range, enriched)
    transactions = []
    for s in sorted(in_range, key=lambda x: x.get("created_at", ""), reverse=True):
        transactions.append({
            "id": s.get("id"),
            "driver_id": s.get("driver_id"),
            "driver_name": drivers_map.get(s.get("driver_id"), s.get("driver_id", "")[:8]),
            "plan_name": s.get("plan_name") or plan_map.get(s.get("plan_id", ""), {}).get("name", "Unknown"),
            "price": float(s.get("price") or 0),
            "status": s.get("status", "unknown"),
            "started_at": s.get("started_at"),
            "expires_at": s.get("expires_at"),
            "created_at": s.get("created_at"),
        })

    return {
        "stats": {
            "total_subscribers": len(all_subs),
            "active": len(active),
            "expired": len(expired),
            "cancelled": len(cancelled),
            "total_revenue": round(total_revenue, 2),
            "active_mrr": round(active_revenue, 2),
            "range_revenue": round(range_revenue, 2),
            "range_transactions": len(in_range),
        },
        "plan_breakdown": [{"plan_id": k, **v} for k, v in plan_stats.items()],
        "charts": {
            "daily_revenue": revenue_chart,
            "daily_subscribers": subscribers_chart,
        },
        "transactions": transactions,
        "service_areas": [{"id": a["id"], "name": a.get("name", "Unknown")}
                          for a in await db.get_rows("service_areas", order="name", limit=200)
                          if not a.get("parent_service_area_id")],
    }


# ============================================================
# Audit Logs
# ============================================================


@admin_router.get("/audit-logs")
async def get_audit_logs(limit: int = Query(50), offset: int = Query(0)):
    """Get audit log entries."""
    logs = await db.get_rows("audit_logs", order="created_at", desc=True, limit=limit)
    return logs


async def log_audit(
    action: str, entity_type: str, entity_id: str, user_email: str, details: str = ""
):
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


# ---------- Cloud Messaging ----------


@admin_router.post("/cloud-messaging/send")
async def admin_send_cloud_message(payload: Dict[str, Any]):
    """Send or schedule a cloud message to users/drivers."""
    title = payload.get("title", "")
    description = payload.get("description", "")
    audience = payload.get("audience", "customers")
    msg_type = payload.get("type", "info")
    channels = payload.get("channels")
    if not channels:
        channel = payload.get("channel", "push")
        channels = [channel]
    particular_ids = payload.get("particular_ids") or []
    if not particular_ids:
        pid = payload.get("particular_id")
        if pid:
            particular_ids = [pid]
    scheduled_at = payload.get("scheduled_at")

    if not title or not description:
        raise HTTPException(status_code=400, detail="Title and description are required")

    is_scheduled = bool(scheduled_at)
    status = "scheduled" if is_scheduled else "sent"

    total_recipients = 1
    successful = 0
    failed_count = 0

    if audience in ("particular_customer", "particular_driver"):
        total_recipients = len(particular_ids) if particular_ids else 1
    elif audience == "customers":
        count = await db.users.count_documents({"role": "rider"})
        total_recipients = count if count > 0 else 0
    elif audience == "drivers":
        count = await db.users.count_documents({"role": "driver"})
        total_recipients = count if count > 0 else 0

    if not is_scheduled:
        # TODO: Integrate with FCM / email service
        successful = total_recipients
        failed_count = 0
        logger.info(f"Cloud message sent to {audience}: {title}")

    doc = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": description,
        "audience": audience,
        "type": msg_type,
        "channel": channels[0],
        "channels": channels,
        "particular_id": particular_ids[0] if particular_ids else None,
        "particular_ids": particular_ids,
        "status": status,
        "scheduled_at": scheduled_at,
        "sent_at": datetime.utcnow().isoformat() if not is_scheduled else None,
        "created_at": datetime.utcnow().isoformat(),
        "total_recipients": total_recipients,
        "successful": successful,
        "failed_count": failed_count,
    }

    try:
        await db.cloud_messages.insert_one(doc)
    except Exception as e:
        logger.error(f"Failed to insert cloud message: {e}")
        raise HTTPException(status_code=500, detail="Failed to save message. The cloud_messages table may not exist yet. Please run migration 06_cloud_messaging.sql.")
    return {"success": True, "message": doc}


@admin_router.get("/cloud-messaging")
async def admin_get_cloud_messages(
    status: Optional[str] = None,
    audience: Optional[str] = None,
    limit: int = Query(100),
    offset: int = Query(0),
):
    """Get cloud messages with optional filters."""
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    if audience:
        filters["audience"] = audience

    try:
        messages = await db.get_rows(
            "cloud_messages",
            filters,
            order="created_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception:
        logger.warning("cloud_messages table may not exist yet")
        return []
    return messages


@admin_router.get("/cloud-messaging/stats")
async def admin_get_cloud_message_stats():
    """Get cloud messaging statistics."""
    try:
        all_messages = await db.get_rows("cloud_messages", {}, limit=10000)
    except Exception:
        logger.warning("cloud_messages table may not exist yet")
        all_messages = []

    total = len(all_messages)
    total_sent = sum(1 for m in all_messages if m.get("status") == "sent")
    total_scheduled = sum(1 for m in all_messages if m.get("status") == "scheduled")
    total_failed = sum(1 for m in all_messages if m.get("status") == "failed")
    total_reached = sum(m.get("successful", 0) for m in all_messages)
    total_recipients = sum(m.get("total_recipients", 0) for m in all_messages)
    success_rate = round((total_reached / total_recipients * 100), 1) if total_recipients > 0 else 0

    return {
        "total_messages": total,
        "total_sent": total_sent,
        "total_scheduled": total_scheduled,
        "total_failed": total_failed,
        "total_recipients_reached": total_reached,
        "success_rate": success_rate,
    }


@admin_router.delete("/cloud-messaging/{message_id}")
async def admin_delete_cloud_message(message_id: str):
    """Cancel/delete a scheduled cloud message."""
    existing = await db.cloud_messages.find_one({"id": message_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Message not found")

    if existing.get("status") == "sent":
        raise HTTPException(status_code=400, detail="Cannot delete a sent message")

    await db.cloud_messages.update_one(
        {"id": message_id},
        {"$set": {"status": "cancelled"}},
    )
    return {"message": "Message cancelled"}
