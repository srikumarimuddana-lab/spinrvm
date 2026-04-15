import asyncio

import db_supabase


async def list_users():
    print("Listing all users...")
    users = await db_supabase.get_rows("users", {}, limit=100)
    for user in users:
        print(f"ID: {user['id']}, Phone: {user.get('phone')}, Role: {user.get('role')}")


if __name__ == "__main__":
    asyncio.run(list_users())
