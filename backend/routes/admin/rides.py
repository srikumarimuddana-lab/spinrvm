import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...features import send_push_notification
    from ...geo_utils import calculate_distance
    from ...settings_loader import get_app_settings
    from ...socket_manager import manager
    from ...utils.audit_logger import log_admin_action
    from ...utils.insurance_periods import record_period_transition
    from ...utils.rate_limiter import default_limiter as limiter
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from features import send_push_notification
    from geo_utils import calculate_distance
    from settings_loader import get_app_settings
    from socket_manager import manager
    from utils.audit_logger import log_admin_action
    from utils.insurance_periods import record_period_transition
    from utils.rate_limiter import default_limiter as limiter

from .drivers import _batch_fetch_drivers_and_users, _user_display_name

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Rides ----------


@router.get("/rides")
async def admin_get_rides(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    is_scheduled: Optional[bool] = None,
):
    """Get all rides with filters, enriched with rider_name and driver_name. Returns paginated.

    ``is_scheduled=true`` returns rider-requested scheduled rides (future pickup).
    These live alongside regular rides with ``status="searching"`` until the
    dispatcher picks them up at scheduled_time, so an explicit filter is the
    only way to see the upcoming queue.
    """
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    if is_scheduled is not None:
        filters["is_scheduled"] = is_scheduled

    # Get total count for pagination
    total_count = await db_supabase.count_documents("rides", filters)

    # Scheduled rides sort naturally by scheduled_time (earliest pickup first);
    # regular rides keep the created_at-desc feed.
    order_col = "scheduled_time" if is_scheduled else "created_at"
    order_desc = not is_scheduled
    rides = await db_supabase.get_rows("rides", filters, order=order_col, desc=order_desc, limit=limit, offset=offset)
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
                "driver_name": (
                    _user_display_name(driver_user) if driver_user else (driver.get("name") if driver else None)
                ),
            }
        )
    return {"rides": out, "total_count": total_count, "limit": limit, "offset": offset}


# ---------- Active Rides (Live Monitoring) ----------


@router.get("/rides/active")
async def admin_get_active_rides():
    """Get all active rides with driver locations for the live monitoring map."""
    active_statuses = [
        "searching",
        "driver_assigned",
        "driver_accepted",
        "driver_arrived",
        "in_progress",
    ]
    try:
        rides = await db.get_rows(
            "rides",
            {"status": {"$in": active_statuses}},
            limit=200,
            order="created_at",
        )
    except Exception as e:
        logger.error(f"Failed to fetch active rides: {e}")
        rides = []

    rider_ids = list({r.get("rider_id") for r in rides if r.get("rider_id")})
    driver_ids = list({r.get("driver_id") for r in rides if r.get("driver_id")})
    drivers_map, users_map = await _batch_fetch_drivers_and_users(rider_ids, driver_ids)

    result = []
    for r in rides:
        rider = users_map.get(r.get("rider_id"))
        driver = drivers_map.get(r.get("driver_id"))
        driver_user = users_map.get(driver.get("user_id")) if driver else None

        result.append(
            {
                "id": r["id"],
                "status": r.get("status"),
                "pickup_address": r.get("pickup_address"),
                "dropoff_address": r.get("dropoff_address"),
                "pickup_lat": r.get("pickup_lat"),
                "pickup_lng": r.get("pickup_lng"),
                "dropoff_lat": r.get("dropoff_lat"),
                "dropoff_lng": r.get("dropoff_lng"),
                "total_fare": r.get("total_fare"),
                "rider_name": _user_display_name(rider),
                "driver_name": (
                    _user_display_name(driver_user) if driver_user else (driver.get("name") if driver else None)
                ),
                "driver_lat": driver.get("lat") if driver else None,
                "driver_lng": driver.get("lng") if driver else None,
                "vehicle_type_id": r.get("vehicle_type_id"),
                "created_at": r.get("created_at"),
            }
        )

    return {"rides": result, "count": len(result)}


@router.get("/rides/unpaid")
async def admin_get_unpaid_rides(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: dict = Depends(get_admin_user),
):
    """Rides where payment failed and all retries are exhausted.

    These rides block the rider from booking new trips. Admin must
    either resolve payment manually or waive via POST /rides/{id}/complete.
    """
    rides = await db.get_rows(
        "rides",
        {
            "status": "completed",
            "payment_status": "failed",
        },
        limit=limit,
        offset=offset,
        order="created_at",
        desc=True,
    )

    rider_ids = list({r.get("rider_id") for r in rides if r.get("rider_id")})
    driver_ids = list({r.get("driver_id") for r in rides if r.get("driver_id")})
    drivers_map, users_map = await _batch_fetch_drivers_and_users(rider_ids, driver_ids)

    result = []
    for r in rides:
        rider = users_map.get(r.get("rider_id"))
        driver = drivers_map.get(r.get("driver_id"))
        driver_user = users_map.get(driver.get("user_id")) if driver else None
        result.append(
            {
                "id": r["id"],
                "status": r.get("status"),
                "payment_status": r.get("payment_status"),
                "payment_retry_count": r.get("payment_retry_count", 0),
                "total_fare": r.get("total_fare"),
                "tip_amount": r.get("tip_amount"),
                "pickup_address": r.get("pickup_address"),
                "dropoff_address": r.get("dropoff_address"),
                "rider_id": r.get("rider_id"),
                "rider_name": _user_display_name(rider),
                "rider_phone": (rider or {}).get("phone"),
                "driver_name": (
                    _user_display_name(driver_user) if driver_user else (driver.get("name") if driver else None)
                ),
                "created_at": r.get("created_at"),
                "ride_completed_at": r.get("ride_completed_at"),
            }
        )

    return {"rides": result, "count": len(result)}


# ---------- Admin Ride Actions ----------


class AdminCancelRideRequest(BaseModel):
    reason: Optional[str] = None


@router.post("/rides/{ride_id}/cancel")
async def admin_cancel_ride(
    ride_id: str,
    body: AdminCancelRideRequest,
    admin_user: dict = Depends(get_admin_user),
):
    """Admin force-cancels an in-flight ride from the live monitoring page.

    Terminal states are rejected. Driver (if assigned) is freed so they
    can immediately accept new requests. Rider + driver both receive a
    ws ride_cancelled push so their apps reset out of the active-ride
    flow — the rider's "Finding driver" screen was otherwise stuck
    showing forever because the UI had no cancel signal.
    """
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("status") in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail="Ride already completed or cancelled")

    reason = (body.reason or "Cancelled by admin").strip()[:500]
    now = datetime.now(timezone.utc)

    # Build the update in layers so we can gracefully degrade when the
    # target schema is behind.  Migration 37 added cancelled_at /
    # cancellation_reason / cancelled_by_admin_id; migration 38 added
    # cancelled_by / cancellation_type.  If either set of columns isn't
    # present yet, retry with a smaller payload so the admin's cancel
    # button still works instead of 503ing.
    minimal = {"status": "cancelled", "updated_at": now}
    with_37 = {
        **minimal,
        "cancelled_at": now,
        "cancellation_reason": reason,
        "cancelled_by_admin_id": admin_user.get("id"),
    }
    with_38 = {**with_37, "cancelled_by": "admin", "cancellation_type": "admin_cancel"}
    try:
        await db_supabase.update_ride(ride_id, with_38)
    except Exception:
        # DB write fallback path — first attempt failed (mig-38 columns may not
        # exist yet on this DB). Surface to Sentry so we know when/where the
        # primary path is failing; the retry below is the recovery action.
        logger.error(
            "admin_cancel_ride: attribution write failed; retrying without mig-38 fields",
            exc_info=True,
        )
        try:
            await db_supabase.update_ride(ride_id, with_37)
        except Exception as e37:
            original = getattr(e37, "details", {}).get("original") if hasattr(e37, "details") else None
            logger.error(
                f"admin_cancel_ride: update failed ride_id={ride_id} "
                f"admin_id={admin_user.get('id')} err={original or e37}"
            )
            raise

    verify = await db_supabase.get_ride(ride_id)
    if not verify or verify.get("status") != "cancelled":
        logger.error(f"admin_cancel_ride: silent no-op on {ride_id}")
        raise HTTPException(
            status_code=500,
            detail="Cancel did not persist — see backend logs.",
        )

    driver_user_id: str | None = None
    driver_id = ride.get("driver_id")
    if driver_id:
        try:
            await db_supabase.set_driver_available(driver_id, True)
        except Exception as e:
            logger.error(
                f"admin_cancel_ride: could not free driver {driver_id}: {e}",
                exc_info=True,
            )

        driver = await db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("user_id"):
            driver_user_id = driver["user_id"]
            await manager.send_personal_message(
                {"type": "ride_cancelled", "ride_id": ride_id, "reason": reason},
                f"driver_{driver_user_id}",
            )
            try:
                await send_push_notification(
                    driver_user_id,
                    "Ride Cancelled",
                    reason,
                    {"type": "ride_cancelled", "ride_id": ride_id},
                )
            except Exception as e:
                logger.warning(f"admin_cancel_ride: driver push failed: {e}")

    rider_id = ride.get("rider_id")
    if rider_id:
        await manager.send_personal_message(
            {"type": "ride_cancelled", "ride_id": ride_id, "reason": reason},
            f"rider_{rider_id}",
        )
        try:
            await send_push_notification(
                rider_id,
                "Ride Cancelled",
                reason,
                {"type": "ride_cancelled", "ride_id": ride_id},
            )
        except Exception as e:
            logger.warning(f"admin_cancel_ride: rider push failed: {e}")

    await manager.broadcast_ride_status(
        ride_id,
        "cancelled",
        rider_id=rider_id,
        driver_user_id=driver_user_id,
        reason=reason,
        source="admin",
    )
    try:
        await manager.broadcast_to_admins(
            {
                "type": "ride_cancelled",
                "ride_id": ride_id,
                "reason": reason,
                "source": "admin",
            }
        )
    except Exception as e:  # pragma: no cover - best effort
        logger.warning(f"admin_cancel_ride: admin broadcast failed: {e}")

    return {"success": True, "ride_id": ride_id, "status": "cancelled"}


@router.post("/rides/{ride_id}/complete")
async def admin_complete_ride(
    ride_id: str,
    admin_user: dict = Depends(get_admin_user),
):
    """Admin force-completes an in-flight ride from the live monitoring page.

    Driver (if assigned) is freed so they can immediately accept new requests.
    Rider + driver both receive a ws ride_completed push so their apps reset
    out of the active-ride flow. payment_status is set to ``waived_admin``
    so the rider's "needs payment" gate (rides.py get_active_ride) treats
    the trip as terminal — no real Stripe charge happened, so we must not
    impersonate ``paid``.
    """
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("status") not in ("driver_arrived", "in_progress"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete ride from state '{ride.get('status')}'",
        )

    now = datetime.now(timezone.utc)
    status_from = ride.get("status")

    update_data = {
        "status": "completed",
        "payment_status": "waived_admin",
        "updated_at": now,
        "ride_completed_at": now,
    }

    try:
        await db_supabase.update_ride(ride_id, update_data)
    except Exception as e:
        original = getattr(e, "details", {}).get("original") if hasattr(e, "details") else None
        logger.error(
            f"admin_complete_ride: update failed ride_id={ride_id} admin_id={admin_user.get('id')} err={original or e}"
        )
        raise HTTPException(status_code=500, detail="Failed to update ride status") from e

    # Audit + period transition are best-effort — they must never roll back
    # the ride status mutation that already succeeded above.
    await log_admin_action(
        admin_user,
        "force_complete_ride",
        "rides",
        ride_id,
        {"status_from": status_from},
    )

    driver_user_id: str | None = None
    driver_id = ride.get("driver_id")
    if driver_id:
        try:
            await db_supabase.set_driver_available(driver_id, True)
            await record_period_transition(driver_id, 1)
        except Exception as e:
            logger.error(
                f"admin_complete_ride: could not free driver {driver_id}: {e}",
                exc_info=True,
            )

        driver = await db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("user_id"):
            driver_user_id = driver["user_id"]
            await manager.send_personal_message(
                {"type": "ride_completed", "ride_id": ride_id},
                f"driver_{driver_user_id}",
            )

    rider_id = ride.get("rider_id")
    if rider_id:
        await manager.send_personal_message(
            {"type": "ride_completed", "ride_id": ride_id},
            f"rider_{rider_id}",
        )

    await manager.broadcast_ride_status(
        ride_id,
        "completed",
        rider_id=rider_id,
        driver_user_id=driver_user_id,
        source="admin",
    )
    try:
        await manager.broadcast_to_admins({"type": "ride_completed", "ride_id": ride_id, "source": "admin"})
    except Exception as e:  # pragma: no cover - best effort
        logger.warning(f"admin_complete_ride: admin broadcast failed: {e}")

    return {"success": True, "ride_id": ride_id, "status": "completed"}


@router.get("/places/autocomplete")
@limiter.limit("60/minute")
async def admin_places_autocomplete(
    request: Request,
    input: str = Query(..., min_length=1, max_length=200),
    session_token: Optional[str] = Query(default=None, max_length=64),
    location: Optional[str] = Query(default=None, max_length=50),
    radius: int = Query(default=50000, ge=1000, le=100000),
    admin_user: dict = Depends(get_admin_user),
):
    """Proxy Google Maps Places Autocomplete API to avoid exposing key to browser.

    Pass session_token to bundle N autocomplete + 1 details call into one billing session
    ($0.017 flat vs per-call). Generate one UUID per user typing session on the client.

    Pass ``location`` ("lat,lng") + ``radius`` (meters, default 50 km) to bias
    results to a point — typically the admin's geolocation or the ride's pickup —
    so a search like "Walmart" returns the nearest stores first instead of
    matches across Canada. Soft bias: distant well-known places still appear.
    Mirrors ``routes/maps_proxy.py:places_autocomplete``.
    """
    import httpx

    settings_row = await get_app_settings()
    api_key = (settings_row or {}).get("google_maps_api_key") or ""
    if not api_key:
        raise HTTPException(status_code=503, detail="Google Maps API key not configured")

    url = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
    params: dict = {
        "input": input,
        "key": api_key,
        "language": "en",
        "components": "country:ca",
    }
    if session_token:
        params["sessiontoken"] = session_token
    if location:
        params["location"] = location
        params["radius"] = str(radius)
        params["origin"] = location

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            if data.get("status") not in ("OK", "ZERO_RESULTS"):
                logger.error(f"Places autocomplete API error: {data.get('status')}")
                raise HTTPException(status_code=502, detail="Places API error")
            return {"predictions": data.get("predictions", [])}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to call Places autocomplete API: {e}")
        raise HTTPException(status_code=502, detail="Failed to call Places API") from e


@router.get("/places/details")
@limiter.limit("60/minute")
async def admin_places_details(
    request: Request,
    place_id: str = Query(...),
    session_token: Optional[str] = None,
    admin_user: dict = Depends(get_admin_user),
):
    """Proxy Google Maps Place Details API to get lat/lng.

    Pass the same session_token used for autocomplete to close the billing session.
    """
    import httpx

    settings_row = await get_app_settings()
    api_key = (settings_row or {}).get("google_maps_api_key") or ""
    if not api_key:
        raise HTTPException(status_code=503, detail="Google Maps API key not configured")

    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params: dict = {
        "place_id": place_id,
        "fields": "geometry,formatted_address",
        "key": api_key,
    }
    if session_token:
        params["sessiontoken"] = session_token

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
            if data.get("status") != "OK":
                logger.error(f"Places details API error: {data.get('status')}")
                raise HTTPException(status_code=502, detail="Places API error")

            loc = data.get("result", {}).get("geometry", {}).get("location", {})
            return {
                "lat": loc.get("lat"),
                "lng": loc.get("lng"),
                "formatted_address": data.get("result", {}).get("formatted_address"),
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to call Places details API: {e}")
        raise HTTPException(status_code=502, detail="Failed to call Places API") from e


@router.get("/rides/fare-estimate")
async def admin_fare_estimate(
    pickup_lat: float = Query(...),
    pickup_lng: float = Query(...),
    dropoff_lat: float = Query(...),
    dropoff_lng: float = Query(...),
    distance_km: float = Query(...),
    duration_minutes: int = Query(...),
    vehicle_type_id: str = Query(...),
    admin_user: dict = Depends(get_admin_user),
):
    """Admin-authenticated wrapper over the public /rides/fare-estimate.

    Returns the same payload (base, distance, time, booking, surge, area
    fees, taxes, grand total). Kept behind admin auth so admin actions on
    behalf of a rider remain auditable and so the admin dashboard does not
    need to mint a rider/driver token to call the rider proxy.
    """
    try:
        from ...features import fare_estimate as _public_fare_estimate
    except ImportError:  # pragma: no cover - dual import path
        from features import fare_estimate as _public_fare_estimate  # type: ignore

    return await _public_fare_estimate(
        pickup_lat=pickup_lat,
        pickup_lng=pickup_lng,
        dropoff_lat=dropoff_lat,
        dropoff_lng=dropoff_lng,
        distance_km=distance_km,
        duration_minutes=duration_minutes,
        vehicle_type_id=vehicle_type_id,
    )


class AdminPromoPreviewRequest(BaseModel):
    rider_id: str
    code: str
    ride_fare: Decimal


@router.post("/promo/preview")
async def admin_promo_preview(
    body: AdminPromoPreviewRequest,
    admin_user: dict = Depends(get_admin_user),
):
    """Validate a promo code on behalf of a rider without recording it.

    Used by the admin Create Ride modal to render the discount line in the
    fare breakdown before submitting. The same 10-rule validation runs as
    the rider self-service path (per-user limit, expiry, first-ride, etc.)
    so the admin cannot bypass restrictions by previewing.
    """
    try:
        from ..promotions import _validate_promo_for_user
    except ImportError:  # pragma: no cover - dual import path
        from routes.promotions import _validate_promo_for_user  # type: ignore

    validation = await _validate_promo_for_user(
        code=body.code,
        user_id=body.rider_id,
        ride_fare=body.ride_fare,
    )
    return {
        "valid": True,
        "code": validation["code"],
        "discount_type": validation["discount_type"],
        "discount_amount": validation["discount_amount"],
        "promo_id": validation["promo_id"],
        "description": validation.get("description", ""),
    }


class AdminCreateRideRequest(BaseModel):
    rider_id: str
    driver_id: Optional[str] = None
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str
    dropoff_lat: float
    dropoff_lng: float
    total_fare: Optional[Decimal] = None
    vehicle_type_id: Optional[str] = None
    subtotal_fare: Optional[Decimal] = None
    discount_amount: Optional[Decimal] = None
    promo_code: Optional[str] = None
    fare_overridden_by_admin: bool = False


@router.post("/rides/create")
async def admin_create_ride(
    body: AdminCreateRideRequest,
    admin_user: dict = Depends(get_admin_user),
):
    """Admin manually creates a ride, optionally assigning a driver directly.

    May optionally redeem a promo code on behalf of the rider — the promo
    is validated against the rider's per-user limit and the redemption is
    recorded in ``promo_applications`` against the rider_id, so it counts
    exactly like a rider-initiated apply.
    """
    now = datetime.now(timezone.utc)
    distance_km = calculate_distance(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng)

    status = "driver_assigned" if body.driver_id else "searching"

    # Apply the promo BEFORE the ride insert so a failed validation does
    # not leave behind a half-created ride. The promo_applications row is
    # written keyed to rider_id; the ride row references it via
    # promo_application_id.
    promo_application_id: Optional[str] = None
    discount_amount: Decimal = Decimal(body.discount_amount) if body.discount_amount is not None else Decimal("0")
    promo_code_normalised: Optional[str] = None
    if body.promo_code:
        try:
            from ..promotions import apply_promo_for_admin
        except ImportError:  # pragma: no cover - dual import path
            from routes.promotions import apply_promo_for_admin  # type: ignore

        # Validate against the pre-discount subtotal — falls back to the
        # admin-supplied total_fare when no breakdown was attached, so the
        # promo per-rider rules see the real ride value.
        promo_basis = body.subtotal_fare or body.total_fare or Decimal("0")
        admin_promo = await apply_promo_for_admin(
            code=body.promo_code,
            rider_id=body.rider_id,
            ride_fare=Decimal(promo_basis),
        )
        promo_application_id = admin_promo["application_id"]
        promo_code_normalised = admin_promo["code"]
        discount_amount = Decimal(admin_promo["discount_amount"])

    # Decimal stays Decimal — _serialize_for_api handles JSON encoding
    # without re-introducing float arithmetic. CLAUDE.md: money never
    # goes through float() on the way to the DB.
    ride_doc = {
        "id": str(uuid.uuid4()),
        "rider_id": body.rider_id,
        "driver_id": body.driver_id,
        "pickup_address": body.pickup_address,
        "pickup_lat": body.pickup_lat,
        "pickup_lng": body.pickup_lng,
        "dropoff_address": body.dropoff_address,
        "dropoff_lat": body.dropoff_lat,
        "dropoff_lng": body.dropoff_lng,
        "status": status,
        "distance_km": distance_km,
        "total_fare": body.total_fare if body.total_fare is not None else Decimal("0"),
        "subtotal_fare": body.subtotal_fare,
        "discount_amount": discount_amount,
        "promo_code": promo_code_normalised,
        "promo_application_id": promo_application_id,
        "fare_overridden_by_admin": bool(body.fare_overridden_by_admin),
        "payment_status": "pending",
        "vehicle_type_id": body.vehicle_type_id,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    try:
        await db_supabase.insert_one("rides", ride_doc)
    except Exception as e:
        original = getattr(e, "details", {}).get("original") if hasattr(e, "details") else None
        logger.error(
            f"admin_create_ride: insert failed admin_id={admin_user.get('id')} err={original or e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to create ride") from e

    # Audit is best-effort and must not roll back the ride insert above.
    await log_admin_action(
        admin_user,
        "admin_create_ride",
        "rides",
        ride_doc["id"],
        {
            "driver_id": body.driver_id,
            "status": status,
            "vehicle_type_id": body.vehicle_type_id,
            "subtotal_fare": (str(body.subtotal_fare) if body.subtotal_fare is not None else None),
            "discount_amount": str(discount_amount),
            "total_fare": str(ride_doc["total_fare"]),
            "promo_code": promo_code_normalised,
            "fare_overridden_by_admin": bool(body.fare_overridden_by_admin),
        },
    )

    if body.driver_id:
        try:
            await db_supabase.set_driver_available(body.driver_id, False)
            await record_period_transition(body.driver_id, 2, ride_id=ride_doc["id"])
        except Exception as e:
            logger.error(
                f"admin_create_ride: driver claim failed driver_id={body.driver_id}: {e}",
                exc_info=True,
            )

        driver = await db_supabase.get_driver_by_id(body.driver_id)
        if driver and driver.get("user_id"):
            rider = await db_supabase.get_user_by_id(body.rider_id)
            rider_name = _user_display_name(rider) if rider else ""

            import asyncio  # noqa: PLC0415

            try:
                from ...routes.rides import _offer_timeout_handler  # type: ignore[attr-defined]
            except ImportError:
                from routes.rides import _offer_timeout_handler  # type: ignore[attr-defined]

            _admin_settings = await get_app_settings()
            _admin_timeout = int(_admin_settings.get("ride_offer_timeout_seconds", 15))
            _offer_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=_admin_timeout + 15)).isoformat()

            dispatch_payload = {
                "type": "new_ride_assignment",
                "ride_id": ride_doc["id"],
                "pickup_address": ride_doc["pickup_address"],
                "dropoff_address": ride_doc["dropoff_address"],
                "pickup_lat": ride_doc["pickup_lat"],
                "pickup_lng": ride_doc["pickup_lng"],
                "dropoff_lat": ride_doc["dropoff_lat"],
                "dropoff_lng": ride_doc["dropoff_lng"],
                # WS uses send_json — cast at the wire boundary only.
                # DB write above kept Decimal precision via _serialize_for_api.
                "fare": float(ride_doc["total_fare"]),
                "distance_km": ride_doc["distance_km"],
                "duration_minutes": int(distance_km / 30 * 60) + 5,
                "rider_name": rider_name,
                "rider_rating": rider.get("rating") if rider else None,
                "countdown_seconds": _admin_timeout,
                "offer_expires_at": _offer_expires_at,
            }
            await manager.send_personal_message(
                dispatch_payload,
                f"driver_{driver['user_id']}",
            )
            await send_push_notification(
                driver["user_id"],
                "New ride request",
                f"{ride_doc['pickup_address']} → {ride_doc['dropoff_address']}",
                {k: str(v) for k, v in dispatch_payload.items() if v is not None},
                priority="dispatch",
            )
            asyncio.create_task(
                _offer_timeout_handler(
                    ride_doc["id"],
                    driver["id"],
                    rider_id=body.rider_id,
                    timeout_seconds=_admin_timeout + 15,
                )
            )

    try:
        await manager.broadcast_ride_status(
            ride_doc["id"],
            status,
            rider_id=body.rider_id,
            source="admin",
        )
    except Exception as e:  # pragma: no cover - best effort
        logger.warning(f"admin_create_ride: broadcast failed: {e}")

    return {"success": True, "ride_id": ride_doc["id"], "status": status}


# ---------- Stats ----------


@router.get("/stats")
async def admin_get_stats():
    """Get admin dashboard statistics."""
    import asyncio  # noqa: PLC0415

    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    # Parallelise all independent DB calls (F-49: previously 11 sequential round-trips).
    (
        total_drivers,
        online_drivers,
        total_rides,
        completed_rides,
        cancelled_rides,
        active_rides,
        total_users,
        rides_today,
        pending_applications,
        completed_today,
        completed_month,
    ) = await asyncio.gather(
        db_supabase.count_documents("drivers", {}),
        db_supabase.count_documents("drivers", {"is_online": True}),
        db_supabase.count_documents("rides", {}),
        db_supabase.count_documents("rides", {"status": "completed"}),
        db_supabase.count_documents("rides", {"status": "cancelled"}),
        db_supabase.count_documents(
            "rides",
            {
                "status": {
                    "$in": [
                        "requested",
                        "driver_assigned",
                        "driver_arrived",
                        "in_progress",
                    ]
                }
            },
        ),
        db_supabase.count_documents("users", {}),
        db_supabase.count_documents("rides", {"created_at": {"$gte": today_start}}),
        db_supabase.count_documents("drivers", {"is_verified": False}),
        db_supabase.get_rows(
            "rides",
            {"status": "completed", "ride_completed_at": {"$gte": today_start}},
            limit=5000,
        ),
        db_supabase.get_rows(
            "rides",
            {"status": "completed", "ride_completed_at": {"$gte": month_start}},
            limit=5000,
        ),
    )

    revenue_today = float(sum(Decimal(str(r.get("total_fare") or 0)) for r in (completed_today or [])))
    revenue_month = float(sum(Decimal(str(r.get("total_fare") or 0)) for r in completed_month))
    # Earnings + tip totals are aggregated over completed rides in the
    # current month; upstream's stats API never wired these up so we compute
    # them here rather than returning stale zeroes.
    total_driver_earnings = float(sum(Decimal(str(r.get("driver_earnings") or 0)) for r in completed_month))
    total_admin_earnings = float(sum(Decimal(str(r.get("admin_earnings") or 0)) for r in completed_month))
    total_tips = float(sum(Decimal(str(r.get("tip_amount") or 0)) for r in completed_month))
    return {
        # Fields the dashboard page expects
        "total_rides": total_rides,
        "completed_rides": completed_rides,
        "cancelled_rides": cancelled_rides,
        "active_rides": active_rides,
        "total_drivers": total_drivers,
        "online_drivers": online_drivers,
        "total_users": total_users,
        "total_driver_earnings": total_driver_earnings,
        "total_admin_earnings": total_admin_earnings,
        "total_tips": total_tips,
        # Legacy fields kept for other consumers
        "active_drivers": online_drivers,
        "rides_today": rides_today,
        "revenue_today": revenue_today,
        "revenue_month": revenue_month,
        "pending_applications": pending_applications,
    }


@router.get("/rides/stats")
async def admin_get_ride_stats():
    """Get ride count/revenue stats for today, yesterday, this week, this month, plus daily chart data."""
    now = datetime.now(timezone.utc)
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
        "rides",
        {"status": "completed", "ride_completed_at": {"$gte": today_start.isoformat()}},
        limit=10000,
    )
    total_revenue = float(sum(Decimal(str(r.get("total_fare") or 0)) for r in completed_today))
    total_tips = float(sum(Decimal(str(r.get("tip_amount") or 0)) for r in completed_today))
    completed_count = len(completed_today)

    # Monthly completed rides for revenue
    completed_month = await db_supabase.get_rows(
        "rides",
        {"status": "completed", "ride_completed_at": {"$gte": month_start.isoformat()}},
        limit=10000,
    )
    month_revenue = float(sum(Decimal(str(r.get("total_fare") or 0)) for r in completed_month))

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

    snapshot = ride.get("fare_breakdown_snapshot")
    fare_locked = False
    if snapshot and isinstance(snapshot, dict) and snapshot.get("lines"):
        try:
            settings = await get_app_settings()
            fare_locked = settings.get("fare_lock_enabled", False) if settings else False
        except Exception:
            fare_locked = False

    invoice_data: dict = {
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
        "actual_distance_km": ride.get("actual_distance_km"),
        "fare_locked": fare_locked,
    }

    if fare_locked and snapshot:
        invoice_data["fare_breakdown"] = snapshot["lines"]
        invoice_data["grand_total"] = snapshot.get("grand_total")
    else:
        invoice_data["grand_total"] = ride.get("grand_total") or ride.get("total_fare", 0)

    return invoice_data


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
            # User-visible upstream failure (admin sees missing map). Per
            # CLAUDE.md observability rules, route to Sentry as error.
            logger.error(
                "Static Maps returned %s for ride %s: %s",
                resp.status_code,
                ride_id,
                resp.text[:200],
            )
            raise HTTPException(status_code=502, detail="Failed to fetch route map")
        return Response(
            content=resp.content,
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except httpx.HTTPError as e:
        logger.error(
            "Static Maps fetch error for ride %s",
            ride_id,
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail="Failed to fetch route map") from e


_HEATMAP_MAX_ROWS = 5_000
_HEATMAP_COLUMNS = "pickup_lat,pickup_lng,dropoff_lat,dropoff_lng,corporate_account_id"


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

    # Fetch only the 5 coordinate/billing columns — ride rows carry large JSONB
    # fields (route_polyline, phase_polylines) that are irrelevant here and
    # would cause OOM when multiplied across thousands of rows.
    rides = await db_supabase.get_rows(
        "rides",
        query_filters,
        order="created_at",
        desc=True,
        limit=_HEATMAP_MAX_ROWS,
        columns=_HEATMAP_COLUMNS,
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


# ---------- Earnings ----------


@router.get("/earnings")
async def admin_get_earnings(period: str = Query("month")):
    """Get earnings statistics from completed rides.

    Uses MongoDB aggregation to calculate totals from ride data.
    """
    # Calculate date range
    now = datetime.now(timezone.utc)
    if period == "day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = now - timedelta(days=7)
    else:  # month
        start_date = now - timedelta(days=30)

    start_date_str = start_date.isoformat()

    # Get completed rides since start_date
    completed_rides = await db_supabase.get_rows(
        "rides",
        {"status": "completed", "ride_completed_at": {"$gte": start_date_str}},
        limit=10000,
    )

    # Calculate totals
    total_revenue = float(sum(Decimal(str(r.get("total_fare") or 0)) for r in completed_rides))
    driver_earnings = float(sum(Decimal(str(r.get("driver_earnings") or 0)) for r in completed_rides))
    platform_fees = float(sum(Decimal(str(r.get("admin_earnings") or 0)) for r in completed_rides))

    return {
        "period": period,
        "total_revenue": total_revenue,
        "total_rides": len(completed_rides),
        "driver_earnings": driver_earnings,
        "platform_fees": platform_fees,
    }


# ---------- CEO-grade earnings overview ----------


_PERIOD_DAYS = {"7d": 7, "30d": 30, "mtd": None, "ytd": None}


def _resolve_period(period: str, now: datetime) -> tuple[datetime, datetime, datetime, datetime, int]:
    """Map a period label to (current_start, current_end, prev_start, prev_end, days).

    For mtd / ytd the previous-period window is the same length so the
    delta is apples-to-apples (e.g. comparing MTD against the same number
    of days in the prior month).
    """
    end = now
    if period == "mtd":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "ytd":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        days = _PERIOD_DAYS.get(period, 7) or 7
        start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)

    span = end - start
    prev_end = start
    prev_start = start - span
    days = max((end - start).days, 1)
    return start, end, prev_start, prev_end, days


def _delta_pct(current: float, previous: float) -> Optional[float]:
    """Period-over-period percentage. None when previous is 0 so the UI can
    render an em-dash instead of a misleading "+Inf%"."""
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _metric(current: float, previous: float) -> Dict[str, Any]:
    return {
        "current": round(float(current), 2),
        "previous": round(float(previous), 2),
        "delta_pct": _delta_pct(current, previous),
    }


def _active_subs_at(subs: list[Dict[str, Any]], cutoff: datetime) -> list[Dict[str, Any]]:
    """Snapshot active subscriptions at a point in time.

    A subscription counts as active at `cutoff` if it started on or before
    cutoff and either never ended OR ended after cutoff.
    """
    cutoff_iso = cutoff.isoformat()
    out = []
    for s in subs:
        started = s.get("started_at") or s.get("created_at") or ""
        if started > cutoff_iso:
            continue
        # ended_at / cancelled_at / expires_at — whichever Stripe wrote
        ended = s.get("cancelled_at") or s.get("expires_at") or ""
        if ended and ended <= cutoff_iso:
            continue
        out.append(s)
    return out


@router.get("/earnings/overview")
async def admin_get_earnings_overview(
    period: str = Query("7d", pattern="^(7d|30d|mtd|ytd)$"),
    service_area_id: Optional[str] = None,
):
    """CEO-grade earnings dashboard with period-over-period deltas + daily series.

    Returns the headline metrics a fleet-ops or exec dashboard needs to
    answer "is the business healthy" in one screen:
      - GBV (sum of completed-ride fares)
      - Net Revenue (platform per-ride margin + Spinr Pass MRR)
      - Take Rate % (net revenue / GBV)
      - Completed trips
      - Active riders (booked at least one ride in window)
      - Active drivers (completed at least one ride in window)
      - Avg fare per trip
      - Spinr Pass MRR (snapshot at window end)

    Every metric carries `current`, `previous` (same-length prior window),
    and `delta_pct` so the UI can render the period-over-period chip
    without a second round-trip.

    `daily_series` is the per-day GBV / trips / net-revenue array for
    the line chart below the cards.
    """
    now = datetime.now(timezone.utc)
    start, end, prev_start, prev_end, days = _resolve_period(period, now)

    # ── Rides ─────────────────────────────────────────────────────────────
    # Pull current + previous window in two queries. Bounded by completed-only
    # so cancellation rows don't inflate GBV.
    base_filter: Dict[str, Any] = {"status": "completed"}
    if service_area_id:
        base_filter["service_area_id"] = service_area_id

    current_rides = await db_supabase.get_rows(
        "rides",
        {**base_filter, "ride_completed_at": {"$gte": start.isoformat(), "$lte": end.isoformat()}},
        limit=50000,
    )
    previous_rides = await db_supabase.get_rows(
        "rides",
        {**base_filter, "ride_completed_at": {"$gte": prev_start.isoformat(), "$lte": prev_end.isoformat()}},
        limit=50000,
    )

    def _agg(rides: list) -> Dict[str, Any]:
        gbv = sum((Decimal(str(r.get("total_fare") or 0)) for r in rides), Decimal("0"))
        platform = sum((Decimal(str(r.get("admin_earnings") or 0)) for r in rides), Decimal("0"))
        rider_ids = {r.get("rider_id") for r in rides if r.get("rider_id")}
        driver_ids = {r.get("driver_id") for r in rides if r.get("driver_id")}
        return {
            "gbv": float(gbv),
            "platform": float(platform),
            "trips": len(rides),
            "riders": len(rider_ids),
            "drivers": len(driver_ids),
        }

    cur = _agg(current_rides)
    prev = _agg(previous_rides)

    # ── Spinr Pass MRR snapshots ─────────────────────────────────────────
    # Sum the monthly price of subscriptions active at each cutoff. Cheap
    # query — bounded by the count of subs ever created.
    all_subs = await db_supabase.get_rows("driver_subscriptions", {}, limit=10000)
    cur_mrr = float(sum((Decimal(str(s.get("price") or 0)) for s in _active_subs_at(all_subs, end)), Decimal("0")))
    prev_mrr = float(
        sum((Decimal(str(s.get("price") or 0)) for s in _active_subs_at(all_subs, prev_end)), Decimal("0"))
    )

    # Net revenue = per-ride platform margin + subscription MRR pro-rated
    # for the window length. For 7d window, attribute 7/30 of the MRR
    # snapshot as period revenue. Crude but matches how SaaS dashboards
    # show recognised-revenue when MRR is bundled with transactional rev.
    def _attribute_mrr(mrr_snapshot: float, window_days: int) -> float:
        return round(mrr_snapshot * (window_days / 30.0), 2)

    cur_net_revenue = cur["platform"] + _attribute_mrr(cur_mrr, days)
    prev_net_revenue = prev["platform"] + _attribute_mrr(prev_mrr, days)

    cur_take_rate = round((cur_net_revenue / cur["gbv"]) * 100, 2) if cur["gbv"] > 0 else 0.0
    prev_take_rate = round((prev_net_revenue / prev["gbv"]) * 100, 2) if prev["gbv"] > 0 else 0.0

    cur_avg_fare = round(cur["gbv"] / cur["trips"], 2) if cur["trips"] > 0 else 0.0
    prev_avg_fare = round(prev["gbv"] / prev["trips"], 2) if prev["trips"] > 0 else 0.0

    # ── Daily series for the line chart ───────────────────────────────────
    daily: Dict[str, Dict[str, Any]] = {}
    cursor = start.date()
    end_date = end.date()
    while cursor <= end_date:
        daily[cursor.isoformat()] = {"date": cursor.isoformat(), "gbv": 0.0, "trips": 0, "net_revenue": 0.0}
        cursor += timedelta(days=1)
    for r in current_rides:
        completed = r.get("ride_completed_at") or r.get("created_at") or ""
        day = completed[:10]
        if day in daily:
            daily[day]["gbv"] = round(daily[day]["gbv"] + float(Decimal(str(r.get("total_fare") or 0))), 2)
            daily[day]["trips"] += 1
            daily[day]["net_revenue"] = round(
                daily[day]["net_revenue"] + float(Decimal(str(r.get("admin_earnings") or 0))),
                2,
            )
    daily_series = list(daily.values())

    period_labels = {"7d": "Last 7 days", "30d": "Last 30 days", "mtd": "Month to date", "ytd": "Year to date"}

    return {
        "period": {
            "key": period,
            "label": period_labels[period],
            "days": days,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "prev_start": prev_start.isoformat(),
            "prev_end": prev_end.isoformat(),
        },
        "metrics": {
            "gbv": _metric(cur["gbv"], prev["gbv"]),
            "net_revenue": _metric(cur_net_revenue, prev_net_revenue),
            "take_rate_pct": _metric(cur_take_rate, prev_take_rate),
            "completed_trips": _metric(cur["trips"], prev["trips"]),
            "active_riders": _metric(cur["riders"], prev["riders"]),
            "active_drivers": _metric(cur["drivers"], prev["drivers"]),
            "avg_fare": _metric(cur_avg_fare, prev_avg_fare),
            "spinr_pass_mrr": _metric(cur_mrr, prev_mrr),
        },
        "daily_series": daily_series,
    }


# ---------- Exports ----------


_EXPORT_MAX_ROWS = 10_000


@router.get("/export/rides")
async def admin_export_rides(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = Query(1000, ge=1, le=_EXPORT_MAX_ROWS),
    admin: dict = Depends(get_admin_user),
):
    """Export rides data (schema: total_fare). Writes an audit log entry (F-41)."""
    import uuid  # noqa: PLC0415

    date_filter: Dict[str, Any] = {}
    if start_date:
        date_filter.setdefault("created_at", {})["$gte"] = start_date
    if end_date:
        date_filter.setdefault("created_at", {})["$lte"] = end_date + "T23:59:59Z"

    rides = await db_supabase.get_rows("rides", date_filter, order="created_at", desc=True, limit=limit)
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
                "driver_name": (
                    _user_display_name(driver_user) if driver_user else (driver.get("name") if driver else None)
                ),
            }
        )
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "export_rides",
            "entity_type": "rides",
            "entity_id": "export",
            "details": {
                "row_count": len(out),
                "start_date": start_date,
                "end_date": end_date,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"rides": out, "count": len(out)}


@router.get("/export/drivers")
async def admin_export_drivers(
    limit: int = Query(1000, ge=1, le=_EXPORT_MAX_ROWS),
    admin: dict = Depends(get_admin_user),
):
    """Export drivers data. Writes an audit log entry (F-41)."""
    import uuid  # noqa: PLC0415

    drivers = await db_supabase.get_rows("drivers", order="created_at", desc=True, limit=limit)
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
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "export_drivers",
            "entity_type": "drivers",
            "entity_id": "export",
            "details": {"row_count": len(out)},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"drivers": out, "count": len(out)}


# ---------- Payouts ----------


@router.get("/payouts")
async def admin_get_payouts(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Get all driver payouts with optional status filter."""
    filters = {}
    if status:
        filters["status"] = status
    try:
        payouts = await db.get_rows(
            "payouts",
            filters,
            order="created_at",
            desc=True,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        logger.error(f"Failed to fetch payouts: {e}")
        payouts = []

    driver_ids = list({p["driver_id"] for p in payouts if p.get("driver_id")})
    drivers_map, users_map = await _batch_fetch_drivers_and_users([], driver_ids)

    enriched = []
    for p in payouts:
        driver = drivers_map.get(p.get("driver_id")) or {}
        user = users_map.get(driver.get("user_id")) if driver.get("user_id") else None
        driver_name = _user_display_name(user) if user else "Unknown"
        enriched.append({**p, "driver_name": driver_name})
    return enriched


@router.get("/payouts/{payout_id}")
async def admin_get_payout(payout_id: str, _: dict = Depends(get_admin_user)):
    """Return a single payout record by ID."""
    payout = await db.find_one("payouts", {"id": payout_id})
    if not payout:
        raise HTTPException(status_code=404, detail="Payout not found")
    driver: dict = {}
    if payout.get("driver_id"):
        try:
            driver = await db.find_one("drivers", {"id": payout["driver_id"]}) or {}
        except Exception:
            # Money-adjacent DB lookup failure — payout response will render
            # with empty driver fields. Surface to Sentry so the gap is visible
            # before an admin acts on a payout missing context.
            logger.error(
                "payout driver lookup failed for %s",
                payout.get("driver_id"),
                exc_info=True,
            )
            driver = {}
    payout["driver_name"] = driver.get("full_name") or driver.get("name") or payout.get("driver_name")
    payout["driver_email"] = driver.get("email")
    payout["driver_phone"] = driver.get("phone")
    return payout


@router.post("/payouts/{payout_id}/retry")
async def admin_retry_payout(payout_id: str, admin: dict = Depends(get_admin_user)):
    """Retry a failed payout.

    Re-queues the payout for processing by setting its status back to
    'pending'. The payment-retry background loop picks it up within its
    next tick. Gated to finance and super_admin roles.
    """
    allowed_roles = {"finance", "super_admin"}
    if admin.get("role") not in allowed_roles:
        raise HTTPException(status_code=403, detail="role_required:finance")

    payout = await db.find_one("payouts", {"id": payout_id})
    if not payout:
        raise HTTPException(status_code=404, detail="Payout not found")
    if payout.get("status") not in ("failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot retry a payout in status '{payout.get('status')}' — only failed or cancelled payouts can be retried",
        )

    await db.update_one(
        "payouts",
        {"id": payout_id},
        {
            "status": "pending",
            "retry_requested_by": admin.get("id"),
            "retry_requested_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.info(f"Payout {payout_id} queued for retry by admin {admin.get('id')}")
    return {"success": True, "payout_id": payout_id, "status": "pending"}


@router.get("/payouts/stats")
async def admin_get_payout_stats():
    """Get payout stats: total paid, pending, failed."""
    try:
        all_payouts = await db.get_rows("payouts", {}, limit=10000)
    except Exception:
        all_payouts = []

    total_paid = float(sum(Decimal(str(p.get("amount", 0))) for p in all_payouts if p.get("status") == "completed"))
    total_pending = float(sum(Decimal(str(p.get("amount", 0))) for p in all_payouts if p.get("status") == "pending"))
    total_failed = float(sum(Decimal(str(p.get("amount", 0))) for p in all_payouts if p.get("status") == "failed"))

    return {
        "total_paid": round(total_paid, 2),
        "total_pending": round(total_pending, 2),
        "total_failed": round(total_failed, 2),
        "payout_count": len(all_payouts),
        "pending_count": sum(1 for p in all_payouts if p.get("status") == "pending"),
    }
