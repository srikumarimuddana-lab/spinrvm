"""Favorite routes — save and reuse frequent pickup→dropoff routes.

Riders can save a completed ride as a favorite route for one-tap rebooking.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from ..db import db
    from ..dependencies import get_current_user
    from ..utils.address_verification import verify_address_matches_coordinate
except ImportError:
    from db import db
    from dependencies import get_current_user
    from utils.address_verification import verify_address_matches_coordinate

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/favorites", tags=["Favorite Routes"])


class SaveFavoriteRequest(BaseModel):
    name: str = Field(..., max_length=100)
    pickup_address: str
    pickup_lat: float = Field(..., ge=-90.0, le=90.0)
    pickup_lng: float = Field(..., ge=-180.0, le=180.0)
    dropoff_address: str
    dropoff_lat: float = Field(..., ge=-90.0, le=90.0)
    dropoff_lng: float = Field(..., ge=-180.0, le=180.0)
    vehicle_type_id: Optional[str] = None


@api_router.get("")
async def get_favorite_routes(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
):
    """Get user's saved favorite routes."""
    try:
        favorites = await db.get_rows(
            "favorite_routes",
            {"user_id": current_user["id"]},
            limit=limit,
            skip=skip,
            order="use_count",
        )
    except Exception as e:
        logger.error(f"Failed to fetch favorites: {e}")
        favorites = []
    return favorites


@api_router.post("")
async def save_favorite_route(req: SaveFavoriteRequest, current_user: dict = Depends(get_current_user)):
    """Save a route as a favorite for quick rebooking."""
    # Check for duplicate (same pickup + dropoff). B9: compares both lat AND
    # lng of both endpoints — the original only compared latitude, so two
    # routes sharing a latitude but with completely different longitudes
    # (e.g. opposite sides of the city) were wrongly treated as duplicates.
    try:
        existing = await db.get_rows(
            "favorite_routes",
            {"user_id": current_user["id"]},
            limit=20,
        )
        for fav in existing:
            if (
                abs(fav.get("pickup_lat", 0) - req.pickup_lat) < 0.001
                and abs(fav.get("pickup_lng", 0) - req.pickup_lng) < 0.001
                and abs(fav.get("dropoff_lat", 0) - req.dropoff_lat) < 0.001
                and abs(fav.get("dropoff_lng", 0) - req.dropoff_lng) < 0.001
            ):
                return fav  # Already saved
    except Exception as e:
        logger.debug(f"Duplicate check failed: {e}")

    # B9: same best-effort address<->coordinate check as POST /addresses —
    # closes the "poisoned ride laundered into a permanent favorite" gap,
    # since save_favorite_from_ride routes through this same function.
    for role, address, lat, lng in (
        ("pickup", req.pickup_address, req.pickup_lat, req.pickup_lng),
        ("dropoff", req.dropoff_address, req.dropoff_lat, req.dropoff_lng),
    ):
        ok, mismatch_reason, _place_id = await verify_address_matches_coordinate(address, lat, lng)
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=f"{role.capitalize()} address and location don't match: {mismatch_reason}",
            )

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
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.insert_one("favorite_routes", fav_data)
    return fav_data


@api_router.post("/{favorite_id}/use")
async def use_favorite_route(favorite_id: str, current_user: dict = Depends(get_current_user)):
    """Increment use count when rider books from a favorite. Returns the route data."""
    fav = await db.find_one("favorite_routes", {"id": favorite_id, "user_id": current_user["id"]})
    if not fav:
        raise HTTPException(status_code=404, detail="Favorite not found")

    updates = {
        "use_count": (fav.get("use_count", 0) or 0) + 1,
        "last_used_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.update_one("favorite_routes", {"id": favorite_id}, {"$set": updates})
    # Merge the write locally rather than re-fetching — the caller expects
    # the post-increment row, not the pre-increment one fetched above.
    return {**fav, **updates}


@api_router.delete("/{favorite_id}")
async def delete_favorite_route(favorite_id: str, current_user: dict = Depends(get_current_user)):
    """Remove a favorite route."""
    fav = await db.find_one("favorite_routes", {"id": favorite_id, "user_id": current_user["id"]})
    if not fav:
        raise HTTPException(status_code=404, detail="Favorite not found")

    await db.delete_one("favorite_routes", {"id": favorite_id})
    return {"success": True}


@api_router.post("/from-ride/{ride_id}")
async def save_favorite_from_ride(
    ride_id: str,
    name: str = Query("My Route"),
    current_user: dict = Depends(get_current_user),
):
    """Save a completed ride's route as a favorite."""
    ride = await db.find_one("rides", {"id": ride_id})
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
