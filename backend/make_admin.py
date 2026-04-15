import asyncio

from dotenv import load_dotenv

# Load .env BEFORE importing db
load_dotenv()

import db_supabase  # noqa: E402


async def make_admin():
    user_id = "71ba3eea-287f-41d8-8e48-9d794ea531e0"
    print(f"Updating user {user_id} to admin...")

    # Check if user exists
    user = await db_supabase.get_user_by_id(user_id)
    if not user:
        print("User not found!")
        # Fallback: list all users to see if ID is different
        print("Listing available users:")
        all_users = await db_supabase.get_rows("users", {}, limit=10)
        for u in all_users:
            print(f" - {u['id']} ({u.get('phone')})")
        return

    print(f"Current role: {user.get('role')}")

    # Update role
    result = await db_supabase.update_one("users", {"id": user_id}, {"role": "admin"})

    print(f"Modified count: {result.modified_count}")

    # Verify
    user = await db_supabase.get_user_by_id(user_id)
    print(f"New role: {user.get('role')}")


if __name__ == "__main__":
    asyncio.run(make_admin())
