"""Quick smoke test using Supabase service role key and our db_supabase helpers.

Make sure the following env vars are set in the environment before running:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Run:
  python tests_smoke_supabase.py
"""
import os
import asyncio
from pprint import pprint

os.environ.setdefault('SUPABASE_URL', 'https://dbbadhihiwztmnqnbdke.supabase.co')
# NOTE: The service role key will be read from env by supabase_client
# You can set it here for local runs if desired
# os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY', 'sb_secret_...')

import sys
sys.path.append('.')
from backend.db_supabase import create_user, find_available_drivers, insert_ride, get_ride, get_user_by_id

async def main():
    print('Running smoke tests (service-role client expected)')

    # Create a test user
    import uuid
    user_id = str(uuid.uuid4())
    user = {
        'id': user_id,
        'phone': '+10000000001',
        'first_name': 'Smoke',
        'last_name': 'Test',
        'role': 'rider',
        'created_at': None
    }
    try:
        created = await create_user(user)
        print('Created user:')
        pprint(created)
    except Exception as e:
        print('Create user failed:', e)

    import uuid
    ride_id = str(uuid.uuid4())
    vehicle_type_id = str(uuid.uuid4())

    # Query drivers (should be none)
    drivers = await find_available_drivers(vehicle_type_id)
    print('Available drivers (count):', len(drivers))

    # Insert and retrieve a ride
    ride = {
        'id': ride_id,
        'rider_id': user_id,
        'vehicle_type_id': vehicle_type_id,
        'pickup_address': '1 Test St',
        'pickup_lat': 0.0,
        'pickup_lng': 0.0,
        'dropoff_address': '2 Test Ave',
        'dropoff_lat': 0.1,
        'dropoff_lng': 0.1,
        'distance_km': 1.0,
        'duration_minutes': 5,
        'base_fare': 5.0,
        'total_fare': 6.0,
        'created_at': None
    }
    try:
        inserted = await insert_ride(ride)
        print('Inserted ride:')
        pprint(inserted)
        fetched = await get_ride(ride_id)
        print('Fetched ride:')
        pprint(fetched)
    except Exception as e:
        print('Insert/get ride failed:', e)

if __name__ == '__main__':
    asyncio.run(main())
