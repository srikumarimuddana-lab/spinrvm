import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...features import send_push_notification
    from ...geo_utils import calculate_distance
    from ...settings_loader import get_app_settings
    from ...socket_manager import manager
    from ...utils.audit_logger import log_admin_action
    from ...utils.google_places_new import (
        PLACES_NEW_AUTOCOMPLETE_FIELD_MASK,
        PLACES_NEW_AUTOCOMPLETE_URL,
        PLACES_NEW_DETAILS_FIELD_MASK,
        build_autocomplete_payload,
        legacy_details_from_new_response,
        legacy_predictions_from_new_response,
        places_new_details_url,
        places_new_headers,
    )
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
    from utils.google_places_new import (
        PLACES_NEW_AUTOCOMPLETE_FIELD_MASK,
        PLACES_NEW_AUTOCOMPLETE_URL,
        PLACES_NEW_DETAILS_FIELD_MASK,
        build_autocomplete_payload,
        legacy_details_from_new_response,
        legacy_predictions_from_new_response,
        places_new_details_url,
        places_new_headers,
    )
    from utils.insurance_periods import record_period_transition
    from utils.rate_limiter import default_limiter as limiter

from .drivers import _batch_fetch_drivers_and_users, _user_display_name

db = db_supabase  # legacy alias

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Rides ----------


_RIDES_SEARCH_COLUMNS = [
    "id",
    "ride_code",
    "pickup_address",
    "dropoff_address",
    "rider_id",
    "driver_id",
]

_VALID_SORT_COLUMNS = {
    "created_at",
    "total_fare",
    "status",
    "pickup_address",
    "scheduled_time",
    "ride_completed_at",
}


async def _resolve_name_search(search: str) -> tuple[list[str], list[str]]:
    """Look up rider/driver IDs whose display name matches the search term.

    Returns (matching_rider_ids, matching_driver_ids).
    """
    pattern = {"$regex": search, "$options": "i"}

    matching_users = await db_supabase.get_rows(
        "users",
        {"$or": [{"first_name": pattern}, {"last_name": pattern}]},
        columns="id",
        limit=200,
    )
    rider_ids = [u["id"] for u in matching_users if u.get("id")]

    matching_drivers = await db_supabase.get_rows(
        "drivers",
        {"name": pattern},
        columns="id",
        limit=200,
    )
    driver_ids = [d["id"] for d in matching_drivers if d.get("id")]

    return rider_ids, driver_ids


async def _build_rides_filters(
    status: Optional[str] = None,
    is_scheduled: Optional[bool] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    service_area_id: Optional[str] = None,
) -> Dict[str, Any]:
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    if is_scheduled is not None:
        filters["is_scheduled"] = is_scheduled
    if service_area_id:
        filters["service_area_id"] = service_area_id
    if date_from:
        iso = date_from if "T" in date_from else f"{date_from}T00:00:00+00:00"
        filters.setdefault("created_at", {})["$gte"] = iso
    if date_to:
        iso = date_to if "T" in date_to else f"{date_to}T23:59:59+00:00"
        filters.setdefault("created_at", {})["$lte"] = iso
    if search and search.strip():
        q = search.strip()
        pattern = {"$regex": q, "$options": "i"}
        or_clauses: list[Dict[str, Any]] = [{col: pattern} for col in _RIDES_SEARCH_COLUMNS]
        rider_ids_match, driver_ids_match = await _resolve_name_search(q)
        if rider_ids_match:
            or_clauses.append({"rider_id": {"$in": rider_ids_match}})
        if driver_ids_match:
            or_clauses.append({"driver_id": {"$in": driver_ids_match}})
        filters["$or"] = or_clauses
    return filters


async def _enrich_rides(rides: list) -> list:
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
    return out


@router.get("/rides")
async def admin_get_rides(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = None,
    is_scheduled: Optional[bool] = None,
    search: Optional[str] = Query(default=None, max_length=200),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    service_area_id: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = Query(default=None, pattern="^(asc|desc)$"),
):
    """Get rides with server-side search, filters, and pagination."""
    filters = await _build_rides_filters(status, is_scheduled, search, date_from, date_to, service_area_id)

    total_count = await db_supabase.count_documents("rides", filters)

    if sort_by and sort_by in _VALID_SORT_COLUMNS:
        order_col = sort_by
        order_desc = sort_dir == "desc" if sort_dir else True
    elif is_scheduled:
        order_col = "scheduled_time"
        order_desc = False
    else:
        order_col = "created_at"
        order_desc = True

    rides = await db_supabase.get_rows("rides", filters, order=order_col, desc=order_desc, limit=limit, offset=offset)
    out = await _enrich_rides(rides)
    return {"rides": out, "total_count": total_count, "limit": limit, "offset": offset}


_EXPORT_MAX_ROWS = 10_000


@router.get("/rides/export")
async def admin_export_filtered_rides(
    status: Optional[str] = None,
    is_scheduled: Optional[bool] = None,
    search: Optional[str] = Query(default=None, max_length=200),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    service_area_id: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = Query(default=None, pattern="^(asc|desc)$"),
    _: dict = Depends(get_admin_user),
):
    """Return all rides matching the active filters for CSV export (no pagination, max 10k rows).

    When no date range is provided, defaults to the last 24 hours to avoid
    accidentally dumping the entire rides table.
    """
    if not date_from and not date_to:
        date_from = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    filters = await _build_rides_filters(status, is_scheduled, search, date_from, date_to, service_area_id)

    if sort_by and sort_by in _VALID_SORT_COLUMNS:
        order_col = sort_by
        order_desc = sort_dir == "desc" if sort_dir else True
    elif is_scheduled:
        order_col = "scheduled_time"
        order_desc = False
    else:
        order_col = "created_at"
        order_desc = True

    rides = await db_supabase.get_rows("rides", filters, order=order_col, desc=order_desc, limit=_EXPORT_MAX_ROWS)
    out = await _enrich_rides(rides)
    return {"rides": out, "total_count": len(out)}


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
            columns=(
                "id,status,rider_id,driver_id,pickup_address,dropoff_address,"
                "pickup_lat,pickup_lng,dropoff_lat,dropoff_lng,"
                "total_fare,vehicle_type_id,created_at"
            ),
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
    """Proxy Google Places API (New) Autocomplete to avoid exposing key to browser.

    Pass session_token to group autocomplete calls with the final details call. Generate one UUID per user typing session on the client.

    Pass ``location`` ("lat,lng") + ``radius`` (meters, default 50 km) to restrict
    results to a point — typically the admin's geolocation or the ride's pickup —
    so a search like "Walmart" returns nearby stores instead of matches across
    Canada.
    Mirrors ``routes/maps_proxy.py:places_autocomplete``.
    """
    import httpx

    settings_row = await get_app_settings()
    api_key = (settings_row or {}).get("google_maps_api_key") or ""
    if not api_key:
        raise HTTPException(status_code=503, detail="Google Maps API key not configured")

    payload = build_autocomplete_payload(input, session_token, location, radius)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                PLACES_NEW_AUTOCOMPLETE_URL,
                headers=places_new_headers(api_key, PLACES_NEW_AUTOCOMPLETE_FIELD_MASK),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return {"predictions": legacy_predictions_from_new_response(data)}
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        logger.error("Places autocomplete(new) API error: %s", e.response.text[:500])
        raise HTTPException(status_code=502, detail="Places API error") from e
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
    """Proxy Google Places API (New) Place Details to get lat/lng.

    Pass the same session_token used for autocomplete to close the session.
    """
    import httpx

    settings_row = await get_app_settings()
    api_key = (settings_row or {}).get("google_maps_api_key") or ""
    if not api_key:
        raise HTTPException(status_code=503, detail="Google Maps API key not configured")

    params: dict = {}
    if session_token:
        params["sessionToken"] = session_token

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                places_new_details_url(place_id),
                headers=places_new_headers(api_key, PLACES_NEW_DETAILS_FIELD_MASK),
                params=params,
            )
            resp.raise_for_status()
            return legacy_details_from_new_response(resp.json())
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        logger.error("Places details(new) API error: %s", e.response.text[:500])
        raise HTTPException(status_code=502, detail="Places API error") from e
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


@router.get("/rides/trend")
async def admin_get_ride_trend(
    days: int = Query(default=14, ge=1, le=90),
):
    """Daily ride counts for the trend chart.

    Single DB round-trip: fetches only the created_at column for rides in the
    window, then buckets in Python. Replaces the 14-sequential-query pattern
    in /rides/stats daily_chart.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = today_start - timedelta(days=days - 1)

    rows = await db_supabase.get_rows(
        "rides",
        {"created_at": {"$gte": window_start.isoformat()}},
        columns="created_at",
        limit=50000,
    )

    buckets: Dict[str, int] = {}
    for i in range(days):
        d = (window_start + timedelta(days=i)).strftime("%Y-%m-%d")
        buckets[d] = 0
    for r in rows:
        ca = r.get("created_at")
        if not ca:
            continue
        day_key = ca[:10]
        if day_key in buckets:
            buckets[day_key] += 1

    daily_chart = [
        {
            "date": (window_start + timedelta(days=i)).strftime("%b %d"),
            "date_iso": (window_start + timedelta(days=i)).strftime("%Y-%m-%d"),
            "rides": buckets.get((window_start + timedelta(days=i)).strftime("%Y-%m-%d"), 0),
        }
        for i in range(days)
    ]

    return {"daily_chart": daily_chart, "days": days}


def _period_range(period: str, now: datetime) -> tuple[datetime, datetime, str]:
    """Return (start, end, label) for a stats period pill."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "yesterday":
        start = today_start - timedelta(days=1)
        return start, today_start, start.strftime("%b %d")
    if period == "week":
        start = today_start - timedelta(days=today_start.weekday())  # Monday
        end = start + timedelta(days=7)
        return start, min(end, now), f"{start.strftime('%b %d')} – {(end - timedelta(days=1)).strftime('%b %d')}"
    if period == "month":
        start = today_start.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
        return start, min(end, now), f"{start.strftime('%b %d')} – {(end - timedelta(days=1)).strftime('%b %d')}"
    # default: today
    return today_start, now, today_start.strftime("%b %d")


@router.get("/rides/financials")
async def admin_get_ride_financials(
    period: str = Query("today", pattern="^(today|yesterday|week|month)$"),
    admin: dict = Depends(get_admin_user),
):
    """Per-period financial rollup for the Rides dashboard pills.

    Breaks the platform's books for the selected window into: rider sales
    (actual, post-promo), gross fare, driver revenue, tips, incentives, GST
    collected (pass-through), promo applied, and platform sales before/after
    promotion. Counts use created_at; money uses completed rides by
    ride_completed_at.
    """
    now = datetime.now(timezone.utc)
    start, end, label = _period_range(period, now)

    rides_count = await db_supabase.get_ride_count_by_date_range(start.isoformat(), end.isoformat())

    completed = await db_supabase.get_rows(
        "rides",
        {"status": "completed", "ride_completed_at": {"$gte": start.isoformat(), "$lt": end.isoformat()}},
        limit=10000,
    )

    def _s(rows, key):
        return float(sum(Decimal(str(r.get(key) or 0)) for r in rows))

    def _rider_paid(r):
        # A free-ride promo persists grand_total = 0 (intentional) while
        # total_fare keeps the pre-promo subtotal. Use an explicit None check so
        # comped $0 rides aren't counted at full fare.
        gt = r.get("grand_total")
        if gt is None:
            gt = r.get("total_fare")
        return Decimal(str(gt or 0))

    rider_paid = float(sum(_rider_paid(r) for r in completed))
    gross_fare = _s(completed, "total_fare")
    driver_revenue = float(
        sum(
            Decimal(str(r.get("base_fare") or 0))
            + Decimal(str(r.get("distance_fare") or 0))
            + Decimal(str(r.get("time_fare") or 0))
            for r in completed
        )
    )
    tips = _s(completed, "tip_amount")
    gst_collected = _s(completed, "tax_amount")
    promo_applied = _s(completed, "discount_amount")
    area_fees = _s(completed, "area_fees_total")
    booking_airport = _s(completed, "admin_earnings")

    # Incentives paid out in the window (platform-funded; ride_incentive_claims
    # is append-only, keyed by claimed_at).
    incentives = 0.0
    try:
        claims = await db_supabase.get_rows(
            "ride_incentive_claims",
            {"claimed_at": {"$gte": start.isoformat(), "$lt": end.isoformat()}},
            limit=10000,
        )
        incentives = float(sum(Decimal(str(c.get("bonus_amount") or 0)) for c in claims))
    except Exception:
        logger.warning("ride_incentive_claims rollup failed for financials", exc_info=True)

    platform_before_promo = round(booking_airport + area_fees, 2)
    platform_after_promo = round(platform_before_promo - incentives - promo_applied, 2)
    # Driver all-in take = ride fare (100%) + tips + platform-funded incentives.
    driver_take = round(driver_revenue + tips + incentives, 2)

    return {
        "period": period,
        "label": label,
        "rides_count": rides_count,
        "completed_count": len(completed),
        "rider_paid": round(rider_paid, 2),
        "gross_fare": round(gross_fare, 2),
        "driver_revenue": round(driver_revenue, 2),
        "driver_take": driver_take,
        "tips": round(tips, 2),
        "incentives": round(incentives, 2),
        "gst_collected": round(gst_collected, 2),
        "promo_applied": round(promo_applied, 2),
        "area_fees": round(area_fees, 2),
        "platform_before_promo": platform_before_promo,
        "platform_after_promo": platform_after_promo,
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
        # PIPEDA: the driver's personal phone and plate are NOT placed on the
        # invoice (a distributable document). The app-wide driver_code gives
        # admins/support a reference without exposing driver PII.
        "driver_code": ride.get("driver_code", ""),
        "driver_vehicle": ride.get("driver_vehicle", ""),
        "actual_distance_km": ride.get("actual_distance_km"),
        "fare_locked": fare_locked,
        # Tax + area-fee breakdowns (migration 46) so the invoice PDF can render
        # GST/PST as separate line items — a Saskatchewan regulatory requirement.
        "tax_breakdown": ride.get("tax_breakdown") or {},
        "area_fees_breakdown": ride.get("area_fees_breakdown") or [],
    }

    if fare_locked and snapshot:
        invoice_data["fare_breakdown"] = snapshot["lines"]
        invoice_data["grand_total"] = snapshot.get("grand_total")
    else:
        invoice_data["grand_total"] = ride.get("grand_total") or ride.get("total_fare", 0)

    return invoice_data


@router.post("/rides/{ride_id}/send-receipt")
async def admin_send_ride_receipt(
    ride_id: str,
    admin_user: dict = Depends(get_admin_user),
):
    """Re-send the ride receipt email to the rider (admin "Send Invoice").

    This only emails the receipt — it never charges or re-settles the ride
    (that is the rider-owned /process-payment endpoint, which an admin token
    is not authorized to call). Delivery goes through email_provider
    (AWS SES primary, Resend guardrail).
    """
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    rider_id = ride.get("rider_id")
    rider = await db_supabase.get_user_by_id(rider_id) if rider_id else None
    if not rider or not rider.get("email"):
        # No address on file — surface clearly instead of a silent failure.
        raise HTTPException(status_code=422, detail="Rider has no email address on file")

    try:
        from ...services.payment_service import send_ride_receipt
    except ImportError:
        from services.payment_service import send_ride_receipt  # type: ignore

    tip_amount = Decimal(str(ride.get("tip_amount") or 0))
    sent = await send_ride_receipt(ride, rider_id, tip_amount)

    await log_admin_action(
        admin_user,
        "send_ride_receipt",
        "ride",
        ride_id,
        {"sent": bool(sent)},
    )

    if not sent:
        # Email provider rejected/failed — 502 so the operator knows it didn't go.
        raise HTTPException(status_code=502, detail="Receipt could not be sent — email provider unavailable")
    return {"sent": True, "ride_id": ride_id}


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

    # Serve the pre-rendered snapshot if available (avoids Google API call).
    snapshot_url = ride.get("route_snapshot_url")
    if snapshot_url:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(snapshot_url)
            if resp.status_code == 200:
                return Response(
                    content=resp.content,
                    media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"},
                )
        except Exception as exc:
            logger.warning("Snapshot fetch failed for ride %s, falling back to Google Static Maps: %s", ride_id, exc)

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


# ---------- Transaction-level earnings rows ----------


@router.get("/earnings/rides")
async def admin_get_earnings_rides(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    service_area_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
):
    """Per-ride money breakdown for finance reconciliation.

    Backs the date-filterable + CSV-exportable transaction table on
    the Rides Earnings tab. Different shape from /earnings (period
    aggregates) and /earnings/overview (PoP deltas + chart) — this
    endpoint returns the individual ride rows finance needs to tie
    out against Stripe charges and the bank ledger.

    Default window: last 30 days when no start/end is supplied.
    """
    now = datetime.now(timezone.utc)
    if not end_date:
        end_iso = now.isoformat()
    else:
        end_iso = end_date if "T" in end_date else f"{end_date}T23:59:59+00:00"
    if not start_date:
        start_iso = (now - timedelta(days=30)).isoformat()
    else:
        start_iso = start_date if "T" in start_date else f"{start_date}T00:00:00+00:00"

    filters: Dict[str, Any] = {
        "status": "completed",
        "ride_completed_at": {"$gte": start_iso, "$lte": end_iso},
    }
    if service_area_id:
        filters["service_area_id"] = service_area_id

    # Over-fetch by `limit + offset` and slice — Supabase helper doesn't
    # expose OFFSET natively. Bounded by the 10k limit cap so the worst
    # case is one full-table scan, which is fine for the finance use case
    # (monthly closeout is the realistic upper bound).
    fetch_size = limit + offset
    rides = await db_supabase.get_rows(
        "rides",
        filters,
        order="ride_completed_at",
        desc=True,
        limit=fetch_size,
    )
    page = rides[offset : offset + limit]

    # Batch-fetch driver + rider names. The reconciliation flow often
    # needs to attach a human name to a Stripe statement line, so this
    # is more than just cosmetic enrichment.
    driver_ids = list({r.get("driver_id") for r in page if r.get("driver_id")})
    rider_ids = list({r.get("rider_id") for r in page if r.get("rider_id")})
    drivers_map, users_map = await _batch_fetch_drivers_and_users(rider_ids, driver_ids)

    def _driver_name(did: Optional[str]) -> Optional[str]:
        if not did:
            return None
        d = drivers_map.get(did) or {}
        u = users_map.get(d.get("user_id")) if d.get("user_id") else None
        return _user_display_name(u) or d.get("name")

    enriched = []
    for r in page:
        rider = users_map.get(r.get("rider_id")) if r.get("rider_id") else None
        enriched.append(
            {
                "ride_id": r.get("id"),
                "ride_code": r.get("ride_code"),
                "status": r.get("status"),
                "total_fare": float(Decimal(str(r.get("total_fare") or 0))),
                "driver_earnings": float(Decimal(str(r.get("driver_earnings") or 0))),
                "admin_earnings": float(Decimal(str(r.get("admin_earnings") or 0))),
                "tip_amount": float(Decimal(str(r.get("tip_amount") or 0))),
                "tax_amount": float(Decimal(str(r.get("tax_amount") or 0))),
                "discount_amount": float(Decimal(str(r.get("discount_amount") or 0))),
                "surge_multiplier": float(Decimal(str(r.get("surge_multiplier") or 1))),
                "stripe_charge_id": r.get("stripe_charge_id"),
                "driver_id": r.get("driver_id"),
                "driver_name": _driver_name(r.get("driver_id")),
                "rider_id": r.get("rider_id"),
                "rider_name": _user_display_name(rider) if rider else None,
                "service_area_id": r.get("service_area_id"),
                "completed_at": r.get("ride_completed_at"),
                "created_at": r.get("created_at"),
            }
        )

    return {
        "rides": enriched,
        "total": len(rides),
        "offset": offset,
        "limit": limit,
    }


# ---------- CEO-grade earnings overview ----------


_PERIOD_DAYS = {"7d": 7, "30d": 30, "mtd": None, "ytd": None}

# Module-level so both the earnings overview and payouts overview
# endpoints render the same human label for a period key.
_PERIOD_LABELS = {
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "mtd": "Month to date",
    "ytd": "Year to date",
}


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
    # ── Rides — completed (the GBV / trips / net rev numerator) ──────────
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

    # ── Rides — cancelled (cancellation rate denominator + fee revenue) ──
    # Cancelled rides have null ride_completed_at, so filter on created_at
    # for the window. Same service-area scoping as completed rides above.
    cancelled_filter: Dict[str, Any] = {"status": "cancelled"}
    if service_area_id:
        cancelled_filter["service_area_id"] = service_area_id
    current_cancelled = await db_supabase.get_rows(
        "rides",
        {**cancelled_filter, "created_at": {"$gte": start.isoformat(), "$lte": end.isoformat()}},
        limit=50000,
    )
    previous_cancelled = await db_supabase.get_rows(
        "rides",
        {**cancelled_filter, "created_at": {"$gte": prev_start.isoformat(), "$lte": prev_end.isoformat()}},
        limit=50000,
    )

    # ── Disputes / refunds (resolved within window) ──────────────────────
    # Refunds are persisted on `disputes.refund_amount` — service area
    # isn't on the dispute row, so when a service-area filter is set we
    # narrow to disputes whose ride_id is in the current ride set.
    refunded_filter: Dict[str, Any] = {
        "resolved_at": {"$gte": start.isoformat(), "$lte": end.isoformat()},
    }
    refunded_filter_prev: Dict[str, Any] = {
        "resolved_at": {"$gte": prev_start.isoformat(), "$lte": prev_end.isoformat()},
    }
    current_refunds = await db_supabase.get_rows("disputes", refunded_filter, limit=10000)
    previous_refunds = await db_supabase.get_rows("disputes", refunded_filter_prev, limit=10000)
    if service_area_id:
        # Cross-reference against the ride set; cheaper than a join.
        current_ride_ids = {r.get("id") for r in current_rides + current_cancelled}
        previous_ride_ids = {r.get("id") for r in previous_rides + previous_cancelled}
        current_refunds = [d for d in current_refunds if d.get("ride_id") in current_ride_ids]
        previous_refunds = [d for d in previous_refunds if d.get("ride_id") in previous_ride_ids]

    def _agg(rides: list) -> Dict[str, Any]:
        gbv = sum((Decimal(str(r.get("total_fare") or 0)) for r in rides), Decimal("0"))
        platform = sum((Decimal(str(r.get("admin_earnings") or 0)) for r in rides), Decimal("0"))
        rider_ids = {r.get("rider_id") for r in rides if r.get("rider_id")}
        driver_ids = {r.get("driver_id") for r in rides if r.get("driver_id")}
        # Surge revenue: portion of total_fare attributable to the
        # surge multiplier. base = total / surge_mult → surge = total -
        # base. Only counts rides where surge actually applied (>1.0).
        surge_rev = Decimal("0")
        for r in rides:
            mult = Decimal(str(r.get("surge_multiplier") or 1.0))
            if mult > 1:
                fare = Decimal(str(r.get("total_fare") or 0))
                surge_rev += fare - (fare / mult)
        # Promo spend: discount_amount on completed rides — what we
        # gave away to acquire/retain the ride.
        promo = sum((Decimal(str(r.get("discount_amount") or 0)) for r in rides), Decimal("0"))
        promo_count = sum(1 for r in rides if Decimal(str(r.get("discount_amount") or 0)) > 0)
        # Tax decomposition from rides.tax_breakdown JSONB — defensive
        # because tax_name keys vary by service area config.
        gst = Decimal("0")
        pst = Decimal("0")
        for r in rides:
            breakdown = r.get("tax_breakdown") or {}
            if isinstance(breakdown, dict):
                for tax_name, info in breakdown.items():
                    amount = Decimal("0")
                    if isinstance(info, dict):
                        amount = Decimal(str(info.get("amount") or 0))
                    name_l = str(tax_name).lower()
                    if "gst" in name_l or name_l == "hst":
                        gst += amount
                    elif "pst" in name_l or "qst" in name_l:
                        pst += amount
        return {
            "gbv": float(gbv),
            "platform": float(platform),
            "trips": len(rides),
            "riders": len(rider_ids),
            "drivers": len(driver_ids),
            "surge_revenue": float(surge_rev),
            "promo_spend": float(promo),
            "promo_count": promo_count,
            "gst_collected": float(gst),
            "pst_collected": float(pst),
        }

    def _agg_cancelled(rides: list) -> Dict[str, Any]:
        """Cancellation-only aggregator. cancellation_fee_admin is the
        platform's share of the cancel fee — that's the revenue side."""
        cancel_revenue = sum(
            (Decimal(str(r.get("cancellation_fee_admin") or 0)) for r in rides),
            Decimal("0"),
        )
        # Rider vs driver vs system cancel breakdown — pulled from the
        # cancellation_reason text field with a simple keyword match.
        # Same convention the admin analytics page already uses.
        rider_cancels = 0
        driver_cancels = 0
        for r in rides:
            reason = (r.get("cancellation_reason") or "").lower()
            if "driver" in reason:
                driver_cancels += 1
            elif "rider" in reason or "user" in reason:
                rider_cancels += 1
        return {
            "count": len(rides),
            "revenue": float(cancel_revenue),
            "rider_cancels": rider_cancels,
            "driver_cancels": driver_cancels,
        }

    cur = _agg(current_rides)
    prev = _agg(previous_rides)
    cur_cx = _agg_cancelled(current_cancelled)
    prev_cx = _agg_cancelled(previous_cancelled)

    # Refund $ in window — disputes resolved during the period with a
    # non-zero refund_amount. We don't distinguish full vs partial here;
    # any payout to the rider counts as refund leakage.
    cur_refund_amt = float(sum((Decimal(str(d.get("refund_amount") or 0)) for d in current_refunds), Decimal("0")))
    prev_refund_amt = float(sum((Decimal(str(d.get("refund_amount") or 0)) for d in previous_refunds), Decimal("0")))
    cur_refund_count = sum(1 for d in current_refunds if Decimal(str(d.get("refund_amount") or 0)) > 0)
    prev_refund_count = sum(1 for d in previous_refunds if Decimal(str(d.get("refund_amount") or 0)) > 0)

    # Cancellation rate: cancelled / (completed + cancelled). Same
    # formula the ops cancellation-rate KPI uses across the codebase.
    def _cancel_rate(completed: int, cancelled: int) -> float:
        total = completed + cancelled
        return round((cancelled / total) * 100, 1) if total > 0 else 0.0

    cur_cancel_rate = _cancel_rate(cur["trips"], cur_cx["count"])
    prev_cancel_rate = _cancel_rate(prev["trips"], prev_cx["count"])

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

    return {
        "period": {
            "key": period,
            "label": _PERIOD_LABELS[period],
            "days": days,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "prev_start": prev_start.isoformat(),
            "prev_end": prev_end.isoformat(),
        },
        "metrics": {
            # Pass 1 — CEO row
            "gbv": _metric(cur["gbv"], prev["gbv"]),
            "net_revenue": _metric(cur_net_revenue, prev_net_revenue),
            "take_rate_pct": _metric(cur_take_rate, prev_take_rate),
            "completed_trips": _metric(cur["trips"], prev["trips"]),
            "active_riders": _metric(cur["riders"], prev["riders"]),
            "active_drivers": _metric(cur["drivers"], prev["drivers"]),
            "avg_fare": _metric(cur_avg_fare, prev_avg_fare),
            "spinr_pass_mrr": _metric(cur_mrr, prev_mrr),
            # Pass 2 — operational health
            "cancellation_rate_pct": _metric(cur_cancel_rate, prev_cancel_rate),
            "cancellation_revenue": _metric(cur_cx["revenue"], prev_cx["revenue"]),
            "cancelled_trips": _metric(cur_cx["count"], prev_cx["count"]),
            "refund_amount": _metric(cur_refund_amt, prev_refund_amt),
            "refund_count": _metric(cur_refund_count, prev_refund_count),
            "promo_spend": _metric(cur["promo_spend"], prev["promo_spend"]),
            "promo_count": _metric(cur["promo_count"], prev["promo_count"]),
            "surge_revenue": _metric(cur["surge_revenue"], prev["surge_revenue"]),
            "gst_collected": _metric(cur["gst_collected"], prev["gst_collected"]),
            "pst_collected": _metric(cur["pst_collected"], prev["pst_collected"]),
        },
        "cancellation_breakdown": {
            "current": {
                "rider": cur_cx["rider_cancels"],
                "driver": cur_cx["driver_cancels"],
                "system": cur_cx["count"] - cur_cx["rider_cancels"] - cur_cx["driver_cancels"],
            },
            "previous": {
                "rider": prev_cx["rider_cancels"],
                "driver": prev_cx["driver_cancels"],
                "system": prev_cx["count"] - prev_cx["rider_cancels"] - prev_cx["driver_cancels"],
            },
        },
        "daily_series": daily_series,
    }


# ---------- Exports ----------


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
    # Export rows only carry name/email/phone from the user row — project those
    # so the export doesn't read base64 profile_image for every driver.
    users_list = (
        await db_supabase.get_rows(
            "users",
            {"id": {"$in": user_ids}},
            columns="id,first_name,last_name,email,phone",
            limit=max(len(user_ids), 1),
        )
        if user_ids
        else []
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


@router.get("/payouts/overview")
async def admin_get_payouts_overview(
    period: str = Query("7d", pattern="^(7d|30d|mtd|ytd)$"),
    service_area_id: Optional[str] = None,
):
    """CEO/CFO-grade payouts dashboard.

    Mirrors the shape of admin_get_earnings_overview so the frontend can
    reuse MetricCard / DeltaChip / the period chip group. Answers
    "is payout flow healthy?" without forcing the operator to read the
    transaction log.

    Money helpers, _resolve_period and _metric live in the same file
    (added with the earnings overview endpoint) — re-used here.

    Service-area scoping: payouts have no service_area_id column, so we
    resolve area → driver_ids first and filter the payout set against
    that. Cheap because area-bounded driver lists are small.
    """
    now = datetime.now(timezone.utc)
    start, end, prev_start, prev_end, _days = _resolve_period(period, now)

    # ── Driver-set scoping when an area filter is set ────────────────────
    driver_id_filter: Optional[set[str]] = None
    if service_area_id:
        area_drivers = await db_supabase.get_rows("drivers", {"service_area_id": service_area_id}, limit=5000)
        driver_id_filter = {d["id"] for d in area_drivers if d.get("id")}
        if not driver_id_filter:
            # No drivers in this area — return an empty shell so the UI
            # renders zeroes instead of erroring out.
            empty = _metric(0.0, 0.0)
            return {
                "period": {
                    "key": period,
                    "label": _PERIOD_LABELS[period],
                    "days": _days,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "prev_start": prev_start.isoformat(),
                    "prev_end": prev_end.isoformat(),
                },
                "metrics": {
                    "outstanding_payable": empty,
                    "total_paid_out": empty,
                    "pending_in_flight": empty,
                    "failed_amount": empty,
                    "success_rate_pct": empty,
                    "median_time_to_payout_hours": empty,
                    "avg_payout_amount": empty,
                    "payouts_count": empty,
                },
                "daily_series": [],
            }

    def _in_scope(row: Dict[str, Any]) -> bool:
        return driver_id_filter is None or row.get("driver_id") in driver_id_filter

    # ── Outstanding payable balance ──────────────────────────────────────
    # Lifetime sum of driver_earnings on completed rides minus payouts
    # that have either completed or are mid-flight. NOT scoped to the
    # period — this is a "right now" snapshot. Showing PoP delta against
    # "same number of days ago" so the operator can see whether the
    # backlog is growing or shrinking.
    def _sum(rows: list, key: str) -> Decimal:
        return sum((Decimal(str(r.get(key) or 0)) for r in rows if _in_scope(r)), Decimal("0"))

    all_completed_rides = await db_supabase.get_rows("rides", {"status": "completed"}, limit=200000)
    all_payouts = await db_supabase.get_rows("payouts", {}, limit=200000)

    earned_total = _sum(all_completed_rides, "driver_earnings")
    paid_or_in_flight = sum(
        (
            Decimal(str(p.get("amount") or 0))
            for p in all_payouts
            if _in_scope(p) and p.get("status") in ("completed", "pending", "processing")
        ),
        Decimal("0"),
    )
    outstanding_now = max(earned_total - paid_or_in_flight, Decimal("0"))  # noqa: F841

    # Same calculation snapshot at prev_end so we can show delta.
    end_iso = end.isoformat()
    prev_end_iso = prev_end.isoformat()

    def _completed_up_to(rows: list, ts: str) -> Decimal:
        return sum(
            (
                Decimal(str(r.get("driver_earnings") or 0))
                for r in rows
                if _in_scope(r) and (r.get("ride_completed_at") or r.get("updated_at") or "") <= ts
            ),
            Decimal("0"),
        )

    def _paid_up_to(rows: list, ts: str) -> Decimal:
        return sum(
            (
                Decimal(str(p.get("amount") or 0))
                for p in rows
                if _in_scope(p)
                and p.get("status") in ("completed", "pending", "processing")
                and (p.get("created_at") or "") <= ts
            ),
            Decimal("0"),
        )

    outstanding_now_calc = _completed_up_to(all_completed_rides, end_iso) - _paid_up_to(all_payouts, end_iso)
    outstanding_prev = _completed_up_to(all_completed_rides, prev_end_iso) - _paid_up_to(all_payouts, prev_end_iso)
    outstanding_now_calc = max(outstanding_now_calc, Decimal("0"))
    outstanding_prev = max(outstanding_prev, Decimal("0"))

    # ── Period-windowed payout metrics ───────────────────────────────────
    def _in_window(p: Dict[str, Any], lo: datetime, hi: datetime) -> bool:
        # Completed payouts use processed_at when present (matches the
        # actual settlement date); pending/failed use created_at since
        # processed_at is null. Either way it's the operationally-relevant
        # timestamp.
        ts = p.get("processed_at") or p.get("created_at") or ""
        return lo.isoformat() <= ts <= hi.isoformat()

    cur_payouts = [p for p in all_payouts if _in_scope(p) and _in_window(p, start, end)]
    prev_payouts = [p for p in all_payouts if _in_scope(p) and _in_window(p, prev_start, prev_end)]

    def _by_status(rows: list, *statuses: str) -> list:
        return [p for p in rows if p.get("status") in statuses]

    cur_completed = _by_status(cur_payouts, "completed")
    prev_completed = _by_status(prev_payouts, "completed")
    cur_pending = _by_status(cur_payouts, "pending", "processing")
    prev_pending = _by_status(prev_payouts, "pending", "processing")
    cur_failed = _by_status(cur_payouts, "failed")
    prev_failed = _by_status(prev_payouts, "failed")

    def _amt(rows: list) -> float:
        return float(sum((Decimal(str(r.get("amount") or 0)) for r in rows), Decimal("0")))

    # ── Success rate ──
    # Pending excluded from denominator — outcome unknown. completed /
    # (completed + failed) over rows that have reached a terminal state.
    def _success_rate(completed: list, failed: list) -> float:
        terminal = len(completed) + len(failed)
        return round((len(completed) / terminal) * 100, 1) if terminal > 0 else 0.0

    cur_success = _success_rate(cur_completed, cur_failed)
    prev_success = _success_rate(prev_completed, prev_failed)

    # ── Median time to payout (hours) ──
    # Difference between created_at (request) and processed_at (Stripe
    # confirmed). Only over completed payouts that have both fields.
    def _median_hours(completed: list) -> float:
        deltas: list[float] = []
        for p in completed:
            requested = p.get("created_at")
            settled = p.get("processed_at")
            if not requested or not settled:
                continue
            try:
                t_req = datetime.fromisoformat(requested.replace("Z", "+00:00"))
                t_set = datetime.fromisoformat(settled.replace("Z", "+00:00"))
            except ValueError:
                continue
            secs = (t_set - t_req).total_seconds()
            if secs >= 0:
                deltas.append(secs / 3600.0)
        if not deltas:
            return 0.0
        deltas.sort()
        n = len(deltas)
        mid = n // 2
        return round((deltas[mid] if n % 2 else (deltas[mid - 1] + deltas[mid]) / 2.0), 2)

    cur_median_hours = _median_hours(cur_completed)
    prev_median_hours = _median_hours(prev_completed)

    # ── Avg payout amount ──
    cur_avg = round(_amt(cur_completed) / len(cur_completed), 2) if cur_completed else 0.0
    prev_avg = round(_amt(prev_completed) / len(prev_completed), 2) if prev_completed else 0.0

    # ── Daily series for the chart ──
    daily: Dict[str, Dict[str, Any]] = {}
    cursor = start.date()
    end_date = end.date()
    while cursor <= end_date:
        daily[cursor.isoformat()] = {
            "date": cursor.isoformat(),
            "paid_out": 0.0,
            "pending": 0.0,
            "failed": 0.0,
        }
        cursor += timedelta(days=1)
    for p in cur_payouts:
        ts = p.get("processed_at") or p.get("created_at") or ""
        day = ts[:10]
        if day not in daily:
            continue
        amt = float(Decimal(str(p.get("amount") or 0)))
        if p.get("status") == "completed":
            daily[day]["paid_out"] = round(daily[day]["paid_out"] + amt, 2)
        elif p.get("status") in ("pending", "processing"):
            daily[day]["pending"] = round(daily[day]["pending"] + amt, 2)
        elif p.get("status") == "failed":
            daily[day]["failed"] = round(daily[day]["failed"] + amt, 2)

    # ── Pass 2: failure breakdown + at-risk + blocked drivers ────────────

    # Failure reasons — group by error_message text. Tight bucket bag
    # so frontend just renders the top few. Empty / null message lumps
    # into "Unknown".
    failure_buckets: Dict[str, Dict[str, float]] = {}
    for p in cur_failed:
        reason_raw = (p.get("error_message") or "").strip()
        # Normalise long stripe error strings to the first 60 chars so
        # similar variants group together; full string is still on the
        # payout row if an operator clicks through.
        reason = (reason_raw[:60] + "…") if len(reason_raw) > 60 else (reason_raw or "Unknown")
        bucket = failure_buckets.setdefault(reason, {"count": 0, "amount": 0.0})
        bucket["count"] += 1
        bucket["amount"] += float(Decimal(str(p.get("amount") or 0)))
    failure_reasons = sorted(
        [{"reason": r, "count": int(b["count"]), "amount": round(b["amount"], 2)} for r, b in failure_buckets.items()],
        key=lambda x: (-x["count"], -x["amount"]),
    )[:8]

    # Stuck pending payouts — anything still pending more than 48h after
    # creation. These are the manual-intervention queue: Stripe webhook
    # didn't return or the rebalance is held up. NOT period-scoped —
    # this is a "right now" signal regardless of the selected window.
    stuck_threshold = (now - timedelta(hours=48)).isoformat()
    stuck_rows = [
        p
        for p in all_payouts
        if _in_scope(p)
        and p.get("status") in ("pending", "processing")
        and (p.get("created_at") or "") < stuck_threshold
    ]
    stuck_over_48h = {
        "count": len(stuck_rows),
        "amount": round(float(sum((Decimal(str(p.get("amount") or 0)) for p in stuck_rows), Decimal("0"))), 2),
    }

    # Blocked drivers — Stripe Connect KYC mirror says payouts are
    # disabled. Uses the partial index from migration 92. Outstanding
    # balance for these drivers is the operational "we can't pay
    # them yet" number.
    blocked_drivers_rows = await db_supabase.get_rows(
        "drivers",
        {"stripe_payouts_enabled": False, "stripe_account_id": {"$ne": None}},
        limit=500,
    )
    if driver_id_filter is not None:
        blocked_drivers_rows = [d for d in blocked_drivers_rows if d.get("id") in driver_id_filter]
    blocked_driver_ids = {d.get("id") for d in blocked_drivers_rows if d.get("id")}
    blocked_outstanding = Decimal("0")
    if blocked_driver_ids:
        for r in all_completed_rides:
            if r.get("driver_id") in blocked_driver_ids:
                blocked_outstanding += Decimal(str(r.get("driver_earnings") or 0))
        for p in all_payouts:
            if p.get("driver_id") in blocked_driver_ids and p.get("status") in ("completed", "pending", "processing"):
                blocked_outstanding -= Decimal(str(p.get("amount") or 0))
    blocked_drivers = {
        "count": len(blocked_driver_ids),
        "outstanding_balance": round(float(max(blocked_outstanding, Decimal("0"))), 2),
    }

    # Top earning drivers in window — sum of completed payout amounts
    # per driver. Capped at 10 so the response stays small.
    completed_by_driver: Dict[str, Dict[str, float]] = {}
    for p in cur_completed:
        did = p.get("driver_id")
        if not did:
            continue
        b = completed_by_driver.setdefault(did, {"amount": 0.0, "count": 0})
        b["amount"] += float(Decimal(str(p.get("amount") or 0)))
        b["count"] += 1
    top_driver_ids = sorted(completed_by_driver.keys(), key=lambda did: -completed_by_driver[did]["amount"])[:10]

    # At-risk drivers — ≥ 2 failures in window. Ops follows up
    # personally; multi-fail is rarely a transient issue.
    failed_by_driver: Dict[str, Dict[str, Any]] = {}
    for p in cur_failed:
        did = p.get("driver_id")
        if not did:
            continue
        b = failed_by_driver.setdefault(did, {"count": 0, "last_reason": None, "last_at": ""})
        b["count"] += 1
        ts = p.get("created_at") or ""
        if ts > b["last_at"]:
            b["last_at"] = ts
            b["last_reason"] = p.get("error_message") or "Unknown"
    at_risk_driver_ids = [did for did, b in failed_by_driver.items() if b["count"] >= 2]

    # Enrich names in a single batch fetch for both lists. Cheap because
    # the union of top + at-risk is bounded at ≤ 20 driver_ids.
    enrich_ids = list(set(top_driver_ids) | set(at_risk_driver_ids))
    drivers_map: Dict[str, Any] = {}
    users_map: Dict[str, Any] = {}
    if enrich_ids:
        drivers_map, users_map = await _batch_fetch_drivers_and_users([], enrich_ids)

    def _display_name(driver_id: str) -> str:
        d = drivers_map.get(driver_id) or {}
        u = users_map.get(d.get("user_id")) if d.get("user_id") else None
        name = _user_display_name(u) if u else ""
        return name or d.get("name") or driver_id[:8]

    top_drivers = [
        {
            "driver_id": did,
            "name": _display_name(did),
            "amount": round(completed_by_driver[did]["amount"], 2),
            "payout_count": int(completed_by_driver[did]["count"]),
        }
        for did in top_driver_ids
    ]
    at_risk_drivers = sorted(
        [
            {
                "driver_id": did,
                "name": _display_name(did),
                "failure_count": int(failed_by_driver[did]["count"]),
                "last_reason": failed_by_driver[did]["last_reason"],
            }
            for did in at_risk_driver_ids
        ],
        key=lambda x: -x["failure_count"],
    )[:10]

    # ── Pass 4: T4A snapshot ─────────────────────────────────────────────
    # CRA T4A threshold for self-employed contractor reporting in Canada
    # is $500 to the same payee in a tax year. The GST/HST mandatory
    # registration threshold is $30,000 — that's the more operationally
    # interesting line because it's when a driver MUST register their
    # GST account. Bucket per driver against both numbers.
    year_start = datetime(now.year, 1, 1, tzinfo=timezone.utc).isoformat()
    ytd_completed = [
        r
        for r in all_completed_rides
        if _in_scope(r) and (r.get("ride_completed_at") or r.get("updated_at") or "") >= year_start
    ]
    ytd_earnings_by_driver: Dict[str, Decimal] = {}
    for r in ytd_completed:
        did = r.get("driver_id")
        if not did:
            continue
        ytd_earnings_by_driver[did] = ytd_earnings_by_driver.get(did, Decimal("0")) + Decimal(
            str(r.get("driver_earnings") or 0)
        )

    t4a_buckets = {
        "under_500": 0,  # below T4A reporting threshold (CRA $500)
        "from_500_to_10k": 0,  # T4A required, GST registration not required
        "from_10k_to_30k": 0,  # T4A required, GST elective
        "over_30k": 0,  # T4A required, GST registration MANDATORY
    }
    for amt in ytd_earnings_by_driver.values():
        if amt < Decimal("500"):
            t4a_buckets["under_500"] += 1
        elif amt < Decimal("10000"):
            t4a_buckets["from_500_to_10k"] += 1
        elif amt < Decimal("30000"):
            t4a_buckets["from_10k_to_30k"] += 1
        else:
            t4a_buckets["over_30k"] += 1

    # Period locks — derived from audit_log entries written by
    # admin_close_payout_period. Surface the most recent N so the UI
    # can show "May 2026 closed by alice@... on Jun 3" without a
    # separate fetch.
    period_locks: list[Dict[str, Any]] = []
    try:
        lock_rows = await db_supabase.get_rows(
            "audit_logs",
            {"action": "payouts_period_closed"},
            order="created_at",
            desc=True,
            limit=24,
        )
        for lr in lock_rows:
            md = lr.get("details") or {}
            period_key = md.get("period")
            if not period_key:
                continue
            period_locks.append(
                {
                    "period": period_key,
                    "closed_at": lr.get("created_at"),
                    "closed_by": lr.get("actor_id"),
                    "actor_role": lr.get("actor_role"),
                }
            )
    except Exception:
        logger.error("payouts overview: period_locks query failed", exc_info=True)

    return {
        "period": {
            "key": period,
            "label": _PERIOD_LABELS[period],
            "days": _days,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "prev_start": prev_start.isoformat(),
            "prev_end": prev_end.isoformat(),
        },
        "metrics": {
            "outstanding_payable": _metric(float(outstanding_now_calc), float(outstanding_prev)),
            "total_paid_out": _metric(_amt(cur_completed), _amt(prev_completed)),
            "pending_in_flight": _metric(_amt(cur_pending), _amt(prev_pending)),
            "failed_amount": _metric(_amt(cur_failed), _amt(prev_failed)),
            "success_rate_pct": _metric(cur_success, prev_success),
            "median_time_to_payout_hours": _metric(cur_median_hours, prev_median_hours),
            "avg_payout_amount": _metric(cur_avg, prev_avg),
            "payouts_count": _metric(len(cur_payouts), len(prev_payouts)),
        },
        "daily_series": list(daily.values()),
        # Pass 2 — operational queues. None are PoP because they're
        # "right now" lists rather than period totals.
        "failure_reasons": failure_reasons,
        "stuck_over_48h": stuck_over_48h,
        "blocked_drivers": blocked_drivers,
        "top_drivers": top_drivers,
        "at_risk_drivers": at_risk_drivers,
        # Pass 4 — compliance.
        "t4a_snapshot": {
            "tax_year": now.year,
            "drivers_with_earnings": len(ytd_earnings_by_driver),
            "buckets": t4a_buckets,
            # Sum of YTD driver_earnings — the gross-side of the T4A
            # generation pipeline, not what's been paid out.
            "ytd_gross_earnings": round(float(sum(ytd_earnings_by_driver.values(), Decimal("0"))), 2),
        },
        "period_locks": period_locks,
    }


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


# IMPORTANT: keep this BEFORE @router.get("/payouts/{payout_id}").
# FastAPI matches routes in registration order — if /payouts/{payout_id}
# is registered first, GET /payouts/stats matches it with payout_id="stats"
# and returns 404 (the admin dashboard then crashes on the missing stats
# response). Any future /payouts/<literal> route must go above the
# {payout_id} handler for the same reason.
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


class BulkRetryPayoutsRequest(BaseModel):
    """Body for POST /payouts/bulk-retry.

    Either pass an explicit list of payout_ids (admin curated), or pass
    a since timestamp and we'll retry every failed/cancelled payout in
    the matching window. The two paths are mutually exclusive — passing
    both returns 400 to avoid ambiguity about which one took effect.
    """

    payout_ids: Optional[list[str]] = None
    since: Optional[str] = None  # ISO timestamp; defaults to 7d ago when only `since` mode is implied
    service_area_id: Optional[str] = None
    max_to_retry: int = Field(50, ge=1, le=500)


@router.post("/payouts/bulk-retry")
async def admin_bulk_retry_payouts(
    body: BulkRetryPayoutsRequest,
    admin: dict = Depends(get_admin_user),
):
    """Retry many failed/cancelled payouts at once.

    Selection modes (exactly one):
      • payout_ids[]   — explicit list (max_to_retry still capped)
      • since (+area)  — every failed/cancelled payout since the
                         timestamp, optionally narrowed to one
                         service area. Useful for "retry everything
                         from this morning after Stripe came back up"

    Per-payout updates are sequential so the audit log keeps a clear
    trail. Each row carries retry_requested_by + retry_requested_at
    just like the single-retry endpoint. The payment-retry background
    loop picks them up on its next tick.

    Restricted to finance + super_admin, same as the single retry.
    """
    allowed_roles = {"finance", "super_admin"}
    if admin.get("role") not in allowed_roles:
        raise HTTPException(status_code=403, detail="role_required:finance")

    if body.payout_ids and body.since:
        raise HTTPException(
            status_code=400,
            detail="Pass either payout_ids[] OR since (and optional service_area_id), not both.",
        )

    # Build the candidate set.
    candidates: list[Dict[str, Any]] = []
    if body.payout_ids:
        # Explicit list. Look each one up individually so a missing or
        # ineligible id is reported in the result rather than failing
        # the whole batch.
        for pid in body.payout_ids[: body.max_to_retry]:
            row = await db.find_one("payouts", {"id": pid})
            if row:
                candidates.append(row)
            else:
                candidates.append({"id": pid, "_missing": True})
    else:
        since_iso = body.since or (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        filters: Dict[str, Any] = {
            "status": {"$in": ["failed", "cancelled"]},
            "created_at": {"$gte": since_iso},
        }
        rows = await db.get_rows("payouts", filters, limit=body.max_to_retry, order="created_at", desc=False)
        # Service-area filter — payouts have no service_area_id, so we
        # have to resolve via drivers. Cheap because the area-bounded
        # driver list is small.
        if body.service_area_id:
            area_drivers = await db.get_rows("drivers", {"service_area_id": body.service_area_id}, limit=5000)
            scope = {d["id"] for d in area_drivers if d.get("id")}
            rows = [r for r in rows if r.get("driver_id") in scope]
        candidates = rows[: body.max_to_retry]

    retried = 0
    skipped = 0
    failed_to_initiate = 0
    details: list[Dict[str, Any]] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for row in candidates:
        pid = row.get("id")
        if row.get("_missing"):
            failed_to_initiate += 1
            details.append({"payout_id": pid, "status": "not_found"})
            continue
        cur_status = row.get("status")
        if cur_status not in ("failed", "cancelled"):
            skipped += 1
            details.append({"payout_id": pid, "status": "skipped", "reason": f"current_status={cur_status}"})
            continue
        try:
            await db.update_one(
                "payouts",
                {"id": pid},
                {
                    "status": "pending",
                    "retry_requested_by": admin.get("id"),
                    "retry_requested_at": now_iso,
                },
            )
            retried += 1
            details.append({"payout_id": pid, "status": "queued"})
        except Exception:
            logger.error(f"bulk-retry: failed to flip {pid} to pending", exc_info=True)
            failed_to_initiate += 1
            details.append({"payout_id": pid, "status": "error"})

    logger.info(
        f"Bulk retry by admin {admin.get('id')}: retried={retried} "
        f"skipped={skipped} failed_to_initiate={failed_to_initiate}"
    )
    return {
        "retried": retried,
        "skipped": skipped,
        "failed_to_initiate": failed_to_initiate,
        "details": details,
    }


class ClosePayoutPeriodRequest(BaseModel):
    """Body for POST /payouts/close-period.

    Closes a calendar month (e.g. 2026-05) for accounting purposes:
      • An audit_log row is written with action='payouts_period_closed'
        and details={period, range_start, range_end, payout_ids[]}.
      • The /payouts/overview response surfaces the lock so the UI can
        show "May 2026 closed by alice@…on Jun 3".

    Closure is advisory — we don't add a `locked` column to the payouts
    table (avoids needing a migration for one extra column). If a
    second close is requested for the same period a new audit_log row
    is written; period_locks shows the most recent one.
    """

    year: int = Field(ge=2024, le=2100)
    month: int = Field(ge=1, le=12)


@router.post("/payouts/close-period")
async def admin_close_payout_period(
    body: ClosePayoutPeriodRequest,
    admin: dict = Depends(get_admin_user),
):
    """Lock a calendar month for accounting purposes.

    Writes an audit_log entry that the /payouts/overview response
    surfaces under period_locks. Snapshots the list of completed
    payout ids in the window so a future reviewer can confirm the
    closure included exactly the right rows.
    """
    allowed_roles = {"finance", "super_admin"}
    if admin.get("role") not in allowed_roles:
        raise HTTPException(status_code=403, detail="role_required:finance")

    # Build month bounds in UTC. Using exclusive end so the half-open
    # interval matches how Python's calendar arithmetic generally works
    # (e.g. May = [2026-05-01, 2026-06-01)).
    range_start = datetime(body.year, body.month, 1, tzinfo=timezone.utc)
    if body.month == 12:
        range_end = datetime(body.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        range_end = datetime(body.year, body.month + 1, 1, tzinfo=timezone.utc)
    period_key = f"{body.year:04d}-{body.month:02d}"

    # Snapshot the completed payout id list for the audit row. Bounded
    # at 5000 — beyond that we'd want a separate snapshot table, but
    # for Spinr-scale fleets this is plenty.
    rows = await db_supabase.get_rows(
        "payouts",
        {
            "status": "completed",
            "processed_at": {"$gte": range_start.isoformat(), "$lt": range_end.isoformat()},
        },
        limit=5000,
    )
    payout_ids = [r.get("id") for r in rows if r.get("id")]
    total_amount = round(
        float(sum((Decimal(str(r.get("amount") or 0)) for r in rows), Decimal("0"))),
        2,
    )

    audit_id = await log_admin_action(
        admin,
        "payouts_period_closed",
        "payouts",
        period_key,
        {
            "period": period_key,
            "range_start": range_start.isoformat(),
            "range_end": range_end.isoformat(),
            "payout_count": len(payout_ids),
            "total_amount": total_amount,
            # First 50 ids inline; the count is the authoritative number.
            # If we ever need the full list later we'd add a separate
            # period_close_snapshot table, but inline is fine for audit.
            "payout_ids": payout_ids[:50],
        },
    )

    logger.info(
        f"Payout period {period_key} closed by admin {admin.get('id')} — "
        f"{len(payout_ids)} payouts, total ${total_amount}"
    )
    return {
        "period": period_key,
        "payout_count": len(payout_ids),
        "total_amount": total_amount,
        "audit_log_id": audit_id,
    }


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
