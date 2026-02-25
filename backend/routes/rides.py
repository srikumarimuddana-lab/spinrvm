from fastapi import APIRouter, Depends, HTTPException
try:
    from ..dependencies import get_current_user, generate_otp
    from ..schemas import CreateRideRequest, Ride, UserProfile
    from ..db import db
    from ..utils import calculate_distance
    from ..socket_manager import manager
except ImportError:
    from dependencies import get_current_user, generate_otp
    from schemas import CreateRideRequest, Ride, UserProfile
    from db import db
    from utils import calculate_distance
    from socket_manager import manager
from .fares import get_fares_for_location
import logging
import asyncio
from typing import List, Tuple
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/rides", tags=["Rides"])

async def create_demo_drivers(vehicle_type_id: str, lat: float, lng: float):
    # This was implicitly present in original but not fully defined in the viewed snippet.
    # Assuming it creates mock drivers for demo purposes.
    # For now, I'll implement a simple placeholder or skip if not strictly required,
    # but the matching logic calls it. I'll add a minimal implementation.
    import random
    
    for i in range(3):
        driver_id = str(uuid.uuid4())
        # Random offset
        d_lat = lat + (random.random() - 0.5) * 0.01
        d_lng = lng + (random.random() - 0.5) * 0.01
        
        driver = {
            'id': driver_id,
            'name': f"Demo Driver {i+1}",
            'phone': f"555000{i}",
            'vehicle_type_id': vehicle_type_id,
            'lat': d_lat,
            'lng': d_lng,
            'is_online': True,
            'is_available': True,
            'rating': 4.8 + (0.1 * random.random()),
            'total_rides': random.randint(10, 500)
        }
        await db.drivers.insert_one(driver)
    logger.info("Created demo drivers")

async def match_driver_to_ride(ride_id: str):
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        return
    
    settings = await db.settings.find_one({'id': 'app_settings'})
    algorithm = settings.get('driver_matching_algorithm', 'nearest') if settings else 'nearest'
    min_rating = settings.get('min_driver_rating', 4.0) if settings else 4.0
    search_radius = settings.get('search_radius_km', 10.0) if settings else 10.0
    
    # Use PostGIS to find nearby drivers
    # Note: radius in meters for RPC
    try:
        nearby_drivers = await db.rpc('find_nearby_drivers', {
            'lat': ride['pickup_lat'],
            'lng': ride['pickup_lng'],
            'radius_meters': search_radius * 1000
        })
    except Exception as e:
        logger.warning(f"find_nearby_drivers RPC not available in match_driver: {e}")
        nearby_drivers = []
    
    if not nearby_drivers:
        nearby_drivers = []
    
    # Filter by vehicle type (rpc returns all types nearby)
    drivers = [d for d in nearby_drivers if d.get('vehicle_type_id') == ride['vehicle_type_id']]

    if not drivers:
        # Create demo drivers if none found (for testing)
        await create_demo_drivers(ride['vehicle_type_id'], ride['pickup_lat'], ride['pickup_lng'])
        # Try finding again
        try:
            nearby_drivers = await db.rpc('find_nearby_drivers', {
                'lat': ride['pickup_lat'],
                'lng': ride['pickup_lng'],
                'radius_meters': search_radius * 1000
            })
        except Exception as e:
            logger.warning(f"find_nearby_drivers RPC retry failed: {e}")
            nearby_drivers = []
        if not nearby_drivers:
            nearby_drivers = []
        drivers = [d for d in nearby_drivers if d.get('vehicle_type_id') == ride['vehicle_type_id']]
    
    if not drivers:
        return

    if algorithm in ['rating_based', 'combined']:
        driver_ids = [d['id'] for d in drivers]
        # Fetch full details
        full_drivers = await db.drivers.find({'id': {'$in': driver_ids}}).to_list(len(driver_ids))
        # Filter by rating
        full_drivers = [d for d in full_drivers if d.get('rating', 5.0) >= min_rating]

        # Map distance back
        dist_map = {d['id']: d['distance_meters'] / 1000.0 for d in drivers} # Convert m to km
        drivers_with_distance = []
        for d in full_drivers:
            if d['id'] in dist_map:
                drivers_with_distance.append((d, dist_map[d['id']]))

    else:
        # RPC result is partial. Let's fetch full objects.
        driver_ids = [d['id'] for d in drivers]
        full_drivers = await db.drivers.find({'id': {'$in': driver_ids}}).to_list(len(driver_ids))
        dist_map = {d['id']: d['distance_meters'] / 1000.0 for d in drivers}

        drivers_with_distance = []
        for d in full_drivers:
             if d['id'] in dist_map:
                drivers_with_distance.append((d, dist_map[d['id']]))

    if not drivers_with_distance:
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

        # Update ride with selected driver
        await db.rides.update_one(
            {'id': ride_id},
            {'$set': {
                'driver_id': selected_driver['id'],
                'status': 'driver_assigned',
                'driver_notified_at': datetime.utcnow(),
                'driver_accepted_at': datetime.utcnow(),  # Auto-accept for demo
                'updated_at': datetime.utcnow()
            }}
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

        # Notify driver via WebSocket
        if selected_driver.get('user_id'):
             await manager.send_personal_message(
                {
                    'type': 'new_ride_assignment',
                    'ride_id': ride_id,
                    'pickup': ride['pickup_address'],
                    'dropoff': ride['dropoff_address'],
                    'fare': ride['driver_earnings']
                },
                f"driver_{selected_driver['user_id']}"
            )


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
    
    distance_fare = fare_info['per_km_rate'] * distance_km
    time_fare = fare_info['per_minute_rate'] * duration_minutes
    booking_fee = fare_info.get('booking_fee', 2.0)
    
    total_fare = fare_info['base_fare'] + distance_fare + time_fare + booking_fee
    total_fare = max(total_fare, fare_info['minimum_fare'])
    
    # Earnings split: Distance fare goes to driver, booking fee goes to admin
    driver_earnings = fare_info['base_fare'] + distance_fare + time_fare
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
        base_fare=fare_info['base_fare'],
        distance_fare=round(distance_fare, 2),
        time_fare=round(time_fare, 2),
        booking_fee=booking_fee,
        total_fare=round(total_fare, 2),
        driver_earnings=round(driver_earnings, 2),
        admin_earnings=round(admin_earnings, 2),
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
    return serialize_doc(updated_ride)

from fastapi import Request

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
