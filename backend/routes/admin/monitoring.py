# backend/routes/admin/monitoring.py
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

try:
    from ...db_supabase import _rows_from_res, run_sync
    from ...dependencies import get_admin_user
    from ...supabase_client import supabase
except ImportError:
    from db_supabase import _rows_from_res, run_sync
    from dependencies import get_admin_user
    from supabase_client import supabase

router = APIRouter(prefix="/admin/monitoring", tags=["Monitoring"])

ACTIVE_RIDE_STATUSES = ["searching", "driver_assigned", "driver_arrived", "in_progress"]
ON_RIDE_STATUSES = ["driver_assigned", "driver_arrived", "in_progress"]


@router.get("/drivers")
async def get_monitoring_drivers(current_admin: dict = Depends(get_admin_user)) -> List[Dict[str, Any]]:
    """Return all drivers with current location and status for the live map."""
    drivers_res = await run_sync(
        lambda: supabase.table("drivers")
        .select(
            "id, user_id, is_online, is_available, lat, lng, "
            "vehicle_make, vehicle_model, vehicle_color, license_plate, "
            "vehicle_type_id, rating, total_rides, service_area_id"
        )
        .execute()
    )
    drivers = _rows_from_res(drivers_res)
    if not drivers:
        return []

    user_ids = [d["user_id"] for d in drivers if d.get("user_id")]
    users_res = await run_sync(
        lambda: supabase.table("users")
        .select("id, first_name, last_name, phone, photo_url")
        .in_("id", user_ids)
        .execute()
    )
    users_by_id = {u["id"]: u for u in _rows_from_res(users_res)}

    driver_ids = [d["id"] for d in drivers]
    rides_res = await run_sync(
        lambda: supabase.table("rides")
        .select("id, driver_id")
        .in_("status", ON_RIDE_STATUSES)
        .in_("driver_id", driver_ids)
        .execute()
    )
    active_ride_by_driver = {r["driver_id"]: r["id"] for r in _rows_from_res(rides_res)}

    result = []
    for d in drivers:
        user = users_by_id.get(d.get("user_id", ""), {})
        first = user.get("first_name") or ""
        last = user.get("last_name") or ""
        result.append(
            {
                "id": d["id"],
                "name": f"{first} {last}".strip() or "Unknown Driver",
                "phone": user.get("phone", ""),
                "photo_url": user.get("photo_url"),
                "lat": d.get("lat"),
                "lng": d.get("lng"),
                "is_online": bool(d.get("is_online")),
                "is_available": bool(d.get("is_available")),
                "vehicle_make": d.get("vehicle_make"),
                "vehicle_model": d.get("vehicle_model"),
                "vehicle_color": d.get("vehicle_color"),
                "license_plate": d.get("license_plate"),
                "vehicle_type_id": d.get("vehicle_type_id"),
                "rating": d.get("rating"),
                "total_rides": d.get("total_rides") or 0,
                "active_ride_id": active_ride_by_driver.get(d["id"]),
                "service_area_id": d.get("service_area_id"),
            }
        )
    return result


@router.get("/rides")
async def get_monitoring_rides(current_admin: dict = Depends(get_admin_user)) -> List[Dict[str, Any]]:
    """Return all active rides with rider/driver info for the live map."""
    rides_res = await run_sync(
        lambda: supabase.table("rides")
        .select(
            "id, status, rider_id, driver_id, "
            "pickup_lat, pickup_lng, pickup_address, "
            "dropoff_lat, dropoff_lng, dropoff_address, "
            "driver_current_lat, driver_current_lng, "
            "total_fare, distance_km, created_at, corporate_account_id"
        )
        .in_("status", ACTIVE_RIDE_STATUSES)
        .execute()
    )
    rides = _rows_from_res(rides_res)
    if not rides:
        return []

    rider_ids = list({r["rider_id"] for r in rides if r.get("rider_id")})
    driver_ids = list({r["driver_id"] for r in rides if r.get("driver_id")})

    riders_res = await run_sync(
        lambda: supabase.table("users")
        .select("id, first_name, last_name, phone, photo_url")
        .in_("id", rider_ids)
        .execute()
    )
    riders_by_id = {u["id"]: u for u in _rows_from_res(riders_res)}

    drivers_map_res = await run_sync(
        lambda: supabase.table("drivers")
        .select("id, user_id, lat, lng")
        .in_("id", driver_ids)
        .execute()
    )
    drivers_rows = _rows_from_res(drivers_map_res)
    drivers_by_id = {d["id"]: d for d in drivers_rows}

    driver_user_ids = [d["user_id"] for d in drivers_rows if d.get("user_id")]
    driver_users_res = await run_sync(
        lambda: supabase.table("users")
        .select("id, first_name, last_name, phone")
        .in_("id", driver_user_ids)
        .execute()
    )
    driver_users_by_id = {u["id"]: u for u in _rows_from_res(driver_users_res)}

    result = []
    for r in rides:
        rider = riders_by_id.get(r.get("rider_id", ""), {})
        drv_row = drivers_by_id.get(r.get("driver_id", ""), {})
        drv_user = driver_users_by_id.get(drv_row.get("user_id", ""), {})
        created = r.get("created_at", "")
        result.append(
            {
                "id": r["id"],
                "status": r["status"],
                "rider_id": r.get("rider_id"),
                "rider_name": f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() or "Unknown",
                "rider_phone": rider.get("phone"),
                "rider_photo": rider.get("photo_url"),
                "driver_id": r.get("driver_id"),
                "driver_name": f"{drv_user.get('first_name', '')} {drv_user.get('last_name', '')}".strip() or None,
                "driver_phone": drv_user.get("phone"),
                "pickup_lat": r.get("pickup_lat"),
                "pickup_lng": r.get("pickup_lng"),
                "pickup_address": r.get("pickup_address"),
                "dropoff_lat": r.get("dropoff_lat"),
                "dropoff_lng": r.get("dropoff_lng"),
                "dropoff_address": r.get("dropoff_address"),
                "driver_lat": r.get("driver_current_lat") or drv_row.get("lat"),
                "driver_lng": r.get("driver_current_lng") or drv_row.get("lng"),
                "total_fare": r.get("total_fare"),
                "distance_km": r.get("distance_km"),
                "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
                "is_corporate": bool(r.get("corporate_account_id")),
            }
        )
    return result
