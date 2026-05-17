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
    from ..models.ride_status import RideStatus
    from ..schemas import CreateRideRequest, DriverPublicView, Ride, RideRatingRequest
    from ..services import DispatchService
    from ..services.dispatch_service import (
        filter_and_rank_drivers,
    )
    from ..services.fare_service import calculate_fare, build_fare_breakdown_lines
    from ..settings_loader import get_app_settings
    from ..sms_service import send_sms
    from ..socket_manager import manager
    from ..utils.audit_logger import log_user_action
    from ..utils.error_handling import (
        ErrorCode,
        RideNotFoundException,
        SpinrException,
    )
    from ..utils.error_keys import ErrorKeys
    from ..utils.idempotency import idempotent_endpoint
    from ..utils.rate_limiter import (
        api_rate_limit,
        cancel_ride_limit,
        payment_action_limit,
        ride_action_limit,
        ride_message_limit,
        ride_rating_limit,
        ride_read_limit,
        ride_request_limit,
    )
    from ..validators import validate_ride_location
except ImportError:
    import db_supabase
    from dependencies import generate_pickup_otp, get_current_user
    from features import calculate_airport_fee, calculate_all_fees, send_push_notification
    from geo_utils import calculate_distance, get_service_area_polygon, point_in_polygon
    from models.ride_status import RideStatus  # noqa: F401
    from schemas import CreateRideRequest, DriverPublicView, Ride, RideRatingRequest
    from services.dispatch_service import (
        DispatchService,
        filter_and_rank_drivers,
    )
    from services.fare_service import calculate_fare, build_fare_breakdown_lines
    from settings_loader import get_app_settings
    from sms_service import send_sms
    from socket_manager import manager
    from utils.audit_logger import log_user_action
    from utils.error_handling import (
        ErrorCode,
        RideNotFoundException,
        SpinrException,
    )
    from utils.error_keys import ErrorKeys
    from utils.idempotency import idempotent_endpoint
    from utils.rate_limiter import (
        api_rate_limit,
        cancel_ride_limit,
        payment_action_limit,
        ride_action_limit,
        ride_message_limit,
        ride_rating_limit,
        ride_read_limit,
        ride_request_limit,
    )
    from validators import validate_ride_location


from .fares import _fares_for_location_impl, get_fares_for_location

try:
    from ..utils.datetime_utils import parse_iso_utc
    from ..utils.insurance_periods import record_period_transition
    from ..utils.ride_code import generate_ride_code
except ImportError:
    from utils.datetime_utils import parse_iso_utc
    from utils.insurance_periods import record_period_transition  # type: ignore[assignment]
    from utils.ride_code import generate_ride_code

try:
    from ..utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )
except ImportError:
    from utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )

try:
    from ..services.corporate_policy_service import evaluate_policy_for_ride  # type: ignore
except ImportError:
    from services.corporate_policy_service import evaluate_policy_for_ride  # type: ignore

try:
    from ..core.config import settings as _settings
except ImportError:
    from core.config import settings as _settings  # noqa: F401 — dual-import pattern

try:
    from ..services.cancellation_service import calculate_cancellation_fee, pay_driver_cancellation_fee
    from ..services.payment_service import send_ride_receipt, settle_card, settle_corporate, settle_wallet
except ImportError:
    from services.cancellation_service import calculate_cancellation_fee, pay_driver_cancellation_fee  # type: ignore
    from services.payment_service import send_ride_receipt, settle_card, settle_corporate, settle_wallet  # type: ignore

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
        raise SpinrException(
            message=f"Ride is in status '{current}'; cannot perform this action from that state (allowed: {list(allowed_states)}).",
            error_code=ErrorCode.RIDE_INVALID_STATUS,
            status_code=409,
            details={"current_status": current, "allowed": list(allowed_states)},
            message_key=ErrorKeys.RIDE_INVALID_STATUS,
        )
    raise RideNotFoundException(
        ride_id=ride_id,
        message_key=ErrorKeys.RIDE_NOT_FOUND,
    )


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
        "distance_fare": _money_str(new_distance_fare),
        "time_fare": _money_str(new_time_fare),
        "estimated_fare": _money_str(new_total),
        "total_fare": _money_str(new_total),
    }


def _f(v: Decimal) -> float:
    """Convert Decimal to float.

    Reserved for legacy callers that genuinely need a float — Pydantic field
    coercion (Ride model takes float for ``surge_multiplier``), signed-token
    payloads (``sign_estimate_token`` / ``verify_estimate_token``), and
    internal helpers like ``calculate_all_fees`` whose contract is float.

    For JSON wire responses use ``_money_str`` instead — Audit-17 P0-1
    mandates that money fields cross the wire as decimal strings, never
    IEEE-754 floats. See CLAUDE.md § Critical Conventions ("Money arithmetic").
    """
    return float(v)


def _money_str(v: Decimal) -> str:
    """Quantize ``v`` to 2 decimal places and emit as a JSON-safe string.

    Use on every money-shaped value placed into a dict response or
    WebSocket payload. Pydantic response models already route money through
    ``DecimalStr``; this helper covers the remaining hand-built dict
    responses where there is no schema between us and the wire.
    """
    return str(_round(_d(v)))


def _is_corporate_paid(
    *,
    payment_method: Optional[str],
    work_profile: Optional[bool],
    corporate_account_id: Optional[str],
) -> bool:
    """True when the ride will be settled against a corporate account.

    Surge does not apply to corporate-paid rides (CLAUDE.md Surge rules:
    "Surge does not apply to corporate account-paid rides"). The caller
    needs the answer before fare arithmetic so the multiplier can be
    pinned to 1.0× before distance/time fares are computed.

    Two booking shapes route to corporate billing:
      1. ``payment_method == "company_allowance"`` — the rider explicitly
         picked Company Allowance.
      2. ``work_profile=True`` with a ``corporate_account_id`` — the
         rider toggled Work mode; rides.py:907 reclassifies these to
         ``payment_method="company_allowance"`` at persist time.
    Both paths must bypass surge before the fare is locked in, otherwise
    the rider would see the surged estimate and the company would be
    billed for it.
    """
    if not corporate_account_id:
        return False
    if (payment_method or "").lower() == "company_allowance":
        return True
    if work_profile:
        return True
    return False


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
        logger.error(f"[DISPATCH] match_driver_to_ride: ride {ride_id} not found")
        return

    # Refuse to dispatch a ride with missing coordinates — the driver-app
    # cannot render the map polyline and would either drop the offer or
    # plot (0,0) (Gulf of Guinea). Surfacing loudly per CLAUDE.md ("Do not
    # silently swallow errors"); insurance Period 2 also requires a known
    # origin/destination.
    _coords = [ride.get("pickup_lat"), ride.get("pickup_lng"), ride.get("dropoff_lat"), ride.get("dropoff_lng")]
    if any(c is None for c in _coords):
        logger.error(
            f"[DISPATCH] ride {ride_id} has missing coordinates "
            f"pickup=({_coords[0]},{_coords[1]}) dropoff=({_coords[2]},{_coords[3]}) — aborting dispatch",
        )
        return

    # Single app_settings fetch — used both for matching config (via
    # DispatchService.resolve_matching_config) and for the offer-timeout
    # lookup at the end. Previously this loaded twice; the dead
    # ``get_rows("service_areas", {"id": ...})`` call that followed has
    # been removed — resolve_matching_config does its own find_one
    # against the same table.
    app_settings = await get_app_settings()
    # Compute offer timeout early so it can be embedded in dispatch payloads —
    # driver-app uses this for the per-offer countdown instead of a cached
    # value, which drifted when admin changed the setting mid-session.
    offer_timeout = int(app_settings.get("ride_offer_timeout_seconds", 15))

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
    _dispatch_filter: dict = {
        "is_online": True,
        "is_available": True,
        "vehicle_type_id": ride["vehicle_type_id"],
    }
    if ride.get("requires_wav"):
        _dispatch_filter["is_wav"] = True
    all_drivers = await db_supabase.get_rows(
        "drivers",
        _dispatch_filter,
        limit=500,
    )

    logger.info(
        f"[DISPATCH] candidate pool (pre-filter): {len(all_drivers)} drivers "
        f"matching vehicle_type_id + online + available"
    )

    # Skip drivers who recently timed out or declined this specific offer
    # so the same driver is not hammered with repeat notifications.
    try:
        from ..utils.redis_client import redis_get as _redis_get  # type: ignore
    except ImportError:
        from utils.redis_client import redis_get as _redis_get  # type: ignore
    _skip_ids: set = set()
    for _d in all_drivers:
        if await _redis_get(f"spinr:offer_skip:{ride_id}:{_d['id']}"):
            _skip_ids.add(_d["id"])
    if _skip_ids:
        all_drivers = [d for d in all_drivers if d["id"] not in _skip_ids]
        logger.info(f"[DISPATCH] skipped {len(_skip_ids)} driver(s) with recent timeout/decline for ride {ride_id}")

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
    _rpc_claimed = False

    if algorithm == "nearest":
        # Fast path: let the DB atomically find-and-claim the nearest driver
        # via the PostGIS `match_and_claim_driver` RPC (migration 77).
        # Falls back to the Python-sort path if the RPC returns None
        # (PostGIS unavailable, no driver found, or any error).
        rpc_result = await db_supabase.match_and_claim_driver(
            vehicle_type_id=ride["vehicle_type_id"],
            pickup_lat=ride["pickup_lat"],
            pickup_lng=ride["pickup_lng"],
            radius_km=search_radius,
            min_rating=min_rating,
        )
        if rpc_result:
            selected_driver = rpc_result
            _rpc_claimed = True
        else:
            drivers_with_distance.sort(key=lambda x: x[1])
            selected_driver = drivers_with_distance[0][0] if drivers_with_distance else None
    elif algorithm == "combined":
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
        # Attempt to atomically claim the driver (only if still available).
        # Skipped when _rpc_claimed is True — the PostGIS RPC already performed
        # the atomic claim with SELECT ... FOR UPDATE SKIP LOCKED.
        if not _rpc_claimed:
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
                "status": RideStatus.DRIVER_ASSIGNED,
                "driver_notified_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        )
        # M-5: SGI insurance period audit — driver_assigned starts period 2
        # (en route to pickup). Helper swallows its own exceptions so a
        # broken audit write cannot block dispatch.
        await record_period_transition(selected_driver["id"], 2, ride_id=ride_id)

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
            logger.error(f"[DISPATCH] could not load rider user {ride['rider_id']}: {e}", exc_info=True)

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
        from datetime import timedelta as _td

        _offer_expires_at = (datetime.now(timezone.utc) + _td(seconds=offer_timeout + 15)).isoformat()
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
            "requires_wav": bool(ride.get("requires_wav")),
            # Per-offer countdown sourced from current app_settings — driver-app
            # honors this over its cached config so admin changes take effect
            # immediately, not on next cold start.
            "countdown_seconds": offer_timeout,
            # Absolute expiry so the client can validate stale FCM offers
            # that arrive after the backend TTL has already fired.
            "offer_expires_at": _offer_expires_at,
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
                        # Coords are guaranteed non-null here — the null-coord
                        # guard at the top of match_driver_to_ride aborts
                        # dispatch before we reach this branch.
                        "pickup_lat": str(ride["pickup_lat"]),
                        "pickup_lng": str(ride["pickup_lng"]),
                        "dropoff_lat": str(ride["dropoff_lat"]),
                        "dropoff_lng": str(ride["dropoff_lng"]),
                        "fare": str(ride.get("driver_earnings") or 0),
                        "distance_km": str(ride.get("distance_km") or ""),
                        "duration_minutes": str(ride.get("duration_minutes") or ""),
                        "rider_name": rider_display_name or "",
                        "rider_rating": str((rider_user or {}).get("rating") or ""),
                        "countdown_seconds": str(offer_timeout),
                        "offer_expires_at": _offer_expires_at,
                        "deeplink": "/driver/",
                    },
                    priority="dispatch",
                )
                logger.info(f"[DISPATCH] push new_ride_assignment sent to user_id={selected_driver['user_id']}")
            except Exception as e:
                logger.error(
                    f"[DISPATCH] push notification failed for user_id={selected_driver['user_id']}: {e}", exc_info=True
                )
        else:
            logger.error(
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
    # it unassigns and re-dispatches. ``offer_timeout`` was computed
    # earlier so it could also be embedded in the dispatch payload.
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
        if ride.get("status") != RideStatus.DRIVER_ASSIGNED or ride.get("driver_id") != driver_id:
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
                    "status": RideStatus.SEARCHING,
                    "driver_id": None,
                    "driver_notified_at": None,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        # M-5: SGI insurance period audit — offer timeout releases the
        # driver from period 2 back to period 1 (online, no ride).
        await record_period_transition(driver_id, 1)

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

        # Notify the driver too — without this the panel just vanishes on
        # the next reconnect with no explanation, which looked like a bug
        # to drivers. Lookup is best-effort; a failure here must not block
        # the rider-side re-dispatch.
        try:
            driver_row = await db_supabase.get_driver_by_id(driver_id)
            driver_user_id = (driver_row or {}).get("user_id")
            if driver_user_id:
                await manager.send_personal_message(
                    {"type": "ride_offer_expired", "ride_id": ride_id},
                    f"driver_{driver_user_id}",
                )
        except Exception as e:
            logger.warning(f"[DISPATCH] could not notify driver of offer expiry for ride {ride_id}: {e}")

        # Record this driver's timeout so they are skipped on the next
        # dispatch cycle for this ride (5-minute cooldown).
        try:
            from ..utils.redis_client import redis_set as _redis_set  # type: ignore
        except ImportError:
            from utils.redis_client import redis_set as _redis_set  # type: ignore
        try:
            await _redis_set(f"spinr:offer_skip:{ride_id}:{driver_id}", "1", ttl=300)
        except Exception as _e:
            logger.warning(f"[DISPATCH] could not set offer cooldown key for ride {ride_id} driver {driver_id}: {_e}")

        # Attempt re-dispatch to the next available driver.
        await match_driver_to_ride(ride_id)

    except Exception as e:
        logger.error(f"[DISPATCH] Offer timeout handler error for ride {ride_id}: {e}", exc_info=True)


class RideEstimateRequest(BaseModel):
    pickup_lat: float = Field(..., ge=-90, le=90)
    pickup_lng: float = Field(..., ge=-180, le=180)
    dropoff_lat: float = Field(..., ge=-90, le=90)
    dropoff_lng: float = Field(..., ge=-180, le=180)
    stops: Optional[List[dict]] = None
    # Corporate-billing context — when present, surge is suppressed so the
    # quote shown to the rider matches what the company will be invoiced.
    # Optional for backwards compatibility with consumer-only callers.
    payment_method: Optional[str] = None
    corporate_account_id: Optional[str] = None
    work_profile: Optional[bool] = False
    requires_wav: bool = False


@api_router.post("/estimate")
@api_rate_limit
async def estimate_ride(
    body: RideEstimateRequest, request: Request = None, current_user: dict = Depends(get_current_user)
):
    validate_ride_location(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng)
    distance_km = calculate_distance(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng)
    duration_minutes = int(distance_km / 30 * 60) + 5

    fares = await get_fares_for_location(body.pickup_lat, body.pickup_lng)

    # Resolve service area once for fees/taxes — shared across all vehicle-type iterations
    # so calculate_all_fees doesn't re-fetch service_areas N times.
    _est_all_areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=100)
    _est_matched_area = next(
        (
            a
            for a in _est_all_areas
            if get_service_area_polygon(a)
            and point_in_polygon(body.pickup_lat, body.pickup_lng, get_service_area_polygon(a))
        ),
        None,
    )
    if _est_matched_area:
        logger.info("[estimate] matched service area '%s' for fees", _est_matched_area.get("name", _est_matched_area.get("id")))
    else:
        logger.info("[estimate] no service area matched pickup (%.5f, %.5f) — area fees will be empty", body.pickup_lat, body.pickup_lng)

    # Fetch all nearby online+available drivers once
    all_drivers = await db_supabase.get_rows(
        "drivers",
        {
            "is_online": True,
            "is_available": True,
        },
        limit=200,
    )

    logger.info(
        "[estimate] fetched %d online+available drivers from DB",
        len(all_drivers),
    )

    # Filter to drivers within 10km radius and group by vehicle_type_id.
    # Exclude drivers without a user_id — those are orphan/demo rows that
    # cannot be dispatched to, and counting them would inflate the rider's
    # "X drivers available" badge and cause rides to fail at dispatch time.
    from collections import defaultdict

    drivers_by_type = defaultdict(list)
    skipped_reasons: dict = defaultdict(int)
    for d in all_drivers:
        if not d.get("user_id"):
            skipped_reasons["no_user_id"] += 1
            continue
        d_lat = d.get("lat")
        d_lng = d.get("lng")
        if not d_lat or not d_lng:
            skipped_reasons["no_lat_lng"] += 1
            continue
        dist = calculate_distance(body.pickup_lat, body.pickup_lng, d_lat, d_lng)
        if dist > 10.0:
            skipped_reasons["outside_10km"] += 1
            continue
        vt_id = d.get("vehicle_type_id")
        if not vt_id:
            skipped_reasons["no_vehicle_type_id"] += 1
            continue
        drivers_by_type[vt_id].append(
            {
                "driver": d,
                "distance_km": dist,
            }
        )

    if skipped_reasons:
        logger.info("[estimate] skipped drivers: %s", dict(skipped_reasons))
    logger.info(
        "[estimate] matched drivers by vehicle_type: %s",
        {k: len(v) for k, v in drivers_by_type.items()},
    )

    # Check airport surcharge (pickup, dropoff, or any stop in airport sub-region)
    airport_result = await calculate_airport_fee(
        body.pickup_lat,
        body.pickup_lng,
        body.dropoff_lat,
        body.dropoff_lng,
        stops=body.stops,
    )
    airport_fee = airport_result.get("airport_fee", 0.0)

    # CLAUDE.md: surge does not apply to corporate-paid rides. Resolve the
    # bypass once per request — fares list is per-vehicle, not per-payment.
    corporate_bypass = _is_corporate_paid(
        payment_method=body.payment_method,
        work_profile=body.work_profile,
        corporate_account_id=body.corporate_account_id,
    )

    logger.info(
        "[estimate] fares=%d vehicle_types=%s",
        len(fares),
        [f.get("vehicle_type", {}).get("name", "?") for f in fares],
    )

    estimates = []
    for fare_info in fares:
        surge = Decimal("1.0") if corporate_bypass else _d(fare_info.get("surge_multiplier", 1.0))
        fb = calculate_fare(fare_info, distance_km, duration_minutes, surge=surge, airport_fee=airport_fee)

        # Calculate area fees + taxes so the rider sees them before booking.
        # Pass the pre-resolved area to avoid a redundant DB fetch per vehicle type.
        fees_result = {}
        try:
            fees_result = await calculate_all_fees(
                body.pickup_lat,
                body.pickup_lng,
                body.dropoff_lat,
                body.dropoff_lng,
                distance_km,
                _f(fb.total_fare),
                _all_areas=_est_all_areas,
                _matched_area=_est_matched_area,
            )
        except Exception as e:
            logger.error("[estimate] calculate_all_fees failed: %s", e, exc_info=True)

        area_fees_total = fees_result.get("fees_total", 0)
        tax_amount = fees_result.get("tax_amount", 0)
        grand_total = _f(_round(fb.total_fare + _d(area_fees_total) + _d(tax_amount)))

        # Check real driver availability for this vehicle type
        vt_id = fare_info["vehicle_type"].get("id")
        nearby_for_type = drivers_by_type.get(vt_id, [])
        driver_count = len(nearby_for_type)
        is_available = driver_count > 0
        wav_available = sum(1 for entry in nearby_for_type if entry["driver"].get("is_wav"))

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
            pickup_lat=body.pickup_lat,
            pickup_lng=body.pickup_lng,
            dropoff_lat=body.dropoff_lat,
            dropoff_lng=body.dropoff_lng,
            surge_multiplier=round(float(surge), 2),
            total_fare=_f(fb.total_fare),
        )

        fare_breakdown_lines = build_fare_breakdown_lines(
            fb,
            distance_km=distance_km,
            duration_minutes=duration_minutes,
            area_fees=fees_result.get("fees", []),
            tax_breakdown=fees_result.get("tax_breakdown", {}),
        )

        estimates.append(
            {
                "vehicle_type": fare_info["vehicle_type"],
                "distance_km": round(distance_km, 2),
                "duration_minutes": duration_minutes,
                "base_fare": _money_str(fb.base_fare),
                "distance_fare": _money_str(fb.distance_fare),
                "time_fare": _money_str(fb.time_fare),
                "booking_fee": _money_str(fb.booking_fee),
                "surge_multiplier": round(float(surge), 2),
                "total_fare": _money_str(fb.total_fare),
                "area_fees": fees_result.get("fees", []),
                "area_fees_total": area_fees_total,
                "tax_breakdown": fees_result.get("tax_breakdown", {}),
                "tax_amount": tax_amount,
                "grand_total": grand_total,
                "fare_breakdown": fare_breakdown_lines,
                "available": is_available,
                "eta_minutes": eta_minutes,
                "driver_count": driver_count,
                "wav_available": wav_available,
                "estimate_token": estimate_token,
            }
        )

    logger.info(
        "[estimate] returning %d estimates: %s",
        len(estimates),
        [(e["vehicle_type"].get("name", "?"), e["available"], e["driver_count"]) for e in estimates],
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
        if current_ride and current_ride.get("status") == RideStatus.SEARCHING:
            now = datetime.now(timezone.utc)
            base_update = {
                "status": RideStatus.CANCELLED,
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
                RideStatus.CANCELLED,
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
        logger.error(f"Ride timeout handler error for {r_id}: {e}", exc_info=True)


@api_router.post("")
@ride_request_limit
@idempotent_endpoint(scope="ride_create")
async def create_ride(body: CreateRideRequest, request: Request = None, current_user: dict = Depends(get_current_user)):
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

    active_statuses = list(RideStatus.active_statuses())
    existing_ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "rides",
            {"rider_id": current_user["id"], "status": {"$in": active_statuses}},
            limit=1,
        )
    )
    if existing_ride:
        raise SpinrException(
            message="You already have an active ride",
            error_code=ErrorCode.RIDE_INVALID_STATUS,
            status_code=409,
            message_key=ErrorKeys.RIDE_INVALID_STATUS,
        )

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
        logger.error(f"Failed to fetch service areas: {e}", exc_info=True)

    # Resolve the pickup service area once and pass the match downstream.
    matched_area = await db_supabase.get_service_area_for_point(body.pickup_lat, body.pickup_lng)
    if matched_area is None and all_areas:
        matched_area = next(
            (
                a
                for a in all_areas
                if get_service_area_polygon(a)
                and point_in_polygon(body.pickup_lat, body.pickup_lng, get_service_area_polygon(a))
            ),
            None,
        )
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

    # Corporate surge bypass — applied AFTER estimate_token resolution so the
    # corporate flag wins over any token-locked multiplier. Without this
    # ordering a rider could pin a surged token in personal mode, switch the
    # toggle to Work, and still bill the company at the surged rate.
    if _is_corporate_paid(
        payment_method=body.payment_method,
        work_profile=body.work_profile,
        corporate_account_id=body.corporate_account_id,
    ) and surge != Decimal("1.0"):
        logger.info(
            f"Corporate surge bypass for rider={current_user['id']} "
            f"corp={body.corporate_account_id}: forcing surge {float(surge)} → 1.0"
        )
        surge = Decimal("1.0")

    # Scheduled ride surge exclusion (policy): a rider who books a scheduled ride
    # locks in the fare at booking time, before the surge window opens. Applying
    # the current area surge at dispatch time would retroactively charge a higher
    # multiplier than the rider was shown — a hidden-fee violation. Reset to 1.0
    # unconditionally when is_scheduled is True or a scheduled_time is present.
    if (body.is_scheduled or body.scheduled_time) and surge > Decimal("1.0"):
        logger.info(f"Scheduled ride: surge reset from {float(surge)} to 1.0 for rider={current_user['id']}")
        surge = Decimal("1.0")

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

    fb = calculate_fare(fare_info, distance_km, duration_minutes, surge=surge, airport_fee=airport_fee)
    base_fare = fb.base_fare
    distance_fare = fb.distance_fare
    time_fare = fb.time_fare
    booking_fee = fb.booking_fee
    total_fare = fb.total_fare
    driver_earnings = fb.driver_earnings
    admin_earnings = fb.admin_earnings

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
        logger.error(f"Failed to calculate area fees: {e}", exc_info=True)

    area_fees_total = fees_result.get("fees_total", 0)
    tax_amount = fees_result.get("tax_amount", 0)
    grand_total = _f(_round(total_fare + _d(area_fees_total) + _d(tax_amount)))

    # Payment pre-validation: reject the ride before dispatching if the rider
    # clearly cannot pay. Wallet rides require sufficient balance upfront;
    # corporate rides have their own policy check below.
    if body.payment_method == "wallet":
        wallet = await db_supabase.find_one("wallets", {"user_id": current_user["id"]})
        wallet_balance = _d((wallet or {}).get("balance", 0))
        if wallet_balance < _d(grand_total):
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient wallet balance. Estimated fare is ${grand_total}, wallet has ${_f(wallet_balance)}. Please top up your wallet or switch to card payment.",
            )

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
            pickup_time=datetime.now(timezone.utc),
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
        surge_multiplier=round(float(surge), 2),
        total_fare=_f(total_fare),
        stops=body.stops,
        is_scheduled=body.is_scheduled,
        requires_wav=body.requires_wav,
        quiet_mode=body.quiet_mode,
        rider_notes=body.rider_notes,
        scheduled_time=body.scheduled_time,
        driver_earnings=_f(driver_earnings),
        admin_earnings=_f(admin_earnings),
        payment_method=body.payment_method,
        payment_method_id=body.payment_method_id,
        # NOTE: ``requires_wav`` was passed twice (once at line ~1090, again
        # here) — Python 3.11+ raises SyntaxError, blocking module import
        # and the entire test suite. Drop-in fix unblocks Audit-17 Phase 1c.
        status=RideStatus.SEARCHING,
        pickup_otp=pickup_otp_plain,
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
            _remaining = _d(str(_allowance.get("amount") or 0)) - max(_d(str(_allowance.get("used") or 0)), _d("0"))
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

    # ── Apply promo code if provided ──
    if body.promo_code:
        try:
            try:
                from ..routes.promotions import _record_promo_application, _validate_promo_for_user
            except ImportError:
                from routes.promotions import _record_promo_application, _validate_promo_for_user

            server_fare = _d(fresh_ride.get("total_fare", 0))
            validation = await _validate_promo_for_user(
                code=body.promo_code,
                user_id=current_user["id"],
                ride_fare=server_fare,
                ride_id=ride.id,
            )
            if validation.get("valid"):
                discount = _d(validation["discount_amount"])
                application_id = await _record_promo_application(
                    promo_id=validation["promo_id"],
                    code=validation["code"],
                    user_id=current_user["id"],
                    discount=discount,
                )
                discounted_total = _f(_round(server_fare - discount))
                discounted_grand = _f(_round(_d(fresh_ride.get("grand_total", server_fare)) - discount))
                await db_supabase.update_one(
                    "rides",
                    {"id": ride.id},
                    {
                        "subtotal_fare": _f(server_fare),
                        "discount_amount": _f(discount),
                        "promo_code": validation["code"],
                        "promo_application_id": application_id,
                        "total_fare": discounted_total,
                        "grand_total": discounted_grand,
                    },
                )
                fresh_ride["subtotal_fare"] = _f(server_fare)
                fresh_ride["discount_amount"] = _f(discount)
                fresh_ride["promo_code"] = validation["code"]
                fresh_ride["promo_application_id"] = application_id
                fresh_ride["total_fare"] = discounted_total
                fresh_ride["grand_total"] = discounted_grand
        except HTTPException:
            pass  # promo invalid/expired — ride still created without discount
        except Exception as e:
            logger.error(f"create_ride: promo application failed for code={body.promo_code}: {e}", exc_info=True)

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

    # Small helper to ensure we return a clean dict
    def serialize_doc(doc):
        return doc

    if updated_ride and updated_ride.get("status") == RideStatus.SEARCHING:
        asyncio.create_task(ride_search_timeout(ride.id))

    asyncio.create_task(
        log_user_action(
            current_user,
            "ride_created",
            "rides",
            ride.id,
            {"status": updated_ride.get("status") if updated_ride else RideStatus.SEARCHING},
        )
    )
    return serialize_doc(updated_ride)


from fastapi import Request  # noqa: E402


@ride_read_limit
@api_router.get("/active")
async def get_active_ride(request: Request = None, current_user: dict = Depends(get_current_user)):
    """Get rider's current active/pending ride (if any). Used on app launch to resume."""
    # First check for rides that need payment (completed but not paid)
    # Then check for active rides
    active_statuses = list(RideStatus.active_statuses())

    # Check for unpaid completed ride first (must pay before new ride).
    # ``waived_admin`` is the terminal value written by admin force-complete
    # (admin/rides.py admin_complete_ride) — no real charge happened, but
    # the rider must not be trapped on the payment screen.
    unpaid_ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "rides",
            {
                "rider_id": current_user["id"],
                "status": RideStatus.COMPLETED,
                "payment_status": {"$nin": ["paid", "waived_admin"]},
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


@ride_read_limit
@api_router.get("/history")
async def get_ride_history(
    request: Request = None,
    limit: int = Query(default=20, ge=1, le=100),
    before: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """Get rider's past rides for the activity tab with cursor-based pagination.

    Pass ``before=<ride_id>`` to fetch the page of rides older than that ride.
    Returns ``next_cursor`` (the id of the last ride in this page) to use as
    the next ``before`` value, or ``null`` when there are no more pages.
    """
    # Resolve cursor to a timestamp so we can push the predicate to the DB.
    # Fetching 500 rows and slicing in Python caused O(n) reads on busy accounts.
    cursor_ts = None
    if before:
        cursor_ride = await db_supabase.find_one("rides", {"id": before, "rider_id": current_user["id"]})
        if cursor_ride:
            cursor_ts = cursor_ride.get("created_at")

    filters: dict = {
        "rider_id": current_user["id"],
        "status": {"$in": list(RideStatus.terminal_statuses())},
    }
    if cursor_ts:
        filters["created_at"] = {"$lt": cursor_ts}

    # Fetch limit+1 so we know whether a next page exists without an extra
    # count query. We then post-filter for meaningful rides (driver_id set
    # on cancellations) and slice to limit.
    candidates = await db_supabase.get_rows(
        "rides",
        filters,
        order="created_at",
        desc=True,
        limit=limit + 10,  # small buffer for the post-filter
    )

    # Exclude cancelled rides where no driver was ever matched
    rides = [
        r
        for r in candidates
        if r.get("status") == RideStatus.COMPLETED or (r.get("status") == RideStatus.CANCELLED and r.get("driver_id"))
    ][:limit]

    next_cursor = rides[-1]["id"] if len(rides) == limit else None

    return {"rides": rides, "limit": limit, "next_cursor": next_cursor}


@ride_read_limit
@api_router.get("/scheduled")
async def get_scheduled_rides(request: Request = None, current_user: dict = Depends(get_current_user)):
    """Get all upcoming scheduled rides for the current rider."""
    rides = await db_supabase.get_rows(
        "rides",
        {
            "rider_id": current_user["id"],
            "is_scheduled": True,
            "status": {"$nin": [RideStatus.COMPLETED, RideStatus.CANCELLED]},
        },
        order="scheduled_time",
        desc=False,
        limit=50,
    )
    return rides


@ride_read_limit
@api_router.get("/{ride_id}")
async def get_ride(ride_id: str, request: Request = None, current_user: dict = Depends(get_current_user)):
    """Fetch details of a specific ride"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise RideNotFoundException(
            ride_id=ride_id,
            message_key=ErrorKeys.RIDE_NOT_FOUND,
        )

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
                photo_url=assigned_driver.get("photo_url"),
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
    cancellation_fee_amount = Decimal("3.0")
    if get_app_settings:
        try:
            settings = await get_app_settings()
            free_cancel_window = int(settings.get("free_cancel_window_seconds", 120))
            cancellation_fee_amount = Decimal(str(settings.get("cancellation_fee", "3.0")))
        except Exception:
            # Non-fatal: fall back to hardcoded defaults if settings fetch fails
            logger.error("Failed to fetch app settings for cancellation config", exc_info=True)

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

    # M-4: Expose offer expiry so the rider app can show an accurate
    # countdown progress bar while waiting for the driver to accept.
    # Only meaningful in driver_assigned state; cleared once accepted.
    if ride.get("status") == RideStatus.DRIVER_ASSIGNED:
        driver_notified_at = ride.get("driver_notified_at")
        offer_timeout_seconds = 15
        if get_app_settings:
            try:
                settings = await get_app_settings()
                offer_timeout_seconds = int(settings.get("ride_offer_timeout_seconds", 15))
            except Exception:
                # Non-fatal: fall back to hardcoded default if settings fetch fails
                logger.error("Failed to fetch app settings for offer timeout config", exc_info=True)
        ride["offer_timeout_seconds"] = offer_timeout_seconds
        if driver_notified_at:
            try:
                from datetime import datetime, timedelta, timezone

                if isinstance(driver_notified_at, str):
                    notified_dt = datetime.fromisoformat(driver_notified_at.replace("Z", "+00:00"))
                else:
                    notified_dt = driver_notified_at
                if notified_dt.tzinfo is None:
                    notified_dt = notified_dt.replace(tzinfo=timezone.utc)
                expires_dt = notified_dt + timedelta(seconds=offer_timeout_seconds + 15)
                ride["offer_expires_at"] = expires_dt.isoformat()
            except Exception:
                ride["offer_expires_at"] = None
        else:
            ride["offer_expires_at"] = None
    else:
        ride["offer_expires_at"] = None
        ride["offer_timeout_seconds"] = None

    # PIPEDA / threat-model RI-2: drivers only need pickup/dropoff addresses
    # while the trip is active. Retaining exact addresses post-completion
    # enables address-based stalking (attack tree RAT-1). Riders retain their
    # own address history (is_rider check).
    if is_driver and not is_rider:
        ride.pop("pickup_otp", None)
        if ride.get("status") in RideStatus.terminal_statuses():
            for _addr_key in (
                "pickup_address",
                "dropoff_address",
                "pickup_lat",
                "pickup_lng",
                "dropoff_lat",
                "dropoff_lng",
            ):
                ride.pop(_addr_key, None)

    # Build dynamic fare_breakdown for the rider UI — labels owned by backend.
    _fb_lines = []
    if ride.get("base_fare"):
        _fb_lines.append({"label": "Base fare", "amount": ride["base_fare"], "type": "fare"})
    if ride.get("distance_fare"):
        _dist_label = f"Distance ({ride.get('distance_km', '?')} km)"
        _fb_lines.append({"label": _dist_label, "amount": ride["distance_fare"], "type": "fare"})
    if ride.get("time_fare"):
        _time_label = f"Time ({ride.get('duration_minutes', '?')} min)"
        _fb_lines.append({"label": _time_label, "amount": ride["time_fare"], "type": "fare"})
    if ride.get("booking_fee"):
        _fb_lines.append({"label": "Booking fee", "amount": ride["booking_fee"], "type": "fee"})
    for _af in (ride.get("area_fees_breakdown") or []):
        _afv = _af.get("calculated_value", 0)
        if float(_afv) > 0:
            _fb_lines.append({"label": _af.get("name", "Fee"), "amount": _afv, "type": "fee"})
    for _tax_name, _tax_info in (ride.get("tax_breakdown") or {}).items():
        if _tax_info.get("amount", 0) > 0:
            _rate = _tax_info.get("rate", 0)
            _lbl = f"{_tax_name} ({_rate}%)" if _rate else _tax_name
            _fb_lines.append({"label": _lbl, "amount": _tax_info["amount"], "type": "tax"})
    if ride.get("tip_amount") and float(ride["tip_amount"]) > 0:
        _fb_lines.append({"label": "Tip", "amount": ride["tip_amount"], "type": "tip"})
    ride["fare_breakdown"] = _fb_lines

    def serialize_doc(doc):
        return doc

    return serialize_doc(ride)


@api_router.post("/{ride_id}/tip")
@payment_action_limit
@idempotent_endpoint(scope="ride_tip")
async def add_tip(
    ride_id: str,
    req: TipRequest,
    request: Request = None,
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

    if ride.get("status") != RideStatus.COMPLETED:
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
            logger.error(f"[TIP] Could not resolve driver user_id for ride {ride_id}: {exc}", exc_info=True)

    if driver_user_id:
        rider = await db_supabase.get_user_by_id(ride["rider_id"]) or {}
        rider_name = (rider.get("first_name") or "Your rider").strip() or "Your rider"
        payload = {
            "type": "tip_received",
            "ride_id": str(ride_id),
            "amount": _money_str(tip_amount),
            "new_total": _money_str(new_tip),
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
            logger.error(f"[TIP] Push notify driver {driver_user_id} failed: {exc}", exc_info=True)

    return {"success": True, "tip_amount": _money_str(new_tip)}


class ProcessPaymentRequest(BaseModel):
    tip_amount: Decimal = Field(default=Decimal("0"), ge=0, le=500)


@api_router.post("/{ride_id}/process-payment")
@payment_action_limit
async def process_payment(
    ride_id: str, req: ProcessPaymentRequest, request: Request = None, current_user: dict = Depends(get_current_user)
):
    """Process payment for completed ride. Charges rider's card for fare + tip."""
    tip_amount = req.tip_amount

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user.get("id"):
        raise HTTPException(status_code=403, detail="Not authorized")

    _ride_status = ride.get("status", "")
    if _ride_status != RideStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Ride is in status '{_ride_status}'; payment requires completed state.",
        )

    if ride.get("payment_status") in ("paid", "processing"):
        logger.info(f"[PAYMENT] Ride {ride_id} already {ride['payment_status']} — skipping duplicate charge")
        return {
            "success": True,
            "charged_amount": _money_str(_d(ride.get("total_fare", 0) or 0) + _d(ride.get("tip_amount", 0) or 0)),
            "already_paid": True,
        }

    guard_row = await db_supabase.update_one(
        "rides",
        {"id": ride_id, "payment_status": "pending"},
        {"payment_status": "processing", "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    if guard_row is None:
        return {
            "success": True,
            "already_paid": True,
            "charged_amount": _money_str(_d(ride.get("total_fare", 0) or 0)),
        }

    if tip_amount < 0:
        raise HTTPException(status_code=400, detail="Tip amount cannot be negative")
    if tip_amount > 500:
        raise HTTPException(status_code=400, detail="Tip amount exceeds maximum ($500)")

    def _q(v) -> Decimal:
        return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    total_charge = _q(ride.get("total_fare", 0) or 0) + _q(tip_amount)
    payment_method = (ride.get("payment_method") or "card").lower()

    if payment_method == "wallet":
        result = await settle_wallet(ride, ride_id, current_user["id"], total_charge, tip_amount)
    elif payment_method == "company_allowance":
        result = await settle_corporate(ride, ride_id, total_charge, tip_amount)
    else:
        result = await settle_card(ride, ride_id, current_user["id"], total_charge, tip_amount)

    if not result.success:
        detail = result.error or "Payment failed"
        if result.error_code:
            detail = {"code": result.error_code, "message": result.error}
            if result.decline_code:
                detail["decline_code"] = result.decline_code
            if result.extra:
                detail.update(result.extra)
        raise HTTPException(status_code=result.status_code, detail=detail)

    email_sent = await send_ride_receipt(ride, current_user["id"], tip_amount)
    return {"success": True, "charged_amount": result.charged_amount, "email_sent": email_sent}


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

    if ride.get("status") in RideStatus.terminal_statuses():
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
@api_rate_limit
async def share_trip_with_contact(
    ride_id: str,
    body: ShareTripWithContactRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Share trip with a specific contact and send them a notification."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") in RideStatus.terminal_statuses():
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
        try:
            await send_push_notification(
                contact_user["id"],
                f"{rider_name} is sharing their ride with you",
                f"Track their live location: {ride.get('pickup_address', '')} → {ride.get('dropoff_address', '')}",
                data={"type": "trip_shared", "share_token": share_token, "ride_id": ride_id},
            )
        except Exception as _push_exc:
            logger.error(f"[SHARE] push to contact {contact_user['id']} failed: {_push_exc}", exc_info=True)

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
            logger.error(f"Malformed shared_trip_token_created_at for ride {ride.get('id')}")

    if ride.get("status") in RideStatus.terminal_statuses():
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
@ride_rating_limit
async def rate_driver(
    ride_id: str,
    rating_data: RideRatingRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider rates the driver"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride or ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=404, detail="Ride not found or unauthorized")

    if ride.get("status") != RideStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Ride must be completed before rating")

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
        # Decimal-safe accumulation: float addition drifts when summing existing
        # tip + this tip (e.g. 0.1 + 0.2 == 0.30000000000000004), and would
        # corrupt driver_earnings on rides that receive multiple tips.
        # Legacy rides may store NULL for tip_amount/driver_earnings rather
        # than 0. ``ride.get(k, 0)`` returns the literal None in that case
        # (the key exists, the value is None) so coerce explicitly.
        tip_delta = _d(rating_data.tip_amount)
        new_tip = _round(_d(ride.get("tip_amount") or 0) + tip_delta)
        new_driver_earnings = _round(_d(ride.get("driver_earnings") or 0) + tip_delta)
        await db_supabase.update_ride(
            ride_id,
            {"tip_amount": _f(new_tip), "driver_earnings": _f(new_driver_earnings)},
        )

    # Aggregate driver rating using rolling average to avoid O(n) ride fetch.
    driver = await db_supabase.get_driver_by_id(driver_id)
    if driver:
        old_count = int(driver.get("total_ratings") or 0)
        old_avg = Decimal(str(driver.get("rating") or 0))
        new_count = old_count + 1
        new_avg = ((old_avg * old_count + Decimal(str(rating_data.rating))) / new_count).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        await db_supabase.update_one(
            "drivers",
            {"id": driver_id},
            {
                "rating": float(new_avg),
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

    asyncio.create_task(
        log_user_action(
            current_user,
            "driver_rated",
            "rides",
            ride_id,
            {"rating": str(rating_data.rating), "driver_id": driver_id},
        )
    )
    return {"success": True}


@api_router.post("/{ride_id}/cancel")
@cancel_ride_limit
async def cancel_ride_rider(ride_id: str, request: Request = None, current_user: dict = Depends(get_current_user)):
    """Rider cancels the ride"""
    try:
        from ..logging_utils import diag_logger  # type: ignore
    except ImportError:
        from logging_utils import diag_logger  # type: ignore

    diag_logger.info(f"[CANCEL] called ride_id={ride_id} user_id={current_user.get('id')}")

    ride = await _require_ride_in_state_rider(
        ride_id,
        current_user["id"],
        (
            "requested",
            RideStatus.SEARCHING,
            RideStatus.DRIVER_ASSIGNED,
            RideStatus.DRIVER_ACCEPTED,
            "en_route",
            RideStatus.DRIVER_ARRIVED,
        ),
    )
    diag_logger.info(
        f"[CANCEL] entry ride_id={ride_id} pre_status={ride.get('status')} driver_id={ride.get('driver_id')}"
    )

    driver_id = ride.get("driver_id")
    settings = await get_app_settings()
    charged_admin, charged_driver = calculate_cancellation_fee(ride, settings)

    total_cancel_fee = _round(charged_admin + charged_driver)

    # Charge the rider the cancellation fee before paying the driver.
    if total_cancel_fee > 0:
        payment_method = (ride.get("payment_method") or "card").lower()
        if payment_method == "wallet":
            rider_wallet = await db_supabase.find_one("wallets", {"user_id": current_user["id"]})
            if rider_wallet:
                old_balance = _round(_d(rider_wallet.get("balance", 0)))
                new_balance = max(_round(old_balance - total_cancel_fee), Decimal("0"))
                actual_charge = _round(old_balance - new_balance)
                if actual_charge > 0:
                    await db_supabase.update_one(
                        "wallets",
                        {"id": rider_wallet["id"]},
                        {"balance": _f(new_balance), "updated_at": datetime.now(timezone.utc).isoformat()},
                    )
                    await db_supabase.insert_one(
                        "wallet_transactions",
                        {
                            "id": str(uuid.uuid4()),
                            "wallet_id": rider_wallet["id"],
                            "user_id": current_user["id"],
                            "type": "cancellation_fee",
                            "amount": -_f(actual_charge),
                            "balance_after": _f(new_balance),
                            "reference_id": ride_id,
                            "description": f"Cancellation fee for ride {ride_id[:8]}",
                            "metadata": {"ride_id": ride_id},
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )

    if driver_id and charged_driver > 0:
        await pay_driver_cancellation_fee(
            ride_id=ride_id,
            driver_id=driver_id,
            fee=charged_driver,
            actor_user_id=current_user["id"],
            ride_status_at_cancel=ride.get("status"),
        )

    _now = datetime.now(timezone.utc)
    _base_update = {
        "status": RideStatus.CANCELLED,
        "cancelled_at": _now,
        "cancellation_fee_admin": _f(charged_admin),
        "cancellation_fee_driver": _f(charged_driver),
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

    if not verify_ride or verify_ride.get("status") != RideStatus.CANCELLED:
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
        # M-5: SGI insurance period audit — rider-side cancel after the
        # driver was assigned releases the driver back to period 1. If
        # the ride had no driver_id we never left period 1, so no row.
        await record_period_transition(driver_id, 1)

        # Notify driver
        driver = await db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("user_id"):
            await manager.send_personal_message(
                {"type": "ride_cancelled", "ride_id": ride_id, "reason": "Rider cancelled"},
                f"driver_{driver['user_id']}",
            )

    # Notify the rider's own connection — broadcast_ride_status only fans
    # out to the rider when rider_id is passed, but an explicit message
    # ensures clearRide() fires immediately in useRiderSocket without
    # waiting for the next poll cycle.
    await manager.send_personal_message(
        {"type": "ride_cancelled", "ride_id": ride_id, "reason": "rider_cancelled"},
        f"rider_{current_user['id']}",
    )
    await manager.broadcast_ride_status(
        ride_id,
        RideStatus.CANCELLED,
        rider_id=current_user["id"],
        reason="rider_cancelled",
    )
    try:
        await manager.broadcast_to_admins({"type": "ride_cancelled", "ride_id": ride_id, "reason": "rider_cancelled"})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"rider cancel admin broadcast failed: {_exc}")

    asyncio.create_task(
        log_user_action(
            current_user,
            "ride_cancelled",
            "rides",
            ride_id,
            {"reason": "rider_cancelled", "cancellation_fee": str(charged_admin + charged_driver)},
        )
    )
    return {"success": True, "cancellation_fee": charged_admin + charged_driver}


# ── Mid-Trip Stop Editing ─────────────────────────────────────────────


class AddStopMidTripRequest(BaseModel):
    address: str
    lat: float
    lng: float
    position: Optional[int] = None  # Insert at this index; None = append


@api_router.post("/{ride_id}/stops")
@ride_action_limit
async def add_stop_mid_trip(
    ride_id: str, req: AddStopMidTripRequest, request: Request = None, current_user: dict = Depends(get_current_user)
):
    """Add a stop to an active ride mid-trip."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") not in (RideStatus.DRIVER_ACCEPTED, RideStatus.DRIVER_ARRIVED, RideStatus.IN_PROGRESS):
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
@ride_action_limit
async def remove_stop_mid_trip(
    ride_id: str, stop_index: int, request: Request = None, current_user: dict = Depends(get_current_user)
):
    """Remove a stop from an active ride by index."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") not in (RideStatus.DRIVER_ACCEPTED, RideStatus.DRIVER_ARRIVED, RideStatus.IN_PROGRESS):
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


class RideNotesUpdateRequest(BaseModel):
    notes: str = Field(default="", max_length=200)


@api_router.patch("/{ride_id}/notes")
@ride_action_limit
async def patch_ride_notes(
    ride_id: str,
    body: RideNotesUpdateRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider attaches/updates a free-text note for the driver.

    UX intent: the booking screen no longer collects this field — riders
    add the note from the ride-status screen *after* confirming, matching
    the Uber/Lyft pattern. Allowed while the driver is still en route;
    once the trip starts the note is locked (the driver is already
    on the way; tail-end edits are confusing).
    """
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    allowed = (
        RideStatus.SEARCHING,
        RideStatus.DRIVER_ASSIGNED,
        RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED,
    )
    if ride.get("status") not in allowed:
        raise HTTPException(
            status_code=409,
            detail="Notes can only be edited before pickup",
        )

    notes = (body.notes or "").strip() or None
    await db.update_one(
        "rides",
        {"id": ride_id},
        {"$set": {"rider_notes": notes, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )

    # Push to the assigned driver if one exists. Best-effort — a failed
    # WS send must not error out the API (the driver-app will reconcile
    # via its next ride fetch).
    if ride.get("driver_id"):
        try:
            driver = await db.find_one("drivers", {"id": ride["driver_id"]})
            if driver and driver.get("user_id"):
                await manager.send_personal_message(
                    {"type": "ride_notes_updated", "ride_id": ride_id, "notes": notes},
                    f"driver_{driver['user_id']}",
                )
        except Exception as e:
            logger.warning(f"[notes] WS push to driver failed for ride {ride_id}: {e}")

    return {"success": True, "notes": notes}


class EmergencyRequest(BaseModel):
    message: str = "Emergency assistance requested"
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@api_router.post("/{ride_id}/emergency")
@ride_action_limit
async def trigger_emergency(
    ride_id: str, body: EmergencyRequest, request: Request = None, current_user: dict = Depends(get_current_user)
):
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
        "message": body.message,
        "status": "open",
        "latitude": body.latitude,
        "longitude": body.longitude,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    await db_supabase.insert_one("emergencies", incident)

    # Notify admin dashboard via Websocket — broadcast across every
    # replica's admin connections. The previous ``admin_notifications``
    # client_id pointed at no real socket, so alerts silently disappeared.
    try:
        await manager.broadcast_to_admins({"type": "emergency_alert", "incident": incident})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.error(f"emergency_alert admin broadcast failed: {_exc}", exc_info=True)
    logger.critical(f"EMERGENCY ALERT TRIGGERED for ride {ride_id} by user {current_user['id']}")

    # Notify emergency contacts via SMS (Twilio when configured, console log in dev)
    contacts_notified = 0
    try:
        sms_settings = await get_app_settings()
        contacts_rows = await db_supabase.get_rows("emergency_contacts", {"user_id": current_user["id"]}, limit=5)
        contacts = list(contacts_rows) if contacts_rows else []

        user = await db_supabase.get_user_by_id(current_user["id"])
        user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "A Spinr user"

        location_text = " Location shared with emergency services." if body.latitude and body.longitude else ""
        sms_body = (
            f"URGENT: {user_name} triggered an emergency alert during a Spinr ride."
            f"{location_text} Call them or emergency services immediately."
        )

        for contact in contacts:
            phone = contact.get("phone", "")
            if not phone:
                continue
            result = await send_sms(
                phone,
                sms_body,
                twilio_sid=sms_settings.get("twilio_account_sid", "") if sms_settings else "",
                twilio_token=sms_settings.get("twilio_auth_token", "") if sms_settings else "",
                twilio_from=sms_settings.get("twilio_from_number", "") if sms_settings else "",
            )
            if result.get("success"):
                contacts_notified += 1
            else:
                logger.error(f"SOS SMS failed for contact {contact.get('id')}: {result.get('error')}")

        if contacts:
            logger.info(
                f"SOS: notified {contacts_notified}/{len(contacts)} emergency contacts for user {current_user['id']}"
            )
    except Exception as e:
        logger.error(f"SOS emergency contact notification failed: {e}", exc_info=True)
        return {
            "success": True,
            "incident_id": incident["id"],
            "contacts_notified": 0,
            "notification_warning": "Emergency contacts could not be reached — please call them directly.",
        }

    return {
        "success": True,
        "incident_id": incident["id"],
        "contacts_notified": contacts_notified,
    }


@api_router.get("/{ride_id}/chat-status")
async def get_chat_status(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Check if chat is available for this ride (active rides + 24h post-trip window)."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    status = ride.get("status", "")
    if status == RideStatus.CANCELLED:
        return {"available": False, "reason": "Ride was cancelled"}

    if status == RideStatus.COMPLETED:
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

    if ride.get("status") in RideStatus.terminal_statuses():
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
    text: str = Field(..., min_length=1, max_length=500)


@api_router.post("/{ride_id}/messages")
@ride_message_limit
async def send_ride_message(
    ride_id: str, body: SendMessageRequest, request: Request = None, current_user: dict = Depends(get_current_user)
):
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
    if ride.get("status") == RideStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Cannot send messages on a cancelled ride")

    # Post-trip chat window: allow messages for 24h after completion
    if ride.get("status") == RideStatus.COMPLETED:
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


@api_router.delete("/scheduled/{ride_id}")
@cancel_ride_limit
async def cancel_scheduled_ride(ride_id: str, request: Request = None, current_user: dict = Depends(get_current_user)):
    """Cancel a scheduled ride."""
    ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "rides", {"id": ride_id, "rider_id": current_user["id"], "is_scheduled": True}, limit=1
        )
    )
    if not ride:
        raise RideNotFoundException(
            ride_id=ride_id,
            message_key=ErrorKeys.RIDE_NOT_FOUND,
        )
    if ride.get("status") in RideStatus.terminal_statuses():
        raise SpinrException(
            message="Ride is already completed or cancelled",
            error_code=ErrorCode.RIDE_ALREADY_CANCELLED,
            status_code=400,
            message_key=ErrorKeys.RIDE_ALREADY_CANCELLED,
        )

    _now = datetime.now(timezone.utc)
    _base = {
        "status": RideStatus.CANCELLED,
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
@api_rate_limit
async def simulate_driver_arrival(
    ride_id: str, request: Request = None, current_user: dict = Depends(get_current_user)
):
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
            "status": RideStatus.DRIVER_ARRIVED,
            "driver_arrived_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    updated_ride = await db_supabase.get_ride(ride_id)
    return {"success": True, "pickup_otp": updated_ride.get("pickup_otp", "0000")}


@api_router.post("/{ride_id}/start")
@ride_action_limit
async def rider_start_ride(ride_id: str, request: Request = None, current_user: dict = Depends(get_current_user)):
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
    if ride.get("status") != RideStatus.DRIVER_ARRIVED:
        raise HTTPException(status_code=400, detail=f"Cannot start ride with status: {ride.get('status')}")

    await db_supabase.update_ride(
        ride_id,
        {
            "status": RideStatus.IN_PROGRESS,
            "ride_started_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    return {"success": True}


@api_router.post("/{ride_id}/complete")
@ride_action_limit
async def rider_complete_ride(ride_id: str, request: Request = None, current_user: dict = Depends(get_current_user)):
    """Rider-initiated ride completion (early end-ride).

    The rider pays the full agreed fare. We mark the ride completed and
    free the driver, mirroring the essential parts of
    drivers.py::complete_ride but skipping GPS aggregation (that data
    is still captured and available for admin review).
    """
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") != RideStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Ride is not in progress")

    now = datetime.now(timezone.utc)
    update_fields = {
        "status": RideStatus.COMPLETED,
        "ride_completed_at": now,
        "updated_at": now,
    }
    await db_supabase.update_one("rides", {"id": ride_id}, update_fields)

    driver_id = ride.get("driver_id")
    driver_user_id = None
    if driver_id:
        await db_supabase.set_driver_available(driver_id, available=True, total_rides_inc=1)
        try:
            await record_period_transition(driver_id, 1)
        except Exception:
            logger.error(f"rider_complete_ride: period transition failed for driver {driver_id}", exc_info=True)
        driver_row = await db_supabase.get_driver_by_id(driver_id)
        driver_user_id = driver_row.get("user_id") if driver_row else None

    completed_ride = await db_supabase.get_ride(ride_id)
    total_fare = (completed_ride or {}).get("total_fare", ride.get("total_fare", 0))

    if driver_user_id:
        await manager.send_personal_message(
            {"type": "ride_completed", "ride_id": ride_id, "total_fare": total_fare},
            f"driver_{driver_user_id}",
        )
    await manager.send_personal_message(
        {"type": "ride_completed", "ride_id": ride_id, "total_fare": total_fare},
        f"rider_{current_user['id']}",
    )
    await manager.broadcast_ride_status(
        ride_id,
        RideStatus.COMPLETED,
        rider_id=current_user["id"],
        driver_user_id=driver_user_id,
        total_fare=total_fare,
    )
    try:
        await manager.broadcast_to_admins(
            {"type": "ride_completed", "ride_id": ride_id, "total_fare": total_fare, "completed_by": "rider"}
        )
    except Exception as _bcast_err:
        logger.warning("admin broadcast failed for ride_completed %s: %s", ride_id, _bcast_err)

    return completed_ride or ride


@api_router.get("/{ride_id}/receipt")
async def get_ride_receipt(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Get a detailed receipt for a completed ride"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view this receipt")

    if ride.get("status") not in RideStatus.terminal_statuses():
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
        "ride_code": ride.get("ride_code"),
        "date": ride.get("ride_completed_at") or ride.get("cancelled_at") or ride.get("created_at"),
        "status": ride.get("status"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "stops": ride.get("stops", []),
        "distance_km": ride.get("distance_km"),
        "duration_minutes": ride.get("duration_minutes"),
        "base_fare": ride.get("base_fare", 0),
        "distance_fare": ride.get("distance_fare", 0),
        "time_fare": ride.get("time_fare", 0),
        "airport_fee": ride.get("airport_fee", 0),
        "booking_fee": ride.get("booking_fee", 0),
        "cancellation_fee": (ride.get("cancellation_fee_admin", 0) + ride.get("cancellation_fee_driver", 0))
        if ride.get("status") == RideStatus.CANCELLED
        else 0,
        # Itemised charges so the rider receipt and the support audit can
        # reconcile to the cent. tax_breakdown / area_fees_breakdown were
        # added in migration 46; they may be missing on legacy rides, in
        # which case clients should fall back to the scalar totals.
        "surge_multiplier": ride.get("surge_multiplier", 1.0),
        "area_fees_total": ride.get("area_fees_total", 0),
        "area_fees_breakdown": ride.get("area_fees_breakdown", []),
        "tax_amount": ride.get("tax_amount", 0),
        "tax_breakdown": ride.get("tax_breakdown", {}),
        "tip_amount": ride.get("tip_amount", 0),
        "total_charged": ride.get("total_fare", 0),
        "grand_total": ride.get("grand_total")
        or (
            (ride.get("total_fare", 0) or 0)
            + (ride.get("area_fees_total", 0) or 0)
            + (ride.get("tax_amount", 0) or 0)
            + (ride.get("tip_amount", 0) or 0)
        ),
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


# ---------------------------------------------------------------------------
# Safety check-in response (Feature D — P3)
# ---------------------------------------------------------------------------


@api_router.post("/{ride_id}/safety-checkin")
@ride_action_limit
async def safety_checkin_response(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider taps 'I'm okay' on the safety check-in push notification.

    Records the response in Redis so the safety_checkin_loop does not escalate
    this ride to the trust-and-safety team.
    """
    try:
        from ..utils.redis_client import redis_set
    except ImportError:
        from utils.redis_client import redis_set  # type: ignore

    user_id = current_user.get("id")

    # Verify the ride belongs to this rider and is still in_progress.
    ride = await db_supabase.get_rows("rides", {"id": ride_id, "rider_id": user_id}, limit=1)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride[0].get("status") != RideStatus.IN_PROGRESS:
        raise HTTPException(status_code=409, detail="Ride is not in progress")

    # 4-hour TTL mirrors the sent/escalated keys in safety_checkin_loop.
    await redis_set(f"safety:checkin:ok:{ride_id}", "1", ttl=4 * 3600)

    logger.info(f"[SAFETY_CHECKIN] Rider {user_id} confirmed OK for ride {ride_id}")
    return {"success": True}
