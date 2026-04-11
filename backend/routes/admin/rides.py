import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

try:
    from ... import db_supabase
    from ...db import db
    from ...dependencies import get_admin_user
    from ...settings_loader import get_app_settings
except ImportError:
    import db_supabase
    from db import db
    from dependencies import get_admin_user
    from settings_loader import get_app_settings

from .drivers import _batch_fetch_drivers_and_users, _user_display_name

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Rides ----------


@router.get("/rides")
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

    rides = await db.get_rows("rides", filters, order="created_at", desc=True, limit=limit, offset=offset)
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


# ---------- Stats ----------


@router.get("/stats")
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


@router.get("/rides/stats")
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

    today_count = await db_supabase.get_ride_count_by_date_range(today_start.isoformat(), now.isoformat())
    yesterday_count = await db_supabase.get_ride_count_by_date_range(
        yesterday_start.isoformat(), today_start.isoformat()
    )
    this_week_count = await db_supabase.get_ride_count_by_date_range(week_start.isoformat(), week_end.isoformat())
    this_month_count = await db_supabase.get_ride_count_by_date_range(month_start.isoformat(), next_month.isoformat())

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
        count = await db_supabase.get_ride_count_by_date_range(day_start.isoformat(), day_end.isoformat())
        daily_chart.append(
            {
                "date": day_start.strftime("%b %d"),
                "rides": count,
            }
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
        "today_revenue": round(total_revenue, 2),
        "today_tips": round(total_tips, 2),
        "today_completed": completed_count,
        "month_revenue": round(month_revenue, 2),
        "daily_chart": daily_chart,
    }


@router.get("/rides/{ride_id}/details")
async def admin_get_ride_details(ride_id: str):
    """Get detailed ride information with rider, driver, flags, complaints, lost items, location trail."""
    ride = await db_supabase.get_ride_details_enriched(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    return ride


@router.get("/rides/{ride_id}/location-trail")
async def admin_get_ride_location_trail(ride_id: str):
    """Get driver location trail for a specific ride."""
    trail = await db_supabase.get_ride_location_trail(ride_id)
    return trail


@router.get("/rides/{ride_id}/live")
async def admin_get_live_ride(ride_id: str):
    """Get live ride data including current driver location."""
    data = await db_supabase.get_live_ride_data(ride_id)
    if not data:
        raise HTTPException(status_code=404, detail="Ride not found")
    return data


@router.get("/rides/{ride_id}/invoice")
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
        "actual_distance_km": ride.get("actual_distance_km"),
        # Privacy: only expose trail phases relevant to the paid ride.
        # Filters out `online_idle` and any other pre-trip wandering so the
        # invoice cannot leak the driver's unrelated movements.
        "location_trail": [
            p
            for p in (ride.get("location_trail") or [])
            if p.get("tracking_phase") in ("navigating_to_pickup", "trip_in_progress")
        ],
    }


@router.get("/rides/{ride_id}/route-map.png")
async def admin_get_ride_route_map(
    ride_id: str,
    admin_user: dict = Depends(get_admin_user),
):
    """Proxy a Google Static Maps image for the ride's actual GPS route.

    Keeps the Google Maps API key server-side (prevents client bundle leak)
    and sidesteps browser CORS when the admin dashboard embeds the image in
    a generated PDF. Returns a PNG binary.
    """
    import httpx

    ride = await db_supabase.get_ride_details_enriched(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    pickup_lat = ride.get("pickup_lat")
    pickup_lng = ride.get("pickup_lng")
    dropoff_lat = ride.get("dropoff_lat")
    dropoff_lng = ride.get("dropoff_lng")
    if pickup_lat is None or dropoff_lat is None:
        raise HTTPException(status_code=400, detail="Ride is missing coordinates")

    # Only include ride-relevant phases (same privacy filter as invoice).
    trail = [
        p
        for p in (ride.get("location_trail") or [])
        if p.get("tracking_phase") in ("navigating_to_pickup", "trip_in_progress")
        and p.get("lat") is not None
        and p.get("lng") is not None
    ]

    # Sample to keep the URL under Google's ~8192 char limit.
    if len(trail) > 30:
        step = max(1, len(trail) // 30)
        sampled = trail[::step]
        # Always include the last point so the path reaches the dropoff area.
        if sampled[-1] is not trail[-1]:
            sampled.append(trail[-1])
    else:
        sampled = trail

    settings_row = await get_app_settings()
    api_key = (settings_row or {}).get("google_maps_api_key") or ""
    if not api_key:
        raise HTTPException(status_code=503, detail="Google Maps API key not configured")

    # Build static map URL
    params = [
        "size=600x240",
        "maptype=roadmap",
        f"markers=color:green|label:P|{pickup_lat},{pickup_lng}",
        f"markers=color:red|label:D|{dropoff_lat},{dropoff_lng}",
    ]
    if len(sampled) >= 2:
        path_str = "|".join(f"{p['lat']},{p['lng']}" for p in sampled)
        params.append(f"path=color:0x3B82F6FF|weight:4|{path_str}")
    params.append(f"key={api_key}")

    url = "https://maps.googleapis.com/maps/api/staticmap?" + "&".join(params)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(f"Static Maps returned {resp.status_code}: {resp.text[:200]}")
            raise HTTPException(status_code=502, detail="Failed to fetch route map")
        return Response(
            content=resp.content,
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except httpx.HTTPError as e:
        logger.warning(f"Static Maps fetch error for ride {ride_id}: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch route map") from e


@router.get("/rides/heatmap-data")
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


# ---------- Earnings ----------


@router.get("/earnings")
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


# ---------- Exports ----------


@router.get("/export/rides")
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


@router.get("/export/drivers")
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
