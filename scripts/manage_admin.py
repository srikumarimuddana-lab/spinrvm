import asyncio
import os
import sys
import argparse
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).parent.parent
sys.path.append(str(ROOT_DIR))

# Load .env manually if needed (before importing db)
from dotenv import load_dotenv
env_path = ROOT_DIR / '.env'
if env_path.exists():
    load_dotenv(env_path)
else:
    print(f"Warning: .env not found at {env_path}")

try:
    from backend.db import db
except ImportError:
    try:
        sys.path.append(str(ROOT_DIR / 'backend'))
        from db import db
    except ImportError as e:
        print(f"Error importing db: {e}")
        sys.exit(1)

async def check_user(phone):
    print(f"Checking user with phone: {phone}")
    print(f"Connecting to database with SUPABASE_URL: {os.environ.get('SUPABASE_URL')}")
    
    try:
        user = await db.users.find_one({'phone': phone})
        if not user:
            print(f"User not found for phone: {phone}")
            return
        
        print(f"User Found:")
        print(f"  ID: {user.get('id')}")
        print(f"  Phone: {user.get('phone')}")
        print(f"  Role: {user.get('role')}")
        print(f"  Is Admin? {'YES' if user.get('role') == 'admin' else 'NO'}")
    except Exception as e:
        print(f"Error checking user: {e}")

async def promote_user(phone):
    print(f"Promoting user with phone: {phone} to admin...")
    try:
        user = await db.users.find_one({'phone': phone})
        if not user:
            print(f"User not found for phone: {phone}")
            return
        
        if user.get('role') == 'admin':
            print("User is already an admin.")
            return

        result = await db.users.update_one(
            {'phone': phone},
            {'$set': {'role': 'admin'}}
        )
        
        print("Update successful!")
        # Verify
        updated_user = await db.users.find_one({'phone': phone})
        print(f"New Role: {updated_user.get('role')}")
        
    except Exception as e:
        print(f"Error promoting user: {e}")

async def main():
    parser = argparse.ArgumentParser(description='Manage Spinr Admin Users')
    parser.add_argument('phone', help='Phone number of the user')
    parser.add_argument('--check', action='store_true', help='Check user status')
    parser.add_argument('--promote', action='store_true', help='Promote user to admin')
    
    args = parser.parse_args()
    
    if args.check:
        await check_user(args.phone)
    elif args.promote:
        await promote_user(args.phone)
    else:
        # Default to check
        await check_user(args.phone)

if __name__ == '__main__':
    asyncio.run(main())
