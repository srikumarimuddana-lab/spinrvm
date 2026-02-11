import os
import asyncio
from dotenv import load_dotenv
from pathlib import Path
from pprint import pprint

# Load env
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import sys
# sys.path.append('.') # Not needed if run as module

from backend.db_supabase import create_user, find_nearby_drivers, insert_ride, get_ride

async def main():
    print('Running smoke tests...')

    # 1. Create User
    import uuid
    user_id = str(uuid.uuid4())
    user = {
        'id': user_id,
        'phone': f'+1555{random_digits(7)}',
        'first_name': 'Smoke',
        'last_name': 'Test',
        'role': 'rider',
        # 'created_at': None # Let DB handle default
    }
    print(f"Creating user {user_id}...")
    try:
        created = await create_user(user)
        print('Created user:', created)
    except Exception as e:
        print('Create user failed:', e)

    # 2. Test RPC (might fail if SQL not run)
    print("\nTesting find_nearby_drivers RPC...")
    try:
        drivers = await find_nearby_drivers(52.1332, -106.6700, 5000)
        print(f"Found {len(drivers)} drivers nearby.")
    except Exception as e:
        print('RPC failed (Expected if SQL not run):', e)

def random_digits(n):
    import random
    return ''.join([str(random.randint(0, 9)) for _ in range(n)])

if __name__ == '__main__':
    asyncio.run(main())
