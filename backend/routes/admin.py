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
    """Get all service areas."""
    areas = await db.get_rows("service_areas", order="name", limit=500)
    return areas


@admin_router.post("/service-areas")
async def admin_create_service_area(area: Dict[str, Any]):
    """Create service area with full configuration."""
    doc = {
        "id": str(uuid.uuid4()),
        "name": area.get("name"),
        "city": area.get("city", ""),
        "province": area.get("province", "SK"),
        "geojson": area.get("geojson"),
        "is_active": area.get("is_active", True),
        # Fees & Taxes
        "platform_fee": area.get("platform_fee", 0),
        "city_fee": area.get("city_fee", 0),
        "airport_fee": area.get("airport_fee", 0),
        "is_airport": area.get("is_airport", False),
        "gst_rate": area.get("gst_rate", 5.0),
        "pst_rate": area.get("pst_rate", 6.0),
        "insurance_fee_percent": area.get("insurance_fee_percent", 2.0),
        # Cancellation fees (with driver/admin split)
        "rider_cancel_fee_before_driver": area.get("rider_cancel_fee_before_driver", 0),
        "rider_cancel_fee_after_arrival": area.get(
            "rider_cancel_fee_after_arrival", 4.50
        ),
        "cancel_fee_driver_share": area.get("cancel_fee_driver_share", 4.00),
        "cancel_fee_admin_share": area.get("cancel_fee_admin_share", 0.50),
        "rider_cancel_fee_after_start": area.get(
            "rider_cancel_fee_after_start", 0
        ),  # 0 = full fare
        "driver_cancel_fee": area.get("driver_cancel_fee", 0),
        "free_cancel_window_seconds": area.get("free_cancel_window_seconds", 120),
        # Required driver documents
        "required_documents": area.get(
            "required_documents",
            [
                {
                    "key": "drivers_license",
                    "label": "Driver's License",
                    "has_expiry": True,
                },
                {
                    "key": "vehicle_insurance",
                    "label": "Vehicle Insurance",
                    "has_expiry": True,
                },
                {
                    "key": "vehicle_registration",
                    "label": "Vehicle Registration",
                    "has_expiry": True,
                },
                {
                    "key": "background_check",
                    "label": "Background Check",
                    "has_expiry": True,
                },
                {
                    "key": "vehicle_inspection",
                    "label": "Vehicle Inspection",
                    "has_expiry": True,
                },
            ],
        ),
        # Vehicle type pricing
        "vehicle_pricing": area.get("vehicle_pricing", []),
        # Spinr Pass — which subscription plans are available here
        "subscription_plan_ids": area.get("subscription_plan_ids", []),
        "spinr_pass_enabled": area.get("spinr_pass_enabled", True),
        # Surge
        "surge_enabled": area.get("surge_enabled", False),
        "surge_multiplier": area.get("surge_multiplier", 1.0),
        # Operational
        "max_pickup_radius_km": area.get("max_pickup_radius_km", 5.0),
        "currency": area.get("currency", "CAD"),
        "created_at": datetime.utcnow().isoformat(),
    }
    row = await db.service_areas.insert_one(doc)
    return {"area_id": doc["id"]}


@admin_router.put("/service-areas/{area_id}")
async def admin_update_service_area(area_id: str, area: Dict[str, Any]):
    """Update service area — accepts any field."""
    # Accept all fields that were sent
    allowed = [
        "name",
        "city",
        "province",
        "geojson",
        "is_active",
        "platform_fee",
        "city_fee",
        "airport_fee",
        "is_airport",
        "gst_rate",
        "pst_rate",
        "insurance_fee_percent",
        "rider_cancel_fee_before_driver",
        "rider_cancel_fee_after_arrival",
        "cancel_fee_driver_share",
        "cancel_fee_admin_share",
        "rider_cancel_fee_after_start",
        "driver_cancel_fee",
        "free_cancel_window_seconds",
        "required_documents",
        "vehicle_pricing",
        "subscription_plan_ids",
        "spinr_pass_enabled",
        "surge_enabled",
        "surge_multiplier",
        "max_pickup_radius_km",
        "currency",
    ]
    update_payload = {k: v for k, v in area.items() if k in allowed and v is not None}

    if update_payload:
        update_payload["updated_at"] = datetime.utcnow().isoformat()
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
    user_ids = [d.get("user_id") for d in drivers if d.get("user_id")]
    users_map = {}
    for uid in user_ids:
        if uid and uid not in users_map:
            u = await db.users.find_one({"id": uid})
            users_map[uid] = u
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


@admin_router.get("/rides")
async def admin_get_rides(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
):
    """Get all rides with filters, enriched with rider_name and driver_name."""
    filters = {}
    if status:
        filters["status"] = status
    rides = await db.get_rows(
        "rides", filters, order="created_at", desc=True, limit=limit, offset=offset
    )
    rider_ids = list({r.get("rider_id") for r in rides if r.get("rider_id")})
    driver_ids = list({r.get("driver_id") for r in rides if r.get("driver_id")})
    users_map = {}
    for uid in rider_ids + driver_ids:
        if uid and uid not in users_map:
            u = await db.users.find_one({"id": uid})
            users_map[uid] = u
    drivers_map = {}
    for did in driver_ids:
        if did:
            dr = await db.drivers.find_one({"id": did})
            drivers_map[did] = dr
            if dr and dr.get("user_id") and dr["user_id"] not in users_map:
                users_map[dr["user_id"]] = await db.users.find_one(
                    {"id": dr["user_id"]}
                )
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
    return out


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

        await db.drivers.update_one(
            {"id": driver_id},
            {"$set": {"is_verified": req.verified}},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update driver {driver_id} verify flag: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update driver: {e}")
    return {"message": f"Driver {'verified' if req.verified else 'unverified'}"}


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
    """Get ride count stats for today, yesterday, this week, this month."""
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

    return {
        "today_count": today_count,
        "yesterday_count": yesterday_count,
        "this_week_count": this_week_count,
        "this_month_count": this_month_count,
        "week_start": week_start.strftime("%b %d"),
        "week_end": (week_end - timedelta(days=1)).strftime("%b %d"),
        "month_start": month_start.strftime("%b %d"),
        "month_end": (next_month - timedelta(days=1)).strftime("%b %d"),
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
        "driver_earnings": ride.get("driver_earnings", 0),
        "admin_earnings": ride.get("admin_earnings", 0),
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
    users_map = {}
    for uid in rider_ids + driver_ids:
        if uid and uid not in users_map:
            u = await db.users.find_one({"id": uid})
            users_map[uid] = u
    drivers_map = {}
    for did in driver_ids:
        if did:
            dr = await db.drivers.find_one({"id": did})
            drivers_map[did] = dr
            if dr and dr.get("user_id") and dr["user_id"] not in users_map:
                users_map[dr["user_id"]] = await db.users.find_one(
                    {"id": dr["user_id"]}
                )
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
    user_ids = [d.get("user_id") for d in drivers if d.get("user_id")]
    users_map = {}
    for uid in user_ids:
        if uid and uid not in users_map:
            users_map[uid] = await db.users.find_one({"id": uid})
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
    disputes = await db.get_rows("disputes", order="created_at", desc=True, limit=500)
    return disputes


@admin_router.get("/disputes/{dispute_id}")
async def admin_get_dispute_details(dispute_id: str):
    """Get detailed dispute information."""
    dispute = await db.disputes.find_one({"id": dispute_id})
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    # Get related ride information
    ride = await db.rides.find_one({"id": dispute.get("ride_id")})

    return {**dispute, "ride_details": ride}


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


# ---------- Support Tickets ----------


@admin_router.get("/tickets")
async def admin_get_tickets():
    """Get all support tickets."""
    tickets = await db.get_rows(
        "support_tickets", order="created_at", desc=True, limit=500
    )
    return tickets


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
        "service_area_id": area_id,
        "fee_type": fee.get("fee_type"),  # airport, toll, surge, etc.
        "amount": fee.get("amount", 0),
        "description": fee.get("description", ""),
        "is_active": fee.get("is_active", True),
        "created_at": datetime.utcnow().isoformat(),
    }
    row = await db.area_fees.insert_one(doc)
    return {"fee_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@admin_router.put("/areas/{area_id}/fees/{fee_id}")
async def admin_update_area_fee(area_id: str, fee_id: str, fee: Dict[str, Any]):
    """Update an area fee."""
    updates = {}
    if fee.get("fee_type") is not None:
        updates["fee_type"] = fee.get("fee_type")
    if fee.get("amount") is not None:
        updates["amount"] = fee.get("amount")
    if fee.get("description") is not None:
        updates["description"] = fee.get("description")
    if fee.get("is_active") is not None:
        updates["is_active"] = fee.get("is_active")

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
    tax = await db.area_taxes.find_one({"service_area_id": area_id})
    return tax or {"service_area_id": area_id, "tax_rate": 0, "tax_name": "Tax"}


@admin_router.put("/areas/{area_id}/tax")
async def admin_update_area_tax(area_id: str, tax: Dict[str, Any]):
    """Update tax configuration for a service area."""
    tax_doc = {
        "service_area_id": area_id,
        "tax_rate": tax.get("tax_rate", 0),
        "tax_name": tax.get("tax_name", "Tax"),
        "updated_at": datetime.utcnow().isoformat(),
    }

    existing = await db.area_taxes.find_one({"service_area_id": area_id})
    if existing:
        await db.area_taxes.update_one({"service_area_id": area_id}, {"$set": tax_doc})
    else:
        await db.area_taxes.insert_one(tax_doc)

    return {"message": "Area tax updated"}


@admin_router.get("/areas/{area_id}/vehicle-pricing")
async def admin_get_vehicle_pricing(area_id: str):
    """Get vehicle pricing configuration for a service area."""
    pricing = await db.get_rows(
        "vehicle_pricing", {"service_area_id": area_id}, order="created_at", limit=100
    )
    return pricing


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
