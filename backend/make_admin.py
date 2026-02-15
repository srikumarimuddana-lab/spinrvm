import asyncio
import os
from dotenv import load_dotenv

# Load .env BEFORE importing db
load_dotenv()

from db import db

async def make_admin():
    user_id = '71ba3eea-287f-41d8-8e48-9d794ea531e0'
    print(f"Updating user {user_id} to admin...")
    
    # Check if user exists
    user = await db.users.find_one({'id': user_id})
    if not user:
        print("User not found!")
        # Fallback: list all users to see if ID is different
        print("Listing available users:")
        all_users = await db.users.find({}).to_list(10)
        for u in all_users:
            print(f" - {u['id']} ({u.get('phone')})")
        return

    print(f"Current role: {user.get('role')}")
    
    # Update role
    result = await db.users.update_one(
        {'id': user_id},
        {'$set': {'role': 'admin'}}
    )
    
    print(f"Modified count: {result.modified_count}")
    
    # Verify
    user = await db.users.find_one({'id': user_id})
    print(f"New role: {user.get('role')}")

if __name__ == "__main__":
    asyncio.run(make_admin())
