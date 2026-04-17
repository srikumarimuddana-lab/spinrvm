"""
Fare-related HTTP routes.

Business logic lives in `services/fare_service.py`. This file should remain
thin: validate input, delegate, return.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query

try:
    from .. import db_supabase
    from ..geo_utils import get_service_area_polygon, point_in_polygon
except ImportError:
    import db_supabase
    from geo_utils import get_service_area_polygon, point_in_polygon

db = db_supabase  # legacy alias


def serialize_doc(doc):
    """Identity passthrough kept for legacy callers (Supabase dicts)."""
    return doc


def _fd(value) -> float:
    """Normalise *value* to a 2-decimal-place float.

    Historical reason: fare_configs may carry values as Decimal, str, int or
    float. Downstream Decimal arithmetic in rides.py expects clean 2-dp floats
    so rounding doesn't drift on IEEE-754 representations.
    """
    try:
        return float(f"{float(value):.2f}")
    except (TypeError, ValueError):
        return 0.0


api_router = APIRouter(tags=["Fares"])
logger = logging.getLogger(__name__)


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

    if not result:
        return _build_default_fares(vehicle_types, surge)

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
    """HTTP handler for /fares. For in-process callers that already have
    ``service_areas`` / ``vehicle_types``, use ``_fares_for_location_impl``
    directly."""
    return await _fares_for_location_impl(lat, lng)
