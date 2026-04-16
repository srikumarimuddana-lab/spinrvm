"""
Fare-related HTTP routes.

Business logic lives in `services/fare_service.py`. This file should remain
thin: validate input, delegate, return.
"""

import logging

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


@api_router.get("/fares")
async def get_fares_for_location(lat: float = Query(...), lng: float = Query(...)):
    import logging

    logger = logging.getLogger(__name__)

    # Fetch all active vehicle types (needed for both paths)
    vehicle_types = await db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=100)
    logger.info(f"Fares: Found {len(vehicle_types)} active vehicle types")

    if not vehicle_types:
        logger.warning("Fares: No active vehicle types found in database!")
        return []

    # Default fares function (used when no service area or no fare_configs).
    # Literal values are passed through _fd() so they are stored as exact
    # 2-dp floats rather than raw IEEE 754 representations.
    def build_default_fares(vt_list, surge=1.0):
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

    # Try to find matching service area
    all_areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=100)
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
    surge = matching_area.get("surge_multiplier", 1.0)

    # Try to get fare_configs for this service area
    fares = await db_supabase.get_rows(
        "fare_configs", {"service_area_id": matching_area["id"], "is_active": True}, limit=100
    )

    if not fares:
        # No fare configs for this area — fall back to defaults with area surge
        logger.info(f"Fares: No fare_configs for area, using defaults with surge={surge}")
        return build_default_fares(vehicle_types, surge)

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
        return build_default_fares(vehicle_types, surge)

    logger.info(f"Fares: Returning {len(result)} fare estimates")
    return result
