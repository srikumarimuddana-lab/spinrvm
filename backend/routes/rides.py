from fastapi import APIRouter, Depends, HTTPException
from decimal import Decimal, ROUND_HALF_UP
try:
    from ..dependencies import get_current_user, generate_otp
    from ..schemas import CreateRideRequest, Ride, UserProfile, RideRatingRequest
    from ..db import db
    from ..utils import calculate_distance
    from ..socket_manager import manager
    from ..settings_loader import get_app_settings
    from ..features import send_push_notification
except ImportError:
    from dependencies import get_current_user, generate_otp
    from schemas import CreateRideRequest, Ride, UserProfile, RideRatingRequest
    from db import db
    from geo_utils import calculate_distance
    from socket_manager import manager
    from settings_loader import get_app_settings
    from features import send_push_notification
from .fares import get_fares_for_location
import asyncio
from loguru import logger
from typing import List, Tuple, Optional
from datetime import datetime, timedelta
import uuid
import secrets
from pydantic import BaseModel

# ── Decimal helpers for accurate currency arithmetic ──────────────────────────
_TWO_PLACES = Decimal('0.01')

def _d(v) -> Decimal:
    """Convert any numeric value to Decimal safely (avoids float precision loss)."""
    return Decimal(str(v))

def _round(v: Decimal) -> Decimal:
    return v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)

def _f(v: Decimal) -> float:
    """Convert Decimal back to float for Pydantic / JSON serialisation."""
    return float(v)
api_router = APIRouter(prefix="/rides", tags=["Rides"])

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

async def match_driver_to_ride(ride_id: str):
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        logger.warning(f"[DISPATCH] match_driver_to_ride: ride {ride_id} not found")
        return

    settings = await get_app_settings()
    algorithm = settings.get('driver_matching_algorithm', 'nearest')
    min_rating = settings.get('min_driver_rating', 4.0)
    search_radius = settings.get('search_radius_km', 10.0)

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
    all_drivers = await db.drivers.find({
        'is_online': True,
        'is_available': True,
        'vehicle_type_id': ride['vehicle_type_id'],
    }).to_list(500)

    logger.info(
        f"[DISPATCH] candidate pool (pre-filter): {len(all_drivers)} drivers "
        f"matching vehicle_type_id + online + available"
    )

    drivers_with_distance = []
    for d in all_drivers:
        # Skip orphan/demo drivers that cannot be notified.
        if not d.get('user_id'):
            continue
        d_lat = d.get('lat')
        d_lng = d.get('lng')
        if d_lat is None or d_lng is None:
            continue
        # Rating floor for rating-based / combined algorithms.
        if algorithm in ('rating_based', 'combined'):
            if float(d.get('rating') or 5.0) < min_rating:
                continue
        dist_km = calculate_distance(ride['pickup_lat'], ride['pickup_lng'], d_lat, d_lng)
        if dist_km <= search_radius:
            drivers_with_distance.append((d, dist_km))

    logger.info(
        f"[DISPATCH] candidate pool (post-filter): {len(drivers_with_distance)} "
        f"real drivers within {search_radius}km with valid lat/lng and "
        f"rating>={min_rating if algorithm in ('rating_based','combined') else 'n/a'}"
    )

    if not drivers_with_distance:
        logger.info(f"[DISPATCH] no eligible drivers for ride {ride_id} — ride stays in searching")
        return
    
    selected_driver = None
    
    if algorithm == 'nearest' or algorithm == 'combined':
        drivers_with_distance.sort(key=lambda x: x[1])
        selected_driver = drivers_with_distance[0][0]
    elif algorithm == 'rating_based':
        drivers_with_distance.sort(key=lambda x: x[0].get('rating', 5.0), reverse=True)
        selected_driver = drivers_with_distance[0][0]
    elif algorithm == 'round_robin':
        last_ride = await db.rides.find_one(
            {'driver_id': {'$ne': None}},
            sort=[('created_at', -1)]
        )
        if last_ride:
            last_driver_idx = next(
                (i for i, (d, _) in enumerate(drivers_with_distance) if d['id'] == last_ride['driver_id']),
                -1
            )
            next_idx = (last_driver_idx + 1) % len(drivers_with_distance)
            selected_driver = drivers_with_distance[next_idx][0]
        else:
            selected_driver = drivers_with_distance[0][0]
    
    if selected_driver:
        # Attempt to atomically claim the driver (only if still available)
        claim_result = await db.drivers.update_one(
            {'id': selected_driver['id'], 'is_available': True},
            {'$set': {'is_available': False}}
        )

        if claim_result.modified_count == 0:
            # Driver was taken by another process; try to find next candidate
            claimed = False
            for d, _ in drivers_with_distance:
                res = await db.drivers.update_one({'id': d['id'], 'is_available': True}, {'$set': {'is_available': False}})
                if res.modified_count > 0:
                    selected_driver = d
                    claimed = True
                    break
            if not claimed:
                # No drivers could be claimed
                return

        # Update ride with selected driver. Do NOT pre-populate
        # driver_accepted_at here — that field is set by the
        # /drivers/rides/{id}/accept endpoint when the driver actually taps
        # Accept. Setting it at dispatch time was a "demo auto-accept" hack
        # that made the rider-app show the driver card before the driver
        # had actually agreed to the ride.
        await db.rides.update_one(
            {'id': ride_id},
            {'$set': {
                'driver_id': selected_driver['id'],
                'status': 'driver_assigned',
                'driver_notified_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            }}
        )

        logger.info(
            f"[DISPATCH] ride {ride_id} assigned to driver_id={selected_driver['id']} "
            f"user_id={selected_driver.get('user_id')} name={selected_driver.get('name')}"
        )

        # Notify rider via WebSocket
        await manager.send_personal_message(
            {
                'type': 'driver_assigned',
                'ride_id': ride_id,
                'driver_id': selected_driver['id']
            },
            f"rider_{ride['rider_id']}"
        )

        # Look up the rider so we can include name/rating in the dispatch
        # payload sent to the driver-app. Missing fields are fine — the
        # driver-app has fallbacks — but sending them avoids an empty popup.
        rider_user = None
        try:
            rider_user = await db.users.find_one({'id': ride['rider_id']})
        except Exception as e:
            logger.warning(f"[DISPATCH] could not load rider user {ride['rider_id']}: {e}")

        rider_display_name = None
        if rider_user:
            first = rider_user.get('first_name') or ''
            last = rider_user.get('last_name') or ''
            rider_display_name = (first + ' ' + last).strip() or rider_user.get('name') or None

        # Build the full dispatch payload. Keys MUST match what the driver
        # app consumes in useDriverDashboard.ts handleWSMessage:
        # ride_id, pickup_address, dropoff_address, pickup_lat, pickup_lng,
        # dropoff_lat, dropoff_lng, fare, distance_km, duration_minutes,
        # rider_name, rider_rating.
        dispatch_payload = {
            'type': 'new_ride_assignment',
            'ride_id': ride_id,
            'pickup_address': ride.get('pickup_address'),
            'dropoff_address': ride.get('dropoff_address'),
            'pickup_lat': ride.get('pickup_lat'),
            'pickup_lng': ride.get('pickup_lng'),
            'dropoff_lat': ride.get('dropoff_lat'),
            'dropoff_lng': ride.get('dropoff_lng'),
            'fare': ride.get('driver_earnings'),
            'distance_km': ride.get('distance_km'),
            'duration_minutes': ride.get('duration_minutes'),
            'rider_name': rider_display_name,
            'rider_rating': (rider_user or {}).get('rating'),
        }

        # Notify driver via WebSocket (only reaches the driver if they have
        # an open WS connection — silent no-op otherwise).
        if selected_driver.get('user_id'):
            logger.info(
                f"[DISPATCH] sending WS new_ride_assignment to "
                f"driver_{selected_driver['user_id']} payload_keys="
                f"{list(dispatch_payload.keys())}"
            )
            await manager.send_personal_message(
                dispatch_payload,
                f"driver_{selected_driver['user_id']}"
            )

            # Push-notification fallback. The driver-app listens for
            # data.type == 'new_ride_offer' (useDriverDashboard.ts:457) and
            # refetches the active ride via HTTP when this arrives, so we
            # don't need to put the full ride data in the push — just
            # a wake-up with the ride id.
            try:
                await send_push_notification(
                    selected_driver['user_id'],
                    'New ride request',
                    (
                        f"{ride.get('pickup_address') or 'Nearby pickup'} "
                        f"→ {ride.get('dropoff_address') or 'destination'}"
                    ),
                    {
                        'type': 'new_ride_offer',
                        'ride_id': ride_id,
                    },
                )
                logger.info(
                    f"[DISPATCH] push new_ride_offer sent to user_id="
                    f"{selected_driver['user_id']}"
                )
            except Exception as e:
                logger.warning(
                    f"[DISPATCH] push notification failed for "
                    f"user_id={selected_driver['user_id']}: {e}"
                )
        else:
            logger.warning(
                f"[DISPATCH] selected_driver has no user_id — cannot notify. "
                f"driver_id={selected_driver.get('id')} name={selected_driver.get('name')}. "
                f"This row is likely an orphan demo driver; clean up the drivers table."
            )


class RideEstimateRequest(BaseModel):
    pickup_lat: float
    pickup_lng: float
    dropoff_lat: float
    dropoff_lng: float
    stops: Optional[List[dict]] = None

@api_router.post("/estimate")
async def estimate_ride(request: RideEstimateRequest, current_user: dict = Depends(get_current_user)):
    distance_km = calculate_distance(
        request.pickup_lat, request.pickup_lng,
        request.dropoff_lat, request.dropoff_lng
    )
    duration_minutes = int(distance_km / 30 * 60) + 5
    
    fares = await get_fares_for_location(request.pickup_lat, request.pickup_lng)
    
    # Fetch all nearby online+available drivers once
    all_drivers = await db.drivers.find({
        'is_online': True,
        'is_available': True,
    }).to_list(200)

    # Filter to drivers within 10km radius and group by vehicle_type_id.
    # Exclude drivers without a user_id — those are orphan/demo rows that
    # cannot be dispatched to, and counting them would inflate the rider's
    # "X drivers available" badge and cause rides to fail at dispatch time.
    from collections import defaultdict
    drivers_by_type = defaultdict(list)
    for d in all_drivers:
        if not d.get('user_id'):
            continue
        d_lat = d.get('lat')
        d_lng = d.get('lng')
        if d_lat and d_lng:
            dist = calculate_distance(request.pickup_lat, request.pickup_lng, d_lat, d_lng)
            if dist <= 10.0:  # 10km radius
                vt_id = d.get('vehicle_type_id')
                drivers_by_type[vt_id].append({
                    'driver': d,
                    'distance_km': dist,
                })
    
    estimates = []
    for fare_info in fares:
        # Use Decimal for all monetary arithmetic (CQ-009 — eliminates float rounding errors)
        surge = _d(fare_info.get('surge_multiplier', 1.0))
        distance_fare = _round(_d(fare_info['per_km_rate']) * _d(distance_km) * surge)
        time_fare = _round(_d(fare_info['per_minute_rate']) * _d(duration_minutes) * surge)
        booking_fee = _d(fare_info.get('booking_fee', 2.0))
        total_fare = _round(_d(fare_info['base_fare']) + distance_fare + time_fare + booking_fee)
        total_fare = max(total_fare, _d(fare_info['minimum_fare']))

        # Check real driver availability for this vehicle type
        vt_id = fare_info['vehicle_type'].get('id')
        nearby_for_type = drivers_by_type.get(vt_id, [])
        driver_count = len(nearby_for_type)
        is_available = driver_count > 0

        # Calculate ETA: closest driver's distance / avg speed (30km/h in city)
        eta_minutes = None
        if nearby_for_type:
            closest = min(nearby_for_type, key=lambda x: x['distance_km'])
            eta_minutes = max(2, int(closest['distance_km'] / 30 * 60) + 1)

        estimates.append({
            'vehicle_type': fare_info['vehicle_type'],
            'distance_km': round(distance_km, 2),
            'duration_minutes': duration_minutes,
            'base_fare': _f(_d(fare_info['base_fare'])),
            'distance_fare': _f(distance_fare),
            'time_fare': _f(time_fare),
            'booking_fee': _f(booking_fee),
            'surge_multiplier': _f(surge),
            'total_fare': _f(total_fare),
            'available': is_available,
            'eta_minutes': eta_minutes,
            'driver_count': driver_count,
        })
        
    return estimates


@api_router.post("")
async def create_ride(request: CreateRideRequest, current_user: dict = Depends(get_current_user)):
    distance_km = calculate_distance(
        request.pickup_lat, request.pickup_lng,
        request.dropoff_lat, request.dropoff_lng
    )
    duration_minutes = int(distance_km / 30 * 60) + 5
    
    fares = await get_fares_for_location(request.pickup_lat, request.pickup_lng)
    
    # Serialize the fare objects if they aren't dicts, or just access them if they are
    # get_fares_for_location returns a list of dictionaries as seen in server.py
    
    fare_info = next((f for f in fares if f['vehicle_type']['id'] == request.vehicle_type_id), fares[0] if fares else None)
    
    if not fare_info:
        raise HTTPException(status_code=400, detail='Invalid vehicle type')
        
    # Use Decimal for all monetary arithmetic (CQ-009 — eliminates float rounding errors)
    surge = _d(fare_info.get('surge_multiplier', 1.0))
    distance_fare = _round(_d(fare_info['per_km_rate']) * _d(distance_km) * surge)
    time_fare = _round(_d(fare_info['per_minute_rate']) * _d(duration_minutes) * surge)
    booking_fee = _d(fare_info.get('booking_fee', 2.0))
    base_fare = _d(fare_info['base_fare'])
    total_fare = _round(base_fare + distance_fare + time_fare + booking_fee)
    total_fare = max(total_fare, _d(fare_info['minimum_fare']))

    # Earnings split: Distance fare goes to driver, booking fee goes to admin
    driver_earnings = _round(base_fare + distance_fare + time_fare)
    admin_earnings = booking_fee

    ride = Ride(
        rider_id=current_user['id'],
        vehicle_type_id=request.vehicle_type_id,
        pickup_address=request.pickup_address,
        pickup_lat=request.pickup_lat,
        pickup_lng=request.pickup_lng,
        dropoff_address=request.dropoff_address,
        dropoff_lat=request.dropoff_lat,
        dropoff_lng=request.dropoff_lng,
        distance_km=round(distance_km, 2),
        duration_minutes=duration_minutes,
        base_fare=_f(base_fare),
        distance_fare=_f(distance_fare),
        time_fare=_f(time_fare),
        booking_fee=_f(booking_fee),
        surge_multiplier=_f(surge),
        total_fare=_f(total_fare),
        stops=request.stops,
        is_scheduled=request.is_scheduled,
        scheduled_time=request.scheduled_time,
        driver_earnings=_f(driver_earnings),
        admin_earnings=_f(admin_earnings),
        payment_method=request.payment_method,
        status='searching',
        pickup_otp=generate_otp(),
        ride_requested_at=datetime.utcnow()
    )
    
    await db.rides.insert_one(ride.dict())
    
    # Match driver
    await match_driver_to_ride(ride.id)
    
    updated_ride = await db.rides.find_one({'id': ride.id})
    # Small helper to ensure we return a clean dict
    def serialize_doc(doc): return doc

    # GAP FIX: Start a background task to auto-cancel if no driver is found within 5 minutes
    async def ride_search_timeout(r_id: str, timeout_seconds: int = 300):
        """Auto-cancel ride if still 'searching' after timeout (default 5 min, matching Uber/Lyft)."""
        await asyncio.sleep(timeout_seconds)
        try:
            current_ride = await db.rides.find_one({'id': r_id})
            if current_ride and current_ride.get('status') == 'searching':
                await db.rides.update_one(
                    {'id': r_id, 'status': 'searching'},
                    {'$set': {
                        'status': 'cancelled',
                        'cancelled_at': datetime.utcnow(),
                        'cancellation_reason': 'No nearby drivers found. Please try again.',
                        'updated_at': datetime.utcnow()
                    }}
                )
                # Notify rider
                await manager.send_personal_message(
                    {
                        'type': 'ride_cancelled',
                        'ride_id': r_id,
                        'reason': 'No nearby drivers available. Your ride has been automatically cancelled.'
                    },
                    f"rider_{current_ride['rider_id']}"
                )
                logger.info(f"Ride {r_id} auto-cancelled after {timeout_seconds}s - no driver found")
        except Exception as e:
            logger.warning(f"Ride timeout handler error for {r_id}: {e}")

    if updated_ride and updated_ride.get('status') == 'searching':
        asyncio.create_task(ride_search_timeout(ride.id))

    return serialize_doc(updated_ride)

from fastapi import Request

@api_router.get("/active")
async def get_active_ride(current_user: dict = Depends(get_current_user)):
    """Get rider's current active/pending ride (if any). Used on app launch to resume."""
    # First check for rides that need payment (completed but not paid)
    # Then check for active rides
    active_statuses = ['searching', 'driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress']

    # Check for unpaid completed ride first (must pay before new ride)
    unpaid_ride = await db.rides.find_one({
        'rider_id': current_user['id'],
        'status': 'completed',
        'payment_status': {'$ne': 'paid'},
    })
    if unpaid_ride:
        ride = unpaid_ride
    else:
        ride = await db.rides.find_one({
            'rider_id': current_user['id'],
            'status': {'$in': active_statuses},
        })

    if not ride:
        return {'active': False, 'ride': None}

    # Attach driver info if assigned
    driver = None
    if ride.get('driver_id'):
        driver = await db.drivers.find_one({'id': ride['driver_id']})
        if driver:
            user = await db.users.find_one({'id': driver.get('user_id')})
            driver = {
                'id': driver['id'],
                'name': f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else 'Driver',
                'rating': driver.get('rating', 4.8),
                'total_rides': driver.get('total_rides', 0),
                'vehicle_make': driver.get('vehicle_make'),
                'vehicle_model': driver.get('vehicle_model'),
                'vehicle_color': driver.get('vehicle_color'),
                'license_plate': driver.get('license_plate'),
                'lat': driver.get('lat'),
                'lng': driver.get('lng'),
                'heading': driver.get('heading'),
            }

    def serialize_doc(doc): return doc
    ride_data = serialize_doc(ride)
    ride_data['driver'] = driver
    return {'active': True, 'ride': ride_data}

@api_router.get("/history")
async def get_ride_history(current_user: dict = Depends(get_current_user)):
    """Get rider's past rides for the activity tab. Only completed or cancelled rides.
    Any stale rides (searching/assigned but old) are auto-cancelled."""
    all_rides = await db.rides.find({
        'rider_id': current_user['id'],
    }).to_list(200)

    # Only show rides where a driver was actually assigned and ride started or completed
    # Exclude: searching, driver_assigned (never picked up), auto-expired
    result = []
    for ride in all_rides:
        status = ride.get('status', '')
        had_driver = bool(ride.get('driver_id'))

        if status == 'completed':
            result.append(ride)
        elif status == 'cancelled' and had_driver:
            # Only show cancelled rides where a driver was involved
            result.append(ride)
        # Skip everything else: searching, assigned but never started, auto-expired, etc.

    result.sort(key=lambda r: str(r.get('created_at', '')), reverse=True)
    def serialize_doc(doc): return doc
    return [serialize_doc(r) for r in result]

@api_router.get("/{ride_id}")
async def get_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Fetch details of a specific ride"""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
        
    # Security check: must be rider or driver of this ride
    is_rider = ride.get('rider_id') == current_user['id']
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    is_driver = driver and ride.get('driver_id') == driver['id']
    
    if not (is_rider or is_driver):
        # Admin check
        if current_user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Not authorized to view this ride")
            
    # Include driver details if assigned
    if ride.get('driver_id'):
        assigned_driver = await db.drivers.find_one({'id': ride['driver_id']})
        if assigned_driver:
            # Add to response
            ride['driver'] = assigned_driver

    def serialize_doc(doc): return doc
    return serialize_doc(ride)

@api_router.post("/{ride_id}/tip")
async def add_tip(ride_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    data = await request.json()
    tip_amount = float(data.get('amount', 0))
    if tip_amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid tip amount")
        
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
        
    if ride.get('rider_id') != current_user.get('id'):
        raise HTTPException(status_code=403, detail="Not authorized to tip this ride")
        
    if ride.get('status') != 'completed':
        raise HTTPException(status_code=400, detail="Can only tip completed rides")
        
    new_tip = ride.get('tip_amount', 0) + tip_amount
    new_driver_earnings = ride.get('driver_earnings', 0) + tip_amount
    
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'tip_amount': new_tip,
            'driver_earnings': new_driver_earnings
        }}
    )
    
    return {'success': True, 'tip_amount': new_tip}


@api_router.post("/{ride_id}/process-payment")
async def process_payment(ride_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    """Process payment for completed ride. Charges rider's card for fare + tip."""
    data = await request.json()
    tip_amount = float(data.get('tip_amount', 0))

    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get('rider_id') != current_user.get('id'):
        raise HTTPException(status_code=403, detail="Not authorized")

    # IDEMPOTENCY: if already paid, return success without charging again
    if ride.get('payment_status') == 'paid':
        logger.info(f"[PAYMENT] Ride {ride_id} already paid — skipping duplicate charge")
        return {'success': True, 'charged_amount': ride.get('total_fare', 0) + (ride.get('tip_amount', 0) or 0), 'already_paid': True}

    total_charge = (ride.get('total_fare', 0) or 0) + tip_amount

    # TODO: Stripe charge — for now mark as paid
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'payment_status': 'paid',
            'tip_amount': tip_amount,
            'updated_at': datetime.utcnow().isoformat(),
        }}
    )

    # Send receipt email (SendGrid when configured, logs otherwise)
    rider = await db.users.find_one({'id': current_user['id']})
    driver_info = None
    if ride.get('driver_id'):
        drv = await db.drivers.find_one({'id': ride['driver_id']})
        if drv:
            du = await db.users.find_one({'id': drv.get('user_id')})
            if du:
                driver_info = {**du, 'name': f"{du.get('first_name','')} {du.get('last_name','')}".strip()}

    email_sent = False
    try:
        from utils.email_receipt import send_receipt_email
        email_sent = await send_receipt_email(ride, rider or {}, driver_info, tip_amount)
    except Exception as e:
        logger.warning(f"Receipt email error: {e}")

    return {'success': True, 'charged_amount': total_charge, 'email_sent': email_sent}


# ============================================================
# GAP FIX: Share Ride Link (Uber/Lyft standard feature)
# ============================================================

@api_router.get("/{ride_id}/share")
async def get_share_trip_link(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Generate a shareable trip tracking link for safety contacts (like Uber's 'Share My Trip')."""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    
    if ride.get('rider_id') != current_user['id']:
        raise HTTPException(status_code=403, detail="Not authorized to share this ride")
    
    if ride.get('status') in ['completed', 'cancelled']:
        raise HTTPException(status_code=400, detail="Cannot share a completed or cancelled ride")
    
    # Generate or reuse a share token
    share_token = ride.get('shared_trip_token')
    if not share_token:
        share_token = secrets.token_urlsafe(32)
        await db.rides.update_one(
            {'id': ride_id},
            {'$set': {'shared_trip_token': share_token}}
        )
    
    # The frontend would use this token to show a read-only tracking page
    # In production, this would be a full URL like: https://spinr.app/track/{share_token}
    share_url = f"/track/{share_token}"
    
    return {
        'success': True,
        'share_token': share_token,
        'share_url': share_url,
        'ride_id': ride_id
    }

@api_router.get("/track/{share_token}")
async def track_shared_ride(share_token: str):
    """Public endpoint - Get ride status via share token (no auth required)."""
    ride = await db.rides.find_one({'shared_trip_token': share_token})
    if not ride:
        raise HTTPException(status_code=404, detail="Shared ride not found or link expired")
    
    if ride.get('status') in ['completed', 'cancelled']:
        return {
            'status': ride.get('status'),
            'message': 'This ride has ended.',
            'pickup_address': ride.get('pickup_address'),
            'dropoff_address': ride.get('dropoff_address'),
        }
    
    # Get driver location for live tracking
    driver_info = None
    if ride.get('driver_id'):
        driver = await db.drivers.find_one({'id': ride['driver_id']})
        if driver:
            driver_info = {
                'name': driver.get('name', 'Driver'),
                'lat': driver.get('lat'),
                'lng': driver.get('lng'),
                'vehicle_make': driver.get('vehicle_make'),
                'vehicle_model': driver.get('vehicle_model'),
                'vehicle_color': driver.get('vehicle_color'),
                'license_plate': driver.get('license_plate'),
            }
    
    return {
        'status': ride.get('status'),
        'pickup_address': ride.get('pickup_address'),
        'dropoff_address': ride.get('dropoff_address'),
        'pickup_lat': ride.get('pickup_lat'),
        'pickup_lng': ride.get('pickup_lng'),
        'dropoff_lat': ride.get('dropoff_lat'),
        'dropoff_lng': ride.get('dropoff_lng'),
        'driver': driver_info,
    }

@api_router.post("/{ride_id}/rate")
async def rate_driver(ride_id: str, rating_data: RideRatingRequest, current_user: dict = Depends(get_current_user)):
    """Rider rates the driver"""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride or ride.get('rider_id') != current_user['id']:
        raise HTTPException(status_code=404, detail="Ride not found or unauthorized")
        
    # Save rating using existing columns (rider_rating = rating rider gave the driver)
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'rider_rating': rating_data.rating,
            'rider_comment': rating_data.comment or '',
            'updated_at': datetime.utcnow()
        }}
    )

    driver_id = ride.get('driver_id')
    if not driver_id:
        return {'success': True}
    
    if rating_data.tip_amount > 0:
        new_tip = ride.get('tip_amount', 0) + rating_data.tip_amount
        new_driver_earnings = ride.get('driver_earnings', 0) + rating_data.tip_amount
        await db.rides.update_one(
            {'id': ride_id},
            {'$set': {
                'tip_amount': new_tip,
                'driver_earnings': new_driver_earnings
            }}
        )

    # Aggregate driver rating accurately
    driver = await db.drivers.find_one({'id': driver_id})
    if driver:
        # Fetch all rides for this driver to compute precise average
        driver_rides = await db.rides.find({'driver_id': driver_id}).to_list(1000)
        rated_rides = [float(r.get('driver_rating')) for r in driver_rides if r.get('driver_rating') is not None]
        
        if rated_rides:
            average_rating = round(sum(rated_rides) / len(rated_rides), 2)
            await db.drivers.update_one(
                {'id': driver_id},
                {'$set': {
                    'rating': average_rating,
                    'average_rating': average_rating,
                    'total_ratings': len(rated_rides)
                }}
            )

    return {'success': True}

@api_router.post("/{ride_id}/cancel")
async def cancel_ride_rider(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Rider cancels the ride"""
    try:
        from ..db import diag_logger  # type: ignore
    except ImportError:
        from db import diag_logger  # type: ignore

    diag_logger.info(f"[CANCEL] called ride_id={ride_id} user_id={current_user.get('id')}")

    ride = await db.rides.find_one({'id': ride_id})
    if not ride or ride.get('rider_id') != current_user['id']:
        diag_logger.info(
            f"[CANCEL] not found or unauthorized ride_id={ride_id} "
            f"ride_exists={ride is not None} "
            f"ride_rider_id={ride.get('rider_id') if ride else None} "
            f"caller_id={current_user['id']}"
        )
        raise HTTPException(status_code=404, detail="Ride not found or unauthorized")

    diag_logger.info(
        f"[CANCEL] entry ride_id={ride_id} pre_status={ride.get('status')} "
        f"driver_id={ride.get('driver_id')}"
    )

    if ride.get('status') in ['completed', 'cancelled']:
        raise HTTPException(status_code=400, detail="Ride already completed or cancelled")

    # Calculate cancellation fee based on time since driver accepted
    driver_id = ride.get('driver_id')
    settings = await get_app_settings()
    cancellation_fee_admin = settings.get('cancellation_fee_admin', 0.50)
    cancellation_fee_driver = settings.get('cancellation_fee_driver', 2.50)
    
    charged_admin = 0.0
    charged_driver = 0.0
    
    # Calculate fee if driver was already assigned and some time passed (e.g. 2 mins)
    if driver_id and ride.get('driver_accepted_at'):
        accepted_at = ride['driver_accepted_at']
        if isinstance(accepted_at, str):
            try:
                accepted_at = datetime.fromisoformat(accepted_at.replace('Z', '+00:00').replace('+00:00', ''))
            except ValueError:
                accepted_at = None
        if accepted_at:
            time_diff = (datetime.utcnow() - accepted_at).total_seconds()
        else:
            time_diff = 0
        if time_diff > 120:  # 2 minutes
            charged_admin = cancellation_fee_admin
            charged_driver = cancellation_fee_driver
            
            # Here we would charge the user's stripe card using stripe API for the fee
            # ... (omitted for brevity, assume successful)

            # Add to driver balance
            if charged_driver > 0:
                 pass # We would potentially log a payout or add to pending earnings

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'cancelled',
            'cancelled_at': datetime.utcnow(),
            'cancellation_fee_admin': charged_admin,
            'cancellation_fee_driver': charged_driver,
            'updated_at': datetime.utcnow()
        }}
    )

    # Verify the cancel actually landed in the database. Same class of
    # silent-failure we hit with go-online and accept: the update_one wrapper
    # returns None when zero rows are affected and the handler would
    # otherwise return {success: true} while the ride is still in its prior
    # state — the rider then reloads and sees the ride still "searching".
    try:
        verify_ride = await db.rides.find_one({'id': ride_id})
    except Exception as e:
        verify_ride = None
        diag_logger.info(f"[CANCEL] verify re-read failed: {e}")

    diag_logger.info(
        f"[CANCEL] post-update ride_id={ride_id} "
        f"post_status={verify_ride.get('status') if verify_ride else 'ROW_GONE'} "
        f"post_cancelled_at={verify_ride.get('cancelled_at') if verify_ride else 'ROW_GONE'}"
    )

    if not verify_ride or verify_ride.get('status') != 'cancelled':
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
        await db.drivers.update_one(
            {'id': driver_id},
            {'$set': {'is_available': True}}
        )
        
        # Notify driver
        driver = await db.drivers.find_one({'id': driver_id})
        if driver and driver.get('user_id'):
            await manager.send_personal_message(
                {'type': 'ride_cancelled', 'ride_id': ride_id, 'reason': 'Rider cancelled'},
                f"driver_{driver['user_id']}"
            )

    return {'success': True, 'cancellation_fee': charged_admin + charged_driver}

class EmergencyRequest(BaseModel):
    message: str = "Emergency assistance requested"
    latitude: Optional[float] = None
    longitude: Optional[float] = None

@api_router.post("/{ride_id}/emergency")
async def trigger_emergency(ride_id: str, request: EmergencyRequest, current_user: dict = Depends(get_current_user)):
    """Trigger an emergency alert for a live ride"""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
        
    # Verify the user is part of the ride
    is_rider = ride.get('rider_id') == current_user['id']
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    is_driver = driver and ride.get('driver_id') == driver['id']
    
    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to trigger emergency for this ride")
        
    incident = {
        'id': str(uuid.uuid4()),
        'ride_id': ride_id,
        'reported_by_user_id': current_user['id'],
        'role': 'rider' if is_rider else 'driver',
        'message': request.message,
        'status': 'open',
        'latitude': request.latitude,
        'longitude': request.longitude,
        'created_at': datetime.utcnow().isoformat()
    }
    
    await db.emergencies.insert_one(incident)
    
    # Notify admin dashboard via Websocket
    await manager.send_personal_message(
        {'type': 'emergency_alert', 'incident': incident},
        "admin_notifications"
    )
    logger.critical(f"EMERGENCY ALERT TRIGGERED for ride {ride_id} by user {current_user['id']}")

    # GAP FIX: Notify emergency contacts via SMS/push
    try:
        contacts_cursor = db.emergency_contacts.find({'user_id': current_user['id']})
        contacts = await contacts_cursor.to_list(length=5) if hasattr(contacts_cursor, 'to_list') else list(contacts_cursor)
        
        user = await db.users.find_one({'id': current_user['id']})
        user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else 'A Spinr user'
        
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
    
    return {'success': True, 'incident_id': incident['id'], 'contacts_notified': len(contacts) if 'contacts' in dir() else 0}

@api_router.get("/{ride_id}/messages")
async def get_ride_messages(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Fetch persistent chat messages for a ride"""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
        
    # Verify the user is part of the ride
    is_rider = ride.get('rider_id') == current_user['id']
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    is_driver = driver and ride.get('driver_id') == driver['id']
    
    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to track this ride")
        
    messages_cursor = db.ride_messages.find({'ride_id': ride_id}).sort('timestamp', 1)
    messages = await messages_cursor.to_list(length=100) if hasattr(messages_cursor, 'to_list') else list(messages_cursor)
    
    # Serialize datetime
    serialized = []
    for msg in messages:
        # Provide fallback serialize
        if 'timestamp' in msg and isinstance(msg['timestamp'], datetime):
            msg['timestamp'] = msg['timestamp'].isoformat()
        serialized.append(msg)
        
    return {'success': True, 'messages': serialized}

@api_router.get("/scheduled")
async def get_scheduled_rides(current_user: dict = Depends(get_current_user)):
    """Get all upcoming scheduled rides for the current rider."""
    rides_cursor = db.rides.find({
        'rider_id': current_user['id'],
        'is_scheduled': True,
        'status': {'$nin': ['completed', 'cancelled']}
    })
    rides = await rides_cursor.to_list(length=50) if hasattr(rides_cursor, 'to_list') else list(rides_cursor)
    return rides

@api_router.delete("/scheduled/{ride_id}")
async def cancel_scheduled_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Cancel a scheduled ride."""
    ride = await db.rides.find_one({'id': ride_id, 'rider_id': current_user['id'], 'is_scheduled': True})
    if not ride:
        raise HTTPException(status_code=404, detail="Scheduled ride not found")
    if ride.get('status') in ['completed', 'cancelled']:
        raise HTTPException(status_code=400, detail="Ride is already completed or cancelled")

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'cancelled',
            'cancelled_at': datetime.utcnow(),
            'cancellation_reason': 'Cancelled by rider (scheduled)',
            'updated_at': datetime.utcnow()
        }}
    )
    return {'success': True}

@api_router.post("/{ride_id}/simulate-arrival")
async def simulate_driver_arrival(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Dev/test only: Simulate driver arriving at pickup, returns OTP."""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get('rider_id') != current_user['id']:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'driver_arrived',
            'driver_arrived_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )
    updated_ride = await db.rides.find_one({'id': ride_id})
    return {'success': True, 'pickup_otp': updated_ride.get('pickup_otp', '0000')}

@api_router.post("/{ride_id}/start")
async def rider_start_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Rider-side: Mark ride as in progress (when OTP already verified or used together with driver)."""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get('rider_id') != current_user['id']:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get('status') not in ['driver_arrived']:
        raise HTTPException(status_code=400, detail=f"Cannot start ride with status: {ride.get('status')}")

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'in_progress',
            'ride_started_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )
    return {'success': True}

@api_router.post("/{ride_id}/complete")
async def rider_complete_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Rider-side: Get completed ride data (ride is completed by driver; this fetches the result)."""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get('rider_id') != current_user['id']:
        raise HTTPException(status_code=403, detail="Not authorized")
    # Return the current ride state (driver will have set it to completed)
    return ride

@api_router.get("/{ride_id}/receipt")
async def get_ride_receipt(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Get a detailed receipt for a completed ride"""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
        
    if ride.get('rider_id') != current_user['id']:
        raise HTTPException(status_code=403, detail="Not authorized to view this receipt")
        
    if ride.get('status') not in ['completed', 'cancelled']:
        raise HTTPException(status_code=400, detail="Receipts are only available for completed or cancelled rides")
        
    driver = None
    if ride.get('driver_id'):
        driver = await db.drivers.find_one({'id': ride['driver_id']})
    
    driver_profile = None
    if driver and driver.get('user_id'):
         driver_profile = await db.users.find_one({'id': driver['user_id']})
         
    vehicle = None
    if ride.get('vehicle_type_id'):
        vehicle = await db.vehicle_types.find_one({'id': ride['vehicle_type_id']})
        
    corporate_account = None
    if ride.get('corporate_account_id'):
        corporate_account = await db.corporate_accounts.find_one({'id': ride['corporate_account_id']})

    receipt_data = {
        'ride_id': ride_id,
        'date': ride.get('ride_completed_at') or ride.get('cancelled_at') or ride.get('created_at'),
        'status': ride.get('status'),
        'pickup_address': ride.get('pickup_address'),
        'dropoff_address': ride.get('dropoff_address'),
        'stops': ride.get('stops', []),
        'distance_km': ride.get('distance_km'),
        'base_fare': ride.get('base_fare', 0),
        'distance_fare': ride.get('distance_fare', 0),
        'time_fare': ride.get('time_fare', 0),
        'airport_fee': ride.get('airport_fee', 0),
        'booking_fee': ride.get('booking_fee', 0),
        'cancellation_fee': (ride.get('cancellation_fee_admin', 0) + ride.get('cancellation_fee_driver', 0)) if ride.get('status') == 'cancelled' else 0,
        'tax_amount': ride.get('tax_amount', 0),
        'tip_amount': ride.get('tip_amount', 0),
        'total_charged': ride.get('total_fare', 0),
        'payment_method': 'Corporate Account' if corporate_account else (ride.get('payment_method_id') or 'Credit Card ending in ****'),
        'corporate_account_name': corporate_account.get('company_name') if corporate_account else None,
        'driver_name': f"{driver_profile.get('first_name', '')} {driver_profile.get('last_name', '')}".strip() if driver_profile else "Unknown Driver",
        'vehicle_type': vehicle.get('name') if vehicle else "Standard"
    }
    
    # Ideally send email here via SendGrid/Mailgun if POST
    
    return {'success': True, 'receipt': receipt_data}
