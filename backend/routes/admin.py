from fastapi import APIRouter, Depends, Query, HTTPException, Header  # type: ignore
from typing import Dict, Any, Optional
from pydantic import BaseModel  # type: ignore
from datetime import datetime, timedelta
import jwt

try:
    from ..dependencies import get_current_user, get_admin_user  # type: ignore
    from ..db import db  # type: ignore
    from ..settings_loader import get_app_settings  # type: ignore
    from ..core.config import settings
except ImportError:
    from dependencies import get_current_user, get_admin_user  # type: ignore
    from db import db  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from core.config import settings

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
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.ALGORITHM])
        user_id = payload.get("user_id")
        role = payload.get("role")
        email = payload.get("email")
        phone = payload.get("phone")
        
        if not user_id:
            return SessionResponse(user=None, authenticated=False)
        
        # Return authenticated user info
        return SessionResponse(
            user={
                "id": user_id,
                "email": email,
                "phone": phone,
                "role": role or "admin"
            },
            authenticated=True
        )
    except jwt.ExpiredSignatureError:
        return SessionResponse(user=None, authenticated=False)
    except jwt.InvalidTokenError:
        return SessionResponse(user=None, authenticated=False)


@admin_auth_router.post("/login")
async def admin_login(request: LoginRequest):
    """Admin login endpoint"""
    # Validate credentials from settings
    if request.email == settings.ADMIN_EMAIL and request.password == settings.ADMIN_PASSWORD:
        # Include user_id claim for get_current_user to work properly
        token = jwt.encode({
            "user_id": "admin-001",
            "email": request.email,
            "role": "admin",
            "phone": request.email  # Use email as phone for admin users
        }, settings.JWT_SECRET, algorithm=settings.ALGORITHM)
        return {
            "user": {
                "id": "admin-001",
                "email": request.email,
                "role": "admin"
            },
            "token": token
        }
    else:
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
    
    payload = {"id": "app_settings", **settings, "updated_at": datetime.utcnow().isoformat()}
    
    if existing:
        # Update existing row - build update dict without 'id'
        update_payload = {k: v for k, v in payload.items() if k != 'id'}
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
    """Create service area."""
    doc = {
        "name": area.get("name"),
        "geojson": area.get("geojson"),
        "is_active": area.get("is_active", True),
        "created_at": datetime.utcnow().isoformat(),
    }
    row = await db.service_areas.insert_one(doc)
    return {"area_id": str(row.get("id") if isinstance(row, dict) else "")}


@admin_router.put("/service-areas/{area_id}")
async def admin_update_service_area(area_id: str, area: Dict[str, Any]):
    """Update service area."""
    update_payload = {}
    if area.get("name") is not None:
        update_payload["name"] = area.get("name")
    if area.get("geojson") is not None:
        update_payload["geojson"] = area.get("geojson")
    if area.get("is_active") is not None:
        update_payload["is_active"] = area.get("is_active")
    
    if update_payload:
        await db.service_areas.update_one(
            {"id": area_id},
            {"$set": update_payload}
        )
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
        await db.vehicle_types.update_one(
            {"id": type_id},
            {"$set": update_payload}
        )
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
    configs = await db.get_rows("fare_configs", order="created_at", desc=True, limit=200)
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
        "per_minute_rate": config.get("price_per_minute", config.get("per_minute_rate", 0)),
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
        "per_minute_rate": config.get("price_per_minute", config.get("per_minute_rate")),
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
    drivers = await db.get_rows("drivers", filters, order="created_at", desc=True, limit=limit, offset=offset)
    user_ids = [d.get("user_id") for d in drivers if d.get("user_id")]
    users_map = {}
    for uid in user_ids:
        if uid and uid not in users_map:
            u = await db.users.find_one({"id": uid})
            users_map[uid] = u
    out = []
    for d in drivers:
        u = users_map.get(d.get("user_id"))
        out.append({
            **d,
            "name": _user_display_name(u) or d.get("name"),
            "email": u.get("email") if u else None,
            "phone": u.get("phone") if u else d.get("phone"),
        })
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
    rides = await db.get_rows("rides", filters, order="created_at", desc=True, limit=limit, offset=offset)
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
                users_map[dr["user_id"]] = await db.users.find_one({"id": dr["user_id"]})
    out = []
    for r in rides:
        rider = users_map.get(r.get("rider_id"))
        driver = drivers_map.get(r.get("driver_id"))
        driver_user = users_map.get(driver.get("user_id")) if driver else None
        out.append({
            **r,
            "rider_name": _user_display_name(rider),
            "driver_name": _user_display_name(driver_user) if driver_user else (driver.get("name") if driver else None),
        })
    return out


@admin_router.post("/drivers/{driver_id}/verify")
async def admin_verify_driver(driver_id: str, req: DriverVerifyRequest):
    """Verify or unverify a driver."""
    await db.drivers.update_one(
        {"id": driver_id},
        {"$set": {"is_verified": req.verified, "verified_at": datetime.utcnow().isoformat()}},
    )
    return {"message": f"Driver {'verified' if req.verified else 'unverified'}"}


# ---------- Stats (count_documents + sum from rides) ----------

@admin_router.get("/stats")
async def admin_get_stats():
    """Get admin dashboard statistics."""
    total_drivers = await db.drivers.count_documents({})
    active_drivers = await db.drivers.count_documents({"is_online": True})
    total_rides = await db.rides.count_documents({})
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    rides_today = await db.rides.count_documents({"created_at": {"$gte": today_start}})
    completed_today = await db.get_rows(
        "rides",
        {"status": "completed", "ride_completed_at": {"$gte": today_start}},
        limit=10000,
    )
    revenue_today = sum(float(r.get("total_fare") or 0) for r in completed_today)
    month_start = (datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)).isoformat()
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


@admin_router.get("/rides/{ride_id}/details")
async def admin_get_ride_details(ride_id: str):
    """Get detailed ride information with rider, driver, vehicle type."""
    ride = await db.rides.find_one({"id": ride_id})
    if not ride:
        return None
    rider = await db.users.find_one({"id": ride.get("rider_id")}) if ride.get("rider_id") else None
    driver = await db.drivers.find_one({"id": ride.get("driver_id")}) if ride.get("driver_id") else None
    driver_user = await db.users.find_one({"id": driver["user_id"]}) if driver and driver.get("user_id") else None
    vt = await db.vehicle_types.find_one({"id": ride.get("vehicle_type_id")}) if ride.get("vehicle_type_id") else None
    return {
        **ride,
        "rider_name": _user_display_name(rider),
        "rider_phone": rider.get("phone") if isinstance(rider, dict) else None,
        "rider_email": rider.get("email") if isinstance(rider, dict) else None,
        "driver_name": _user_display_name(driver_user) if driver_user else (driver.get("name") if isinstance(driver, dict) else None),
        "driver_phone": driver_user.get("phone") if isinstance(driver_user, dict) else (driver.get("phone") if isinstance(driver, dict) else None),
        "vehicle_type": vt.get("name") if isinstance(vt, dict) else None,
    }


@admin_router.get("/drivers/{driver_id}/rides")
async def admin_get_driver_rides(driver_id: str):
    """Get all rides for a specific driver."""
    rides = await db.get_rows("rides", {"driver_id": driver_id}, order="created_at", desc=True, limit=500)
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
        limit=10000
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
                users_map[dr["user_id"]] = await db.users.find_one({"id": dr["user_id"]})
    out = []
    for r in rides:
        rider = users_map.get(r.get("rider_id"))
        driver = drivers_map.get(r.get("driver_id"))
        driver_user = users_map.get(driver.get("user_id")) if driver else None
        out.append({
            "id": r.get("id"),
            "pickup_address": r.get("pickup_address"),
            "dropoff_address": r.get("dropoff_address"),
            "fare": r.get("total_fare"),
            "status": r.get("status"),
            "created_at": r.get("created_at"),
            "rider_name": _user_display_name(rider),
            "driver_name": _user_display_name(driver_user) if driver_user else (driver.get("name") if driver else None),
        })
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
        out.append({
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
        })
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
    
    users = await db.get_rows("users", filters, order="created_at", desc=True, limit=limit, offset=offset)
    return users


@admin_router.get("/users/{user_id}")
async def admin_get_user_details(user_id: str):
    """Get detailed user information."""
    user = await db.users.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get user's recent rides
    rides = await db.get_rows("rides", {"rider_id": user_id}, order="created_at", desc=True, limit=10)
    
    return {
        **user,
        "total_rides": await db.rides.count_documents({"rider_id": user_id}),
        "recent_rides": rides
    }


@admin_router.put("/users/{user_id}/status")
async def admin_update_user_status(user_id: str, status_data: Dict[str, Any]):
    """Update user status (e.g., suspend, activate)."""
    valid_status = ["active", "suspended", "banned"]
    new_status = status_data.get("status")
    
    if new_status not in valid_status:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_status}")
    
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"status": new_status, "updated_at": datetime.utcnow().isoformat()}}
    )
    return {"message": f"User status updated to {new_status}"}


# ---------- Promotions (Discount Codes) ----------

@admin_router.get("/promotions")
async def admin_get_promotions():
    """Get all promotions/discount codes."""
    promotions = await db.get_rows("promotions", order="created_at", desc=True, limit=500)
    return promotions


@admin_router.post("/promotions")
async def admin_create_promotion(promotion: Dict[str, Any]):
    """Create a new promotion/discount code."""
    doc = {
        "code": (promotion.get("code") or "").strip().upper(),
        "description": promotion.get("description", ""),
        "promo_type": promotion.get("promo_type", "discount"),
        "discount_type": promotion.get("discount_type", "flat"),
        "discount_value": promotion.get("discount_value", 0),
        "max_discount": promotion.get("max_discount"),
        "max_uses": promotion.get("max_uses", 100),
        "max_uses_per_user": promotion.get("max_uses_per_user", 1),
        "uses": 0,
        "valid_from": promotion.get("valid_from", datetime.utcnow().isoformat()),
        "expiry_date": promotion.get("expiry_date"),
        "min_ride_fare": promotion.get("min_ride_fare", 0),
        "first_ride_only": promotion.get("first_ride_only", False),
        "new_user_days": promotion.get("new_user_days", 0),
        "applicable_areas": promotion.get("applicable_areas", []),
        "applicable_vehicles": promotion.get("applicable_vehicles", []),
        "user_segments": promotion.get("user_segments", []),
        "total_budget": promotion.get("total_budget", 0),
        "budget_used": 0,
        "valid_days": promotion.get("valid_days", []),
        "valid_hours_start": promotion.get("valid_hours_start"),
        "valid_hours_end": promotion.get("valid_hours_end"),
        "referrer_user_id": promotion.get("referrer_user_id"),
        "referrer_reward": promotion.get("referrer_reward", 0),
        "is_active": promotion.get("is_active", True),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    row = await db.promotions.insert_one(doc)
    return {"promotion_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@admin_router.put("/promotions/{promotion_id}")
async def admin_update_promotion(promotion_id: str, promotion: Dict[str, Any]):
    """Update a promotion."""
    allowed_fields = [
        "code", "description", "promo_type", "discount_type", "discount_value",
        "max_discount", "max_uses", "max_uses_per_user", "valid_from", "expiry_date",
        "min_ride_fare", "first_ride_only", "new_user_days", "applicable_areas",
        "applicable_vehicles", "user_segments", "total_budget", "valid_days",
        "valid_hours_start", "valid_hours_end", "referrer_reward", "is_active",
    ]
    updates = {k: v for k, v in promotion.items() if k in allowed_fields and v is not None}

    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
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
    
    return {
        **dispute,
        "ride_details": ride
    }


@admin_router.put("/disputes/{dispute_id}/resolve")
async def admin_resolve_dispute(dispute_id: str, resolution: Dict[str, Any]):
    """Resolve a dispute."""
    resolution_data = {
        "resolution_status": resolution.get("status"),  # resolved, rejected, pending
        "resolution_notes": resolution.get("notes", ""),
        "resolved_at": datetime.utcnow().isoformat(),
        "resolved_by": resolution.get("resolved_by", "admin")
    }
    
    await db.disputes.update_one(
        {"id": dispute_id},
        {"$set": resolution_data}
    )
    return {"message": "Dispute resolved"}


# ---------- Support Tickets ----------

@admin_router.get("/tickets")
async def admin_get_tickets():
    """Get all support tickets."""
    tickets = await db.get_rows("support_tickets", order="created_at", desc=True, limit=500)
    return tickets


@admin_router.get("/tickets/{ticket_id}")
async def admin_get_ticket_details(ticket_id: str):
    """Get detailed ticket information."""
    ticket = await db.support_tickets.find_one({"id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    # Get ticket messages
    messages = await db.get_rows("support_messages", {"ticket_id": ticket_id}, order="created_at", limit=100)
    
    return {
        **ticket,
        "messages": messages
    }


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
            {"$set": {"status": reply.get("status"), "updated_at": datetime.utcnow().isoformat()}}
        )
    
    return {"message": "Reply sent"}


@admin_router.post("/tickets/{ticket_id}/close")
async def admin_close_ticket(ticket_id: str):
    """Close a support ticket."""
    await db.support_tickets.update_one(
        {"id": ticket_id},
        {"$set": {"status": "closed", "closed_at": datetime.utcnow().isoformat()}}
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
    """Send a notification to a specific user."""
    # This would integrate with your notification service
    # For now, just log the notification
    notification_doc = {
        "user_id": notification.get("user_id"),
        "title": notification.get("title"),
        "body": notification.get("body"),
        "type": notification.get("type", "general"),
        "sent_at": datetime.utcnow().isoformat(),
        "status": "sent"
    }
    
    await db.notifications.insert_one(notification_doc)
    return {"message": "Notification sent"}


# ---------- Area Management (Pricing, Tax, Vehicle Pricing) ----------

@admin_router.get("/areas/{area_id}/fees")
async def admin_get_area_fees(area_id: str):
    """Get all fees for a service area."""
    fees = await db.get_rows("area_fees", {"service_area_id": area_id}, order="created_at", limit=100)
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
    pricing = await db.get_rows("vehicle_pricing", {"service_area_id": area_id}, order="created_at", limit=100)
    return pricing


# ---------- Driver Area Assignment ----------

@admin_router.put("/drivers/{driver_id}/area")
async def admin_assign_driver_area(driver_id: str, service_area_id: str):
    """Assign a driver to a specific service area."""
    await db.drivers.update_one(
        {"id": driver_id},
        {"$set": {
            "service_area_id": service_area_id,
            "updated_at": datetime.utcnow().isoformat()
        }}
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
        await db.surge_pricing.update_one({"service_area_id": area_id}, {"$set": surge_doc})
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
    return [{"lat": loc.get("lat"), "lng": loc.get("lng"), "timestamp": loc.get("timestamp")} for loc in locations]


# ---------- Document Requirements ----------

@admin_router.get("/documents/requirements")
async def admin_get_document_requirements():
    """Get all document requirements."""
    requirements = await db.get_rows("document_requirements", order="created_at", limit=100)
    return requirements or []


@admin_router.post("/documents/requirements")
async def admin_create_document_requirement(requirement: Dict[str, Any]):
    """Create a new document requirement."""
    doc = {
        "name": requirement.get("name"),
        "description": requirement.get("description", ""),
        "document_type": requirement.get("document_type"),
        "is_required": requirement.get("is_required", True),
        "applicable_to": requirement.get("applicable_to", "driver"),  # driver, rider, vehicle
        "created_at": datetime.utcnow().isoformat(),
    }
    row = await db.document_requirements.insert_one(doc)
    return {"requirement_id": str(row.get("id") if row and isinstance(row, dict) else "")}


@admin_router.put("/documents/requirements/{requirement_id}")
async def admin_update_document_requirement(requirement_id: str, requirement: Dict[str, Any]):
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
        await db.document_requirements.update_one({"id": requirement_id}, {"$set": updates})
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
        limit=100
    )
    return documents or []


@admin_router.post("/documents/{document_id}/review")
async def admin_review_driver_document(
    document_id: str,
    review_data: Dict[str, Any]
):
    """Review and approve/reject a driver document."""
    status = review_data.get("status")
    rejection_reason = review_data.get("rejection_reason")
    
    if status not in ["approved", "rejected", "pending"]:
        raise HTTPException(status_code=400, detail="Invalid status")
    
    updates = {
        "status": status,
        "reviewed_at": datetime.utcnow().isoformat(),
    }
    if rejection_reason:
        updates["rejection_reason"] = rejection_reason
    
    await db.driver_documents.update_one(
        {"id": document_id},
        {"$set": updates}
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

    rides = await db.get_rows("rides", query_filters, order="created_at", desc=True, limit=10000)

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
        await db.settings.update_one({"id": _HEATMAP_SETTINGS_ID}, {"$set": update_fields})
    else:
        await db.settings.insert_one(payload)

    return {"message": "Heat map settings updated"}


# ---------- Corporate Accounts (moved to dedicated routes) ----------

# Note: Corporate accounts functionality has been moved to dedicated routes
# in /api/admin/corporate-accounts to ensure consistency and proper validation
# See routes/corporate_accounts.py for implementation

