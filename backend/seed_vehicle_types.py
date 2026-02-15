import asyncio
from dotenv import load_dotenv
import os
from pathlib import Path

# Load env before importing db to ensure client is initialized
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(env_path)

from db import db
from datetime import datetime
import uuid

async def seed_vehicle_types():
    print("Seeding vehicle types...")
    
    vehicle_types = [
        {
            "id": str(uuid.uuid4()),
            "name": "Economy",
            "description": "Affordable rides for everyday use",
            "icon": "car-compact",
            "capacity": 4,
            "is_active": True,
            "created_at": datetime.utcnow().isoformat()
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Premium",
            "description": "High-end cars for style and comfort",
            "icon": "car-sport",
            "capacity": 4,
            "is_active": True,
            "created_at": datetime.utcnow().isoformat()
        },
        {
            "id": str(uuid.uuid4()),
            "name": "Van",
            "description": "Spacious rides for groups and luggage",
            "icon": "bus",
            "capacity": 6,
            "is_active": True,
            "created_at": datetime.utcnow().isoformat()
        },
        {
            "id": str(uuid.uuid4()),
            "name": "XL",
            "description": "Extra large vehicles for bigger groups",
            "icon": "bus-outline",
            "capacity": 6,
            "is_active": True,
            "created_at": datetime.utcnow().isoformat()
        }
    ]

    # Check for existing types to avoid duplicates
    existing_count = await db.vehicle_types.count_documents({})
    if existing_count > 0:
        print(f"Database already has {existing_count} vehicle types. Skipping seed.")
        return

    result = await db.vehicle_types.insert_many(vehicle_types)
    print(f"Successfully inserted {len(result.inserted_ids)} vehicle types.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(seed_vehicle_types())
