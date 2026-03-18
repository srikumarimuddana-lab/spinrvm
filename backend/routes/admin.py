from fastapi import APIRouter, Depends, Query  # type: ignore
from typing import Dict, Any, Optional
from pydantic import BaseModel  # type: ignore
from datetime import datetime, timedelta

try:
    from ..dependencies import get_current_user, get_admin_user  # type: ignore
    from ..db import db  # type: ignore
    from ..settings_loader import get_app_settings  # type: ignore
except ImportError:
    from dependencies import get_current_user, get_admin_user  # type: ignore
    from db import db  # type: ignore
    from settings_loader import get_app_settings  # type: ignore

admin_router = APIRouter(prefix="/admin", tags=["Admin"])


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
    types = await db.get_rows("vehicle_types", order="display_order", limit=100)
    if not types and db.vehicle_types.name:
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
        "display_order": vtype.get("display_order", 1),
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
    """Get earnings statistics (schema: total_fare, ride_completed_at, admin_earnings).
    
    Note: For large datasets, consider creating a PostgreSQL aggregate function in Supabase
    and calling it via db.rpc('function_name', {...}) for better performance.
    """
    query = ""
    params = {}
    
    if period == "day":
        query = "SELECT SUM(total_fare) as total_revenue, SUM(driver_earnings) as driver_earnings, SUM(admin_earnings) as platform_fees FROM rides WHERE status = 'completed' AND ride_completed_at >= $1"
        params = {"start": datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()}
    elif period == "week":
        query = "SELECT SUM(total_fare) as total_revenue, SUM(driver_earnings) as driver_earnings, SUM(admin_earnings) as platform_fees FROM rides WHERE status = 'completed' AND ride_completed_at >= $1"
        params = {"start": (datetime.utcnow() - timedelta(days=7)).isoformat()}
    else:  # month
        query = "SELECT SUM(total_fare) as total_revenue, SUM(driver_earnings) as driver_earnings, SUM(admin_earnings) as platform_fees FROM rides WHERE status = 'completed' AND ride_completed_at >= $1"
        params = {"start": (datetime.utcnow() - timedelta(days=30)).isoformat()}
    
    result = await db.fetchone(query, params)
    
    return {
        "period": period,
        "total_revenue": float(result.get("total_revenue") or 0),
        "total_rides": 0,  # This would require a separate count query
        "driver_earnings": float(result.get("driver_earnings") or 0),
        "platform_fees": float(result.get("platform_fees") or 0),
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


# ---------- Corporate Accounts (moved to dedicated routes) ----------

# Note: Corporate accounts functionality has been moved to dedicated routes
# in /api/admin/corporate-accounts to ensure consistency and proper validation
# See routes/corporate_accounts.py for implementation

