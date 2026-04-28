import asyncio
import secrets
import uuid
from datetime import datetime, timezone
from datetime import timezone as _tz
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

try:
    from .. import db_supabase
    from ..dependencies import generate_pickup_otp, get_current_user
    from ..features import calculate_airport_fee, calculate_all_fees, send_push_notification
    from ..geo_utils import calculate_distance, get_service_area_polygon, point_in_polygon
    from ..schemas import CreateRideRequest, DriverPublicView, Ride, RideRatingRequest
    from ..services import DispatchService
    from ..services.dispatch_service import (
        filter_and_rank_drivers,
    )
    from ..settings_loader import get_app_settings
    from ..socket_manager import manager
    from ..utils.crypto import hash_otp
    from ..utils.idempotency import idempotent_endpoint
    from ..utils.rate_limiter import cancel_ride_limit, ride_request_limit
    from ..validators import validate_ride_location
except ImportError:
    import db_supabase
    from dependencies import generate_pickup_otp, get_current_user
    from features import calculate_airport_fee, calculate_all_fees, send_push_notification
    from geo_utils import calculate_distance, get_service_area_polygon, point_in_polygon
    from schemas import CreateRideRequest, DriverPublicView, Ride, RideRatingRequest
    from services.dispatch_service import (
        DispatchService,
        filter_and_rank_drivers,
    )
    from settings_loader import get_app_settings
    from socket_manager import manager
    from utils.crypto import hash_otp
    from utils.idempotency import idempotent_endpoint
    from utils.rate_limiter import cancel_ride_limit, ride_request_limit
    from validators import validate_ride_location


from .fares import _fares_for_location_impl, get_fares_for_location

try:
    from ..utils.datetime_utils import parse_iso_utc
    from ..utils.ride_code import generate_ride_code
except ImportError:
    from utils.datetime_utils import parse_iso_utc
    from utils.ride_code import generate_ride_code

try:
    from ..services import corporate_allowance_service, corporate_wallet_service  # type: ignore
    from ..services.corporate_policy_service import evaluate_policy  # type: ignore
    from ..utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )
except ImportError:
    from services import corporate_allowance_service, corporate_wallet_service  # type: ignore
    from services.corporate_policy_service import evaluate_policy  # type: ignore
    from utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )

# Lift to module scope so tests can patch backend.routes.rides.charge_ride
# directly; the handler's card branch references this bound name.
try:
    from ..utils.stripe_charge import charge_ride
except ImportError:
    from utils.stripe_charge import charge_ride

try:
    from ..services import corporate_allowance_service, corporate_wallet_service  # type: ignore
    from ..services.corporate_policy_service import evaluate_policy, evaluate_policy_for_ride  # type: ignore
except ImportError:
    from services import corporate_allowance_service, corporate_wallet_service  # type: ignore
    from services.corporate_policy_service import evaluate_policy, evaluate_policy_for_ride  # type: ignore

try:
    from ..core.config import settings as _settings
except ImportError:
    from core.config import settings as _settings  # noqa: F401 — dual-import pattern

db = db_supabase  # legacy alias


async def _require_ride_in_state_rider(ride_id: str, rider_id: str, allowed_states: tuple) -> dict:
    """Load a rider's ride only if it is in one of allowed_states.

    Raises 409 if the ride exists but is in the wrong state.
    Raises 404 if the ride doesn't exist or isn't owned by this rider.
    """
    ride = await db.find_one(
        "rides",
        {"id": ride_id, "rider_id": rider_id, "status": {"$in": list(allowed_states)}},
    )
    if ride:
        return ride
    existing = await db.find_one("rides", {"id": ride_id, "rider_id": rider_id})
    if existing:
        current = existing.get("status", "unknown")
        raise HTTPException(
            status_code=409,
            detail=f"Ride is in status '{current}'; cannot perform this action from that state (allowed: {list(allowed_states)}).",
        )
    raise HTTPException(status_code=404, detail="Ride not found or unauthorized")


dispatch = DispatchService(db_supabase)  # module-level instance for legacy call sites

# ── Decimal helpers for accurate currency arithmetic ──────────────────────────
_TWO_PLACES = Decimal("0.01")


def _d(v) -> Decimal:
    """Convert any numeric value to Decimal safely (avoids float precision loss)."""
    return Decimal(str(v))


def _round(v: Decimal) -> Decimal:
    return v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _reestimate_fare_for_stops(ride: dict, new_stops: list) -> dict:
    """Recalculate distance, duration, and fare after a stop mutation.

    Derives per-km and per-minute rates from the values already stored on the
    ride row (distance_fare / (distance_km * surge) and time_fare / (duration *
    surge)), then applies them to the new multi-leg distance.  Returns a dict
    suitable for merging into a $set update.
    """
    # Build ordered waypoints: pickup → stops → dropoff
    waypoints = [
        (ride["pickup_lat"], ride["pickup_lng"]),
        *[(s["lat"], s["lng"]) for s in new_stops],
        (ride["dropoff_lat"], ride["dropoff_lng"]),
    ]
    new_distance_km = sum(
        calculate_distance(waypoints[i][0], waypoints[i][1], waypoints[i + 1][0], waypoints[i + 1][1])
        for i in range(len(waypoints) - 1)
    )
    new_duration_minutes = max(5, int(new_distance_km / 30 * 60) + 5)

    surge = _d(ride.get("surge_multiplier", 1.0)) or _d(1)
    old_dist = _d(ride.get("distance_km") or 1)
    old_dur = _d(ride.get("duration_minutes") or 1)

    per_km_effective = _d(ride.get("distance_fare", 0)) / (old_dist * surge)
    per_min_effective = _d(ride.get("time_fare", 0)) / (old_dur * surge)

    new_distance_fare = _round(per_km_effective * _d(new_distance_km) * surge)
    new_time_fare = _round(per_min_effective * _d(new_duration_minutes) * surge)
    new_total = _round(
        _d(ride.get("base_fare", 0)) + new_distance_fare + new_time_fare + _d(ride.get("booking_fee", 0))
    )

    return {
        "distance_km": round(new_distance_km, 2),
        "duration_minutes": new_duration_minutes,
        "distance_fare": _f(new_distance_fare),
        "time_fare": _f(new_time_fare),
        "estimated_fare": _f(new_total),
        "total_fare": _f(new_total),
    }


def _f(v: Decimal) -> float:
    """Convert Decimal back to float for Pydantic / JSON serialisation."""
    return float(v)


api_router = APIRouter(prefix="/rides", tags=["Rides"])


class TipRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, le=500, description="Tip in CAD (max $500)")


async def create_demo_drivers(vehicle_type_id: str, lat: float, lng: float):
    """DEPRECATED — intentionally a no-op.

    This used to insert 3 fake driver rows with user_id=NULL whenever the
    dispatch RPC returned zero drivers. That turned the `drivers` table into
    a junkyard of orphan rows that polluted the rider-app's home-map pins,
    inflated `/rides/estimate` driver counts for vehicle types where no real
    driver was online, and wasted dispatch cycles on drivers that could
    never be notified (no user_id = no WebSocket key = no push token).

    The dispatch path no longer calls this function. Keeping the symbol
    exported as a no-op so any stale import from callers outside this file
    still resolves and short-circuits instead of inserting garbage rows.
    Delete entirely once you've confirmed no other module references it.
    """
    logger.warning(
        "[DISPATCH] create_demo_drivers was called but is deprecated and does nothing. "
        f"vehicle_type_id={vehicle_type_id} pickup=({lat},{lng})"
    )
    return


async def match_driver_to_ride(ride_id: str, *, ride: Optional[dict] = None):
    """Dispatch a driver for ``ride_id``.

    ``ride`` may be passed when the caller already has the fresh row
    (e.g. straight after ``insert_ride``) so we skip a redundant
    ``get_ride`` round-trip. When omitted, the ride is re-fetched —
    needed by the offer-timeout and scheduled-ride paths that run
    much later and must see the current state.
    """
    if ride is None:
        ride = await db_supabase.get_ride(ride_id)
    if not ride:
        logger.warning(f"[DISPATCH] match_driver_to_ride: ride {ride_id} not found")
        return

    # Single app_settings fetch — used both for matching config (via
    # DispatchService.resolve_matching_config) and for the offer-timeout
    # lookup at the end. Previously this loaded twice; the dead
    # ``get_rows("service_areas", {"id": ...})`` call that followed has
    # been removed — resolve_matching_config does its own find_one
    # against the same table.
    app_settings = await get_app_settings()

    # Algorithm + radius + rating floor (area overrides app settings).
    algorithm, min_rating, search_radius = await dispatch.resolve_matching_config(ride, app_settings=app_settings)

    logger.info(
        f"[DISPATCH] match start ride_id={ride_id} "
        f"pickup=({ride['pickup_lat']},{ride['pickup_lng']}) "
        f"vehicle_type_id={ride['vehicle_type_id']} algorithm={algorithm} "
        f"radius_km={search_radius}"
    )

    # Find candidate drivers. We read the drivers table directly and filter
    # in Python using the legacy lat/lng columns — same pattern as /rides/estimate.
    # We deliberately DO NOT use the find_nearby_drivers RPC because it reads
    # the PostGIS `location` column, which update_driver_location does not
    # populate, so the RPC would always return zero drivers.
    #
    # We also require user_id IS NOT NULL to skip legacy "demo" driver rows
    # that lack a real user and can never be notified.
    all_drivers = await db_supabase.get_rows(
        "drivers",
        {
            "is_online": True,
            "is_available": True,
            "vehicle_type_id": ride["vehicle_type_id"],
        },
        limit=500,
    )

    logger.info(
        f"[DISPATCH] candidate pool (pre-filter): {len(all_drivers)} drivers "
        f"matching vehicle_type_id + online + available"
    )

    # Pure filter+rank: drops orphan/no-location/low-rated drivers and
    # attaches per-driver distance. Pure function — no I/O.
    drivers_with_distance = filter_and_rank_drivers(ride, all_drivers, algorithm, min_rating, search_radius)
    logger.info(
        f"[DISPATCH] candidate pool (post-filter): {len(drivers_with_distance)} "
        f"real drivers within {search_radius}km with valid lat/lng and "
        f"rating>={min_rating if algorithm in ('rating_based', 'combined') else 'n/a'}"
    )

    if not drivers_with_distance:
        logger.info(f"[DISPATCH] no eligible drivers for ride {ride_id} — ride stays in searching")
        return

    selected_driver = None

    if algorithm == "nearest" or algorithm == "combined":
        drivers_with_distance.sort(key=lambda x: x[1])
        selected_driver = drivers_with_distance[0][0]
    elif algorithm == "rating_based":
        drivers_with_distance.sort(key=lambda x: x[0].get("rating", 5.0), reverse=True)
        selected_driver = drivers_with_distance[0][0]
    elif algorithm == "round_robin":
        last_ride = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("rides", {"driver_id": {"$ne": None}}, sort=[("created_at", -1)], limit=1)
        )
        if last_ride:
            last_driver_idx = next(
                (i for i, (d, _) in enumerate(drivers_with_distance) if d["id"] == last_ride["driver_id"]), -1
            )
            next_idx = (last_driver_idx + 1) % len(drivers_with_distance)
            selected_driver = drivers_with_distance[next_idx][0]
        else:
            selected_driver = drivers_with_distance[0][0]

    if selected_driver:
        # Attempt to atomically claim the driver (only if still available)
        claim_result = await db_supabase.claim_driver_atomic(selected_driver["id"])

        if not claim_result:
            # Driver was taken by another process; try to find next candidate
            claimed = False
            for d, _ in drivers_with_distance:
                if await db_supabase.claim_driver_atomic(d["id"]):
                    selected_driver = d
                    claimed = True
                    break
            if not claimed:
                # No drivers could be claimed
                return

        # Guard: re-verify the claimed driver is still online. A driver can
        # toggle offline between the candidate read and the atomic claim write,
        # which would leave a ride silently assigned to a driver who will never
        # see it (ghost assignment).
        fresh_driver = await db_supabase.get_driver_by_id(selected_driver["id"])
        if not fresh_driver or not fresh_driver.get("is_online"):
            logger.warning(
                f"[DISPATCH] Driver {selected_driver['id']} went offline before "
                f"claim — releasing and re-dispatching ride {ride_id}"
            )
            await db_supabase.set_driver_available(selected_driver["id"], True)
            await match_driver_to_ride(ride_id)
            return

        # Update ride with selected driver. Do NOT pre-populate
        # driver_accepted_at here — that field is set by the
        # /drivers/rides/{id}/accept endpoint when the driver actually taps
        # Accept. Setting it at dispatch time was a "demo auto-accept" hack
        # that made the rider-app show the driver card before the driver
        # had actually agreed to the ride.
        await db_supabase.update_ride(
            ride_id,
            {
                "driver_id": selected_driver["id"],
                "status": "driver_assigned",
                "driver_notified_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        )

        logger.info(
            f"[DISPATCH] ride {ride_id} assigned to driver_id={selected_driver['id']} "
            f"user_id={selected_driver.get('user_id')} name={selected_driver.get('name')}"
        )

        # No rider-facing WS event here — the rider app waits for
        # ``driver_accepted`` (emitted when the driver actually taps
        # Accept). Notifying on bare assignment caused premature "driver
        # found" banners that reverted if the driver timed out.

        # Look up the rider so we can include name/rating in the dispatch
        # payload sent to the driver-app. Missing fields are fine — the
        # driver-app has fallbacks — but sending them avoids an empty popup.
        rider_user = None
        try:
            rider_user = await db_supabase.get_user_by_id(ride["rider_id"])
        except Exception as e:
            logger.warning(f"[DISPATCH] could not load rider user {ride['rider_id']}: {e}")

        rider_display_name = None
        if rider_user:
            first = rider_user.get("first_name") or ""
            last = rider_user.get("last_name") or ""
            rider_display_name = (first + " " + last).strip() or rider_user.get("name") or None

        # Build the full dispatch payload. Keys MUST match what the driver
        # app consumes in useDriverDashboard.ts handleWSMessage:
        # ride_id, pickup_address, dropoff_address, pickup_lat, pickup_lng,
        # dropoff_lat, dropoff_lng, fare, distance_km, duration_minutes,
        # rider_name, rider_rating.
        dispatch_payload = {
            "type": "new_ride_assignment",
            "ride_id": ride_id,
            "pickup_address": ride.get("pickup_address"),
            "dropoff_address": ride.get("dropoff_address"),
            "pickup_lat": ride.get("pickup_lat"),
            "pickup_lng": ride.get("pickup_lng"),
            "dropoff_lat": ride.get("dropoff_lat"),
            "dropoff_lng": ride.get("dropoff_lng"),
            "fare": ride.get("driver_earnings"),
            "distance_km": ride.get("distance_km"),
            "duration_minutes": ride.get("duration_minutes"),
            "rider_name": rider_display_name,
            "rider_rating": (rider_user or {}).get("rating"),
        }

        # Notify driver via WebSocket (only reaches the driver if they have
        # an open WS connection — silent no-op otherwise).
        if selected_driver.get("user_id"):
            logger.info(
                f"[DISPATCH] sending WS new_ride_assignment to "
                f"driver_{selected_driver['user_id']} payload_keys="
                f"{list(dispatch_payload.keys())}"
            )
            await manager.send_personal_message(dispatch_payload, f"driver_{selected_driver['user_id']}")

            # Push-notification fallback for backgrounded/killed app.
            # The driver-app background handler (app/_layout.tsx) persists
            # the full ride data to AsyncStorage; useDriverDashboard.ts then
            # hydrates the store on cold-start without a network round-trip.
            # FCM `data` values MUST be strings — all numbers are str()-wrapped.
            try:
                await send_push_notification(
                    selected_driver["user_id"],
                    "New ride request",
                    (
                        f"{ride.get('pickup_address') or 'Nearby pickup'} "
                        f"→ {ride.get('dropoff_address') or 'destination'}"
                    ),
                    {
                        "type": "new_ride_assignment",
                        "ride_id": ride_id,
                        "pickup_address": ride.get("pickup_address") or "",
                        "dropoff_address": ride.get("dropoff_address") or "",
                        "pickup_lat": str(ride.get("pickup_lat") or 0),
                        "pickup_lng": str(ride.get("pickup_lng") or 0),
                        "dropoff_lat": str(ride.get("dropoff_lat") or 0),
                        "dropoff_lng": str(ride.get("dropoff_lng") or 0),
                        "fare": str(ride.get("driver_earnings") or 0),
                        "distance_km": str(ride.get("distance_km") or ""),
                        "duration_minutes": str(ride.get("duration_minutes") or ""),
                        "rider_name": rider_display_name or "",
                        "rider_rating": str((rider_user or {}).get("rating") or ""),
                        "deeplink": "/driver/",
                    },
                )
                logger.info(f"[DISPATCH] push new_ride_assignment sent to user_id={selected_driver['user_id']}")
            except Exception as e:
                logger.warning(f"[DISPATCH] push notification failed for user_id={selected_driver['user_id']}: {e}")
        else:
            logger.warning(
                f"[DISPATCH] selected_driver has no user_id — cannot notify. "
                f"driver_id={selected_driver.get('id')} name={selected_driver.get('name')}. "
                f"This row is likely an orphan demo driver; clean up the drivers table."
            )

    # ── Backend-enforced offer TTL ─────────────────────────────────
    # The driver-app's countdown timer handles the happy path (driver
    # taps Decline before timeout), but if the device dies, loses
    # network, or the app crashes, the ride is stuck in
    # `driver_assigned` forever and the rider waits endlessly.
    #
    # This background task fires after the configured timeout + a
    # 15 s grace period (for network latency and FCM delivery). If
    # the ride is STILL `driver_assigned` to THIS specific driver,
    # it unassigns and re-dispatches.
    offer_timeout = int(app_settings.get("ride_offer_timeout_seconds", 15))
    asyncio.create_task(
        _offer_timeout_handler(
            ride_id,
            selected_driver["id"],
            rider_id=ride.get("rider_id"),
            timeout_seconds=offer_timeout + 15,
        )
    )


async def _offer_timeout_handler(
    ride_id: str,
    driver_id: str,
    rider_id: str | None,
    timeout_seconds: int = 30,
):
    """Auto-expire a driver's ride offer if they don't accept/decline.

    Sleeps for `timeout_seconds` then checks whether the ride is still
    in `driver_assigned` status with this specific `driver_id`. If yes,
    releases the driver, sets the ride back to `searching`, notifies the
    rider, and re-dispatches.

    This mirrors the driver-app's client-side countdown timer but is
    authoritative — it fires even if the device crashes or loses network.
    The 15 s grace period between the driver-app countdown (default 15 s)
    and this handler (default 30 s = 15 + 15) avoids racing the
    client-side decline call.
    """
    await asyncio.sleep(timeout_seconds)
    try:
        ride = await db.find_one("rides", {"id": ride_id})
        if not ride:
            return

        # Only act if the ride hasn't progressed past assignment.
        if ride.get("status") != "driver_assigned" or ride.get("driver_id") != driver_id:
            return

        logger.info(
            f"[DISPATCH] Offer expired: ride {ride_id} driver {driver_id} "
            f"didn't respond within {timeout_seconds}s — re-searching"
        )

        # Release the driver back to the available pool.
        await db.update_one(
            "drivers",
            {"id": driver_id},
            {"$set": {"is_available": True}},
        )

        # Put the ride back in the searching state so it can be
        # re-dispatched or picked up by the next dispatch cycle.
        await db.update_one(
            "rides",
            {"id": ride_id},
            {
                "$set": {
                    "status": "searching",
                    "driver_id": None,
                    "driver_notified_at": None,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )

        # Notify rider via WebSocket.
        if rider_id:
            await manager.send_personal_message(
                {
                    "type": "driver_timeout",
                    "ride_id": ride_id,
                    "message": "Driver didn't respond. Finding another driver...",
                },
                f"rider_{rider_id}",
            )

        # Attempt re-dispatch to the next available driver.
        await match_driver_to_ride(ride_id)

    except Exception as e:
        logger.warning(f"[DISPATCH] Offer timeout handler error for ride {ride_id}: {e}")


class RideEstimateRequest(BaseModel):
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    stops: Optional[List[dict]] = None


@api_router.post("/estimate")
async def estimate_ride(request: RideEstimateRequest, current_user: dict = Depends(get_current_user)):
    validate_ride_location(request.pickup_lat, request.pickup_lng, request.dropoff_lat, request.dropoff_lng)
    distance_km = calculate_distance(request.pickup_lat, request.pickup_lng, request.dropoff_lat, request.dropoff_lng)
    duration_minutes = int(distance_km / 30 * 60) + 5

    fares = await get_fares_for_location(request.pickup_lat, request.pickup_lng)

    # Fetch all nearby online+available drivers once
    all_drivers = await db_supabase.get_rows(
        "drivers",
        {
            "is_online": True,
            "is_available": True,
        },
        limit=200,
    )

    # Filter to drivers within 10km radius and group by vehicle_type_id.
    # Exclude drivers without a user_id — those are orphan/demo rows that
    # cannot be dispatched to, and counting them would inflate the rider's
    # "X drivers available" badge and cause rides to fail at dispatch time.
    from collections import defaultdict

    drivers_by_type = defaultdict(list)
    for d in all_drivers:
        if not d.get("user_id"):
            continue
        d_lat = d.get("lat")
        d_lng = d.get("lng")
        if d_lat and d_lng:
            dist = calculate_distance(request.pickup_lat, request.pickup_lng, d_lat, d_lng)
            if dist <= 10.0:  # 10km radius
                vt_id = d.get("vehicle_type_id")
                drivers_by_type[vt_id].append(
                    {
                        "driver": d,
                        "distance_km": dist,
                    }
                )

    # Check airport surcharge (pickup, dropoff, or any stop in airport sub-region)
    airport_result = await calculate_airport_fee(
        request.pickup_lat,
        request.pickup_lng,
        request.dropoff_lat,
        request.dropoff_lng,
        stops=request.stops,
    )
    airport_fee = airport_result.get("airport_fee", 0.0)

    estimates = []
    for fare_info in fares:
        # Use Decimal for all monetary arithmetic (CQ-009 — eliminates float rounding errors)
        # Use Decimal for all monetary arithmetic (CQ-009 — eliminates float rounding errors)
        surge = _d(fare_info.get("surge_multiplier", 1.0))
        distance_fare = _round(_d(fare_info["per_km_rate"]) * _d(distance_km) * surge)
        time_fare = _round(_d(fare_info["per_minute_rate"]) * _d(duration_minutes) * surge)
        booking_fee = _d(fare_info.get("booking_fee", 2.0))
        total_fare = _round(_d(fare_info["base_fare"]) + distance_fare + time_fare + booking_fee + _d(airport_fee))
        total_fare = max(total_fare, _d(fare_info["minimum_fare"]))

        # Check real driver availability for this vehicle type
        vt_id = fare_info["vehicle_type"].get("id")
        nearby_for_type = drivers_by_type.get(vt_id, [])
        driver_count = len(nearby_for_type)
        is_available = driver_count > 0

        # Calculate ETA: closest driver's distance / avg speed (30km/h in city)
        eta_minutes = None
        if nearby_for_type:
            closest = min(nearby_for_type, key=lambda x: x["distance_km"])
            eta_minutes = max(2, int(closest["distance_km"] / 30 * 60) + 1)

        # P0-4 surge-lock: sign a token per vehicle_type so POST /rides can
        # reuse the surge_multiplier shown here instead of re-reading the
        # service area (which may have changed between estimate + confirm).
        estimate_token = sign_estimate_token(
            rider_id=current_user["id"],
            vehicle_type_id=vt_id,
            pickup_lat=request.pickup_lat,
            pickup_lng=request.pickup_lng,
            dropoff_lat=request.dropoff_lat,
            dropoff_lng=request.dropoff_lng,
            surge_multiplier=_f(surge),
            total_fare=_f(total_fare),
        )

        estimates.append(
            {
                "vehicle_type": fare_info["vehicle_type"],
                "distance_km": round(distance_km, 2),
                "duration_minutes": duration_minutes,
                "base_fare": _f(_d(fare_info["base_fare"])),
                "distance_fare": _f(distance_fare),
                "time_fare": _f(time_fare),
                "booking_fee": _f(booking_fee),
                "surge_multiplier": _f(surge),
                "total_fare": _f(total_fare),
                "available": is_available,
                "eta_minutes": eta_minutes,
                "driver_count": driver_count,
                "estimate_token": estimate_token,
            }
        )
    return estimates


async def ride_search_timeout(r_id: str, timeout_seconds: int = 300):
    """Auto-cancel a ride if it's still ``searching`` after ``timeout_seconds``.

    Matches Uber/Lyft's 5-minute default. Publishes a ``ride_cancelled`` WS
    message to the rider's channel and fires a push notification so the rider
    is alerted even if the app is backgrounded.

    Extracted from ``create_ride`` so it can be unit-tested directly — see
    backend/tests/test_p0_ship_blockers.py::TestNoDriversAvailableTimeout.
    """
    await asyncio.sleep(timeout_seconds)
    try:
        current_ride = await db_supabase.get_ride(r_id)
        if current_ride and current_ride.get("status") == "searching":
            now = datetime.now(timezone.utc)
            base_update = {
                "status": "cancelled",
                "cancelled_at": now,
                "cancellation_reason": "No nearby drivers found. Please try again.",
                "updated_at": now,
            }
            # Migration 38 adds cancelled_by / cancellation_type so the
            # admin panel can filter "No Driver Found" separately. Fall
            # back to base_update on PGRST204 ("column does not exist")
            # so the rider-facing cancel still succeeds before the
            # migration lands in prod.
            try:
                await db_supabase.update_ride(
                    r_id,
                    {**base_update, "cancelled_by": "system", "cancellation_type": "no_drivers_found"},
                )
            except Exception as _col_exc:
                logger.warning(f"[AUTO-CANCEL] attribution write failed ({_col_exc}); retrying minimal")
                await db_supabase.update_ride(r_id, base_update)
            await manager.send_personal_message(
                {
                    "type": "ride_cancelled",
                    "ride_id": r_id,
                    "reason": "No nearby drivers available. Your ride has been automatically cancelled.",
                },
                f"rider_{current_ride['rider_id']}",
            )
            await manager.broadcast_ride_status(
                r_id,
                "cancelled",
                rider_id=current_ride["rider_id"],
                reason="no_drivers_found",
                is_auto=True,
            )
            try:
                await manager.broadcast_to_admins(
                    {"type": "ride_cancelled", "ride_id": r_id, "reason": "no_drivers_found", "is_auto": True}
                )
            except Exception as _exc:  # pragma: no cover - best effort
                logger.warning(f"ride timeout admin broadcast failed: {_exc}")
            await send_push_notification(
                current_ride["rider_id"],
                "Ride Cancelled ❌",
                "No nearby drivers were found. Your ride has been automatically cancelled. Please try again.",
                {"type": "ride_cancelled", "ride_id": r_id, "is_auto": True},
            )
            logger.info(f"Ride {r_id} auto-cancelled after {timeout_seconds}s - no driver found")
    except Exception as e:
        logger.warning(f"Ride timeout handler error for {r_id}: {e}")


@api_router.post("")
@ride_request_limit
@idempotent_endpoint(scope="ride_create")
async def create_ride(request: Request, body: CreateRideRequest, current_user: dict = Depends(get_current_user)):
    # SlowAPI's @ride_request_limit needs a parameter literally named
    # ``request`` that is a starlette Request; otherwise it raises
    # "parameter `request` must be an instance of starlette.requests.Request"
    # for every call. The Pydantic body is ``body`` — do not rename it
    # back to ``request`` without also reworking the rate-limit decorator.
    validate_ride_location(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng)

    # R-P1-7: Idempotency — if the client retries after a network drop, return
    # the previously-created ride instead of charging the rider twice.
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing = await db_supabase.find_one(
            "rides",
            {"idempotency_key": idempotency_key, "rider_id": current_user["id"]},
        )
        if existing:
            return existing

    # Ban check + payment method validation share a single users-row read.
    # Previously this was two round-trips: ``find_one(users)`` (for card path)
    # + ``get_user_status`` (always). ``status`` lives on the same row.
    rider_row = await db.find_one("users", {"id": current_user["id"]})
    user_status = (rider_row or {}).get("status", "active")
    if user_status == "banned":
        raise HTTPException(status_code=403, detail="Your account has been suspended due to policy violations.")
    if user_status == "suspended":
        raise HTTPException(status_code=403, detail="Your account is currently suspended. Please contact support.")
    if body.payment_method == "card" and not body.work_profile:
        if not rider_row or not rider_row.get("stripe_customer_id"):
            raise HTTPException(status_code=400, detail="No payment method on file. Please add a card first.")

    active_statuses = ["searching", "driver_assigned", "driver_accepted", "driver_arrived", "in_progress"]
    existing_ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "rides",
            {"rider_id": current_user["id"], "status": {"$in": active_statuses}},
            limit=1,
        )
    )
    if existing_ride:
        raise HTTPException(status_code=409, detail="You already have an active ride")

    distance_km = calculate_distance(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng)
    duration_minutes = int(distance_km / 30 * 60) + 5

    # Fetch service_areas ONCE for this request and share across:
    # (1) fare resolution, (2) airport-fee lookup, (3) area-fees/taxes,
    # (4) service_area_id resolution. Previously each of these hit the
    # table independently — 3-4 full scans per POST /rides.
    all_areas = []
    try:
        all_areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=100)
    except Exception as e:
        logger.warning(f"Failed to fetch service areas: {e}")

    # Resolve the pickup service area once and pass the match downstream.
    matched_area = None
    for area in all_areas:
        poly = get_service_area_polygon(area)
        if poly and point_in_polygon(body.pickup_lat, body.pickup_lng, poly):
            matched_area = area
            break
    service_area_id = matched_area["id"] if matched_area else None

    # Vehicle types are also needed by fare building — fetch once, reuse.
    vehicle_types = await db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=100)

    fares = await _fares_for_location_impl(
        body.pickup_lat,
        body.pickup_lng,
        all_areas=all_areas,
        vehicle_types=vehicle_types,
    )

    fare_info = next((f for f in fares if f["vehicle_type"]["id"] == body.vehicle_type_id), fares[0] if fares else None)

    if not fare_info:
        raise HTTPException(status_code=400, detail="Invalid vehicle type")

    # Use Decimal for all monetary arithmetic (CQ-009 — eliminates float rounding errors)
    # P0-4 surge-lock: if the client sent back the estimate_token we issued
    # from /rides/estimate, reuse that surge instead of re-reading the area.
    # Invalid / expired tokens fall back to current surge — we do not 400
    # the request, because that would strand a rider mid-confirm if the
    # token just expired. The worst case of a bad token is parity with the
    # pre-token behavior (read current surge).
    current_surge = _d(fare_info.get("surge_multiplier", 1.0))
    surge = current_surge
    if body.estimate_token:
        try:
            payload = verify_estimate_token(
                body.estimate_token,
                rider_id=current_user["id"],
                vehicle_type_id=body.vehicle_type_id,
                pickup_lat=body.pickup_lat,
                pickup_lng=body.pickup_lng,
                dropoff_lat=body.dropoff_lat,
                dropoff_lng=body.dropoff_lng,
            )
            surge = _d(payload["sm"])
            logger.info(
                f"Surge locked from estimate_token for rider={current_user['id']} "
                f"vt={body.vehicle_type_id}: {float(surge)} (current was {float(current_surge)})"
            )
        except EstimateTokenError as e:
            logger.warning(
                f"estimate_token rejected ({e}); falling back to current surge "
                f"{float(current_surge)} for rider={current_user['id']}"
            )
    distance_fare = _round(_d(fare_info["per_km_rate"]) * _d(distance_km) * surge)
    time_fare = _round(_d(fare_info["per_minute_rate"]) * _d(duration_minutes) * surge)
    booking_fee = _d(fare_info.get("booking_fee", 2.0))
    base_fare = _d(fare_info["base_fare"])

    # Airport surcharge (pickup, dropoff, or any stop in airport sub-region).
    # Reuses the same all_areas list — filters for is_airport locally.
    airport_result = await calculate_airport_fee(
        body.pickup_lat,
        body.pickup_lng,
        body.dropoff_lat,
        body.dropoff_lng,
        stops=body.stops,
        _all_areas=all_areas,
    )
    airport_fee = _d(airport_result.get("airport_fee", 0.0))
    airport_zone_name = airport_result.get("airport_zone_name")

    total_fare = _round(base_fare + distance_fare + time_fare + booking_fee + airport_fee)
    total_fare = max(total_fare, _d(fare_info["minimum_fare"]))

    # Calculate area fees + taxes (reuses all_areas + pre-resolved match)
    fees_result = {}
    try:
        fees_result = await calculate_all_fees(
            body.pickup_lat,
            body.pickup_lng,
            body.dropoff_lat,
            body.dropoff_lng,
            distance_km,
            _f(total_fare),
            _all_areas=all_areas,
            _matched_area=matched_area,
        )
    except Exception as e:
        logger.warning(f"Failed to calculate area fees: {e}")

    area_fees_total = fees_result.get("fees_total", 0)
    tax_amount = fees_result.get("tax_amount", 0)
    grand_total = _f(_round(total_fare + _d(area_fees_total) + _d(tax_amount)))

    # Earnings split: Distance fare goes to driver, booking + airport fee goes to admin
    driver_earnings = _round(base_fare + distance_fare + time_fare)
    admin_earnings = _round(booking_fee + airport_fee)

    # Pre-dispatch corporate policy check (spec §4 — booking phase).
    # Only runs when rider explicitly books with company_allowance payment method.
    _corp_member_id: Optional[str] = None
    if body.corporate_account_id and body.payment_method == "company_allowance":
        try:
            from ..services.corporate_policy_service import evaluate_policy  # type: ignore
        except ImportError:
            from services.corporate_policy_service import evaluate_policy  # type: ignore

        _policy_result = await evaluate_policy(
            corporate_account_id=body.corporate_account_id,
            rider_id=current_user["id"],
            estimated_fare=total_fare,
            ride_type="standard",
            pickup_time=datetime.utcnow(),
        )
        if not _policy_result.allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": _policy_result.reason,
                    "failed_rules": _policy_result.failed_rules,
                },
            )
        _corp_members = await db_supabase.get_rows(
            "corporate_members",
            {"company_id": body.corporate_account_id, "user_id": current_user["id"], "status": "active"},
            limit=1,
        )
        if _corp_members:
            _corp_member_id = _corp_members[0]["id"]

    pickup_otp_plain = generate_pickup_otp()
    ride = Ride(
        rider_id=current_user["id"],
        vehicle_type_id=body.vehicle_type_id,
        pickup_address=body.pickup_address,
        pickup_lat=body.pickup_lat,
        pickup_lng=body.pickup_lng,
        dropoff_address=body.dropoff_address,
        dropoff_lat=body.dropoff_lat,
        dropoff_lng=body.dropoff_lng,
        distance_km=round(distance_km, 2),
        duration_minutes=duration_minutes,
        base_fare=_f(base_fare),
        distance_fare=_f(distance_fare),
        time_fare=_f(time_fare),
        booking_fee=_f(booking_fee),
        surge_multiplier=_f(surge),
        total_fare=_f(total_fare),
        stops=body.stops,
        is_scheduled=body.is_scheduled,
        scheduled_time=body.scheduled_time,
        driver_earnings=_f(driver_earnings),
        admin_earnings=_f(admin_earnings),
        payment_method=body.payment_method,
        payment_method_id=body.payment_method_id,
        status="searching",
        pickup_otp=hash_otp(pickup_otp_plain),
        ride_requested_at=datetime.now(timezone.utc),
    )

    ride_data = ride.dict()
    if body.corporate_account_id:
        ride_data["corporate_account_id"] = body.corporate_account_id
    if _corp_member_id:
        ride_data["corporate_member_id"] = _corp_member_id
    if service_area_id:
        ride_data["service_area_id"] = service_area_id
    # Preserve the original planned (straight-line) distance. ride.distance_km
    # will be overwritten with the actual GPS-measured distance on completion.
    ride_data["planned_distance_km"] = round(distance_km, 2)
    # Only store airport surcharge when it actually applies
    if airport_fee > 0:
        ride_data["airport_fee"] = _f(airport_fee)
        if airport_zone_name:
            ride_data["airport_zone_name"] = airport_zone_name

    ride_data["area_fees_breakdown"] = fees_result.get("fees", [])
    ride_data["area_fees_total"] = area_fees_total
    ride_data["tax_amount"] = tax_amount
    ride_data["tax_breakdown"] = fees_result.get("tax_breakdown", {})
    ride_data["grand_total"] = grand_total
    if idempotency_key:
        ride_data["idempotency_key"] = idempotency_key

    # ── Corporate work-profile pre-dispatch check ─────────────────────────────
    # Activated when the rider sends work_profile=true + corporate_account_id.
    # Policy violation or insufficient funds → reject before a driver is
    # disturbed. Personal rides (no work_profile flag) skip this block entirely.
    if body.work_profile and body.corporate_account_id:
        _corp_company_id = body.corporate_account_id

        # 1. Resolve active membership
        _memberships = await db_supabase.list_active_memberships_for_user(current_user["id"])
        _membership = next((m for m in _memberships if m.get("company_id") == _corp_company_id), None)
        if not _membership:
            raise HTTPException(
                status_code=400,
                detail={"reason": "no_corporate_membership"},
            )

        # 2–4. Fetch allowance + policy, evaluate all rules.
        _policy_result = await evaluate_policy_for_ride(
            corporate_account_id=_corp_company_id,
            rider_id=current_user["id"],
            estimated_fare=total_fare,
            ride_type=body.vehicle_type_id or "standard",
            pickup_time=datetime.now(_tz.utc),
            policy_override=_membership.get("policy_override", False),
        )
        if not _policy_result.passed:
            raise HTTPException(
                status_code=400,
                detail={"reason": "policy_violation", "failed_rules": _policy_result.failed_rules},
            )

        # 5. Pre-auth buffer check (skip for unlimited allowances).
        # Uses allowance + policy surfaced by evaluate_policy_for_ride so we
        # don't need a second round-trip to fetch them separately.
        _allowance = _policy_result.allowance
        _policy = _policy_result.policy
        if _allowance.get("type") != "unlimited":
            _remaining = _d(str(_allowance.get("amount") or 0)) - _d(str(max(float(_allowance.get("used") or 0), 0.0)))
            _master_permitted = _policy.get("allowed_payment_source", "both") in ("master_only", "both")
            if _remaining < _round(_d(str(_f(total_fare))) * _d("1.5")) and not _master_permitted:
                raise HTTPException(
                    status_code=400,
                    detail={"reason": "allowance_low"},
                )

        # 6. Tag ride as corporate
        ride_data["corporate_account_id"] = _corp_company_id
        ride_data["payment_method"] = "company_allowance"

    # ``insert_ride`` returns the row Supabase just wrote — use it directly
    # instead of a follow-up ``get_ride`` round-trip. Fall back to the
    # local ride_data if the driver returns None (e.g. stub DB in tests).
    #
    # ride_code (migration 40) is a short SPR-XXXXXX string operators and
    # riders can quote. The UUID in ride_data["id"] stays primary key.
    # On the astronomically-unlikely unique-constraint collision we retry
    # with a fresh code; on PGRST204 ("column does not exist", migration
    # hasn't landed yet) fall back to inserting without the code.
    inserted = None
    last_exc: Optional[Exception] = None
    for _attempt in range(3):
        ride_data["ride_code"] = generate_ride_code()
        try:
            inserted = await db_supabase.insert_ride(ride_data)
            break
        except Exception as e:
            last_exc = e
            msg = str(e).lower()
            if "column" in msg or "pgrst204" in msg:
                ride_data.pop("ride_code", None)
                inserted = await db_supabase.insert_ride(ride_data)
                break
            if "rides_one_active_per_rider" in msg:
                raise HTTPException(status_code=409, detail="You already have an active ride") from e
            if "unique" in msg or "duplicate" in msg or "23505" in msg:
                continue  # retry with a new code for ride_code conflicts
            raise
    else:
        logger.error(f"create_ride: could not allocate unique ride_code after 3 tries: {last_exc}")
        raise HTTPException(status_code=503, detail="Could not allocate ride code")

    fresh_ride = inserted or ride_data

    # Let admin live-monitoring see the request before dispatch starts —
    # previously the dashboard only observed a ride once a driver accepted,
    # which made it impossible to watch an unassigned ride sit in queue.
    try:
        await manager.broadcast_to_admins(
            {
                "type": "ride_requested",
                "ride_id": ride.id,
                "rider_id": fresh_ride.get("rider_id"),
                "pickup_address": fresh_ride.get("pickup_address"),
                "dropoff_address": fresh_ride.get("dropoff_address"),
                "pickup_lat": fresh_ride.get("pickup_lat"),
                "pickup_lng": fresh_ride.get("pickup_lng"),
                "dropoff_lat": fresh_ride.get("dropoff_lat"),
                "dropoff_lng": fresh_ride.get("dropoff_lng"),
                "fare": fresh_ride.get("total_fare"),
                "status": fresh_ride.get("status"),
            }
        )
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"create_ride: admin broadcast failed: {_exc}")

    # Match driver — pass the fresh ride through so the dispatch path
    # doesn't re-fetch the row we just inserted.
    await match_driver_to_ride(ride.id, ride=fresh_ride)

    # Dispatch may have set driver_id / status; read the current state
    # for the response. Skipping this extra fetch would mean the rider
    # app sees "searching" even when a driver was already assigned in
    # the same request, so we keep this one round-trip on purpose.
    updated_ride = await db_supabase.get_ride(ride.id)
    # The DB stores the SHA-256 hash; return the plain code to the rider
    # so the app can display it. Only this one response carries the plain text.
    if updated_ride:
        updated_ride["pickup_otp"] = pickup_otp_plain

    # Small helper to ensure we return a clean dict
    def serialize_doc(doc):
        return doc

    if updated_ride and updated_ride.get("status") == "searching":
        asyncio.create_task(ride_search_timeout(ride.id))

    return serialize_doc(updated_ride)


from fastapi import Request  # noqa: E402


@api_router.get("/active")
async def get_active_ride(current_user: dict = Depends(get_current_user)):
    """Get rider's current active/pending ride (if any). Used on app launch to resume."""
    # First check for rides that need payment (completed but not paid)
    # Then check for active rides
    active_statuses = ["searching", "driver_assigned", "driver_accepted", "driver_arrived", "in_progress"]

    # Check for unpaid completed ride first (must pay before new ride)
    unpaid_ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "rides",
            {
                "rider_id": current_user["id"],
                "status": "completed",
                "payment_status": {"$ne": "paid"},
            },
            limit=1,
        )
    )
    if unpaid_ride:
        ride = unpaid_ride
    else:
        ride = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows(
                "rides",
                {
                    "rider_id": current_user["id"],
                    "status": {"$in": active_statuses},
                },
                limit=1,
            )
        )

    if not ride:
        return {"active": False, "ride": None}

    # Attach driver info if assigned
    driver = None
    if ride.get("driver_id"):
        driver = await db_supabase.get_driver_by_id(ride["driver_id"])
        if driver:
            user = await db_supabase.get_user_by_id(driver.get("user_id"))
            driver = {
                "id": driver["id"],
                "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Driver",
                "rating": driver.get("rating", 4.8),
                "total_rides": driver.get("total_rides", 0),
                "vehicle_make": driver.get("vehicle_make"),
                "vehicle_model": driver.get("vehicle_model"),
                "vehicle_color": driver.get("vehicle_color"),
                "license_plate": driver.get("license_plate"),
                "lat": driver.get("lat"),
                "lng": driver.get("lng"),
                "heading": driver.get("heading"),
            }

    def serialize_doc(doc):
        return doc

    ride_data = serialize_doc(ride)
    ride_data["driver"] = driver
    return {"active": True, "ride": ride_data}


@api_router.get("/history")
async def get_ride_history(
    limit: int = Query(default=20, ge=1, le=100),
    before: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """Get rider's past rides for the activity tab with cursor-based pagination.

    Pass ``before=<ride_id>`` to fetch the page of rides older than that ride.
    Returns ``next_cursor`` (the id of the last ride in this page) to use as
    the next ``before`` value, or ``null`` when there are no more pages.
    """
    all_rides = await db_supabase.get_rows(
        "rides",
        {
            "rider_id": current_user["id"],
            "status": {"$in": ["completed", "cancelled"]},
        },
        limit=2000,
    )

    # Exclude cancelled rides that never had a driver (auto-expired searching)
    result = []
    for ride in all_rides:
        status = ride.get("status", "")
        had_driver = bool(ride.get("driver_id"))

        if status == "completed":
            result.append(ride)
        elif status == "cancelled" and had_driver:
            result.append(ride)

    result.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)

    # Cursor-based pagination: skip rides up to and including the cursor id
    if before:
        cursor_idx = next((i for i, r in enumerate(result) if r.get("id") == before), None)
        if cursor_idx is not None:
            result = result[cursor_idx + 1 :]

    rides = result[:limit]
    next_cursor = rides[-1]["id"] if len(rides) == limit else None

    return {"rides": rides, "limit": limit, "next_cursor": next_cursor}


@api_router.get("/{ride_id}")
async def get_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Fetch details of a specific ride"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Security check: must be rider or driver of this ride
    is_rider = ride.get("rider_id") == current_user["id"]
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        # Admin check
        if current_user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Not authorized to view this ride")

    # Include driver details if assigned.
    # Previously this dumped the ENTIRE driver row to the rider, which
    # included license_number, insurance_expiry_date,
    # background_check_expiry_date, work_eligibility_expiry_date,
    # vehicle_vin, document URLs, and the driver's stored phone — a
    # material PII leak to any rider on any ride. Only surface the
    # fields the rider actually needs to identify the driver and the
    # car pulling up (name, plate + make/model/color, rating, and the
    # live coordinates used for the map marker).
    if ride.get("driver_id"):
        assigned_driver = await db_supabase.get_driver_by_id(ride["driver_id"])
        if assigned_driver:
            ride["driver"] = DriverPublicView(
                id=assigned_driver.get("id", ""),
                name=assigned_driver.get("name", ""),
                rating=assigned_driver.get("rating"),
                total_rides=assigned_driver.get("total_rides"),
                profile_image_url=assigned_driver.get("profile_image_url"),
                vehicle_make=assigned_driver.get("vehicle_make"),
                vehicle_model=assigned_driver.get("vehicle_model"),
                vehicle_color=assigned_driver.get("vehicle_color"),
                license_plate=assigned_driver.get("license_plate"),
                vehicle_year=assigned_driver.get("vehicle_year"),
                lat=assigned_driver.get("lat"),
                lng=assigned_driver.get("lng"),
            ).dict()

    # Derive free_cancel_seconds_remaining + cancellation_fee from app_settings (UX-001).
    # These allow the frontend to show accurate countdown/fee without hardcoding.
    try:
        from settings_loader import get_app_settings  # type: ignore
    except ImportError:
        try:
            from ..settings_loader import get_app_settings  # type: ignore
        except ImportError:
            get_app_settings = None  # type: ignore

    free_cancel_window = 120
    cancellation_fee_amount = 3.0
    if get_app_settings:
        try:
            settings = await get_app_settings()
            free_cancel_window = int(settings.get("free_cancel_window_seconds", 120))
            cancellation_fee_amount = float(settings.get("cancellation_fee", 3.0))
        except Exception:  # noqa: S110
            pass

    driver_accepted_at = ride.get("driver_accepted_at")
    if driver_accepted_at:
        from datetime import datetime, timezone

        try:
            if isinstance(driver_accepted_at, str):
                accepted_dt = datetime.fromisoformat(driver_accepted_at.replace("Z", "+00:00"))
            else:
                accepted_dt = driver_accepted_at
            if accepted_dt.tzinfo is None:
                accepted_dt = accepted_dt.replace(tzinfo=timezone.utc)
            elapsed = int((datetime.now(timezone.utc) - accepted_dt).total_seconds())
            ride["free_cancel_seconds_remaining"] = max(0, free_cancel_window - elapsed)
        except Exception:
            ride["free_cancel_seconds_remaining"] = 0
    else:
        ride["free_cancel_seconds_remaining"] = None  # driver not yet accepted

    ride["free_cancel_window_seconds"] = free_cancel_window
    ride["cancellation_fee"] = cancellation_fee_amount

    def serialize_doc(doc):
        return doc

    return serialize_doc(ride)


@api_router.post("/{ride_id}/tip")
@idempotent_endpoint(scope="ride_tip")
async def add_tip(
    ride_id: str,
    req: TipRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    # Money arithmetic uses Decimal per CLAUDE.md. The old `float(req.amount)`
    # path drifted when summed with existing driver_earnings.
    tip_amount = _round(_d(req.amount))
    if tip_amount <= 0:
        raise HTTPException(status_code=400, detail="Tip amount must be greater than zero")

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user.get("id"):
        raise HTTPException(status_code=403, detail="Not authorized to tip this ride")

    if ride.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Can only tip completed rides")

    # R-P1-20: Block duplicate tips — one tip per ride.
    existing_tip = _d(ride.get("tip_amount") or 0)
    if existing_tip > 0:
        raise HTTPException(status_code=400, detail="ERR_TIP_DUPLICATE")

    existing_earnings = _d(ride.get("driver_earnings") or 0)
    new_tip = _round(existing_tip + tip_amount)
    new_driver_earnings = _round(existing_earnings + tip_amount)

    await db_supabase.update_ride(
        ride_id,
        {"tip_amount": _f(new_tip), "driver_earnings": _f(new_driver_earnings)},
    )

    # Notify the assigned driver so the tip shows up immediately instead
    # of only after the next earnings refresh. Best-effort — the tip has
    # already been persisted, so a failed notification must not surface
    # as a rider-facing error.
    driver_user_id = None
    driver_row_id = ride.get("driver_id")
    if driver_row_id:
        try:
            driver = await db_supabase.get_driver_by_id(driver_row_id)
            driver_user_id = driver.get("user_id") if driver else None
        except Exception as exc:
            logger.warning(f"[TIP] Could not resolve driver user_id for ride {ride_id}: {exc}")

    if driver_user_id:
        rider = await db_supabase.get_user_by_id(ride["rider_id"]) or {}
        rider_name = (rider.get("first_name") or "Your rider").strip() or "Your rider"
        payload = {
            "type": "tip_received",
            "ride_id": str(ride_id),
            "amount": _f(tip_amount),
            "new_total": _f(new_tip),
            "rider_name": rider_name,
        }
        try:
            await manager.send_personal_message(payload, f"driver_{driver_user_id}")
        except Exception as exc:
            logger.warning(f"[TIP] WS notify driver {driver_user_id} failed: {exc}")
        try:
            await send_push_notification(
                driver_user_id,
                "You got a tip! 💸",
                f"{rider_name} tipped you ${tip_amount:.2f}",
                data={"type": "tip_received", "ride_id": str(ride_id), "amount": f"{tip_amount:.2f}"},
            )
        except Exception as exc:
            logger.warning(f"[TIP] Push notify driver {driver_user_id} failed: {exc}")

    return {"success": True, "tip_amount": _f(new_tip)}


class ProcessPaymentRequest(BaseModel):
    tip_amount: Decimal = Field(default=Decimal("0"), ge=0, le=500)


@api_router.post("/{ride_id}/process-payment")
async def process_payment(ride_id: str, req: ProcessPaymentRequest, current_user: dict = Depends(get_current_user)):
    """Process payment for completed ride. Charges rider's card for fare + tip."""
    tip_amount = req.tip_amount  # already Decimal, validated by ProcessPaymentRequest

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user.get("id"):
        raise HTTPException(status_code=403, detail="Not authorized")

    _ride_status = ride.get("status", "")
    if _ride_status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Ride is in status '{_ride_status}'; payment requires completed state.",
        )

    # IDEMPOTENCY: if already paid, return success without charging again
    if ride.get("payment_status") in ("paid", "processing"):
        logger.info(f"[PAYMENT] Ride {ride_id} already {ride['payment_status']} — skipping duplicate charge")
        return {
            "success": True,
            "charged_amount": ride.get("total_fare", 0) + (ride.get("tip_amount", 0) or 0),
            "already_paid": True,
        }

    # Atomic guard: set payment_status to "processing" only if it's still "pending".
    # Filter on payment_status="pending" so concurrent requests can't both proceed —
    # Supabase returns the updated row only when the filter matches; None means
    # another request won the race first.
    guard_row = await db_supabase.update_one(
        "rides",
        {"id": ride_id, "payment_status": "pending"},
        {"payment_status": "processing", "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    if guard_row is None:
        return {"success": True, "already_paid": True, "charged_amount": 0}

    if tip_amount < 0:
        raise HTTPException(status_code=400, detail="Tip amount cannot be negative")
    if tip_amount > 500:
        raise HTTPException(status_code=400, detail="Tip amount exceeds maximum ($500)")

    def _q(v) -> Decimal:
        return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    total_charge = _q(ride.get("total_fare", 0) or 0) + _q(tip_amount)

    # Branch on payment method.
    payment_method = (ride.get("payment_method") or "card").lower()

    if payment_method == "wallet":
        from .wallet import _record_transaction, get_or_create_wallet

        wallet = await get_or_create_wallet(current_user["id"])
        if not wallet.get("is_active", True):
            # Release the processing lock so a retry with a different
            # method (e.g. card) isn't blocked by the atomic guard above.
            await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
            raise HTTPException(status_code=403, detail="Wallet is suspended")

        old_balance = _q(wallet.get("balance", 0))
        debit = total_charge
        if old_balance < debit:
            await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient wallet balance. Need ${debit}, have ${old_balance}",
            )

        new_balance = old_balance - debit
        await db.update_one(
            "wallets",
            {"id": wallet["id"]},
            {"$set": {"balance": _f(new_balance), "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        await _record_transaction(
            wallet_id=wallet["id"],
            user_id=current_user["id"],
            txn_type="ride_payment",
            amount=-_f(debit),
            balance_after=_f(new_balance),
            reference_id=ride_id,
            description=f"Ride payment ${_f(debit):.2f}",
        )
        await db_supabase.update_ride(
            ride_id,
            {
                "payment_status": "paid",
                "tip_amount": _f(tip_amount),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    elif payment_method == "company_allowance":
        _company_id = ride.get("corporate_account_id")
        if not _company_id:
            raise HTTPException(status_code=400, detail="Corporate account not set on ride")

        # 1. Resolve membership
        _corp_memberships = await db_supabase.list_active_memberships_for_user(ride["rider_id"])
        _corp_membership = next((m for m in _corp_memberships if m.get("company_id") == _company_id), None)
        if not _corp_membership:
            await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
            raise HTTPException(status_code=400, detail="Corporate membership not found")

        # 2. Fetch allowance and wallet
        _corp_allowance = await db_supabase.get_member_allowance(_corp_membership["id"]) or {}
        _corp_wallet = await db_supabase.get_corporate_wallet_by_company(_company_id) or {}

        # 3. Compute split — allowance covers what it can, master covers rest
        _total = _round(_d(str(total_charge)))
        if _corp_allowance.get("type") == "unlimited":
            _allowance_debit = _total
            _master_debit = _round(Decimal("0"))
        else:
            _remaining = _round(
                _d(str(_corp_allowance.get("amount") or 0)) - _d(str(max(float(_corp_allowance.get("used") or 0), 0.0)))
            )
            _remaining = max(_remaining, _round(Decimal("0")))
            _allowance_debit = min(_remaining, _total)
            _master_debit = _total - _allowance_debit

        # 4. Check master fallback permission
        _corp_policy = await db_supabase.get_corporate_policy(_company_id) or {}
        _flag_violation = False
        if _master_debit > 0 and _corp_policy.get("allowed_payment_source") == "allowance_only":
            # Debit-and-flag: driver must be paid, never strand the ride
            _flag_violation = True

        # 5. Apply allowance debit (calls corporate_allowance_apply_delta RPC)
        if _allowance_debit > 0 and _corp_allowance.get("id") and _corp_wallet.get("id"):
            await corporate_allowance_service.apply_rollback(
                wallet_id=_corp_wallet["id"],
                allowance_id=_corp_allowance["id"],
                member_id=_corp_membership["id"],
                amount=_f(_allowance_debit),
                notes=f"ride:{ride_id}:allowance",
            )

        # 6. Apply master wallet debit (calls corporate_wallet_apply_delta RPC)
        if _master_debit > 0 and _corp_wallet.get("id"):
            await corporate_wallet_service.apply_adjustment(
                wallet_id=_corp_wallet["id"],
                amount=-_f(_master_debit),
                notes=f"Ride fallback debit {ride_id}",
                actor_user_id=ride.get("rider_id", "system"),
            )

        # 7. Insert ride_payment_sources row
        await db_supabase.insert_one(
            "ride_payment_sources",
            {
                "ride_id": ride_id,
                "source_type": "company_allowance",
                "allowance_debit_amount": _f(_allowance_debit),
                "master_fallback_amount": _f(_master_debit),
                "member_id": _corp_membership["id"],
                "company_id": _company_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # 8. Policy re-check at completion (log only — never strand driver)
        _completion_ctx = {
            "final_fare": _f(_total),
            "phase": "completion",
            "allowance": _corp_allowance,
        }
        _completion_eval = evaluate_policy(_corp_policy, _completion_ctx)
        if not _completion_eval["pass"] or _flag_violation:
            await db_supabase.insert_one(
                "corporate_policy_evaluations",
                {
                    "ride_id": ride_id,
                    "member_id": _corp_membership["id"],
                    "company_id": _company_id,
                    "phase": "completion",
                    "result": "violation",
                    "failed_rules": _completion_eval.get("failed_rules", []),
                    "bypassed_rules": [],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        await db_supabase.update_ride(
            ride_id,
            {
                "payment_status": "paid",
                "tip_amount": _f(tip_amount),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    else:
        # Card path: real Stripe charge via charge_ride() helper.
        # multi-outcome (succeeded / requires_action / declined / failed).
        rider_user = await db_supabase.get_user_by_id(current_user["id"])
        stripe_customer_id = (rider_user or {}).get("stripe_customer_id")
        payment_method_id = ride.get("payment_method_id") or (rider_user or {}).get("default_payment_method")

        if not payment_method_id:
            await db_supabase.update_ride(ride_id, {"payment_status": "pending"})
            raise HTTPException(status_code=400, detail="No payment method on file. Please add a card.")

        outcome = await charge_ride(
            ride=ride,
            rider_id=current_user["id"],
            total_amount=_f(total_charge),
            payment_method_id=payment_method_id,
            stripe_customer_id=stripe_customer_id,
            payment_intent_id=ride.get("payment_intent_id"),
        )

        if outcome.status == "succeeded":
            await db_supabase.update_ride(
                ride_id,
                {
                    "payment_status": "paid",
                    "payment_intent_id": outcome.payment_intent_id,
                    "tip_amount": _f(tip_amount),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            await manager.send_personal_message(
                {"type": "payment_completed", "ride_id": ride_id, "charged_amount": _f(total_charge)},
                f"rider_{current_user['id']}",
            )
        elif outcome.status == "requires_action":
            # Off-session charges that require 3DS cannot be completed without rider interaction.
            # Treat as a payment failure so the rider is prompted to use a different card.
            await db_supabase.update_ride(
                ride_id,
                {
                    "payment_status": "failed",
                    "payment_intent_id": outcome.payment_intent_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "authentication_required",
                    "message": "Card requires authentication. Please update your payment method.",
                },
            )
        elif outcome.status == "declined":
            await db_supabase.update_ride(
                ride_id,
                {
                    "payment_status": "failed",
                    "payment_intent_id": outcome.payment_intent_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "card_declined",
                    "decline_code": outcome.decline_code,
                    "message": outcome.error_message or "Your card was declined.",
                    "suggested_action": "change_card",
                },
            )
        elif outcome.status == "unconfigured":
            logger.warning("Stripe unconfigured — marking ride %s paid without real charge", ride_id)
            await db_supabase.update_ride(
                ride_id,
                {
                    "payment_status": "paid",
                    "tip_amount": _f(tip_amount),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        else:
            await db_supabase.update_ride(
                ride_id,
                {
                    "payment_status": "failed",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "payment_error",
                    "message": outcome.error_message or "Payment could not be processed.",
                },
            )

    # Send receipt email (SendGrid when configured, logs otherwise)
    rider = await db_supabase.get_user_by_id(current_user["id"])
    driver_info = None
    if ride.get("driver_id"):
        drv = await db_supabase.get_driver_by_id(ride["driver_id"])
        if drv:
            du = await db_supabase.get_user_by_id(drv.get("user_id"))
            if du:
                driver_info = {**du, "name": f"{du.get('first_name', '')} {du.get('last_name', '')}".strip()}

    email_sent = False
    try:
        from utils.email_receipt import send_receipt_email

        email_sent = await send_receipt_email(ride, rider or {}, driver_info, _f(tip_amount))
    except Exception as e:
        logger.warning(f"Receipt email error: {e}")

    return {"success": True, "charged_amount": _f(total_charge), "email_sent": email_sent}


# ============================================================
# GAP FIX: Share Ride Link (Uber/Lyft standard feature)
# ============================================================


@api_router.get("/{ride_id}/share")
async def get_share_trip_link(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Generate a shareable trip tracking link for safety contacts (like Uber's 'Share My Trip')."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to share this ride")

    if ride.get("status") in ["completed", "cancelled"]:
        raise HTTPException(status_code=400, detail="Cannot share a completed or cancelled ride")

    # Generate or reuse a share token (with creation timestamp for expiry)
    share_token = ride.get("shared_trip_token")
    if not share_token:
        share_token = secrets.token_urlsafe(32)
        await db_supabase.update_ride(ride_id, {"shared_trip_token": share_token})

    # The frontend would use this token to show a read-only tracking page
    # In production, this would be a full URL like: https://spinr.app/track/{share_token}
    share_url = f"/track/{share_token}"

    return {"success": True, "share_token": share_token, "share_url": share_url, "ride_id": ride_id}


class ShareTripWithContactRequest(BaseModel):
    contact_name: str
    contact_phone: str


@api_router.post("/{ride_id}/share")
async def share_trip_with_contact(
    ride_id: str, body: ShareTripWithContactRequest, current_user: dict = Depends(get_current_user)
):
    """Share trip with a specific contact and send them a notification."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") in ["completed", "cancelled"]:
        raise HTTPException(status_code=400, detail="Cannot share a completed or cancelled ride")

    # Get or create share token
    share_token = ride.get("shared_trip_token")
    if not share_token:
        share_token = secrets.token_urlsafe(32)
        await db.update_one(
            "rides",
            {"id": ride_id},
            {
                "$set": {
                    "shared_trip_token": share_token,
                    "shared_trip_token_created_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    # Record the contact in shared_with list
    shared_with = ride.get("shared_with") or []
    contact_entry = {
        "name": body.contact_name,
        "phone": body.contact_phone,
        "shared_at": datetime.now(timezone.utc).isoformat(),
    }
    # Avoid duplicates by phone
    if not any(c.get("phone") == body.contact_phone for c in shared_with):
        shared_with.append(contact_entry)
        await db.update_one(
            "rides",
            {"id": ride_id},
            {"$set": {"shared_with": shared_with}},
        )

    share_url = f"/track/{share_token}"

    # Send push notification to contact if they're a registered user
    contact_user = await db.find_one("users", {"phone": body.contact_phone})
    if contact_user:
        rider = await db.find_one("users", {"id": current_user["id"]})
        rider_name = f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() if rider else "Someone"
        await send_push_notification(
            contact_user["id"],
            f"{rider_name} is sharing their ride with you",
            f"Track their live location: {ride.get('pickup_address', '')} → {ride.get('dropoff_address', '')}",
            data={"type": "trip_shared", "share_token": share_token, "ride_id": ride_id},
        )

    return {
        "success": True,
        "share_token": share_token,
        "share_url": share_url,
        "contact_notified": contact_user is not None,
        "shared_with": shared_with,
    }


@api_router.get("/{ride_id}/shared-contacts")
async def get_shared_contacts(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Get list of contacts this ride has been shared with."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"contacts": ride.get("shared_with") or []}


@api_router.get("/track/{share_token}")
async def track_shared_ride(share_token: str):
    """Public endpoint - Get ride status via share token (no auth required)."""
    ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("rides", {"shared_trip_token": share_token}, limit=1)
    )
    if not ride:
        raise HTTPException(status_code=404, detail="Shared ride not found or link expired")

    # Expire share tokens after 24 hours
    token_created = ride.get("shared_trip_token_created_at")
    if token_created:
        from datetime import timedelta

        try:
            created_dt = datetime.fromisoformat(token_created) if isinstance(token_created, str) else token_created
            if datetime.now(timezone.utc) - created_dt > timedelta(hours=24):
                raise HTTPException(status_code=404, detail="Share link has expired")
        except (ValueError, TypeError):
            pass  # Malformed timestamp — allow access but log
            logger.warning(f"Malformed shared_trip_token_created_at for ride {ride.get('id')}")

    if ride.get("status") in ["completed", "cancelled"]:
        return {
            "status": ride.get("status"),
            "message": "This ride has ended.",
            "pickup_address": ride.get("pickup_address"),
            "dropoff_address": ride.get("dropoff_address"),
        }

    # Get driver location for live tracking — only expose what safety contacts need
    driver_info = None
    if ride.get("driver_id"):
        driver = await db_supabase.get_driver_by_id(ride["driver_id"])
        if driver:
            driver_info = {
                "name": driver.get("name", "Driver"),
                "lat": driver.get("lat"),
                "lng": driver.get("lng"),
                "vehicle_make": driver.get("vehicle_make"),
                "vehicle_model": driver.get("vehicle_model"),
                "vehicle_color": driver.get("vehicle_color"),
            }

    return {
        "status": ride.get("status"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "driver": driver_info,
    }


@api_router.post("/{ride_id}/rate")
async def rate_driver(ride_id: str, rating_data: RideRatingRequest, current_user: dict = Depends(get_current_user)):
    """Rider rates the driver"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride or ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=404, detail="Ride not found or unauthorized")

    # Save rating using existing columns (rider_rating = rating rider gave the driver)
    await db_supabase.update_ride(
        ride_id,
        {
            "rider_rating": rating_data.rating,
            "rider_comment": rating_data.comment or "",
            "updated_at": datetime.now(timezone.utc),
        },
    )

    driver_id = ride.get("driver_id")
    if not driver_id:
        return {"success": True}

    if rating_data.tip_amount > 0:
        new_tip = ride.get("tip_amount", 0) + rating_data.tip_amount
        new_driver_earnings = ride.get("driver_earnings", 0) + rating_data.tip_amount
        await db_supabase.update_ride(ride_id, {"tip_amount": new_tip, "driver_earnings": new_driver_earnings})

    # Aggregate driver rating using rolling average to avoid O(n) ride fetch.
    driver = await db_supabase.get_driver_by_id(driver_id)
    if driver:
        old_count = int(driver.get("total_ratings") or 0)
        old_avg = float(driver.get("rating") or 0)
        new_count = old_count + 1
        new_avg = round((old_avg * old_count + float(rating_data.rating)) / new_count, 2)
        await db_supabase.update_one(
            "drivers",
            {"id": driver_id},
            {
                "rating": new_avg,
                "average_rating": new_avg,
                "total_ratings": new_count,
            },
        )

    # G19: Notify the driver that they received a rating. This creates a
    # feedback loop — drivers see their rating improve/decline in real time
    # instead of only noticing on their next profile check.
    if driver and driver.get("user_id") and rating_data.rating:
        stars = "⭐" * int(rating_data.rating)
        tip_note = f" + ${rating_data.tip_amount:.2f} tip!" if rating_data.tip_amount > 0 else ""
        try:
            await send_push_notification(
                driver["user_id"],
                f"New Rating: {stars}",
                f"A rider rated you {rating_data.rating}/5{tip_note}",
                {"type": "rating_received", "rating": str(rating_data.rating), "ride_id": ride_id},
            )
        except Exception as push_err:
            logger.warning(f"[RATING] Push notification failed: {push_err}")

    return {"success": True}


@api_router.post("/{ride_id}/cancel")
@cancel_ride_limit
async def cancel_ride_rider(request: Request, ride_id: str, current_user: dict = Depends(get_current_user)):
    """Rider cancels the ride"""
    try:
        from ..logging_utils import diag_logger  # type: ignore
    except ImportError:
        from logging_utils import diag_logger  # type: ignore

    diag_logger.info(f"[CANCEL] called ride_id={ride_id} user_id={current_user.get('id')}")

    ride = await _require_ride_in_state_rider(
        ride_id,
        current_user["id"],
        ("requested", "searching", "driver_assigned", "en_route", "driver_arrived"),
    )
    diag_logger.info(
        f"[CANCEL] entry ride_id={ride_id} pre_status={ride.get('status')} driver_id={ride.get('driver_id')}"
    )

    # Calculate cancellation fee based on time since driver accepted
    driver_id = ride.get("driver_id")
    settings = await get_app_settings()
    cancellation_fee_admin = settings.get("cancellation_fee_admin", 0.50)
    cancellation_fee_driver = settings.get("cancellation_fee_driver", 2.50)

    charged_admin = 0.0
    charged_driver = 0.0

    # Flat $5.00 fee when the driver has already arrived — overrides the
    # time-based check below because the driver has made the full trip to
    # the pickup and the wait is no longer relevant.
    if ride.get("status") == "driver_arrived" and driver_id:
        charged_admin = cancellation_fee_admin
        charged_driver = Decimal("5.00")

    # Calculate fee if driver was already assigned and some time passed (e.g. 2 mins)
    elif driver_id and ride.get("driver_accepted_at"):
        accepted_at = parse_iso_utc(ride["driver_accepted_at"])
        time_diff = (datetime.now(timezone.utc) - accepted_at).total_seconds() if accepted_at else 0
        if time_diff > 120:  # 2 minutes
            charged_admin = cancellation_fee_admin
            charged_driver = cancellation_fee_driver

    # Pay out charged_driver to the driver's wallet and push-notify them.
    if driver_id and charged_driver > 0:
        try:
            fee_dec = _d(str(charged_driver))
            driver_for_fee = await db_supabase.get_driver_by_id(driver_id)
            driver_user_id = driver_for_fee.get("user_id") if driver_for_fee else None
            if driver_user_id:
                wallet = await db.find_one("wallets", {"user_id": driver_user_id})
                if wallet:
                    new_balance = _round(_d(str(wallet.get("balance", 0))) + fee_dec)
                    await db.update_one(
                        "wallets",
                        {"id": wallet["id"]},
                        {"$set": {"balance": _f(new_balance), "updated_at": datetime.now(timezone.utc).isoformat()}},
                    )
                    await db.insert_one(
                        "wallet_transactions",
                        {
                            "id": str(uuid.uuid4()),
                            "wallet_id": wallet["id"],
                            "user_id": driver_user_id,
                            "type": "cancellation_fee",
                            "amount": _f(fee_dec),
                            "balance_after": _f(new_balance),
                            "reference_id": ride_id,
                            "description": f"Cancellation fee for ride {ride_id}",
                            "metadata": {"ride_id": ride_id, "status_at_cancel": ride.get("status")},
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                await send_push_notification(
                    driver_user_id,
                    title="Cancellation fee earned",
                    body=f"${fee_amount:.2f} cancellation fee added to your earnings.",
                    data={"type": "cancellation_fee_paid", "ride_id": ride_id},
                )
        except Exception as fee_err:
            logger.warning(f"[CANCEL] cancellation fee payout failed for driver {driver_id}: {fee_err}")

    _now = datetime.now(timezone.utc)
    _base_update = {
        "status": "cancelled",
        "cancelled_at": _now,
        "cancellation_fee_admin": charged_admin,
        "cancellation_fee_driver": charged_driver,
        "updated_at": _now,
    }
    # Migration 38 — attribution. Fall back to the legacy payload on
    # PGRST204 so the rider's cancel button never 503s if the column
    # isn't in prod yet.
    try:
        await db_supabase.update_ride(
            ride_id,
            {**_base_update, "cancelled_by": "rider", "cancellation_type": "rider_cancel"},
        )
    except Exception as _col_exc:
        logger.warning(f"[CANCEL] attribution write failed ({_col_exc}); retrying minimal")
        await db_supabase.update_ride(ride_id, _base_update)

    # Verify the cancel actually landed in the database. Same class of
    # silent-failure we hit with go-online and accept: the update_one wrapper
    # returns None when zero rows are affected and the handler would
    # otherwise return {success: true} while the ride is still in its prior
    # state — the rider then reloads and sees the ride still "searching".
    try:
        verify_ride = await db_supabase.get_ride(ride_id)
    except Exception as e:
        verify_ride = None
        diag_logger.info(f"[CANCEL] verify re-read failed: {e}")

    diag_logger.info(
        f"[CANCEL] post-update ride_id={ride_id} "
        f"post_status={verify_ride.get('status') if verify_ride else 'ROW_GONE'} "
        f"post_cancelled_at={verify_ride.get('cancelled_at') if verify_ride else 'ROW_GONE'}"
    )

    if not verify_ride or verify_ride.get("status") != "cancelled":
        diag_logger.info(
            f"[CANCEL] SILENT NO-OP: ride_id={ride_id} did not flip to "
            f"'cancelled'. Likely a missing column in the rides table "
            f"(e.g. cancelled_at / cancellation_fee_admin / "
            f"cancellation_fee_driver) or a wrapper dispatching the "
            f"update to the wrong path. Rider will see the ride as still "
            f"active after reload."
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Cancel did not persist. Backend write returned successfully "
                "but the ride row is unchanged. Check backend logs for "
                "[CANCEL] lines."
            ),
        )

    if driver_id:
        await db_supabase.set_driver_available(driver_id, True)

        # Notify driver
        driver = await db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("user_id"):
            await manager.send_personal_message(
                {"type": "ride_cancelled", "ride_id": ride_id, "reason": "Rider cancelled"},
                f"driver_{driver['user_id']}",
            )

    await manager.broadcast_ride_status(ride_id, "cancelled", reason="rider_cancelled")
    try:
        await manager.broadcast_to_admins({"type": "ride_cancelled", "ride_id": ride_id, "reason": "rider_cancelled"})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"rider cancel admin broadcast failed: {_exc}")

    return {"success": True, "cancellation_fee": charged_admin + charged_driver}


# ── Mid-Trip Stop Editing ─────────────────────────────────────────────


class AddStopMidTripRequest(BaseModel):
    address: str
    lat: float
    lng: float
    position: Optional[int] = None  # Insert at this index; None = append


@api_router.post("/{ride_id}/stops")
async def add_stop_mid_trip(ride_id: str, req: AddStopMidTripRequest, current_user: dict = Depends(get_current_user)):
    """Add a stop to an active ride mid-trip."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") not in ("driver_accepted", "driver_arrived", "in_progress"):
        raise HTTPException(status_code=400, detail="Can only edit stops on an active ride")

    stops = ride.get("stops") or []
    new_stop = {"address": req.address, "lat": req.lat, "lng": req.lng}

    if req.position is not None and 0 <= req.position <= len(stops):
        stops.insert(req.position, new_stop)
    else:
        stops.append(new_stop)

    fare_update = _reestimate_fare_for_stops(ride, stops)
    await db.update_one(
        "rides",
        {"id": ride_id},
        {"$set": {**fare_update, "stops": stops, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )

    # Notify driver via WebSocket
    if ride.get("driver_id"):
        driver = await db.find_one("drivers", {"id": ride["driver_id"]})
        if driver and driver.get("user_id"):
            await manager.send_personal_message(
                {
                    "type": "stops_updated",
                    "ride_id": ride_id,
                    "stops": stops,
                    "estimated_fare": fare_update["estimated_fare"],
                },
                f"driver_{driver['user_id']}",
            )

    return {"success": True, "stops": stops, **fare_update}


@api_router.delete("/{ride_id}/stops/{stop_index}")
async def remove_stop_mid_trip(ride_id: str, stop_index: int, current_user: dict = Depends(get_current_user)):
    """Remove a stop from an active ride by index."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") not in ("driver_accepted", "driver_arrived", "in_progress"):
        raise HTTPException(status_code=400, detail="Can only edit stops on an active ride")

    stops = ride.get("stops") or []
    if stop_index < 0 or stop_index >= len(stops):
        raise HTTPException(status_code=400, detail="Invalid stop index")

    stops.pop(stop_index)

    fare_update = _reestimate_fare_for_stops(ride, stops)
    await db.update_one(
        "rides",
        {"id": ride_id},
        {"$set": {**fare_update, "stops": stops, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )

    # Notify driver
    if ride.get("driver_id"):
        driver = await db.find_one("drivers", {"id": ride["driver_id"]})
        if driver and driver.get("user_id"):
            await manager.send_personal_message(
                {
                    "type": "stops_updated",
                    "ride_id": ride_id,
                    "stops": stops,
                    "estimated_fare": fare_update["estimated_fare"],
                },
                f"driver_{driver['user_id']}",
            )

    return {"success": True, "stops": stops, **fare_update}


class EmergencyRequest(BaseModel):
    message: str = "Emergency assistance requested"
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@api_router.post("/{ride_id}/emergency")
async def trigger_emergency(ride_id: str, request: EmergencyRequest, current_user: dict = Depends(get_current_user)):
    """Trigger an emergency alert for a live ride"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Verify the user is part of the ride
    is_rider = ride.get("rider_id") == current_user["id"]
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to trigger emergency for this ride")

    incident = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "reported_by_user_id": current_user["id"],
        "role": "rider" if is_rider else "driver",
        "message": request.message,
        "status": "open",
        "latitude": request.latitude,
        "longitude": request.longitude,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    await db_supabase.insert_one("emergencies", incident)

    # Notify admin dashboard via Websocket — broadcast across every
    # replica's admin connections. The previous ``admin_notifications``
    # client_id pointed at no real socket, so alerts silently disappeared.
    try:
        await manager.broadcast_to_admins({"type": "emergency_alert", "incident": incident})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"emergency_alert admin broadcast failed: {_exc}")
    logger.critical(f"EMERGENCY ALERT TRIGGERED for ride {ride_id} by user {current_user['id']}")

    # GAP FIX: Notify emergency contacts via SMS/push
    try:
        contacts_cursor = db_supabase.get_rows("emergency_contacts", {"user_id": current_user["id"]}, limit=100)
        contacts = (
            await contacts_cursor.to_list(length=5) if hasattr(contacts_cursor, "to_list") else list(contacts_cursor)
        )

        user = await db_supabase.get_user_by_id(current_user["id"])
        user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "A Spinr user"

        for contact in contacts:
            # In production, this would send an actual SMS via Twilio
            logger.info(
                f"EMERGENCY SMS to {contact.get('name')} ({contact.get('phone')}): "
                f"{user_name} triggered an emergency alert during their Spinr ride. "
                f"Location: {request.latitude}, {request.longitude}"
            )

        if contacts:
            logger.info(f"Notified {len(contacts)} emergency contacts for user {current_user['id']}")
    except Exception as e:
        logger.warning(f"Could not notify emergency contacts: {e}")

    return {
        "success": True,
        "incident_id": incident["id"],
        "contacts_notified": len(contacts) if "contacts" in dir() else 0,
    }


@api_router.get("/{ride_id}/chat-status")
async def get_chat_status(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Check if chat is available for this ride (active rides + 24h post-trip window)."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    status = ride.get("status", "")
    if status == "cancelled":
        return {"available": False, "reason": "Ride was cancelled"}

    if status == "completed":
        completed_at = parse_iso_utc(ride.get("ride_completed_at") or ride.get("updated_at"))
        if completed_at:
            elapsed = (datetime.now(timezone.utc) - completed_at).total_seconds()
            remaining = max(0, 86400 - elapsed)
            if remaining <= 0:
                return {"available": False, "reason": "Post-trip chat window expired"}
            hours_left = int(remaining // 3600)
            return {"available": True, "post_trip": True, "hours_remaining": hours_left}
        return {"available": True, "post_trip": True, "hours_remaining": 24}

    # Active ride — chat is fully available
    return {"available": True, "post_trip": False}


@api_router.get("/{ride_id}/call")
async def get_call_info(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Get masked phone number for calling the other party during an active ride.

    Returns a proxy number or the real number depending on Twilio config.
    In production, this would create a Twilio Proxy session to mask both
    parties' real numbers. For now, it returns the other party's phone
    directly so the call button works immediately.
    """
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("status") in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail="Cannot call on a completed or cancelled ride")

    is_rider = ride.get("rider_id") == current_user["id"]
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not part of this ride")

    if is_rider:
        # Rider wants to call the driver
        if not ride.get("driver_id"):
            raise HTTPException(status_code=400, detail="No driver assigned yet")
        target_driver = await db.find_one("drivers", {"id": ride["driver_id"]})
        if not target_driver:
            raise HTTPException(status_code=404, detail="Driver not found")
        target_user = await db.find_one("users", {"id": target_driver.get("user_id")})
        phone = target_user.get("phone") if target_user else None
        name = (
            f"{target_user.get('first_name', '')} {target_user.get('last_name', '')}".strip()
            if target_user
            else "Driver"
        )
    else:
        # Driver wants to call the rider
        target_user = await db.find_one("users", {"id": ride["rider_id"]})
        phone = target_user.get("phone") if target_user else None
        name = (
            f"{target_user.get('first_name', '')} {target_user.get('last_name', '')}".strip()
            if target_user
            else "Rider"
        )

    if not phone:
        raise HTTPException(status_code=404, detail="Phone number not available")

    # In production: create Twilio Proxy session here and return proxy number
    # For now, return the real number with a masked display
    masked = f"({'*' * (len(phone) - 4)}{phone[-4:]})" if len(phone) > 4 else phone

    return {
        "phone": phone,
        "masked": masked,
        "name": name,
        "proxy": False,  # Set to True when Twilio Proxy is configured
    }


@api_router.get("/{ride_id}/messages")
async def get_ride_messages(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Fetch persistent chat messages for a ride"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Verify the user is part of the ride
    is_rider = ride.get("rider_id") == current_user["id"]
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to track this ride")

    messages = await db_supabase.get_rows(
        "ride_messages", {"ride_id": ride_id}, limit=100, order="timestamp", desc=False
    )

    # Serialize datetime
    serialized = []
    for msg in messages:
        # Provide fallback serialize
        if "timestamp" in msg and isinstance(msg["timestamp"], datetime):
            msg["timestamp"] = msg["timestamp"].isoformat()
        serialized.append(msg)

    return {"success": True, "messages": serialized}


class SendMessageRequest(BaseModel):
    text: str


@api_router.post("/{ride_id}/messages")
async def send_ride_message(ride_id: str, body: SendMessageRequest, current_user: dict = Depends(get_current_user)):
    """Send a chat message for an active or recently completed ride.

    Persists the message in `ride_messages` and forwards it to the
    other party via WebSocket (if they're connected). Works as a REST
    fallback for screens that don't hold a direct WS reference (e.g.
    the rider-app chat screen).

    Post-trip chat: messages are allowed for 24 hours after ride
    completion to support lost-item, feedback, and coordination use cases.
    Only the rider or the assigned driver of the ride can send.
    """
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Block chat on cancelled rides
    if ride.get("status") == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot send messages on a cancelled ride")

    # Post-trip chat window: allow messages for 24h after completion
    if ride.get("status") == "completed":
        completed_at = parse_iso_utc(ride.get("ride_completed_at") or ride.get("updated_at"))
        if completed_at and (datetime.now(timezone.utc) - completed_at).total_seconds() > 86400:
            raise HTTPException(status_code=400, detail="Post-trip chat window has expired (24 hours)")

    is_rider = ride.get("rider_id") == current_user["id"]
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to send messages in this ride")

    sender = "rider" if is_rider else "driver"
    msg_data = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "text": body.text.strip(),
        "sender": sender,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await db.insert_one("ride_messages", msg_data)

    # Forward to the other party via WebSocket.
    target = None
    if sender == "rider" and ride.get("driver_id"):
        d = await db.find_one("drivers", {"id": ride["driver_id"]})
        if d and d.get("user_id"):
            target = f"driver_{d['user_id']}"
    elif sender == "driver":
        target = f"rider_{ride['rider_id']}"

    if target:
        await manager.send_personal_message({**msg_data, "type": "chat_message"}, target)

    return {"success": True, "message": msg_data}


@api_router.get("/scheduled")
async def get_scheduled_rides(current_user: dict = Depends(get_current_user)):
    """Get all upcoming scheduled rides for the current rider."""
    rides_cursor = db_supabase.get_rides_for_user(current_user, limit=100)
    rides = await rides_cursor.to_list(length=50) if hasattr(rides_cursor, "to_list") else list(rides_cursor)
    return rides


@api_router.delete("/scheduled/{ride_id}")
async def cancel_scheduled_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Cancel a scheduled ride."""
    ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "rides", {"id": ride_id, "rider_id": current_user["id"], "is_scheduled": True}, limit=1
        )
    )
    if not ride:
        raise HTTPException(status_code=404, detail="Scheduled ride not found")
    if ride.get("status") in ["completed", "cancelled"]:
        raise HTTPException(status_code=400, detail="Ride is already completed or cancelled")

    _now = datetime.now(timezone.utc)
    _base = {
        "status": "cancelled",
        "cancelled_at": _now,
        "cancellation_reason": "Cancelled by rider (scheduled)",
        "updated_at": _now,
    }
    try:
        await db_supabase.update_ride(
            ride_id,
            {**_base, "cancelled_by": "rider", "cancellation_type": "rider_cancel"},
        )
    except Exception as _col_exc:
        logger.warning(f"[SCHED-CANCEL] attribution write failed ({_col_exc}); retrying minimal")
        await db_supabase.update_ride(ride_id, _base)
    return {"success": True}


@api_router.post("/{ride_id}/simulate-arrival")
async def simulate_driver_arrival(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Dev/test only: Simulate driver arriving at pickup, returns OTP."""
    if _settings.ENV.lower() == "production":
        raise HTTPException(status_code=403, detail="Not available in production")
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db_supabase.update_ride(
        ride_id,
        {
            "status": "driver_arrived",
            "driver_arrived_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    updated_ride = await db_supabase.get_ride(ride_id)
    return {"success": True, "pickup_otp": updated_ride.get("pickup_otp", "0000")}


@api_router.post("/{ride_id}/start")
async def rider_start_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Start a ride. Restricted to the assigned driver only (R-P1-17)."""
    # R-P1-17: Only the assigned driver may mark a ride as started.
    if not current_user.get("is_driver"):
        raise HTTPException(status_code=403, detail="ERR_DRIVER_ONLY")
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    # Verify this driver is the one assigned to the ride
    driver_row = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver_row or ride.get("driver_id") != driver_row["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") not in ["driver_arrived"]:
        raise HTTPException(status_code=400, detail=f"Cannot start ride with status: {ride.get('status')}")

    await db_supabase.update_ride(
        ride_id,
        {
            "status": "in_progress",
            "ride_started_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    return {"success": True}


@api_router.post("/{ride_id}/complete")
async def rider_complete_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Rider-side: Get completed ride data (ride is completed by driver; this fetches the result)."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    # Return the current ride state (driver will have set it to completed)
    return ride


@api_router.get("/{ride_id}/receipt")
async def get_ride_receipt(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Get a detailed receipt for a completed ride"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view this receipt")

    if ride.get("status") not in ["completed", "cancelled"]:
        raise HTTPException(status_code=400, detail="Receipts are only available for completed or cancelled rides")

    driver = None
    if ride.get("driver_id"):
        driver = await db_supabase.get_driver_by_id(ride["driver_id"])

    driver_profile = None
    if driver and driver.get("user_id"):
        driver_profile = await db_supabase.get_user_by_id(driver["user_id"])

    vehicle = None
    if ride.get("vehicle_type_id"):
        vehicle = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("vehicle_types", {"id": ride["vehicle_type_id"]}, limit=1)
        )

    corporate_account = None
    if ride.get("corporate_account_id"):
        corporate_account = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("corporate_accounts", {"id": ride["corporate_account_id"]}, limit=1)
        )

    receipt_data = {
        "ride_id": ride_id,
        "date": ride.get("ride_completed_at") or ride.get("cancelled_at") or ride.get("created_at"),
        "status": ride.get("status"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "stops": ride.get("stops", []),
        "distance_km": ride.get("distance_km"),
        "base_fare": ride.get("base_fare", 0),
        "distance_fare": ride.get("distance_fare", 0),
        "time_fare": ride.get("time_fare", 0),
        "airport_fee": ride.get("airport_fee", 0),
        "booking_fee": ride.get("booking_fee", 0),
        "cancellation_fee": (ride.get("cancellation_fee_admin", 0) + ride.get("cancellation_fee_driver", 0))
        if ride.get("status") == "cancelled"
        else 0,
        "tax_amount": ride.get("tax_amount", 0),
        "tip_amount": ride.get("tip_amount", 0),
        "total_charged": ride.get("total_fare", 0),
        "payment_method": "Corporate Account"
        if corporate_account
        else (ride.get("payment_method_id") or "Credit Card ending in ****"),
        "corporate_account_name": corporate_account.get("company_name") if corporate_account else None,
        "driver_name": f"{driver_profile.get('first_name', '')} {driver_profile.get('last_name', '')}".strip()
        if driver_profile
        else "Unknown Driver",
        "vehicle_type": vehicle.get("name") if vehicle else "Standard",
    }

    # Ideally send email here via SendGrid/Mailgun if POST

    return {"success": True, "receipt": receipt_data}
