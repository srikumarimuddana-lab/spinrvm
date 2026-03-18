from fastapi import APIRouter, Depends, HTTPException, Query, Body
from typing import Optional, List, Union, Dict, Any
try:
    from ..dependencies import get_current_user, get_admin_user
    from ..schemas import Driver, Ride, RideRatingRequest
    from ..db import db
    from ..socket_manager import manager
    from ..features import send_push_notification
except ImportError:
    from dependencies import get_current_user, get_admin_user
    from schemas import Driver, Ride, RideRatingRequest
    from db import db
    from socket_manager import manager
    from features import send_push_notification
from datetime import datetime, timedelta
import json
import logging
import os
import stripe
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
        'stripe_account_onboarded': bool(driver.get('stripe_account_onboarded', False)),
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

@api_router.get("/nearby")
async def get_nearby_drivers_public(
    lat: float = Query(...),
    lng: float = Query(...),
    radius: float = Query(5.0),
    vehicle_type: str = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Get nearby active drivers for riders."""
    # Simplified geospatial logic returning online drivers
    # In production use PostGIS or geospatial index
    query = {'is_online': True, 'is_available': True}
    if vehicle_type:
        query['vehicle_type_id'] = vehicle_type
        
    drivers = await db.drivers.find(query).to_list(100)
    
    # Optional manual filtering by distance
    try:
        from ..utils import calculate_distance
    except ImportError:
        from utils import calculate_distance
    nearby = []
    for d in drivers:
        d_lat = d.get('lat')
        d_lng = d.get('lng')
        if d_lat and d_lng:
            dist = calculate_distance(lat, lng, d_lat, d_lng)
            if dist <= radius:
                # hide personal info for riders
                safe_driver = {
                    'id': d['id'],
                    'lat': d_lat,
                    'lng': d_lng,
                    'vehicle_type_id': d.get('vehicle_type_id'),
                    'vehicle_make': d.get('vehicle_make'),
                    'vehicle_model': d.get('vehicle_model')
                }
                nearby.append(safe_driver)
                
    return nearby

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

    # GAP FIX: Check driver document expiry before allowing online
    if is_online:
        now = datetime.utcnow()
        expiry_checks = [
            ('license_expiry_date', 'Driving license'),
            ('insurance_expiry_date', 'Vehicle insurance'),
            ('vehicle_inspection_expiry_date', 'Vehicle inspection'),
            ('background_check_expiry_date', 'Background check'),
        ]
        for field, label in expiry_checks:
            expiry_val = driver.get(field)
            if expiry_val:
                if isinstance(expiry_val, str):
                    try:
                        expiry_val = datetime.fromisoformat(expiry_val.replace('Z', '+00:00').replace('+00:00', ''))
                    except ValueError:
                        continue
                if expiry_val < now:
                    raise HTTPException(
                        status_code=400,
                        detail=f'{label} has expired ({field}). Please update your documents before going online.'
                    )

        # Check if driver is verified
        if not driver.get('is_verified', False):
            raise HTTPException(
                status_code=400,
                detail='Your driver profile has not been verified yet. Please wait for admin approval.'
            )

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
        
    if driver.get('stripe_account_onboarded'):
        return {'has_bank_account': True, 'bank_account': {'bank_name': 'Stripe Connect', 'account_number_last4': '****'}}
        
    return {'has_bank_account': False, 'bank_account': None}

@api_router.post("/stripe-onboard")
async def onboard_stripe(current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user.get('id')})
    user = await db.users.find_one({'id': current_user.get('id')})
    if not driver or not user:
        raise HTTPException(status_code=404, detail="Driver/User profile not found")
        
    from ..settings_loader import get_app_settings
    settings = await get_app_settings()
    stripe_secret = settings.get('stripe_secret_key', '')
    
    if not stripe_secret:
        return {'url': 'https://spinr-demo-onboard.com', 'mock': True}
        
    try:
        stripe.api_key = stripe_secret
        account_id = driver.get('stripe_account_id')
        
        if not account_id:
            account = stripe.Account.create(
                type='express',
                country='CA',
                email=user.get('email'),
                capabilities={
                    'transfers': {'requested': True},
                },
                business_type='individual'
            )
            account_id = account.id
            await db.drivers.update_one({'id': driver['id']}, {'$set': {'stripe_account_id': account_id}})
            
        account_link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=f"{settings.get('base_url', 'http://localhost:8000')}/api/drivers/stripe-refresh",
            return_url=f"{settings.get('base_url', 'http://localhost:8000')}/api/drivers/stripe-return",
            type='account_onboarding',
        )
        # Mark as onboarded optimistically or handle via webhook/return_url properly in production
        await db.drivers.update_one({'id': driver['id']}, {'$set': {'stripe_account_onboarded': True}})
        
        return {'url': account_link.url, 'mock': False}
    except Exception as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        
    stripe_account_id = driver.get('stripe_account_id')
    account = await db.bank_accounts.find_one({'driver_id': driver['id']})
    
    if not stripe_account_id and not account:
        raise HTTPException(status_code=400, detail="No bank account linked")
    
    from ..settings_loader import get_app_settings
    settings = await get_app_settings()
    stripe_secret = settings.get('stripe_secret_key', '')
    
    status = 'pending'
    stripe_payout_id = None
    
    if stripe_secret and stripe_account_id:
        try:
            stripe.api_key = stripe_secret
            transfer = stripe.Transfer.create(
                amount=int(req.amount * 100),
                currency='cad',
                destination=stripe_account_id,
            )
            status = 'completed'
            stripe_payout_id = transfer.id
        except Exception as e:
            logger.error(f"Stripe transfer failed: {e}")
            raise HTTPException(status_code=500, detail=f"Payout failed: {str(e)}")
            
    payout = {
        'id': str(uuid.uuid4()),
        'driver_id': driver['id'],
        'amount': req.amount,
        'status': status,
        'stripe_payout_id': stripe_payout_id,
        'bank_name': account.get('bank_name') if account else 'Stripe Connect',
        'account_last4': account.get('account_number_last4') if account else '****',
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
        await send_push_notification(
            ride['rider_id'],
            "Driver Assigned! 🚗",
            "Your driver has accepted the ride and is on the way."
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
            'status': 'searching',  # returned to pool
            'updated_at': datetime.utcnow()
        }}
    )

    # GAP FIX: Re-match to find the next available driver
    try:
        from .rides import match_driver_to_ride
        import asyncio
        asyncio.create_task(match_driver_to_ride(ride_id))
        logger.info(f"Re-matching ride {ride_id} after driver {driver['id']} declined")
    except Exception as e:
        logger.warning(f"Could not trigger re-matching for ride {ride_id}: {e}")
    
    return {'success': True}

@api_router.post("/rides/{ride_id}/arrive")
async def arrive_at_pickup(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')

    ride = await db.rides.find_one({'id': ride_id, 'driver_id': driver['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')

    # GAP FIX: Geofence check - verify driver is within 200m of pickup location
    ARRIVAL_RADIUS_KM = 0.2  # 200 meters
    try:
        from ..utils import calculate_distance
    except ImportError:
        from utils import calculate_distance

    driver_lat = driver.get('lat', 0)
    driver_lng = driver.get('lng', 0)
    pickup_lat = ride.get('pickup_lat', 0)
    pickup_lng = ride.get('pickup_lng', 0)

    if driver_lat and driver_lng and pickup_lat and pickup_lng:
        distance_to_pickup = calculate_distance(driver_lat, driver_lng, pickup_lat, pickup_lng)
        if distance_to_pickup > ARRIVAL_RADIUS_KM:
            raise HTTPException(
                status_code=400,
                detail=f'You are {distance_to_pickup:.0f}km away from the pickup. '
                       f'Please move within 200m of the pickup location to mark arrival.'
            )

    await db.rides.update_one(
        {'id': ride_id, 'driver_id': driver['id']},
        {'$set': {
            'status': 'driver_arrived',
            'driver_arrived_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }}
    )
    
    if ride.get('rider_id'):
        await manager.send_personal_message(
            {'type': 'driver_arrived', 'ride_id': ride_id},
            f"rider_{ride['rider_id']}"
        )
        await send_push_notification(
            ride['rider_id'],
            "Driver Arrived! 📍",
            "Your driver has arrived at the pickup location."
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
        await send_push_notification(
            ride['rider_id'],
            "Ride Started! ▶️",
            "Your ride has started. Have a safe trip!"
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
        await send_push_notification(
            ride['rider_id'],
            "Ride Started! ▶️",
            "Your ride has started. Have a safe trip!"
        )
    return {'success': True}

@api_router.post("/rides/{ride_id}/complete")
async def complete_ride(ride_id: str, current_user: dict = Depends(get_current_user)):
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail='Driver not found')

    ride = await db.rides.find_one({'id': ride_id, 'driver_id': driver['id']})
    if not ride:
        raise HTTPException(status_code=404, detail='Ride not found')

    # GAP FIX: Recalculate fare based on actual GPS distance from location history
    actual_distance_km = ride.get('distance_km', 0)
    try:
        from ..utils import calculate_distance
    except ImportError:
        from utils import calculate_distance

    try:
        breadcrumbs = await db.driver_location_history.find({
            'ride_id': ride_id,
            'tracking_phase': 'trip_in_progress'
        }).to_list(10000)

        if breadcrumbs and len(breadcrumbs) >= 2:
            # Sort by timestamp
            breadcrumbs.sort(key=lambda b: str(b.get('timestamp', '')))
            total_dist = 0.0
            for i in range(1, len(breadcrumbs)):
                prev = breadcrumbs[i - 1]
                curr = breadcrumbs[i]
                if prev.get('lat') and prev.get('lng') and curr.get('lat') and curr.get('lng'):
                    total_dist += calculate_distance(
                        prev['lat'], prev['lng'], curr['lat'], curr['lng']
                    )
            if total_dist > 0:
                actual_distance_km = round(total_dist, 2)
                logger.info(f"Ride {ride_id}: Recalculated distance = {actual_distance_km}km (estimated was {ride.get('distance_km', 0)}km)")
    except Exception as e:
        logger.warning(f"Could not recalculate distance for ride {ride_id}: {e}")

    # Recalculate fare if actual distance differs
    update_fields = {
        'status': 'completed',
        'ride_completed_at': datetime.utcnow(),
        'payment_status': 'completed',
        'updated_at': datetime.utcnow()
    }
    
    if actual_distance_km != ride.get('distance_km', 0):
        update_fields['distance_km'] = actual_distance_km
        # Logic to recalculate final fare would go here if needed.
        # Assuming the final fare remains what was agreed initially unless surge/etc changes
        # For this gap fix, we just record the actual distance for audit.
        
    await db.rides.update_one(
        {'id': ride_id},
        {'$set': update_fields}
    )

    # GAP FIX: Post-ride receipt (email/in-app)
    # Stub: Send email receipt to rider
    rider = await db.users.find_one({'id': ride.get('rider_id')})
    if rider and rider.get('email'):
        logger.info(f"Sending email receipt for ride {ride_id} to {rider['email']}")
        # In a real implementation: send_email(to=rider['email'], template="ride_receipt", data=ride)


    if actual_distance_km != ride.get('distance_km', 0) and actual_distance_km > 0:
        # Recalculate using per_km_rate
        per_km_rate = ride.get('distance_fare', 0) / ride.get('distance_km', 1) if ride.get('distance_km', 0) > 0 else 0
        new_distance_fare = round(per_km_rate * actual_distance_km, 2)
        new_total_fare = round(
            ride.get('base_fare', 0) + new_distance_fare + ride.get('time_fare', 0) + ride.get('booking_fee', 0),
            2
        )
        new_driver_earnings = round(
            ride.get('base_fare', 0) + new_distance_fare + ride.get('time_fare', 0),
            2
        )
        update_fields.update({
            'actual_distance_km': actual_distance_km,
            'distance_fare': new_distance_fare,
            'total_fare': new_total_fare,
            'driver_earnings': new_driver_earnings,
        })

    await db.rides.update_one(
        {'id': ride_id, 'driver_id': driver['id']},
        {'$set': update_fields}
    )
    
    # Update driver stats
    await db.drivers.update_one(
        {'id': driver['id']},
        {
            '$inc': {'total_rides': 1},
            '$set': {'is_available': True}
        }
    )
    
    completed_ride = await db.rides.find_one({'id': ride_id})
    
    if completed_ride and completed_ride.get('rider_id'):
        await manager.send_personal_message(
            {
                'type': 'ride_completed', 
                'ride_id': ride_id,
                'total_fare': completed_ride.get('total_fare', ride.get('total_fare', 0))
            },
            f"rider_{completed_ride['rider_id']}"
        )
        await send_push_notification(
            completed_ride['rider_id'],
            "Ride Completed! ✅",
            f"Your ride has finished. Total fare: ${completed_ride.get('total_fare', ride.get('total_fare', 0))}"
        )
        
    return serialize_doc(completed_ride)

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
            'cancelled_by': 'driver',
            'updated_at': datetime.utcnow()
        }}
    )
    
    # Make driver available
    await db.drivers.update_one(
        {'id': driver['id']},
        {'$set': {'is_available': True}}
    )

    # GAP FIX: Track driver cancellation frequency — auto-offline after 3 cancels in 1 hour
    try:
        one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        cancel_cursor = db.rides.find({
            'driver_id': driver['id'],
            'cancelled_by': 'driver',
            'cancelled_at': {'$gte': one_hour_ago}
        })
        recent_cancels = await cancel_cursor.to_list(length=100) if hasattr(cancel_cursor, 'to_list') else list(cancel_cursor)
        cancel_count = len(recent_cancels)

        MAX_CANCELS_PER_HOUR = 3
        if cancel_count >= MAX_CANCELS_PER_HOUR:
            await db.drivers.update_one(
                {'id': driver['id']},
                {'$set': {'is_online': False, 'is_available': False}}
            )
            logger.warning(
                f"Driver {driver['id']} auto-set offline after {cancel_count} cancellations in 1 hour"
            )
            # Notify the driver
            if driver.get('user_id'):
                await manager.send_personal_message(
                    {
                        'type': 'auto_offline',
                        'reason': f'You have been set offline due to {cancel_count} ride cancellations in the past hour. '
                                  f'Please take a break and try again later.'
                    },
                    f"driver_{driver['user_id']}"
                )
    except Exception as e:
        logger.warning(f"Could not check cancellation frequency for driver {driver['id']}: {e}")
    
    ride = await db.rides.find_one({'id': ride_id})
    if ride and ride.get('rider_id'):
        await manager.send_personal_message(
            {'type': 'ride_cancelled', 'ride_id': ride_id, 'reason': reason},
            f"rider_{ride['rider_id']}"
        )
        await send_push_notification(
            ride['rider_id'],
            "Ride Cancelled ❌",
            f"Your driver has cancelled the ride."
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
