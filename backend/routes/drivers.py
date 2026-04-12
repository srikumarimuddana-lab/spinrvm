from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

try:
    from ..db import db, diag_logger
    from ..dependencies import get_admin_user, get_current_user
    from ..features import send_push_notification
    from ..geo_utils import calculate_distance
    from ..schemas import Driver, RideRatingRequest
    from ..socket_manager import manager
except ImportError:
    from db import db, diag_logger
    from dependencies import get_admin_user, get_current_user
    from features import send_push_notification
    from geo_utils import calculate_distance
    from schemas import Driver, RideRatingRequest
    from socket_manager import manager
import logging
from datetime import datetime, timedelta

import stripe
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class RideOTPRequest(BaseModel):
    otp: str


api_router = APIRouter(prefix="/drivers", tags=["Drivers"])


@api_router.get("/config")
async def get_driver_config(current_user: dict = Depends(get_current_user)):
    """Return operational settings the driver-app should honor at runtime.

    Driver-app constants that used to live hardcoded in
    `driver-app/shared/config/spinr.config.ts` and
    `driver-app/store/driverStore.ts` are now served from the backend
    so operations can tune them per deploy without shipping a new app
    build. Fields fall back to sensible defaults when the DB
    `settings` row doesn't include them yet.

    * ``ride_offer_timeout_seconds`` — how long a driver has to
      accept/decline a ride offer before it auto-declines. Default 15.
      Capped to [5, 60] so a bad admin input can't brick the UX.
    * ``pickup_radius_meters`` — how close the driver must be to the
      pickup point to mark "arrived" (geofence check). Default 100.
      Capped to [10, 1000].
    """
    try:
        from ..settings_loader import get_app_settings  # type: ignore
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore

    try:
        app_settings = await get_app_settings() or {}
    except Exception as e:
        logger.warning(f"get_driver_config: failed to read app_settings: {e}")
        app_settings = {}

    def _clamp(value, lo, hi, default):
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, n))

    return {
        "ride_offer_timeout_seconds": _clamp(app_settings.get("ride_offer_timeout_seconds"), 5, 60, 15),
        "pickup_radius_meters": _clamp(app_settings.get("pickup_radius_meters"), 10, 1000, 100),
    }


def serialize_doc(doc):
    return doc


@api_router.get("/me")
async def get_my_driver(current_user: dict = Depends(get_current_user)):
    """Get the current user's driver profile."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    return serialize_doc(driver)


@api_router.put("/me")
async def update_my_driver(body: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """Update the current user's driver profile.

    Accepts vehicle info, personal details, and preferences. When a
    verified driver changes vehicle fields, they are automatically
    un-verified and must wait for admin re-approval.
    """
    driver = await db.drivers.find_one({"user_id": current_user["id"]})

    # Fields that always update without affecting verification
    safe_fields = {"gst_number", "preferred_language", "photo_url"}
    # Vehicle/doc fields — changing these on a verified driver triggers re-review
    vehicle_fields = {
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
        "city",
        "service_area_id",
    }
    allowed_fields = safe_fields | vehicle_fields

    updates = {k: v for k, v in body.items() if k in allowed_fields and v is not None}
    if not updates:
        return {"success": True}

    # Auto-create a driver row if one doesn't exist yet (new driver adding
    # vehicle details for the first time from the vehicle-info screen).
    if not driver:
        import uuid

        first = current_user.get("first_name", "")
        last = current_user.get("last_name", "")
        new_driver = {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "name": f"{first} {last}".strip() or current_user.get("phone", ""),
            "phone": current_user.get("phone", ""),
            "status": "pending",
            "is_verified": False,
            "is_online": False,
            "is_available": False,
            "rating": 5.0,
            "total_rides": 0,
            "lat": 0,
            "lng": 0,
            "created_at": datetime.utcnow().isoformat(),
            **updates,
        }
        await db.drivers.insert_one(new_driver)
        # Also flip the user role to 'driver' if not already
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {"role": "driver", "is_driver": True}},
        )
        return serialize_doc(new_driver)

    # Check if an active driver changed vehicle/document fields → needs review
    changed_vehicle = any(k in vehicle_fields for k in updates)
    if changed_vehicle and driver.get("status") == "active":
        updates["status"] = "needs_review"
        updates["is_online"] = False
        updates["is_available"] = False
        logger.info(f"[DRIVER] Driver {driver['id']} updated vehicle info → status set to needs_review")

    updates["updated_at"] = datetime.utcnow().isoformat()
    await db.drivers.update_one({"id": driver["id"]}, {"$set": updates})
    updated = await db.drivers.find_one({"id": driver["id"]})
    return serialize_doc(updated)


@api_router.get("/demand-heatmap")
async def get_demand_heatmap(current_user: dict = Depends(get_current_user)):
    """Return recent ride pickup locations as heatmap points for the driver.

    Scoped to the driver's service area (if set) and the last 7 days.
    Only returns data when the admin has enabled `show_demand_heatmap`
    on the driver's service area.
    """
    driver = await db.drivers.find_one({"user_id": current_user["id"]})

    # Check if heatmap is enabled for this driver's service area
    service_area = None
    if driver and driver.get("service_area_id"):
        service_area = await db.service_areas.find_one({"id": driver["service_area_id"]})

    enabled = bool(service_area and service_area.get("show_demand_heatmap"))
    if not enabled:
        return {"enabled": False, "points": [], "total_rides": 0}

    query_filters: dict = {}

    # Last 7 days
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
    query_filters["created_at"] = {"$gte": cutoff}
    query_filters["service_area_id"] = driver["service_area_id"]

    rides = await db.get_rows("rides", query_filters, order="created_at", desc=True, limit=5000)

    points = []
    for r in rides:
        lat = r.get("pickup_lat")
        lng = r.get("pickup_lng")
        if lat is not None and lng is not None:
            points.append([float(lat), float(lng), 1])

    return {"enabled": True, "points": points, "total_rides": len(rides)}


@api_router.post("/register")
async def register_driver(
    body: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Create or update the driver row for the authenticated user (become-driver flow).

    Called by the driver app's `registerDriver()` in authStore after the user
    submits vehicle + document info. Upsert so re-submission updates the row
    rather than erroring.
    """
    user_id = current_user["id"]

    # Build name/phone from user if not supplied
    first_name = body.get("first_name") or ""
    last_name = body.get("last_name") or ""
    full_name = (
        f"{first_name} {last_name}".strip() or current_user.get("name") or current_user.get("full_name") or "Driver"
    )

    existing = await db.drivers.find_one({"user_id": user_id})

    # Fields the client is allowed to set on register
    allowed = {
        "first_name",
        "last_name",
        "email",
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
        "documents",
        "photo_url",
    }
    payload = {k: v for k, v in body.items() if k in allowed and v is not None}

    if existing:
        payload["updated_at"] = datetime.utcnow().isoformat()
        payload["submitted_at"] = datetime.utcnow().isoformat()
        await db.drivers.update_one({"id": existing["id"]}, {"$set": payload})
        driver = await db.drivers.find_one({"id": existing["id"]})
        return serialize_doc(driver)

    # Create new row
    import uuid as _uuid

    new_driver = {
        "id": str(_uuid.uuid4()),
        "user_id": user_id,
        "name": full_name,
        "phone": current_user.get("phone", ""),
        "rating": 5.0,
        "total_rides": 0,
        "is_online": False,
        "is_available": False,
        "is_verified": False,
        "status": "pending",
        "lat": 0.0,
        "lng": 0.0,
        "created_at": datetime.utcnow().isoformat(),
        "submitted_at": datetime.utcnow().isoformat(),
        **payload,
    }
    await db.drivers.insert_one(new_driver)
    return serialize_doc(new_driver)


class PushTokenPayload(BaseModel):
    push_token: str
    platform: Optional[str] = None


@api_router.post("/push-token")
async def register_driver_push_token(
    payload: PushTokenPayload,
    current_user: dict = Depends(get_current_user),
):
    """
    Register the driver-app's push token so the dispatch path can deliver
    `new_ride_offer` push notifications as a fallback when the WebSocket
    isn't connected.

    The driver-app's useDriverDashboard.ts calls this with an Expo push
    token from Notifications.getExpoPushTokenAsync(). We store it on the
    user row. Note: features.py:send_push_notification currently uses
    Firebase Admin SDK and expects a native FCM token, so Expo push tokens
    won't deliver via that path. TODO: add Expo push service support or
    convert Expo tokens server-side via Expo's /push/send API.
    """
    diag_logger.info(
        f"[PUSH-TOKEN] register user_id={current_user.get('id')} "
        f"platform={payload.platform} "
        f"token_prefix={(payload.push_token or '')[:20]}..."
    )
    try:
        # Only write fcm_token — the users table may not have a
        # push_platform column (the previous version tried to write it
        # and hit PGRST204 "column not found in schema cache").
        await db.users.update_one({"id": current_user["id"]}, {"$set": {"fcm_token": payload.push_token}})
        diag_logger.info(f"[PUSH-TOKEN] saved fcm_token for user_id={current_user['id']}")
    except Exception as e:
        diag_logger.info(f"[PUSH-TOKEN] update failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to store push token") from e

    return {"success": True}


@api_router.post("/status")
async def update_driver_status_self(
    is_online: bool = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Toggle the authenticated driver's online status.

    Called by `updateDriverStatus()` in the shared authStore when the driver
    flips the Go Online switch.
    """
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    updates = {
        "is_online": is_online,
        "is_available": is_online,
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db.drivers.update_one({"id": driver["id"]}, {"$set": updates})
    return {"success": True, "is_online": is_online}


@api_router.get("/balance")
async def get_driver_balance(current_user: dict = Depends(get_current_user)):
    """Get driver's current balance/earnings summary."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    try:
        rides = await db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": "completed",
            },
            limit=10000,
        )
        total_earnings = sum(r.get("driver_earnings", 0) or 0 for r in rides)
        total_tips = sum(r.get("tip_amount", 0) or 0 for r in rides)
        total_rides = len(rides)

        payouts = await db.get_rows(
            "payouts",
            {
                "driver_id": driver["id"],
                "status": "pending",
            },
            limit=1000,
        )
        pending_payouts = sum(p.get("amount", 0) or 0 for p in payouts)
    except Exception as e:
        logger.error(f"Error fetching balance: {e}")
        total_earnings = total_tips = total_rides = pending_payouts = 0

    return {
        "total_earnings": total_earnings,
        "available_balance": total_earnings - pending_payouts,
        "pending_payouts": pending_payouts,
        "total_paid_out": 0,
        "has_bank_account": bool(driver.get("bank_account")),
        "stripe_account_onboarded": bool(driver.get("stripe_account_onboarded", False)),
        "total_tips": total_tips,
        "total_rides": total_rides,
    }


@api_router.get("/earnings")
async def get_driver_earnings(period: str = Query("week"), current_user: dict = Depends(get_current_user)):
    """Get driver's earnings summary for a period."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        # Try to find by id directly in case user_id isn't set, or log error
        logger.error(f"Driver not found for user {current_user['id']}")
        raise HTTPException(status_code=404, detail="Driver not found")

    logger.info(f"Fetching earnings for driver {driver['id']} period {period}")

    # Calculate date range
    now = datetime.utcnow()
    # 'today' and 'day' both mean since midnight today
    # 'all' means no date restriction — fetch all-time
    use_date_filter = True
    if period in ("today", "day"):
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = now - timedelta(days=7)
    elif period == "month":
        start_date = now - timedelta(days=30)
    elif period == "all":
        use_date_filter = False
        start_date = None
    else:
        # Fallback: treat unknown period as 'week'
        start_date = now - timedelta(days=7)

    try:
        filters: Dict[str, Any] = {"driver_id": driver["id"], "status": "completed"}
        if use_date_filter and start_date:
            filters["ride_completed_at"] = {"$gte": start_date.isoformat()}

        rides = await db.get_rows("rides", filters, limit=10000)

        stats = {
            "total_earnings": sum(r.get("driver_earnings", 0) or 0 for r in rides),
            "total_tips": sum(r.get("tip_amount", 0) or 0 for r in rides),
            "total_rides": len(rides),
            "total_distance_km": sum(r.get("distance_km", 0) or 0 for r in rides),
            "total_duration_minutes": sum(r.get("duration_minutes", 0) or 0 for r in rides),
        }
    except Exception as e:
        logger.error(f"Error fetching earnings: {e}")
        stats = {
            "total_earnings": 0,
            "total_tips": 0,
            "total_rides": 0,
            "total_distance_km": 0,
            "total_duration_minutes": 0,
        }

    return {
        "period": period,
        "total_earnings": stats.get("total_earnings", 0),
        "total_tips": stats.get("total_tips", 0),
        "total_rides": stats.get("total_rides", 0),
        "total_distance_km": stats.get("total_distance_km", 0),
        "total_duration_minutes": stats.get("total_duration_minutes", 0),
        "average_per_ride": stats.get("total_earnings", 0) / stats.get("total_rides", 1)
        if stats.get("total_rides", 0) > 0
        else 0,
    }


@api_router.get("/earnings/daily")
async def get_driver_daily_earnings(days: int = Query(7), current_user: dict = Depends(get_current_user)):
    """Get driver's daily earnings breakdown."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    start_date = datetime.utcnow() - timedelta(days=days)

    # Fetch completed rides in the period using the shared db layer
    try:
        rides = await db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": "completed",
                "ride_completed_at": {"$gte": start_date.isoformat()},
            },
            order="ride_completed_at",
            limit=5000,
        )

        # Group by date (small dataset per driver, fine in Python)
        daily_data: dict = {}
        for r in rides:
            date_str = (r.get("ride_completed_at") or "")[:10]
            if not date_str:
                continue
            if date_str not in daily_data:
                daily_data[date_str] = {"earnings": 0, "tips": 0, "rides": 0, "distance_km": 0}
            daily_data[date_str]["earnings"] += r.get("driver_earnings", 0) or 0
            daily_data[date_str]["tips"] += r.get("tip_amount", 0) or 0
            daily_data[date_str]["rides"] += 1
            daily_data[date_str]["distance_km"] += r.get("distance_km", 0) or 0

        results = [{"date": date, **data} for date, data in sorted(daily_data.items())]
    except Exception as e:
        logger.error(f"Error fetching daily earnings: {e}")
        results = []

    return results


@api_router.get("/earnings/trips")
async def get_driver_trip_earnings(
    limit: int = Query(20), offset: int = Query(0), current_user: dict = Depends(get_current_user)
):
    """Get driver's individual trip earnings."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    try:
        rides = await db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
                "status": "completed",
            },
            order="ride_completed_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        logger.error(f"Error fetching trip earnings: {e}")
        rides = []

    return [
        {
            "ride_id": r["id"],
            "pickup_address": r.get("pickup_address", ""),
            "dropoff_address": r.get("dropoff_address", ""),
            "distance_km": r.get("distance_km", 0),
            "duration_minutes": r.get("duration_minutes", 0),
            "base_fare": r.get("base_fare", 0),
            "distance_fare": r.get("distance_fare", 0),
            "time_fare": r.get("time_fare", 0),
            "driver_earnings": r.get("driver_earnings", 0),
            "tip_amount": r.get("tip_amount", 0),
            "rider_rating": r.get("rider_rating"),
            "completed_at": r.get("ride_completed_at") if r.get("ride_completed_at") else None,
        }
        for r in rides
    ]


@api_router.get("/nearby")
async def get_nearby_drivers_public(
    lat: float = Query(...),
    lng: float = Query(...),
    radius: float = Query(5.0),
    vehicle_type: str = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get nearby active drivers for riders. Filters by service area + vehicle type."""
    query = {"is_online": True, "is_available": True}
    if vehicle_type:
        query["vehicle_type_id"] = vehicle_type

    # Get all matching drivers — service area filtering by distance (not polygon yet)
    drivers = await db.drivers.find(query).to_list(100)

    # Manual filtering by distance
    nearby = []
    for d in drivers:
        # Exclude orphan/demo driver rows (no user_id → cannot be dispatched).
        if not d.get("user_id"):
            continue
        d_lat = d.get("lat")
        d_lng = d.get("lng")
        if d_lat and d_lng:
            dist = calculate_distance(lat, lng, d_lat, d_lng)
            if dist <= radius:
                # hide personal info for riders
                safe_driver = {
                    "id": d["id"],
                    "lat": d_lat,
                    "lng": d_lng,
                    "vehicle_type_id": d.get("vehicle_type_id"),
                    "vehicle_make": d.get("vehicle_make"),
                    "vehicle_model": d.get("vehicle_model"),
                }
                nearby.append(safe_driver)

    return nearby


@api_router.get("")
async def get_drivers(
    lat: float = Query(None),
    lng: float = Query(None),
    radius: float = Query(5.0),
    vehicle_type: str = Query(None),
    admin_user: dict = Depends(get_admin_user),
):
    """
    Get all drivers (admin only) or nearby drivers (if lat/lng provided).
    """
    if lat and lng:
        # Should rely on RPC or geospatial query
        # For now, simplistic implementation as seen in other parts
        drivers = await db.drivers.find({"is_online": True}).to_list(100)
        return serialize_doc(drivers)

    # Return all drivers for admin
    drivers = await db.drivers.find({}).to_list(100)
    return serialize_doc(drivers)


@api_router.post("")
async def create_driver(driver: Driver, admin_user: dict = Depends(get_admin_user)):
    """Register a new driver (admin only or internal process)"""
    existing = await db.drivers.find_one({"phone": driver.phone})
    if existing:
        raise HTTPException(status_code=400, detail="Driver with this phone already exists")

    await db.drivers.insert_one(driver.dict())
    return driver.dict()


@api_router.post("/location-batch")
async def update_location_batch(batch: Union[List[dict], dict], current_user: dict = Depends(get_current_user)):
    """Update driver location in batch (from background tracking)."""

    points = []
    if isinstance(batch, list):
        points = batch
    elif isinstance(batch, dict):
        points = batch.get("locations") or batch.get("points") or []

    # Simply take the last point and update current location
    if not points:
        return {"success": True}

    latest = points[-1]
    lat = latest.get("latitude") or latest.get("lat")
    lng = latest.get("longitude") or latest.get("lng")
    latest.get("heading", 0)

    if lat and lng:
        # Update via Supabase wrapper which now handles casting
        # Note: 'heading' column might not exist in Supabase 'drivers' table yet.
        update_data = {"lat": lat, "lng": lng, "updated_at": datetime.utcnow()}
        # If heading is supported later, add it back. Currently causing 500 error if column missing.
        # if heading:
        #    update_data['heading'] = heading

        await db.drivers.update_one({"user_id": current_user["id"]}, {"$set": update_data})
        # Also sync to generic lat/lng fields if they exist to support legacy queries
        # (Though update_one might not support setting multiple top-level fields easily if we rely on $set mapping)
        # Let's trust db.drivers.update_one to handle the schema or the wrapper.

    return {"success": True}


import uuid  # noqa: E402


class BankAccountCreate(BaseModel):
    bank_name: str
    institution_number: str
    transit_number: str
    account_number: str
    account_holder_name: str
    account_type: str = "checking"


class PayoutRequest(BaseModel):
    amount: float


@api_router.get("/bank-account")
async def get_bank_account(current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user.get("id")})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    account = await db.bank_accounts.find_one({"driver_id": driver["id"]})
    if account:
        return {"has_bank_account": True, "bank_account": serialize_doc(account)}

    if driver.get("stripe_account_onboarded"):
        return {
            "has_bank_account": True,
            "bank_account": {"bank_name": "Stripe Connect", "account_number_last4": "****"},
        }

    return {"has_bank_account": False, "bank_account": None}


@api_router.post("/stripe-onboard")
async def onboard_stripe(current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user.get("id")})
    user = await db.users.find_one({"id": current_user.get("id")})
    if not driver or not user:
        raise HTTPException(status_code=404, detail="Driver/User profile not found")

    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    if not stripe_secret:
        return {"url": "https://spinr-demo-onboard.com", "mock": True}

    try:
        stripe.api_key = stripe_secret
        account_id = driver.get("stripe_account_id")

        if not account_id:
            account = stripe.Account.create(
                type="express",
                country="CA",
                email=user.get("email"),
                capabilities={
                    "transfers": {"requested": True},
                },
                business_type="individual",
            )
            account_id = account.id
            await db.drivers.update_one({"id": driver["id"]}, {"$set": {"stripe_account_id": account_id}})

        account_link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=f"{settings.get('base_url', 'http://localhost:8000')}/api/drivers/stripe-refresh",
            return_url=f"{settings.get('base_url', 'http://localhost:8000')}/api/drivers/stripe-return",
            type="account_onboarding",
        )
        # Mark as onboarded optimistically or handle via webhook/return_url properly in production
        await db.drivers.update_one({"id": driver["id"]}, {"$set": {"stripe_account_onboarded": True}})

        return {"url": account_link.url, "mock": False}
    except Exception as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@api_router.post("/bank-account")
async def save_bank_account(req: BankAccountCreate, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user.get("id")})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    account_data = req.dict()
    account_data["id"] = str(uuid.uuid4())
    account_data["driver_id"] = driver["id"]
    acc_num = account_data.pop("account_number")

    # Canadian routing number for Stripe is generally 0 + Institution (3) + Transit (5)
    # Ensure zero-padding if needed
    inst = req.institution_number.zfill(3)
    trans = req.transit_number.zfill(5)
    account_data["routing_number"] = f"0{inst}{trans}"

    account_data["account_number_last4"] = acc_num[-4:] if len(acc_num) >= 4 else acc_num
    account_data["stripe_bank_id"] = None  # Would be populated after calling Stripe's API
    account_data["currency"] = "cad"
    account_data["country"] = "CA"
    account_data["is_verified"] = False
    account_data["created_at"] = datetime.utcnow().isoformat()

    await db.bank_accounts.delete_many({"driver_id": driver["id"]})
    await db.bank_accounts.insert_one(account_data)

    return {"success": True, "bank_account": serialize_doc(account_data)}


@api_router.delete("/bank-account")
async def delete_bank_account(current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user.get("id")})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
    await db.bank_accounts.delete_many({"driver_id": driver["id"]})
    return {"success": True}


@api_router.post("/payouts")
async def request_payout(req: PayoutRequest, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user.get("id")})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    balance = await get_driver_balance(current_user)
    if req.amount > balance.get("available_balance", 0):
        raise HTTPException(status_code=400, detail="Insufficient funds")

    stripe_account_id = driver.get("stripe_account_id")
    account = await db.bank_accounts.find_one({"driver_id": driver["id"]})

    if not stripe_account_id and not account:
        raise HTTPException(status_code=400, detail="No bank account linked")

    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    status = "pending"
    stripe_payout_id = None

    if stripe_secret and stripe_account_id:
        try:
            stripe.api_key = stripe_secret
            transfer = stripe.Transfer.create(
                amount=int(req.amount * 100),
                currency="cad",
                destination=stripe_account_id,
            )
            status = "completed"
            stripe_payout_id = transfer.id
        except Exception as e:
            logger.error(f"Stripe transfer failed: {e}")
            raise HTTPException(status_code=500, detail=f"Payout failed: {str(e)}") from e

    payout = {
        "id": str(uuid.uuid4()),
        "driver_id": driver["id"],
        "amount": req.amount,
        "status": status,
        "stripe_payout_id": stripe_payout_id,
        "bank_name": account.get("bank_name") if account else "Stripe Connect",
        "account_last4": account.get("account_number_last4") if account else "****",
        "created_at": datetime.utcnow().isoformat(),
    }
    await db.payouts.insert_one(payout)
    return {"success": True, "payout": serialize_doc(payout)}


@api_router.get("/payouts")
async def get_payout_history(
    limit: int = Query(20), offset: int = Query(0), current_user: dict = Depends(get_current_user)
):
    driver = await db.drivers.find_one({"user_id": current_user.get("id")})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    payouts_cursor = db.payouts.find({"driver_id": driver["id"]})
    if hasattr(payouts_cursor, "sort"):
        payouts_cursor = payouts_cursor.sort("created_at", -1).skip(offset).limit(limit)

    payouts = await payouts_cursor.to_list(length=limit) if hasattr(payouts_cursor, "to_list") else list(payouts_cursor)
    return {"success": True, "payouts": [serialize_doc(p) for p in payouts]}


@api_router.get("/t4a/{year}")
async def get_t4a_summary(year: int, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user.get("id")})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    start_date = datetime(year, 1, 1).isoformat()
    end_date = datetime(year, 12, 31, 23, 59, 59).isoformat()

    rides_cursor = db.rides.find(
        {"driver_id": driver["id"], "status": "completed", "created_at": {"$gte": start_date, "$lte": end_date}}
    )
    rides = await rides_cursor.to_list(length=10000) if hasattr(rides_cursor, "to_list") else list(rides_cursor)

    total_earnings = sum(r.get("driver_earnings", 0) for r in rides)

    return {
        "year": year,
        "total_earnings": total_earnings,
        "total_trips": len(rides),
        "platform_fees": 0,
        "net_earnings": total_earnings,
        "generated_at": datetime.utcnow().isoformat(),
    }


@api_router.get("/earnings/export")
async def export_earnings(year: int = Query(None), current_user: dict = Depends(get_current_user)):
    if not year:
        year = datetime.utcnow().year

    summary_data = await get_t4a_summary(year, current_user)

    csv_data = f"Year,Total Earnings,Total Trips,Net Earnings\n{year},{summary_data['total_earnings']},{summary_data['total_trips']},{summary_data['net_earnings']}"
    filename = f"earnings_export_{year}.csv"

    return {"data": csv_data, "filename": filename}


# ==========================================
# RIDE MANAGEMENT ENDPOINTS
# ==========================================


@api_router.get("/rides/active")
async def get_active_ride(current_user: dict = Depends(get_current_user)):
    """Get the driver's current active ride."""
    diag_logger.info(f"[ACTIVE] called by user_id={current_user.get('id')}")
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        diag_logger.info(f"[ACTIVE] no driver row for user_id={current_user.get('id')}")
        raise HTTPException(status_code=404, detail="Driver not found")

    diag_logger.info(f"[ACTIVE] lookup user_id={current_user.get('id')} driver_id={driver.get('id')}")

    # improved query to catch any active state
    ride = await db.rides.find_one(
        {
            "driver_id": driver["id"],
            "status": {"$in": ["driver_assigned", "driver_accepted", "driver_arrived", "in_progress"]},
        }
    )

    if not ride:
        # Help diagnose: list the driver's most recent rides regardless of
        # status so we can see whether the $in filter missed something, or
        # the driver_id on the latest ride doesn't match.
        try:
            recent = await db.rides.find({"driver_id": driver["id"]}).to_list(5)
            recent_summary = [
                {"id": r.get("id"), "status": r.get("status"), "driver_id": r.get("driver_id")} for r in (recent or [])
            ]
        except Exception as e:
            recent_summary = f"(failed to load recent: {e})"
        diag_logger.info(
            f"[ACTIVE] no active ride for driver_id={driver['id']}. recent_rides_by_driver={recent_summary}"
        )
        return {"ride": None}

    diag_logger.info(
        f"[ACTIVE] found ride_id={ride.get('id')} status={ride.get('status')} "
        f"driver_id={ride.get('driver_id')} rider_id={ride.get('rider_id')}"
    )

    # Get rider info. `db.user_profiles` does not exist as a registered
    # collection in db.py — the rider is a row in `users`. The old name
    # raised AttributeError, which made this endpoint return 500 and
    # silently broke the driver-app's active-ride fetch (activeRide stayed
    # null → ActiveRidePanel returned null → driver saw a blank map after
    # accepting).
    try:
        rider = await db.users.find_one({"id": ride["rider_id"]})
    except Exception as e:
        logger.warning(f"get_active_ride: failed to load rider {ride['rider_id']}: {e}")
        rider = None
    try:
        vehicle_type = await db.vehicle_types.find_one({"id": ride["vehicle_type_id"]})
    except Exception as e:
        logger.warning(f"get_active_ride: failed to load vehicle_type {ride['vehicle_type_id']}: {e}")
        vehicle_type = None

    return {
        "ride": serialize_doc(ride),
        "rider": serialize_doc(rider) if rider else None,
        "vehicle_type": serialize_doc(vehicle_type) if vehicle_type else None,
    }


@api_router.get("/rides/history")
async def get_ride_history(
    limit: int = Query(20), offset: int = Query(0), current_user: dict = Depends(get_current_user)
):
    """Get driver's ride history."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    try:
        total = await db.rides.count_documents({"driver_id": driver["id"]})
        rides = await db.get_rows(
            "rides",
            {
                "driver_id": driver["id"],
            },
            order="created_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        logger.error(f"Error fetching ride history: {e}")
        total = 0
        rides = []

    return {"total": total, "rides": [serialize_doc(r) for r in rides]}


@api_router.post("/rides/{ride_id}/accept")
async def accept_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Accept a ride offer.

    Uses a single atomic conditional UPDATE (see
    ``db_supabase.claim_ride_atomic``) so two drivers racing to accept
    the same offer cannot both succeed — the loser's UPDATE matches zero
    rows and we return 400. This replaces an earlier read-modify-write
    implementation that had a real time-of-check-time-of-use race: two
    drivers reading `searching` simultaneously could both pass the
    status check and both overwrite `driver_id`, leaving the second
    write as the "winner" without either side knowing they raced.
    """
    try:
        from ..db_supabase import claim_ride_atomic  # noqa: E402
    except ImportError:
        from db_supabase import claim_ride_atomic  # noqa: E402

    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    diag_logger.info(f"[ACCEPT] attempt ride_id={ride_id} driver_id={driver.get('id')}")

    claimed = await claim_ride_atomic(ride_id, driver["id"])
    if not claimed:
        # Distinguish "ride doesn't exist" from "ride already taken" so
        # the driver-app can surface the right UX — driverStore.acceptRide
        # maps the 400 with `already` in the detail into a graceful
        # "next offer coming" toast instead of a hard error.
        ride = await db.rides.find_one({"id": ride_id})
        if not ride:
            raise HTTPException(status_code=404, detail="Ride not found")
        diag_logger.info(
            f"[ACCEPT] claim rejected ride_id={ride_id} "
            f"current_status={ride.get('status')} current_driver_id={ride.get('driver_id')}"
        )
        raise HTTPException(status_code=400, detail="Ride already accepted by another driver")

    # Re-read the now-claimed ride so we can notify the rider with fresh data.
    ride = await db.rides.find_one({"id": ride_id})
    diag_logger.info(
        f"[ACCEPT] success ride_id={ride_id} driver_id={driver['id']} "
        f"post_status={ride.get('status') if ride else 'ROW_GONE'}"
    )

    # Notify rider via both WebSocket (for the instant in-app
    # transition) AND FCM push (so the rider still gets the update
    # if the app was backgrounded when the driver accepted).
    # The `data` payload lets the rider-app foreground FCM handler in
    # app/_layout.tsx route the event without reparsing the title.
    if ride and ride.get("rider_id"):
        await manager.send_personal_message(
            {"type": "driver_accepted", "ride_id": ride_id}, f"rider_{ride['rider_id']}"
        )
        await send_push_notification(
            ride["rider_id"],
            "Driver Assigned! 🚗",
            "Your driver has accepted the ride and is on the way.",
            data={"type": "driver_accepted", "ride_id": str(ride_id)},
        )

    return {"success": True}


@api_router.post("/rides/{ride_id}/decline")
async def decline_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # If assigned, unassign. If searching, just ignore/record decline.
    await db.rides.update_one(
        {"id": ride_id, "driver_id": driver["id"]},
        {
            "$set": {
                "driver_id": None,
                "status": "searching",  # returned to pool
                "updated_at": datetime.utcnow(),
            }
        },
    )

    # GAP FIX: Re-match to find the next available driver
    try:
        import asyncio

        from .rides import match_driver_to_ride

        asyncio.create_task(match_driver_to_ride(ride_id))
        logger.info(f"Re-matching ride {ride_id} after driver {driver['id']} declined")
    except Exception as e:
        logger.warning(f"Could not trigger re-matching for ride {ride_id}: {e}")

    return {"success": True}


@api_router.post("/rides/{ride_id}/arrive")
async def arrive_at_pickup(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = await db.rides.find_one({"id": ride_id, "driver_id": driver["id"]})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Geofence check - verify driver is within 200m of pickup location
    ARRIVAL_RADIUS_KM = 0.2  # 200 meters
    driver_lat = driver.get("lat", 0)
    driver_lng = driver.get("lng", 0)
    pickup_lat = ride.get("pickup_lat", 0)
    pickup_lng = ride.get("pickup_lng", 0)

    if driver_lat and driver_lng and pickup_lat and pickup_lng:
        distance_to_pickup = calculate_distance(driver_lat, driver_lng, pickup_lat, pickup_lng)
        if distance_to_pickup > ARRIVAL_RADIUS_KM:
            distance_m = int(distance_to_pickup * 1000)
            raise HTTPException(
                status_code=400,
                detail=f"You are {distance_m}m away from the pickup. "
                f"Please move within 200m of the pickup location to mark arrival.",
            )

    await db.rides.update_one(
        {"id": ride_id, "driver_id": driver["id"]},
        {"$set": {"status": "driver_arrived", "driver_arrived_at": datetime.utcnow(), "updated_at": datetime.utcnow()}},
    )

    if ride.get("rider_id"):
        await manager.send_personal_message({"type": "driver_arrived", "ride_id": ride_id}, f"rider_{ride['rider_id']}")
        await send_push_notification(
            ride["rider_id"],
            "Driver Arrived! 📍",
            "Your driver has arrived at the pickup location.",
            data={"type": "driver_arrived", "ride_id": str(ride_id)},
        )

    return {"success": True}


@api_router.post("/rides/{ride_id}/verify-otp")
async def verify_pickup_otp(ride_id: str, request: RideOTPRequest, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = await db.rides.find_one({"id": ride_id, "driver_id": driver["id"]})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("pickup_otp") != request.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")

    # OTP correct, start ride
    await db.rides.update_one(
        {"id": ride_id},
        {"$set": {"status": "in_progress", "ride_started_at": datetime.utcnow(), "updated_at": datetime.utcnow()}},
    )

    if ride.get("rider_id"):
        await manager.send_personal_message({"type": "ride_started", "ride_id": ride_id}, f"rider_{ride['rider_id']}")
        await send_push_notification(
            ride["rider_id"],
            "Ride Started! ▶️",
            "Your ride has started. Have a safe trip!",
            data={"type": "ride_started", "ride_id": str(ride_id)},
        )

    return {"success": True}


@api_router.post("/rides/{ride_id}/start")
async def start_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Start ride without OTP (if configured) or fallback."""
    # Logic similar to verify_otp but without check
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    await db.rides.update_one(
        {"id": ride_id, "driver_id": driver["id"]},
        {"$set": {"status": "in_progress", "ride_started_at": datetime.utcnow(), "updated_at": datetime.utcnow()}},
    )

    ride = await db.rides.find_one({"id": ride_id})
    if ride and ride.get("rider_id"):
        await manager.send_personal_message({"type": "ride_started", "ride_id": ride_id}, f"rider_{ride['rider_id']}")
        await send_push_notification(
            ride["rider_id"],
            "Ride Started! ▶️",
            "Your ride has started. Have a safe trip!",
            data={"type": "ride_started", "ride_id": str(ride_id)},
        )
    return {"success": True}


@api_router.post("/rides/{ride_id}/complete")
async def complete_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = await db.rides.find_one({"id": ride_id, "driver_id": driver["id"]})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # ── Aggregate all GPS breadcrumbs for this ride ──
    # On completion we compute everything once and store it on the ride row.
    # After this the admin dashboard reads from the ride row directly — no
    # need to join against driver_location_history for historical rides.
    planned_distance = ride.get("planned_distance_km") or ride.get("distance_km", 0) or 0
    actual_distance_km = planned_distance
    phase_distances = {}
    pickup_to_driver_km = 0.0
    route_polyline = []
    gps_points_count = 0

    try:
        all_breadcrumbs = await db.driver_location_history.find(
            {
                "ride_id": ride_id,
            }
        ).to_list(10000)
        all_breadcrumbs = [b for b in all_breadcrumbs if b.get("lat") and b.get("lng")]
        all_breadcrumbs.sort(key=lambda b: str(b.get("timestamp", "")))
        gps_points_count = len(all_breadcrumbs)

        if gps_points_count >= 2:
            # Compute per-phase distances (attribute segment to current point's phase)
            phase_totals: Dict[str, float] = {}
            for i in range(1, len(all_breadcrumbs)):
                prev = all_breadcrumbs[i - 1]
                curr = all_breadcrumbs[i]
                phase = curr.get("tracking_phase") or "unknown"
                seg = calculate_distance(prev["lat"], prev["lng"], curr["lat"], curr["lng"])
                phase_totals[phase] = phase_totals.get(phase, 0.0) + seg
            phase_distances = {k: round(v, 3) for k, v in phase_totals.items()}

            # Actual distance = trip_in_progress only (the paid portion)
            actual_distance_km = round(phase_distances.get("trip_in_progress", 0.0), 2)
            if actual_distance_km == 0:
                actual_distance_km = planned_distance

            pickup_to_driver_km = round(phase_distances.get("navigating_to_pickup", 0.0), 2)

            # Downsample route polyline to max ~200 points for fast rendering.
            # Keep trip_in_progress + navigating_to_pickup (drop idle noise).
            trip_points = [
                b for b in all_breadcrumbs if b.get("tracking_phase") in ("navigating_to_pickup", "trip_in_progress")
            ]
            if trip_points:
                MAX_POINTS = 200
                step = max(1, len(trip_points) // MAX_POINTS)
                sampled = trip_points[::step]
                # Always include the last point so the polyline ends at dropoff
                if sampled and sampled[-1] is not trip_points[-1]:
                    sampled.append(trip_points[-1])
                route_polyline = [
                    [round(p["lat"], 6), round(p["lng"], 6), p.get("tracking_phase", "")] for p in sampled
                ]
    except Exception as e:
        logger.warning(f"Could not aggregate GPS data for ride {ride_id}: {e}")

    # ── Build update payload ──
    update_fields: Dict[str, Any] = {
        "status": "completed",
        "ride_completed_at": datetime.utcnow(),
        "payment_status": "completed",
        "updated_at": datetime.utcnow(),
        "planned_distance_km": planned_distance,
        "actual_distance_km": actual_distance_km,
        "pickup_to_driver_km": pickup_to_driver_km,
        "phase_distances": phase_distances,
        "route_polyline": route_polyline,
        "gps_points_count": gps_points_count,
    }

    # Recalculate fare if actual distance differs materially from estimate
    if actual_distance_km > 0 and abs(actual_distance_km - planned_distance) > 0.1:
        per_km_rate = (ride.get("distance_fare", 0) / planned_distance) if planned_distance > 0 else 0
        new_distance_fare = round(per_km_rate * actual_distance_km, 2)
        new_total_fare = round(
            ride.get("base_fare", 0)
            + new_distance_fare
            + ride.get("time_fare", 0)
            + ride.get("booking_fee", 0)
            + (ride.get("airport_fee") or 0),
            2,
        )
        new_driver_earnings = round(
            ride.get("base_fare", 0) + new_distance_fare + ride.get("time_fare", 0),
            2,
        )
        update_fields.update(
            {
                "distance_km": actual_distance_km,  # overwrite with actual
                "distance_fare": new_distance_fare,
                "total_fare": new_total_fare,
                "driver_earnings": new_driver_earnings,
            }
        )
        logger.info(
            f"Ride {ride_id}: fare recalculated on completion. "
            f"planned={planned_distance}km actual={actual_distance_km}km"
        )
    else:
        update_fields["distance_km"] = actual_distance_km

    try:
        await db.rides.update_one({"id": ride_id, "driver_id": driver["id"]}, {"$set": update_fields})
    except Exception as e:
        # Some columns may not exist yet in older deployments. Retry with only
        # the essential fields so ride completion never fails.
        err_msg = str(e).lower()
        if "column" in err_msg or "pgrst204" in err_msg:
            logger.warning(f"Retrying ride update with minimal fields: {e}")
            safe_keys = {"status", "ride_completed_at", "payment_status", "updated_at", "distance_km"}
            safe_updates = {k: v for k, v in update_fields.items() if k in safe_keys}
            await db.rides.update_one({"id": ride_id, "driver_id": driver["id"]}, {"$set": safe_updates})
        else:
            raise

    # Post-ride receipt notification stub
    rider = await db.users.find_one({"id": ride.get("rider_id")})
    if rider and rider.get("email"):
        logger.info(f"Sending email receipt for ride {ride_id} to {rider['email']}")

    # Update driver stats
    await db.drivers.update_one({"id": driver["id"]}, {"$inc": {"total_rides": 1}, "$set": {"is_available": True}})

    completed_ride = await db.rides.find_one({"id": ride_id})

    if completed_ride and completed_ride.get("rider_id"):
        await manager.send_personal_message(
            {
                "type": "ride_completed",
                "ride_id": ride_id,
                "total_fare": completed_ride.get("total_fare", ride.get("total_fare", 0)),
            },
            f"rider_{completed_ride['rider_id']}",
        )
        await send_push_notification(
            completed_ride["rider_id"],
            "Ride Completed! ✅",
            f"Your ride has finished. Total fare: ${completed_ride.get('total_fare', ride.get('total_fare', 0))}",
            data={"type": "ride_completed", "ride_id": str(ride_id)},
        )

    return serialize_doc(completed_ride)


@api_router.post("/rides/{ride_id}/cancel")
async def cancel_ride(ride_id: str, reason: str = Query(""), current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Only write columns guaranteed to exist. cancelled_by and
    # cancellation_reason may not be in the Supabase schema — including
    # them causes PGRST204 which crashes the whole cancel with 500.
    await db.rides.update_one(
        {"id": ride_id},
        {
            "$set": {
                "status": "cancelled",
                "cancelled_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        },
    )

    # Make driver available again
    await db.drivers.update_one({"id": driver["id"]}, {"$set": {"is_available": True}})

    ride = await db.rides.find_one({"id": ride_id})
    if ride and ride.get("rider_id"):
        await manager.send_personal_message(
            {"type": "ride_cancelled", "ride_id": ride_id, "reason": reason}, f"rider_{ride['rider_id']}"
        )
        await send_push_notification(
            ride["rider_id"],
            "Ride Cancelled ❌",
            "Your driver has cancelled the ride.",
            data={"type": "ride_cancelled", "ride_id": str(ride_id)},
        )

    return {"success": True}


@api_router.post("/rides/{ride_id}/rate-rider")
async def rate_rider(ride_id: str, rating_data: RideRatingRequest, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Update ride with rating
    await db.rides.update_one(
        {"id": ride_id, "driver_id": driver["id"]},
        {
            "$set": {
                "rider_rating": rating_data.rating,
                "rider_comment": rating_data.comment,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    return {"success": True}


# ============ Referral Program Endpoints ============


class ApplyReferralCodeRequest(BaseModel):
    referral_code: str


@api_router.get("/referral")
async def get_driver_referral_info(current_user: dict = Depends(get_current_user)):
    """Get driver's referral code and earnings from referrals."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Get or create referral code (use driver ID as default code)
    referral_code = driver.get("referral_code", f"DRIVER{driver['id'][:8].upper()}")

    # Find users who used this referral code
    referred_users_cursor = db.users.find({"referral_code_used": referral_code})
    referred_users = (
        await referred_users_cursor.to_list(100)
        if hasattr(referred_users_cursor, "to_list")
        else list(referred_users_cursor)
    )

    # Calculate referral earnings (e.g., $10 per referred driver who completes 10 rides)
    total_referrals = len(referred_users)
    referral_earnings = 0

    # Check how many referred drivers have completed rides
    for user in referred_users:
        # Check if user became a driver and completed rides
        referred_driver = await db.drivers.find_one({"user_id": user["id"]})
        if referred_driver:
            completed_rides = await db.rides.count_documents(
                {"driver_id": referred_driver["id"], "status": "completed"}
            )
            if completed_rides >= 10:
                referral_earnings += 10  # $10 bonus

    return {
        "referral_code": referral_code,
        "referral_link": f"https://spinr.app/join/{referral_code}",
        "total_referrals": total_referrals,
        "referral_earnings": referral_earnings,
        "terms": "Earn $10 for each driver who signs up with your code and completes 10 rides.",
    }


@api_router.post("/referral/apply")
async def apply_referral_code(req: ApplyReferralCodeRequest, current_user: dict = Depends(get_current_user)):
    """Apply a referral code during driver onboarding."""
    code = req.referral_code.strip().upper()

    # Check if user already has a referral code applied
    user = await db.users.find_one({"id": current_user["id"]})
    if user and user.get("referral_code_used"):
        raise HTTPException(status_code=400, detail="Referral code already applied")

    # Validate referral code exists (check if any driver has this code)
    ref_driver = await db.drivers.find_one({"referral_code": code})
    if not ref_driver:
        # Legacy fallback: allow `DRIVER<id-suffix>` format where the
        # suffix is the last 8 chars of a driver ID. The original
        # implementation used a `$regex` filter which (a) the Supabase
        # translator silently dropped, so this path never matched, and
        # (b) would have been a ReDoS vector on MongoDB.
        #
        # Replacement: accept only an 8-char alphanumeric suffix, then
        # use a bounded PostgREST `.ilike()` lookup. The `%` suffix
        # wildcard means "id ends with this string" — exactly what the
        # original code was trying to do.
        potential_id = code.replace("DRIVER", "")
        if len(potential_id) == 8 and potential_id.isalnum():
            try:
                from ..db_supabase import run_sync, supabase  # type: ignore
            except ImportError:
                from db_supabase import run_sync, supabase  # type: ignore

            if supabase:

                def _lookup():
                    res = supabase.table("drivers").select("*").ilike("id", f"%{potential_id}").limit(1).execute()
                    rows = res.data if res.data else []
                    return rows[0] if rows else None

                ref_driver = await run_sync(_lookup)

    if not ref_driver:
        raise HTTPException(status_code=404, detail="Invalid referral code")

    # Apply referral code to user
    await db.users.update_one(
        {"id": current_user["id"]}, {"$set": {"referral_code_used": code, "referred_by": ref_driver["id"]}}
    )

    return {"success": True, "referral_code": code}


@api_router.get("/referrals")
async def get_referred_drivers(
    limit: int = Query(50), offset: int = Query(0), current_user: dict = Depends(get_current_user)
):
    """Get list of drivers referred by current driver."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    referral_code = driver.get("referral_code", f"DRIVER{driver['id'][:8].upper()}")

    # Find users who used this referral code and became drivers
    referred_users_cursor = db.users.find({"referral_code_used": referral_code})
    referred_users = (
        await referred_users_cursor.to_list(100)
        if hasattr(referred_users_cursor, "to_list")
        else list(referred_users_cursor)
    )

    referred_drivers = []
    for user in referred_users:
        referred_driver = await db.drivers.find_one({"user_id": user["id"]})
        if referred_driver:
            # Get completed rides count
            completed_rides = await db.rides.count_documents(
                {"driver_id": referred_driver["id"], "status": "completed"}
            )
            referred_drivers.append(
                {
                    "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or "Driver",
                    "email": user.get("email", ""),
                    "referred_at": user.get("created_at", ""),
                    "total_trips": completed_rides,
                    "status": "active" if completed_rides > 0 else "pending",
                }
            )

    return {"referred_drivers": referred_drivers[:limit]}


# ─── Catch-all driver ID routes MUST be last to avoid shadowing named routes ───


@api_router.get("/{driver_id}")
async def get_driver(driver_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({"id": driver_id})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    return serialize_doc(driver)


@api_router.put("/{driver_id}/status")
async def update_driver_status(
    driver_id: str,
    # `embed=True` so FastAPI accepts `{"is_online": true}` as the JSON body
    # instead of interpreting a bare primitive as the whole body. Without the
    # explicit Body() wrapper, FastAPI treats non-Pydantic primitives as
    # query parameters, and the mobile client (which posts it as body) got
    # a 422 and surfaced it as "Request failed".
    is_online: bool = Body(..., embed=True),
    current_user: dict = Depends(get_current_user),
):
    driver = await db.drivers.find_one({"id": driver_id})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    if driver.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Ban check: prevent banned drivers from going online
    if is_online and driver.get("status") == "banned":
        raise HTTPException(
            status_code=403, detail="Your account has been permanently suspended due to policy violations."
        )
    if is_online and driver.get("status") == "suspended":
        raise HTTPException(status_code=403, detail="Your account is currently suspended. Please contact support.")
    if is_online and driver.get("status") == "needs_review":
        raise HTTPException(
            status_code=400, detail="Your account is under review. Please wait for admin approval before going online."
        )
    if is_online and driver.get("status") not in ("active",):
        # Pending, rejected, or any unknown status
        if not driver.get("is_verified", False) and driver.get("status") != "active":
            raise HTTPException(
                status_code=400, detail="Your driver profile has not been verified yet. Please wait for admin approval."
            )

    if is_online:
        now = datetime.utcnow()

        # Prefer the dynamic driver_documents collection over the legacy
        # top-level expiry fields on the drivers row. The legacy fields are
        # only written once during onboarding and never refreshed when a
        # driver re-uploads a document, which used to leave drivers stuck
        # offline even after admin re-approval.
        try:
            approved_docs = await db.driver_documents.find(
                {
                    "driver_id": driver_id,
                    "status": "approved",
                }
            ).to_list(200)
        except Exception:
            approved_docs = []

        def _parse_expiry(val):
            if not val:
                return None
            if isinstance(val, datetime):
                return val.replace(tzinfo=None) if val.tzinfo else val
            if isinstance(val, str):
                try:
                    dt = datetime.fromisoformat(val.replace("Z", "+00:00").replace("+00:00", ""))
                    return dt.replace(tzinfo=None) if dt.tzinfo else dt
                except ValueError:
                    return None
            return None

        # For each mandatory requirement, the latest approved doc wins. If
        # it has an expiry and that expiry is in the past, block.
        try:
            requirements = await db.driver_requirements.find({}).to_list(100)
        except Exception:
            requirements = []
        mandatory_reqs = [r for r in (requirements or []) if r.get("is_mandatory")]

        covered_legacy_fields = set()
        for req_row in mandatory_reqs:
            req_id = req_row.get("id")
            req_name = req_row.get("name") or "Document"
            # Pick the most recent approved doc for this requirement.
            docs = [d for d in approved_docs if d.get("requirement_id") == req_id]
            if not docs:
                continue
            docs.sort(key=lambda d: str(d.get("uploaded_at") or ""), reverse=True)
            latest = docs[0]
            exp = _parse_expiry(latest.get("expiry_date") or latest.get("expires_at"))
            if exp and exp < now:
                raise HTTPException(
                    status_code=400,
                    detail=f"{req_name} has expired. Please update your documents before going online.",
                )
            # This requirement was covered by a fresh doc — do not re-check
            # the legacy column for the same thing below.
            nm = (req_name or "").lower()
            if "license" in nm or "driving" in nm or "permit" in nm:
                covered_legacy_fields.add("license_expiry_date")
            if "insurance" in nm:
                covered_legacy_fields.add("insurance_expiry_date")
            if "inspection" in nm:
                covered_legacy_fields.add("vehicle_inspection_expiry_date")
            if "background" in nm:
                covered_legacy_fields.add("background_check_expiry_date")

        # Legacy fallback: only enforce top-level expiry columns that were
        # NOT already satisfied by a fresh approved doc above.
        expiry_checks = [
            ("license_expiry_date", "Driving license"),
            ("insurance_expiry_date", "Vehicle insurance"),
            ("vehicle_inspection_expiry_date", "Vehicle inspection"),
            ("background_check_expiry_date", "Background check"),
        ]
        for field, label in expiry_checks:
            if field in covered_legacy_fields:
                continue
            expiry_val = driver.get(field)
            if expiry_val:
                if isinstance(expiry_val, str):
                    try:
                        expiry_val = datetime.fromisoformat(expiry_val.replace("Z", "+00:00").replace("+00:00", ""))
                    except ValueError:
                        continue
                if expiry_val < now:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{label} has expired ({field}). Please update your documents before going online.",
                    )

        # is_verified check removed — status field is the single source of truth now.
        # Only status='active' drivers reach this point (blocked above).

        # Check active Spinr Pass subscription. The enforcement is toggled
        # by the admin-controlled app setting `require_driver_subscription`
        # so the business team can flip it without a redeploy. When the
        # setting is false (default, pre-launch), we skip the subscription
        # query entirely — the `driver_subscriptions` table may not even
        # exist in the database yet during pre-launch, so querying it would
        # raise PostgREST PGRST205. The query only runs when enforcement
        # is actively turned on.
        try:
            from ..settings_loader import get_app_settings  # type: ignore
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        app_settings = await get_app_settings()
        require_sub = bool(app_settings.get("require_driver_subscription", False))

        if require_sub:
            try:
                sub = await db.driver_subscriptions.find_one(
                    {
                        "driver_id": driver_id,
                        "status": "active",
                    }
                )
            except Exception as e:
                # The table doesn't exist yet (PGRST205) or the query failed
                # for some other reason. Fail loudly with a clear message so
                # the operator knows the admin toggle was flipped before the
                # subscription infrastructure was ready.
                logger.error(f"driver_subscriptions lookup failed: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Spinr Pass enforcement is enabled in settings but the "
                        "driver_subscriptions table is not available. Disable "
                        'the "Require Spinr Pass to go online" toggle in admin '
                        "settings, or finish the subscription setup first."
                    ),
                ) from e

            if not sub:
                raise HTTPException(
                    status_code=402,
                    detail="You need an active Spinr Pass subscription to go online. Subscribe from your dashboard.",
                )

            # Check expiry on the active subscription row.
            if sub.get("expires_at"):
                try:
                    exp = datetime.fromisoformat(str(sub["expires_at"]).replace("Z", "+00:00").replace("+00:00", ""))
                    if exp < datetime.utcnow():
                        await db.driver_subscriptions.update_one({"id": sub["id"]}, {"$set": {"status": "expired"}})
                        raise HTTPException(
                            status_code=402,
                            detail="Your Spinr Pass has expired. Please renew to go online.",
                        )
                except HTTPException:
                    raise
                except Exception:  # noqa: S110
                    # Malformed expiry string, unparseable date, etc. — let
                    # the driver go online rather than blocking on a data bug.
                    pass

    logger.info(
        f"[GO-ONLINE] handler CALL update_one driver_id={driver_id} "
        f"requested_is_online={is_online} "
        f"pre_update_row_is_online={driver.get('is_online')} "
        f"pre_update_row_is_available={driver.get('is_available')}"
    )
    await db.drivers.update_one(
        {"id": driver_id},
        {"$set": {"is_online": is_online, "is_available": is_online, "updated_at": datetime.utcnow().isoformat()}},
    )

    # Verify the update actually landed. db_supabase.update_one silently
    # returns None if the write matched zero rows (RLS deny, schema cache miss,
    # wrong key role, etc.), which would otherwise leak out as a fake
    # {success: true} response and a driver-app that claims "You're online"
    # while the DB row never changes. Re-read the row and raise loudly if the
    # flag did not flip.
    verify = await db.drivers.find_one({"id": driver_id})
    logger.info(
        f"[GO-ONLINE] handler VERIFY driver_id={driver_id} "
        f"post_update_is_online={verify.get('is_online') if verify else 'ROW_GONE'} "
        f"post_update_is_available={verify.get('is_available') if verify else 'ROW_GONE'} "
        f"post_update_updated_at={verify.get('updated_at') if verify else 'ROW_GONE'}"
    )
    if verify is None:
        logger.error(f"[go-online] driver row disappeared immediately after update: driver_id={driver_id}")
        raise HTTPException(status_code=500, detail="Driver row missing after status update.")
    if bool(verify.get("is_online")) != bool(is_online):
        logger.error(
            f"[go-online] silent no-op: driver_id={driver_id} "
            f"requested is_online={is_online} but DB still shows "
            f"is_online={verify.get('is_online')}. "
            f"Likely causes: SUPABASE_SERVICE_ROLE_KEY in backend .env is "
            f"the anon key (not service_role), or RLS is enabled on drivers "
            f"with no permissive UPDATE policy for the role in use."
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Status update did not apply. Backend Supabase credentials "
                "may be misconfigured — verify SUPABASE_SERVICE_ROLE_KEY."
            ),
        )

    return {"success": True, "is_online": is_online}


# ============================================================
# Spinr Pass — Driver Subscription
# ============================================================


@api_router.get("/subscription/plans")
async def get_subscription_plans(current_user: dict = Depends(get_current_user)):
    """Get available subscription plans for the driver's service area.

    Respects the per-area kill switch: if the driver's service area has
    spinr_pass_enabled=false, returns an empty list so the driver never
    sees subscription options.
    """
    driver = await db.drivers.find_one({"user_id": current_user["id"]})

    # Check the area-level kill switch — when Spinr Pass is disabled for
    # the driver's area, return a friendly free-ride message instead of plans.
    if driver and driver.get("service_area_id"):
        area = await db.service_areas.find_one({"id": driver["service_area_id"]})
        if area and area.get("spinr_pass_enabled") is False:
            return {
                "plans": [],
                "free_mode": True,
                "message": "No subscription needed — you're riding free right now! Drive on and enjoy the open road.",
            }

    plans = await db.subscription_plans.find({"is_active": True}).to_list(50)

    # Filter by driver's service area if plans have area restrictions
    if driver:
        driver_area = driver.get("service_area_id")
        filtered = []
        for p in plans:
            plan_areas = p.get("service_areas")
            if plan_areas is None or (driver_area and driver_area in plan_areas):
                filtered.append(p)
            elif not plan_areas:  # empty list = all areas
                filtered.append(p)
        plans = filtered

    return {"plans": plans, "free_mode": False, "message": None}


@api_router.get("/subscription/current")
async def get_current_subscription(current_user: dict = Depends(get_current_user)):
    """Get driver's active subscription."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        return {"has_subscription": False, "subscription": None}

    sub = await db.driver_subscriptions.find_one(
        {
            "driver_id": driver["id"],
            "status": "active",
        }
    )

    if not sub:
        return {"has_subscription": False, "subscription": None}

    # Check if expired
    if sub.get("expires_at"):
        from datetime import datetime

        try:
            exp = datetime.fromisoformat(str(sub["expires_at"]).replace("Z", "+00:00"))
            if exp.tzinfo:
                exp = exp.replace(tzinfo=None)
            if exp < datetime.utcnow():
                await db.driver_subscriptions.update_one({"id": sub["id"]}, {"$set": {"status": "expired"}})
                return {"has_subscription": False, "subscription": None, "expired": True}
        except Exception:  # noqa: S110
            pass

    # Get today's ride count
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_rides = await db.rides.count_documents(
        {
            "driver_id": driver["id"],
            "status": "completed",
            "ride_completed_at": {"$gte": today_start.isoformat()},
        }
    )

    rides_per_day = sub.get("rides_per_day", -1)
    rides_remaining = "unlimited" if rides_per_day == -1 else max(0, rides_per_day - today_rides)

    return {
        "has_subscription": True,
        "subscription": sub,
        "today_rides": today_rides,
        "rides_remaining": rides_remaining,
        "can_accept_rides": rides_per_day == -1 or today_rides < rides_per_day,
    }


@api_router.post("/subscription/subscribe")
async def subscribe_to_plan(request: Request, current_user: dict = Depends(get_current_user)):
    """Subscribe driver to a plan."""
    data = await request.json()
    plan_id = data.get("plan_id")

    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    # Block subscription if Spinr Pass is disabled for this area
    if driver.get("service_area_id"):
        area = await db.service_areas.find_one({"id": driver["service_area_id"]})
        if area and area.get("spinr_pass_enabled") is False:
            raise HTTPException(status_code=403, detail="Spinr Pass is not available in your service area")

    plan = await db.subscription_plans.find_one({"id": plan_id, "is_active": True})
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found or inactive")

    # Check for existing active subscription
    existing = await db.driver_subscriptions.find_one(
        {
            "driver_id": driver["id"],
            "status": "active",
        }
    )
    if existing:
        # Cancel old subscription
        await db.driver_subscriptions.update_one(
            {"id": existing["id"]}, {"$set": {"status": "cancelled", "cancelled_at": datetime.utcnow().isoformat()}}
        )

    # Create new subscription
    now = datetime.utcnow()
    expires = now + timedelta(days=plan.get("duration_days", 30))

    subscription = {
        "id": str(uuid.uuid4()),
        "driver_id": driver["id"],
        "plan_id": plan["id"],
        "plan_name": plan["name"],
        "price": plan["price"],
        "rides_per_day": plan.get("rides_per_day", -1),
        "duration_days": plan.get("duration_days", 30),
        "status": "active",
        "started_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "payment_status": "paid",  # TODO: Stripe charge
        "created_at": now.isoformat(),
    }

    await db.driver_subscriptions.insert_one(subscription)

    # Update plan subscriber count
    await db.subscription_plans.update_one(
        {"id": plan_id}, {"$set": {"subscriber_count": (plan.get("subscriber_count", 0) or 0) + 1}}
    )

    logger.info(f"Driver {driver['id']} subscribed to {plan['name']} (${plan['price']})")

    return {"success": True, "subscription": subscription}


@api_router.post("/subscription/cancel")
async def cancel_subscription(current_user: dict = Depends(get_current_user)):
    """Cancel driver's active subscription."""
    driver = await db.drivers.find_one({"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    sub = await db.driver_subscriptions.find_one(
        {
            "driver_id": driver["id"],
            "status": "active",
        }
    )
    if not sub:
        raise HTTPException(status_code=400, detail="No active subscription")

    await db.driver_subscriptions.update_one(
        {"id": sub["id"]}, {"$set": {"status": "cancelled", "cancelled_at": datetime.utcnow().isoformat()}}
    )

    return {"success": True}
