"""
Fare-related HTTP routes.

Business logic lives in `services/fare_service.py`. This file should remain
thin: validate input, delegate, return.
"""

import logging

from fastapi import APIRouter, Query

try:
    from ..db import db
    from ..services import FareService
except ImportError:
    from db import db
    from services import FareService

api_router = APIRouter(tags=["Fares"])
logger = logging.getLogger(__name__)


@api_router.get("/vehicle-types")
async def get_vehicle_types():
    return await FareService(db).list_active_vehicle_types()


@api_router.get("/fares")
async def get_fares_for_location(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    fares = await FareService(db).fares_for_location(lat, lng)
    logger.info(f"Fares: Returning {len(fares)} entries for ({lat}, {lng})")
    return fares
