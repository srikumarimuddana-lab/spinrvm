import json
import logging
import os
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Query

try:
    from .. import db_supabase
    from ..core.config import settings
    from ..geo_utils import get_service_area_polygon, point_in_polygon
    from ..utils.redis_client import redis_delete_pattern, redis_get, redis_set
except ImportError:
    import db_supabase
    from core.config import settings
    from geo_utils import get_service_area_polygon, point_in_polygon
    from utils.redis_client import redis_delete_pattern, redis_get, redis_set

db = db_supabase  # legacy alias
logger = logging.getLogger(__name__)
api_router = APIRouter(tags=["Fares"])

# PERF-001: Fare cache TTL in seconds (default 5 min)
_FARE_CACHE_TTL = int(os.environ.get("FARE_CACHE_TTL_SECONDS", "300"))

# ── Decimal helpers (CQ-009) ──────────────────────────────────────────
_TWO_PLACES = Decimal("0.01")


def _fd(v) -> float:
    """Round a raw DB float to 2 decimal places via Decimal to avoid float drift."""
    try:
        return float(Decimal(str(v)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    except (TypeError, ValueError, decimal.InvalidOperation):
        return 0.0

def serialize_doc(doc):
    """Identity passthrough kept for legacy callers (Supabase dicts)."""
    return doc


def _fare_cache_key(lat: float, lng: float) -> str:
    """~1.1 km grid cell key — rounds to 2 decimal places."""
    return f"fares:{round(lat, 2)}:{round(lng, 2)}"


async def invalidate_fare_cache() -> int:
    """Flush all fare cache entries. Call after any service-area or fare-config update."""
    deleted = await redis_delete_pattern("fares:*")
    if deleted:
        logger.info(f"Fare cache invalidated: {deleted} keys removed")
    return deleted

@api_router.get("/vehicle-types")
async def get_vehicle_types():
    types = await db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=100)
    return serialize_doc(types)


def _build_default_fares(vt_list, surge=1.0):
    """Default fare rows when no service area / fare_configs apply.

    Literal values go through ``_fd()`` so they are stored as exact 2-dp
    floats rather than raw IEEE-754 representations — keeps downstream
    Decimal arithmetic drift-free.
    """
    return [
        serialize_doc(
            {
                "vehicle_type": vt,
                "base_fare": _fd(3.50),
                "per_km_rate": _fd(1.50),
                "per_minute_rate": _fd(0.25),
                "minimum_fare": _fd(8.00),
                "booking_fee": _fd(2.00),
                "surge_multiplier": _fd(surge),
            }
        )
        for vt in vt_list
    ]


async def resolve_service_area_for_point(
    lat: float,
    lng: float,
    all_areas: Optional[list] = None,
):
    """Return the first active service area whose polygon contains (lat, lng), or None.

    ``all_areas`` may be passed by callers that already fetched the active
    service areas list to avoid a redundant round-trip. When omitted, this
    falls back to fetching the list itself.
    """
    if all_areas is None:
        all_areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=100)
    for area in all_areas:
        poly = get_service_area_polygon(area)
        if poly and point_in_polygon(lat, lng, poly):
            return area
    return None


async def build_fares_for_area(matched_area, vehicle_types):
    """Build the fare estimate list for an already-matched service area.

    Extracted from ``get_fares_for_location`` so callers that already
    resolved the area (e.g. ``create_ride``) can skip the second
    ``service_areas`` fetch. If ``matched_area`` is None, returns the
    default fares.
    """
    if not vehicle_types:
        return []

    if not matched_area:
        return _build_default_fares(vehicle_types)

    surge = matched_area.get("surge_multiplier", 1.0)

    fares = await db_supabase.get_rows(
        "fare_configs", {"service_area_id": matched_area["id"], "is_active": True}, limit=100
    )

    if not fares:
        return _build_default_fares(vehicle_types, surge)

    vt_map = {vt["id"]: serialize_doc(vt) for vt in vehicle_types}

    result = []
    for fare in fares:
        vt = vt_map.get(fare["vehicle_type_id"])
        if vt:
            # Normalise all monetary values from DB through _fd() so downstream
            # Decimal arithmetic in rides.py starts from clean 2-dp floats.
            result.append(
                {
                    "vehicle_type": vt,
                    "base_fare": _fd(fare["base_fare"]),
                    "per_km_rate": _fd(fare["per_km_rate"]),
                    "per_minute_rate": _fd(fare["per_minute_rate"]),
                    "minimum_fare": _fd(fare["minimum_fare"]),
                    "booking_fee": _fd(fare["booking_fee"]),
                    "surge_multiplier": _fd(surge),
                }
            )

    # If fare_configs exist but none matched vehicle types, fall back
    if not result:
        logger.info("Fares: fare_configs found but no matching vehicle types, using defaults")
        return _build_default_fares(vehicle_types, surge)

    logger.info(f"Fares: Returning {len(result)} fare estimates")
    return result


async def _fares_for_location_impl(
    lat: float,
    lng: float,
    all_areas: Optional[list] = None,
    vehicle_types: Optional[list] = None,
):
    """Shared implementation for /fares.

    Accepts optional pre-fetched ``all_areas`` / ``vehicle_types`` so
    callers that already loaded those lists (e.g. ``create_ride``) can
    skip redundant round-trips.
    """
    if vehicle_types is None:
        vehicle_types = await db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=100)
    logger.info(f"Fares: Found {len(vehicle_types)} active vehicle types")

    if not vehicle_types:
        logger.warning("Fares: No active vehicle types found in database!")
        return []

    matching_area = await resolve_service_area_for_point(lat, lng, all_areas=all_areas)
    if not matching_area:
        logger.info(f"Fares: No matching service area for ({lat}, {lng}), using defaults")
        return _build_default_fares(vehicle_types)

    logger.info(f"Fares: Matched service area '{matching_area.get('name', matching_area['id'])}'")
    return await build_fares_for_area(matching_area, vehicle_types)


@api_router.get("/fares")
async def get_fares_for_location(lat: float = Query(...), lng: float = Query(...)):
    """HTTP handler for /fares with Redis caching.

    Check cache first using a coordinates-based key (~1.1km grid).
    If missing, compute using _fares_for_location_impl and cache.
    """
    # PERF-001: Check Redis cache first
    cache_key = _fare_cache_key(lat, lng)
    try:
        cached = await redis_get(cache_key)
        if cached:
            logger.debug(f"Fare cache HIT for key {cache_key}")
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"Fare cache read error: {e}")

    # Compute fresh result
    result = await _fares_for_location_impl(lat, lng)

    # Cache the computed result
    try:
        ttl = settings.FARE_CACHE_TTL_SECONDS
        await redis_set(cache_key, json.dumps(result), ttl=ttl)
        logger.debug(f"Fare cache SET for key {cache_key} (TTL={ttl}s)")
    except Exception as e:
        logger.warning(f"Could not cache fare result: {e}")

    return result
