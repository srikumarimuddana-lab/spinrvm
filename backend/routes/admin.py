from fastapi import APIRouter, Depends, Query
from typing import Dict, Any, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta

try:
    from ..dependencies import get_current_user, get_admin_user
    from ..db import db
except ImportError:
    from dependencies import get_current_user, get_admin_user
    from db import db

admin_router = APIRouter(prefix="/admin", tags=["Admin"])


class DriverVerifyRequest(BaseModel):
    verified: bool


@admin_router.get("/settings")
async def admin_get_settings():
    """Get all settings"""
    settings = await db.fetchall("SELECT * FROM settings")
    return {s["key"]: s["value"] for s in settings}


@admin_router.put("/settings")
async def admin_update_settings(settings: Dict[str, Any]):
    """Update settings"""
    for key, value in settings.items():
        await db.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (%s, %s, NOW())
               ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = NOW()""",
            (key, value, value)
        )
    return {"message": "Settings updated"}


@admin_router.get("/service-areas")
async def admin_get_service_areas():
    """Get all service areas"""
    areas = await db.fetchall("SELECT * FROM service_areas ORDER BY name")
    return areas


@admin_router.post("/service-areas")
async def admin_create_service_area(area: Dict[str, Any]):
    """Create service area"""
    area_id = await db.execute(
        """INSERT INTO service_areas (name, geojson, is_active, created_at)
           VALUES (%s, %s, %s, NOW())""",
        (area.get("name"), area.get("geojson"), area.get("is_active", True))
    )
    return {"area_id": str(area_id)}


@admin_router.put("/service-areas/{area_id}")
async def admin_update_service_area(area_id: str, area: Dict[str, Any]):
    """Update service area"""
    await db.execute(
        """UPDATE service_areas SET name = %s, geojson = %s, is_active = %s
           WHERE id = %s""",
        (area.get("name"), area.get("geojson"), area.get("is_active"), area_id)
    )
    return {"message": "Service area updated"}


@admin_router.delete("/service-areas/{area_id}")
async def admin_delete_service_area(area_id: str):
    """Delete service area"""
    await db.execute("DELETE FROM service_areas WHERE id = %s", (area_id,))
    return {"message": "Service area deleted"}


@admin_router.get("/vehicle-types")
async def admin_get_vehicle_types():
    """Get all vehicle types"""
    types = await db.fetchall("SELECT * FROM vehicle_types ORDER BY display_order")
    return types


@admin_router.post("/vehicle-types")
async def admin_create_vehicle_type(vtype: Dict[str, Any]):
    """Create vehicle type"""
    type_id = await db.execute(
        """INSERT INTO vehicle_types (name, description, icon, base_fare, price_per_km, price_per_minute, is_active, display_order)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (vtype.get("name"), vtype.get("description"), vtype.get("icon"),
         vtype.get("base_fare"), vtype.get("price_per_km"), vtype.get("price_per_minute"),
         vtype.get("is_active", True), vtype.get("display_order", 1))
    )
    return {"type_id": str(type_id)}


@admin_router.put("/vehicle-types/{type_id}")
async def admin_update_vehicle_type(type_id: str, vtype: Dict[str, Any]):
    """Update vehicle type"""
    await db.execute(
        """UPDATE vehicle_types SET name = %s, description = %s, icon = %s,
           base_fare = %s, price_per_km = %s, price_per_minute = %s, is_active = %s
           WHERE id = %s""",
        (vtype.get("name"), vtype.get("description"), vtype.get("icon"),
         vtype.get("base_fare"), vtype.get("price_per_km"), vtype.get("price_per_minute"),
         vtype.get("is_active"), type_id)
    )
    return {"message": "Vehicle type updated"}


@admin_router.delete("/vehicle-types/{type_id}")
async def admin_delete_vehicle_type(type_id: str):
    """Delete vehicle type"""
    await db.execute("DELETE FROM vehicle_types WHERE id = %s", (type_id,))
    return {"message": "Vehicle type deleted"}


@admin_router.get("/fare-configs")
async def admin_get_fare_configs():
    """Get all fare configurations"""
    configs = await db.fetchall("SELECT * FROM fare_configurations ORDER BY created_at DESC")
    return configs


@admin_router.post("/fare-configs")
async def admin_create_fare_config(config: Dict[str, Any]):
    """Create fare configuration"""
    config_id = await db.execute(
        """INSERT INTO fare_configurations (name, base_fare, price_per_km, price_per_minute, area_geojson, is_active)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (config.get("name"), config.get("base_fare"), config.get("price_per_km"),
         config.get("price_per_minute"), config.get("area_geojson"), config.get("is_active", True))
    )
    return {"config_id": str(config_id)}


@admin_router.put("/fare-configs/{config_id}")
async def admin_update_fare_config(config_id: str, config: Dict[str, Any]):
    """Update fare configuration"""
    await db.execute(
        """UPDATE fare_configurations SET name = %s, base_fare = %s, price_per_km = %s,
           price_per_minute = %s, area_geojson = %s, is_active = %s
           WHERE id = %s""",
        (config.get("name"), config.get("base_fare"), config.get("price_per_km"),
         config.get("price_per_minute"), config.get("area_geojson"), config.get("is_active"), config_id)
    )
    return {"message": "Fare configuration updated"}


@admin_router.delete("/fare-configs/{config_id}")
async def admin_delete_fare_config(config_id: str):
    """Delete fare configuration"""
    await db.execute("DELETE FROM fare_configurations WHERE id = %s", (config_id,))
    return {"message": "Fare configuration deleted"}


@admin_router.get("/drivers")
async def admin_get_drivers(
    limit: int = 50,
    offset: int = 0,
    is_verified: Optional[bool] = None,
    is_online: Optional[bool] = None
):
    """Get all drivers with filters"""
    query = "SELECT d.*, u.name, u.email, u.phone FROM drivers d JOIN users u ON d.user_id = u.id WHERE 1=1"
    params = []
    
    if is_verified is not None:
        query += " AND d.is_verified = %s"
        params.append(is_verified)
    if is_online is not None:
        query += " AND d.is_online = %s"
        params.append(is_online)
    
    query += " ORDER BY d.created_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    
    drivers = await db.fetchall(query, tuple(params))
    return drivers


@admin_router.get("/rides")
async def admin_get_rides(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None
):
    """Get all rides with filters"""
    query = """SELECT r.*, u.name as rider_name, d.name as driver_name 
               FROM rides r 
               JOIN users u ON r.rider_id = u.id 
               LEFT JOIN drivers dr ON r.driver_id = dr.id
               LEFT JOIN users d ON dr.user_id = d.id
               WHERE 1=1"""
    params = []
    
    if status:
        query += " AND r.status = %s"
        params.append(status)
    
    query += " ORDER BY r.created_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    
    rides = await db.fetchall(query, tuple(params))
    return rides


@admin_router.post("/drivers/{driver_id}/verify")
async def admin_verify_driver(driver_id: str, req: DriverVerifyRequest):
    """Verify or unverify a driver"""
    await db.execute(
        "UPDATE drivers SET is_verified = %s, verified_at = NOW() WHERE id = %s",
        (req.verified, driver_id)
    )
    return {"message": f"Driver {'verified' if req.verified else 'unverified'}"}


@admin_router.get("/stats")
async def admin_get_stats():
    """Get admin dashboard statistics"""
    # Total drivers
    total_drivers = await db.fetchone("SELECT COUNT(*) as count FROM drivers")
    
    # Active drivers (online)
    active_drivers = await db.fetchone(
        "SELECT COUNT(*) as count FROM drivers WHERE is_online = true"
    )
    
    # Total rides
    total_rides = await db.fetchone("SELECT COUNT(*) as count FROM rides")
    
    # Rides today
    rides_today = await db.fetchone(
        """SELECT COUNT(*) as count FROM rides 
           WHERE created_at >= CURRENT_DATE"""
    )
    
    # Revenue today
    revenue_today = await db.fetchone(
        """SELECT COALESCE(SUM(fare), 0) as total FROM rides 
           WHERE status = 'completed' AND completed_at >= CURRENT_DATE"""
    )
    
    # Revenue this month
    revenue_month = await db.fetchone(
        """SELECT COALESCE(SUM(fare), 0) as total FROM rides 
           WHERE status = 'completed' AND completed_at >= DATE_TRUNC('month', CURRENT_DATE)"""
    )
    
    # Pending driver applications
    pending_applications = await db.fetchone(
        "SELECT COUNT(*) as count FROM drivers WHERE is_verified = false"
    )
    
    return {
        "total_drivers": total_drivers["count"],
        "active_drivers": active_drivers["count"],
        "total_rides": total_rides["count"],
        "rides_today": rides_today["count"],
        "revenue_today": float(revenue_today["total"]),
        "revenue_month": float(revenue_month["total"]),
        "pending_applications": pending_applications["count"]
    }


@admin_router.get("/rides/{ride_id}/details")
async def admin_get_ride_details(ride_id: str):
    """Get detailed ride information"""
    ride = await db.fetchone(
        """SELECT r.*, 
           u1.name as rider_name, u1.phone as rider_phone, u1.email as rider_email,
           d.name as driver_name, d.phone as driver_phone,
           vt.name as vehicle_type
           FROM rides r
           JOIN users u1 ON r.rider_id = u1.id
           LEFT JOIN drivers dr ON r.driver_id = dr.id
           LEFT JOIN users d ON dr.user_id = d.id
           LEFT JOIN vehicle_types vt ON r.vehicle_type_id = vt.id
           WHERE r.id = %s""",
        (ride_id,)
    )
    return ride


@admin_router.get("/drivers/{driver_id}/rides")
async def admin_get_driver_rides(driver_id: str):
    """Get all rides for a specific driver"""
    rides = await db.fetchall(
        """SELECT * FROM rides WHERE driver_id = %s ORDER BY created_at DESC""",
        (driver_id,)
    )
    return rides


@admin_router.get("/earnings")
async def admin_get_earnings(
    period: str = Query('month')
):
    """Get earnings statistics"""
    if period == 'day':
        start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'week':
        start_date = datetime.now() - timedelta(days=7)
    else:
        start_date = datetime.now() - timedelta(days=30)
    
    stats = await db.fetchone(
        """SELECT 
           COALESCE(SUM(fare), 0) as total_revenue,
           COUNT(*) as total_rides,
           COALESCE(SUM(driver_earnings), 0) as driver_earnings,
           COALESCE(SUM(platform_fee), 0) as platform_fees
           FROM rides 
           WHERE status = 'completed' AND completed_at >= %s""",
        (start_date,)
    )
    
    return {
        "period": period,
        "total_revenue": float(stats["total_revenue"]),
        "total_rides": stats["total_rides"],
        "driver_earnings": float(stats["driver_earnings"]),
        "platform_fees": float(stats["platform_fees"])
    }


@admin_router.get("/export/rides")
async def admin_export_rides(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Export rides data as CSV"""
    # In production, generate actual CSV
    rides = await db.fetchall(
        """SELECT r.id, r.pickup_address, r.dropoff_address, r.fare, r.status, r.created_at,
           u.name as rider_name, d.name as driver_name
           FROM rides r
           JOIN users u ON r.rider_id = u.id
           LEFT JOIN drivers dr ON r.driver_id = dr.id
           LEFT JOIN users d ON dr.user_id = d.id
           ORDER BY r.created_at DESC
           LIMIT 1000"""
    )
    return {"rides": rides, "count": len(rides)}


@admin_router.get("/export/drivers")
async def admin_export_drivers():
    """Export drivers data as CSV"""
    drivers = await db.fetchall(
        """SELECT d.id, u.name, u.email, u.phone, d.vehicle_make, d.vehicle_model,
           d.license_plate, d.is_verified, d.is_online, d.total_rides, d.created_at
           FROM drivers d
           JOIN users u ON d.user_id = u.id
           ORDER BY d.created_at DESC
           LIMIT 1000"""
    )
    return {"drivers": drivers, "count": len(drivers)}


@admin_router.get("/drivers/{driver_id}/location-trail")
async def admin_get_driver_location_trail(
    driver_id: str,
    hours: int = Query(24)
):
    """Get driver's location history"""
    locations = await db.fetchall(
        """SELECT lat, lng, timestamp FROM driver_locations
           WHERE driver_id = %s AND timestamp >= NOW() - INTERVAL '%s hours'
           ORDER BY timestamp ASC""",
        (driver_id, hours)
    )
    return locations
