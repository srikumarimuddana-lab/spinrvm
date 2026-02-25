from fastapi import APIRouter, Depends, HTTPException, Query, Body
from typing import Optional, List, Union, Dict, Any
try:
    from ..dependencies import get_current_user, get_admin_user
    from ..schemas import Driver, Ride, RideRatingRequest
    from ..db import db
    from ..socket_manager import manager
except ImportError:
    from dependencies import get_current_user, get_admin_user
    from schemas import Driver, Ride, RideRatingRequest
    from db import db
    from socket_manager import manager
from datetime import datetime, timedelta
from datetime import datetime as dt
from datetime import datetime as dt
import json
import logging
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class RideOTPRequest(BaseModel):
    otp: str

api_router = APIRouter(prefix="/drivers", tags=["Drivers"])

def serialize_doc(doc):
    return doc

@api_router.get("/me")
async def get_my_driver(current_user: dict = Depends(get_current_user)):
    """Get the current user's driver profile."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
    return serialize_doc(driver)

@api_router.get("/balance")
async def get_driver_balance(current_user: dict = Depends(get_current_user)):
    """Get driver's current balance/earnings summary."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
    
    # Use Supabase instead of aggregate
    try:
        from supabase import create_client
        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        if supabase_url and supabase_key:
            supabase = create_client(supabase_url, supabase_key)
            
            # Get completed rides
            rides_res = supabase.table('rides').select(
                'driver_earnings, tip_amount'
            ).eq('driver_id', driver['id']).eq('status', 'completed').execute()
            
            rides = rides_res.data or []
            total_earnings = sum(r.get('driver_earnings', 0) or 0 for r in rides)
            total_tips = sum(r.get('tip_amount', 0) or 0 for r in rides)
            total_rides = len(rides)
            
            # Get pending payouts
            payouts_res = supabase.table('payouts').select('amount').eq('driver_id', driver['id']).eq('status', 'pending').execute()
            payouts = payouts_res.data or []
            pending_payouts = sum(p.get('amount', 0) or 0 for p in payouts)
        else:
            total_earnings = total_tips = total_rides = pending_payouts = 0
    except Exception as e:
        logger.error(f"Error fetching balance: {e}")
        total_earnings = total_tips = total_rides = pending_payouts = 0
    
    return {
        'total_earnings': total_earnings,
        'available_balance': total_earnings - pending_payouts,
        'pending_payouts': pending_payouts,
        'total_paid_out': 0,
        'has_bank_account': bool(driver.get('bank_account')),
        'total_tips': total_tips,
        'total_rides': total_rides
    }

@api_router.get("/earnings")
async def get_driver_earnings(
    period: str = Query('week'),
    current_user: dict = Depends(get_current_user)
):
    """Get driver's earnings summary for a period."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        # Try to find by id directly in case user_id isn't set, or log error
        logger.error(f"Driver not found for user {current_user['id']}")
        raise HTTPException(status_code=404, detail='Driver not found')
    
    logger.info(f"Fetching earnings for driver {driver['id']} period {period}")
    
    # Calculate date range
    now = datetime.utcnow()
    if period == 'day':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'week':
        start_date = now - timedelta(days=7)
    elif period == 'month':
        start_date = now - timedelta(days=30)
    else:
        start_date = now - timedelta(days=7)
    
    # Use Supabase RPC or manual calculation instead of aggregate
    try:
        from supabase import create_client
        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        if supabase_url and supabase_key:
            supabase = create_client(supabase_url, supabase_key)
            
            # Fetch completed rides in the period
            rides_res = supabase.table('rides').select(
                'driver_earnings, tip_amount, distance_km, duration_minutes'
            ).eq('driver_id', driver['id']).eq('status', 'completed').gte('ride_completed_at', start_date.isoformat()).execute()
            
            rides = rides_res.data or []
            
            total_earnings = sum(r.get('driver_earnings', 0) or 0 for r in rides)
            total_tips = sum(r.get('tip_amount', 0) or 0 for r in rides)
            total_rides = len(rides)
            total_distance_km = sum(r.get('distance_km', 0) or 0 for r in rides)
            total_duration_minutes = sum(r.get('duration_minutes', 0) or 0 for r in rides)
            
            stats = {
                'total_earnings': total_earnings,
                'total_tips': total_tips,
                'total_rides': total_rides,
                'total_distance_km': total_distance_km,
                'total_duration_minutes': total_duration_minutes
            }
        else:
            stats = {'total_earnings': 0, 'total_tips': 0, 'total_rides': 0, 'total_distance_km': 0, 'total_duration_minutes': 0}
    except Exception as e:
        logger.error(f"Error fetching earnings: {e}")
        stats = {'total_earnings': 0, 'total_tips': 0, 'total_rides': 0, 'total_distance_km': 0, 'total_duration_minutes': 0}
    
    return {
        'period': period,
        'total_earnings': stats.get('total_earnings', 0),
        'total_tips': stats.get('total_tips', 0),
        'total_rides': stats.get('total_rides', 0),
        'total_distance_km': stats.get('total_distance_km', 0),
        'total_duration_minutes': stats.get('total_duration_minutes', 0),
        'average_per_ride': stats.get('total_earnings', 0) / stats.get('total_rides', 1) if stats.get('total_rides', 0) > 0 else 0
    }

@api_router.get("/earnings/daily")
async def get_driver_daily_earnings(
    days: int = Query(7),
    current_user: dict = Depends(get_current_user)
):
    """Get driver's daily earnings breakdown."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
    
    start_date = datetime.utcnow() - timedelta(days=days)
    
    # Use Supabase instead of aggregate
    try:
        from supabase import create_client
        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        if supabase_url and supabase_key:
            supabase = create_client(supabase_url, supabase_key)
            
            # Fetch all completed rides in the period
            rides_res = supabase.table('rides').select(
                'ride_completed_at, driver_earnings, tip_amount, distance_km'
            ).eq('driver_id', driver['id']).eq('status', 'completed').gte('ride_completed_at', start_date.isoformat()).execute()
            
            rides = rides_res.data or []
            
            # Group by date manually
            daily_data = {}
            for r in rides:
                date_str = r.get('ride_completed_at', '')[:10]  # Get YYYY-MM-DD
                if date_str not in daily_data:
                    daily_data[date_str] = {'earnings': 0, 'tips': 0, 'rides': 0, 'distance_km': 0}
                daily_data[date_str]['earnings'] += r.get('driver_earnings', 0) or 0
                daily_data[date_str]['tips'] += r.get('tip_amount', 0) or 0
                daily_data[date_str]['rides'] += 1
                daily_data[date_str]['distance_km'] += r.get('distance_km', 0) or 0
            
            results = [
                {'date': date, **data}
                for date, data in sorted(daily_data.items())
            ]
        else:
            results = []
    except Exception as e:
        logger.error(f"Error fetching daily earnings: {e}")
        results = []
    
    return results

@api_router.get("/earnings/trips")
async def get_driver_trip_earnings(
    limit: int = Query(20),
    offset: int = Query(0),
    current_user: dict = Depends(get_current_user)
):
    """Get driver's individual trip earnings."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
    
    # Use Supabase instead of MongoDB cursor
    try:
        from supabase import create_client
        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        if supabase_url and supabase_key:
            supabase = create_client(supabase_url, supabase_key)
            
            rides_res = supabase.table('rides').select(
                'id, pickup_address, dropoff_address, distance_km, duration_minutes, '
                'base_fare, distance_fare, time_fare, driver_earnings, tip_amount, '
                'rider_rating, ride_completed_at'
            ).eq('driver_id', driver['id']).eq('status', 'completed').order('ride_completed_at', desc=True).range(offset, offset + limit - 1).execute()
            
            rides = rides_res.data or []
        else:
            rides = []
    except Exception as e:
        logger.error(f"Error fetching trip earnings: {e}")
        rides = []
    
    return [
        {
            'ride_id': r['id'],
            'pickup_address': r.get('pickup_address', ''),
            'dropoff_address': r.get('dropoff_address', ''),
            'distance_km': r.get('distance_km', 0),
            'duration_minutes': r.get('duration_minutes', 0),
            'base_fare': r.get('base_fare', 0),
            'distance_fare': r.get('distance_fare', 0),
            'time_fare': r.get('time_fare', 0),
            'driver_earnings': r.get('driver_earnings', 0),
            'tip_amount': r.get('tip_amount', 0),
            'rider_rating': r.get('rider_rating'),
            'completed_at': r.get('ride_completed_at') if r.get('ride_completed_at') else None
        }
        for r in rides
    ]

@api_router.get("")
async def get_drivers(
    lat: float = Query(None),
    lng: float = Query(None),
    radius: float = Query(5.0),
    vehicle_type: str = Query(None),
    admin_user: dict = Depends(get_admin_user)
):
    """
    Get all drivers (admin only) or nearby drivers (if lat/lng provided).
    """
    if lat and lng:
        # Should rely on RPC or geospatial query
        # For now, simplistic implementation as seen in other parts
        drivers = await db.drivers.find({'is_online': True}).to_list(100)
        return serialize_doc(drivers)
    
    # Return all drivers for admin
    drivers = await db.drivers.find({}).to_list(100)
    return serialize_doc(drivers)

@api_router.post("")
async def create_driver(driver: Driver, admin_user: dict = Depends(get_admin_user)):
    """Register a new driver (admin only or internal process)"""
    existing = await db.drivers.find_one({'phone': driver.phone})
    if existing:
        raise HTTPException(status_code=400, detail='Driver with this phone already exists')
    
    await db.drivers.insert_one(driver.dict())
    return driver.dict()

@api_router.post("/location-batch")
async def update_location_batch(
    batch: Union[List[dict], dict], 
    current_user: dict = Depends(get_current_user)
):
    """Update driver location in batch (from background tracking)."""
    
    points = []
    if isinstance(batch, list):
        points = batch
    elif isinstance(batch, dict):
        points = batch.get('locations') or batch.get('points') or []
        
    # Simply take the last point and update current location
    if not points:
        return {'success': True}
        
    latest = points[-1]
    lat = latest.get('latitude') or latest.get('lat')
    lng = latest.get('longitude') or latest.get('lng')
    heading = latest.get('heading', 0)
    
    if lat and lng:
        # Update via Supabase wrapper which now handles casting
        # Note: 'heading' column might not exist in Supabase 'drivers' table yet.
        update_data = {
            'lat': lat,
            'lng': lng,
            'updated_at': datetime.utcnow()
        }
        # If heading is supported later, add it back. Currently causing 500 error if column missing.
        # if heading:
        #    update_data['heading'] = heading
            
        await db.drivers.update_one(
            {'user_id': current_user['id']},
            {'$set': update_data}
        )
        # Also sync to generic lat/lng fields if they exist to support legacy queries
        # (Though update_one might not support setting multiple top-level fields easily if we rely on $set mapping)
        # Let's trust db.drivers.update_one to handle the schema or the wrapper.
        
    return {'success': True}

@api_router.get("/{driver_id}")
async def get_driver(driver_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'id': driver_id})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
    return serialize_doc(driver)

@api_router.put("/{driver_id}/status")
async def update_driver_status(
    driver_id: str, 
    is_online: bool, 
    current_user: dict = Depends(get_current_user)
):
    driver = await db.drivers.find_one({'id': driver_id})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
    
    # Ensure user owns this driver profile
    if driver.get('user_id') != current_user['id']:
        raise HTTPException(status_code=403, detail='Not authorized')

    await db.drivers.update_one(
        {'id': driver_id}, 
        {'$set': {'is_online': is_online, 'updated_at': datetime.utcnow()}}
    )
    return {'success': True, 'is_online': is_online}

import uuid

class BankAccountCreate(BaseModel):
    bank_name: str
    institution_number: str
    transit_number: str
    account_number: str
    account_holder_name: str
    account_type: str = 'checking'

class PayoutRequest(BaseModel):
    amount: float

@api_router.get("/bank-account")
async def get_bank_account(current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user.get('id')})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
    account = await db.bank_accounts.find_one({'driver_id': driver['id']})
    if account:
        return {'has_bank_account': True, 'bank_account': serialize_doc(account)}
    return {'has_bank_account': False, 'bank_account': None}

@api_router.post("/bank-account")
async def save_bank_account(req: BankAccountCreate, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user.get('id')})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
    
    account_data = req.dict()
    account_data['id'] = str(uuid.uuid4())
    account_data['driver_id'] = driver['id']
    acc_num = account_data.pop('account_number')
    
    # Canadian routing number for Stripe is generally 0 + Institution (3) + Transit (5)
    # Ensure zero-padding if needed
    inst = req.institution_number.zfill(3)
    trans = req.transit_number.zfill(5)
    account_data['routing_number'] = f"0{inst}{trans}"
    
    account_data['account_number_last4'] = acc_num[-4:] if len(acc_num) >= 4 else acc_num
    account_data['stripe_bank_id'] = None # Would be populated after calling Stripe's API
    account_data['currency'] = 'cad'
    account_data['country'] = 'CA'
    account_data['is_verified'] = False
    account_data['created_at'] = datetime.utcnow().isoformat()
    
    await db.bank_accounts.delete_many({'driver_id': driver['id']})
    await db.bank_accounts.insert_one(account_data)
    
    return {'success': True, 'bank_account': serialize_doc(account_data)}

@api_router.delete("/bank-account")
async def delete_bank_account(current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user.get('id')})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
    await db.bank_accounts.delete_many({'driver_id': driver['id']})
    return {'success': True}

@api_router.get("/balance")
async def get_balance_alias(current_user: dict = Depends(get_current_user)):
    return await get_driver_balance(current_user)

@api_router.post("/payouts")
async def request_payout(req: PayoutRequest, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user.get('id')})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
    
    balance = await get_driver_balance(current_user)
    if req.amount > balance.get('available_balance', 0):
        raise HTTPException(status_code=400, detail="Insufficient funds")
        
    account = await db.bank_accounts.find_one({'driver_id': driver['id']})
    if not account:
        raise HTTPException(status_code=400, detail="No bank account linked")
    
    payout = {
        'id': str(uuid.uuid4()),
        'driver_id': driver['id'],
        'amount': req.amount,
        'status': 'pending',
        'stripe_payout_id': None,
        'bank_name': account.get('bank_name'),
        'account_last4': account.get('account_number_last4'),
        'created_at': datetime.utcnow().isoformat()
    }
    await db.payouts.insert_one(payout)
    return {'success': True, 'payout': serialize_doc(payout)}

@api_router.get("/payouts")
async def get_payout_history(limit: int = Query(20), offset: int = Query(0), current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user.get('id')})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
        
    payouts_cursor = db.payouts.find({'driver_id': driver['id']})
    if hasattr(payouts_cursor, 'sort'):
        payouts_cursor = payouts_cursor.sort('created_at', -1).skip(offset).limit(limit)
    
    payouts = await payouts_cursor.to_list(length=limit) if hasattr(payouts_cursor, 'to_list') else list(payouts_cursor)
    return {'success': True, 'payouts': [serialize_doc(p) for p in payouts]}

@api_router.get("/t4a/{year}")
async def get_t4a_summary(year: int, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user.get('id')})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
        
    start_date = datetime(year, 1, 1).isoformat()
    end_date = datetime(year, 12, 31, 23, 59, 59).isoformat()
    
    rides_cursor = db.rides.find({
        'driver_id': driver['id'],
        'status': 'completed',
        'created_at': {'$gte': start_date, '$lte': end_date}
    })
    rides = await rides_cursor.to_list(length=10000) if hasattr(rides_cursor, 'to_list') else list(rides_cursor)
    
    total_earnings = sum(r.get('driver_earnings', 0) for r in rides)
    
    return {
        'year': year,
        'total_earnings': total_earnings,
        'total_trips': len(rides),
        'platform_fees': 0,
        'net_earnings': total_earnings,
        'generated_at': datetime.utcnow().isoformat()
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
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
    
    # improved query to catch any active state
    ride = await db.rides.find_one({
        'driver_id': driver['id'],
        'status': {'$in': ['driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress']}
    })
    
    if not ride:
        return {'ride': None}
    
    # Get rider info
    rider = await db.user_profiles.find_one({'id': ride['rider_id']})
    vehicle_type = await db.vehicle_types.find_one({'id': ride['vehicle_type_id']})
    
    return {
        'ride': serialize_doc(ride),
        'rider': serialize_doc(rider) if rider else None,
        'vehicle_type': serialize_doc(vehicle_type) if vehicle_type else None
    }

@api_router.get("/rides/history")
async def get_ride_history(
    limit: int = Query(20),
    offset: int = Query(0),
    current_user: dict = Depends(get_current_user)
):
    """Get driver's ride history."""
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
    
    # Use Supabase instead of MongoDB cursor
    try:
        from supabase import create_client
        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        if supabase_url and supabase_key:
            supabase = create_client(supabase_url, supabase_key)
            
            # Get total count
            count_res = supabase.table('rides').select('id', count='exact').eq('driver_id', driver['id']).execute()
            total = count_res.count if hasattr(count_res, 'count') else 0
            
            # Get rides with pagination
            rides_res = supabase.table('rides').select('*').eq('driver_id', driver['id']).order('created_at', desc=True).range(offset, offset + limit - 1).execute()
            rides = rides_res.data or []
        else:
            total = 0
            rides = []
    except Exception as e:
        logger.error(f"Error fetching ride history: {e}")
        total = 0
        rides = []
    
    return {
        'total': total,
        'rides': [serialize_doc(r) for r in rides]
    }

@api_router.post("/rides/{ride_id}/accept")
async def accept_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
        
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')
        
    # Verify this driver was assigned
    if ride.get('driver_id') != driver['id']:
        # Check if it's open (searching) and we can claim it? 
        # For now assume mostly assigned flow. 
        # If status is searching, we might allow claim if using broadcast.
        if ride['status'] == 'searching':
             # Allow claim
             pass
        else:
             raise HTTPException(status_code=400, detail='Ride not assigned to you')

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'driver_accepted',
            'driver_id': driver['id'], # ensure set
            'driver_accepted_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )
    
    # Notify rider
    if ride.get('rider_id'):
        await manager.send_personal_message(
            {'type': 'driver_accepted', 'ride_id': ride_id},
            f"rider_{ride['rider_id']}"
        )
        
    return {'success': True}

@api_router.post("/rides/{ride_id}/decline")
async def decline_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
        
    # If assigned, unassign. If searching, just ignore/record decline.
    await db.rides.update_one(
        {'id': ride_id, 'driver_id': driver['id']},
        {'$set': {
            'driver_id': None,
            'status': 'searching', # returned to pool
            'updated_at': datetime.utcnow()
        }}
    )
    # Ideally trigger re-matching logic here
    
    return {'success': True}

@api_router.post("/rides/{ride_id}/arrive")
async def arrive_at_pickup(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')

    await db.rides.update_one(
        {'id': ride_id, 'driver_id': driver['id']},
        {'$set': {
            'status': 'driver_arrived',
            'driver_arrived_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )
    
    ride = await db.rides.find_one({'id': ride_id})
    if ride and ride.get('rider_id'):
        await manager.send_personal_message(
            {'type': 'driver_arrived', 'ride_id': ride_id},
            f"rider_{ride['rider_id']}"
        )
        
    return {'success': True}

@api_router.post("/rides/{ride_id}/verify-otp")
async def verify_pickup_otp(ride_id: str, request: RideOTPRequest, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
        
    ride = await db.rides.find_one({'id': ride_id, 'driver_id': driver['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')
        
    if ride.get('pickup_otp') != request.otp:
        raise HTTPException(status_code=400, detail='Invalid OTP')
        
    # OTP correct, start ride
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'status': 'in_progress',
            'ride_started_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )
    
    if ride.get('rider_id'):
        await manager.send_personal_message(
            {'type': 'ride_started', 'ride_id': ride_id},
            f"rider_{ride['rider_id']}"
        )
        
    return {'success': True}

@api_router.post("/rides/{ride_id}/start")
async def start_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Start ride without OTP (if configured) or fallback."""
    # Logic similar to verify_otp but without check
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
        
    await db.rides.update_one(
        {'id': ride_id, 'driver_id': driver['id']},
        {'$set': {
            'status': 'in_progress',
            'ride_started_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )
    
    ride = await db.rides.find_one({'id': ride_id})
    if ride and ride.get('rider_id'):
        await manager.send_personal_message(
            {'type': 'ride_started', 'ride_id': ride_id},
            f"rider_{ride['rider_id']}"
        )
    return {'success': True}

@api_router.post("/rides/{ride_id}/complete")
async def complete_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')
        
    # Calculate final fare if needed, or just use estimated
    # For now uses existing fare info
    
    await db.rides.update_one(
        {'id': ride_id, 'driver_id': driver['id']},
        {'$set': {
            'status': 'completed',
            'ride_completed_at': datetime.utcnow(),
            'payment_status': 'completed', # Mock payment success
            'updated_at': datetime.utcnow()
        }}
    )
    
    # Update driver stats
    await db.drivers.update_one(
        {'id': driver['id']},
        {
            '$inc': {'total_rides': 1},
            '$set': {'is_available': True} # Make driver available again
        }
    )
    
    ride = await db.rides.find_one({'id': ride_id})
    
    if ride and ride.get('rider_id'):
        await manager.send_personal_message(
            {
                'type': 'ride_completed', 
                'ride_id': ride_id,
                'total_fare': ride['total_fare']
            },
            f"rider_{ride['rider_id']}"
        )
        
    return serialize_doc(ride)

@api_router.post("/rides/{ride_id}/cancel")
async def cancel_ride(ride_id: str, reason: str = Query(""), current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')

    await db.rides.update_one(
        {'id': ride_id, 'driver_id': driver['id']},
        {'$set': {
            'status': 'cancelled',
            'cancelled_at': datetime.utcnow(),
            'cancellation_reason': reason,
            'updated_at': datetime.utcnow()
        }}
    )
    
    # Make driver available
    await db.drivers.update_one(
        {'id': driver['id']},
        {'$set': {'is_available': True}}
    )
    
    ride = await db.rides.find_one({'id': ride_id})
    if ride and ride.get('rider_id'):
        await manager.send_personal_message(
            {'type': 'ride_cancelled', 'ride_id': ride_id, 'reason': reason},
            f"rider_{ride['rider_id']}"
        )
        
    return {'success': True}

@api_router.post("/rides/{ride_id}/rate-rider")
async def rate_rider(ride_id: str, rating_data: RideRatingRequest, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')

    # Update ride with rating
    await db.rides.update_one(
        {'id': ride_id, 'driver_id': driver['id']},
        {'$set': {
            'rider_rating': rating_data.rating,
            'rider_comment': rating_data.comment,
            'updated_at': datetime.utcnow()
        }}
    )
    
    return {'success': True}
