from fastapi import APIRouter, Query, Depends
import json
import logging
import os
try:
    from ..db import db
    from ..geo_utils import point_in_polygon, get_service_area_polygon
    from ..dependencies import get_current_user
    from ..utils.redis_client import redis_get, redis_set, redis_delete_pattern
except ImportError:
    from db import db
    from geo_utils import point_in_polygon, get_service_area_polygon
    from dependencies import get_current_user
    from utils.redis_client import redis_get, redis_set, redis_delete_pattern

logger = logging.getLogger(__name__)
api_router = APIRouter(tags=["Fares"])

# PERF-001: Fare cache TTL in seconds (default 5 min)
_FARE_CACHE_TTL = int(os.environ.get('FARE_CACHE_TTL_SECONDS', '300'))

def serialize_doc(doc):
    return doc

def _fare_cache_key(lat: float, lng: float) -> str:
    """~1.1 km grid cell key — rounds to 2 decimal places."""
    return f'fares:{round(lat, 2)}:{round(lng, 2)}'

async def invalidate_fare_cache() -> int:
    """Flush all fare cache entries. Call after any service-area or fare-config update."""
    deleted = await redis_delete_pattern('fares:*')
    if deleted:
        logger.info(f'Fare cache invalidated: {deleted} keys removed')
    return deleted

@api_router.get("/vehicle-types")
async def get_vehicle_types():
    types = await db.vehicle_types.find({'is_active': True}).to_list(100)
    return serialize_doc(types)

@api_router.get("/fares")
async def get_fares_for_location(lat: float = Query(...), lng: float = Query(...)):
    # PERF-001: Check Redis cache first
    cache_key = _fare_cache_key(lat, lng)
    cached = await redis_get(cache_key)
    if cached:
        logger.debug(f'Fare cache HIT for key {cache_key}')
        return json.loads(cached)

    result = await _compute_fares_for_location(lat, lng)

    # Cache the computed result
    try:
        await redis_set(cache_key, json.dumps(result), ttl=_FARE_CACHE_TTL)
        logger.debug(f'Fare cache SET for key {cache_key} (TTL={_FARE_CACHE_TTL}s)')
    except Exception as e:
        logger.warning(f'Could not cache fare result: {e}')

    return result


async def _compute_fares_for_location(lat: float, lng: float) -> list:
    """Inner computation — separated so cache logic stays clean."""
    # Fetch all active vehicle types (needed for both paths)
    vehicle_types = await db.vehicle_types.find({'is_active': True}).to_list(100)
    logger.info(f"Fares: Found {len(vehicle_types)} active vehicle types")

    if not vehicle_types:
        logger.warning("Fares: No active vehicle types found in database!")
        return []

    # Default fares function (used when no service area or no fare_configs)
    def build_default_fares(vt_list, surge=1.0):
        return [serialize_doc({
            'vehicle_type': vt,
            'base_fare': 3.50,
            'per_km_rate': 1.50,
            'per_minute_rate': 0.25,
            'minimum_fare': 8.00,
            'booking_fee': 2.00,
            'surge_multiplier': surge
        }) for vt in vt_list]

    # Try to find matching service area
    all_areas = await db.service_areas.find({'is_active': True}).to_list(100)
    matching_area = None
    for area in all_areas:
        poly = get_service_area_polygon(area)
        if poly and point_in_polygon(lat, lng, poly):
            matching_area = area
            break

    if not matching_area:
        logger.info(f"Fares: No matching service area for ({lat}, {lng}), using defaults")
        return build_default_fares(vehicle_types)

    logger.info(f"Fares: Matched service area '{matching_area.get('name', matching_area['id'])}'")
    surge = matching_area.get('surge_multiplier', 1.0)

    # Try to get fare_configs for this service area
    fares = await db.fare_configs.find({
        'service_area_id': matching_area['id'],
        'is_active': True
    }).to_list(100)

    if not fares:
        # No fare configs for this area — fall back to defaults with area surge
        logger.info(f"Fares: No fare_configs for area, using defaults with surge={surge}")
        return build_default_fares(vehicle_types, surge)

    vt_map = {vt['id']: serialize_doc(vt) for vt in vehicle_types}

    result = []
    for fare in fares:
        vt = vt_map.get(fare['vehicle_type_id'])
        if vt:
            result.append({
                'vehicle_type': vt,
                'base_fare': fare['base_fare'],
                'per_km_rate': fare['per_km_rate'],
                'per_minute_rate': fare['per_minute_rate'],
                'minimum_fare': fare['minimum_fare'],
                'booking_fee': fare['booking_fee'],
                'surge_multiplier': surge
            })

    # If fare_configs exist but none matched vehicle types, fall back
    if not result:
        logger.info("Fares: fare_configs found but no matching vehicle types, using defaults")
        return build_default_fares(vehicle_types, surge)

    logger.info(f"Fares: Returning {len(result)} fare estimates")
    return result
