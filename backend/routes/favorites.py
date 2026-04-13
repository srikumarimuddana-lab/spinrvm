"""Favorite routes — save and reuse frequent pickup→dropoff routes.

Riders can save a completed ride as a favorite route for one-tap rebooking.
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

try:
    from ..db import db
    from ..dependencies import get_current_user
except ImportError:
    from db import db
    from dependencies import get_current_user

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/favorites", tags=["Favorite Routes"])


class SaveFavoriteRequest(BaseModel):
    name: str
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str
    dropoff_lat: float
    dropoff_lng: float
    vehicle_type_id: Optional[str] = None


@api_router.get("")
async def get_favorite_routes(current_user: dict = Depends(get_current_user)):
    """Get user's saved favorite routes."""
    try:
        favorites = await db.get_rows(
            "favorite_routes",
            {"user_id": current_user["id"]},
            limit=20,
            order_by="use_count",
            order_desc=True,
        )
    except Exception as e:
        logger.error(f"Failed to fetch favorites: {e}")
        favorites = []
    return favorites


@api_router.post("")
async def save_favorite_route(req: SaveFavoriteRequest, current_user: dict = Depends(get_current_user)):
    """Save a route as a favorite for quick rebooking."""
    # Check for duplicate (same pickup + dropoff)
    try:
        existing = await db.get_rows(
            "favorite_routes",
            {"user_id": current_user["id"]},
            limit=20,
        )
        for fav in existing:
            if (abs(fav.get("pickup_lat", 0) - req.pickup_lat) < 0.001 and
                abs(fav.get("dropoff_lat", 0) - req.dropoff_lat) < 0.001):
                return fav  # Already saved
    except Exception:
        pass

    fav_data = {
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "name": req.name,
        "pickup_address": req.pickup_address,
        "pickup_lat": req.pickup_lat,
        "pickup_lng": req.pickup_lng,
        "dropoff_address": req.dropoff_address,
        "dropoff_lat": req.dropoff_lat,
        "dropoff_lng": req.dropoff_lng,
        "vehicle_type_id": req.vehicle_type_id,
        "use_count": 0,
        "created_at": datetime.utcnow().isoformat(),
    }
    await db.favorite_routes.insert_one(fav_data)
    return fav_data


@api_router.post("/{favorite_id}/use")
async def use_favorite_route(favorite_id: str, current_user: dict = Depends(get_current_user)):
    """Increment use count when rider books from a favorite. Returns the route data."""
    fav = await db.favorite_routes.find_one({"id": favorite_id, "user_id": current_user["id"]})
    if not fav:
        raise HTTPException(status_code=404, detail="Favorite not found")

    await db.favorite_routes.update_one(
        {"id": favorite_id},
        {"$set": {"use_count": (fav.get("use_count", 0) or 0) + 1, "last_used_at": datetime.utcnow().isoformat()}},
    )
    return fav


@api_router.delete("/{favorite_id}")
async def delete_favorite_route(favorite_id: str, current_user: dict = Depends(get_current_user)):
    """Remove a favorite route."""
    fav = await db.favorite_routes.find_one({"id": favorite_id, "user_id": current_user["id"]})
    if not fav:
        raise HTTPException(status_code=404, detail="Favorite not found")

    await db.favorite_routes.delete_one({"id": favorite_id})
    return {"success": True}


@api_router.post("/from-ride/{ride_id}")
async def save_favorite_from_ride(ride_id: str, name: str = Query("My Route"), current_user: dict = Depends(get_current_user)):
    """Save a completed ride's route as a favorite."""
    ride = await db.rides.find_one({"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    req = SaveFavoriteRequest(
        name=name,
        pickup_address=ride.get("pickup_address", ""),
        pickup_lat=ride.get("pickup_lat", 0),
        pickup_lng=ride.get("pickup_lng", 0),
        dropoff_address=ride.get("dropoff_address", ""),
        dropoff_lat=ride.get("dropoff_lat", 0),
        dropoff_lng=ride.get("dropoff_lng", 0),
        vehicle_type_id=ride.get("vehicle_type_id"),
    )
    return await save_favorite_route(req, current_user)
