"""
FareService — pricing logic separated from the HTTP layer.

This is the reference implementation of the service-layer pattern
(see services/README.md). The route in `routes/fares.py` should now
be a thin wrapper that delegates here.

Pure helpers (`_fd`, `build_default_fares`) are static so they can be
tested without instantiating the service.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

try:
    from ..geo_utils import get_service_area_polygon, point_in_polygon
    from ..utils.surge_engine import SURGE_CAP
except ImportError:  # pragma: no cover - allow direct module imports in tests
    from geo_utils import get_service_area_polygon, point_in_polygon
    from utils.surge_engine import SURGE_CAP


_TWO_PLACES = Decimal("0.01")

# Default fare values used when no service-area-specific config exists.
# Centralized here so they're discoverable and changeable in one place.
DEFAULT_FARE = {
    "base_fare": 3.50,
    "per_km_rate": 1.50,
    "per_minute_rate": 0.25,
    "minimum_fare": 8.00,
    "booking_fee": 2.00,
}


def _d(v: Any) -> Decimal:
    """Convert any numeric value to Decimal without float precision loss."""
    return Decimal(str(v))


def _round(v: Decimal) -> Decimal:
    """Round a Decimal to 2 decimal places (ROUND_HALF_UP)."""
    return v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _f(v: Decimal) -> float:
    """Convert Decimal to float — only for use at the final JSON response boundary."""
    return float(v)


def _fd(v: Any) -> float:
    """Convert any numeric value to a 2-decimal-place float (backward-compat alias for tests)."""
    return float(_round(_d(v)))


def build_default_fares(vehicle_types: List[Dict[str, Any]], surge: Any = _d(1)) -> List[Dict[str, Any]]:
    """
    Build a default fare entry per vehicle type.

    Pure function — no DB or external calls. Useful when:
      - No matching service area is found
      - A service area exists but has no fare_configs

    Returns one entry per vehicle type in `vehicle_types`.
    """
    return [
        {
            "vehicle_type": vt,
            "base_fare": _round(_d(DEFAULT_FARE["base_fare"])),
            "per_km_rate": _round(_d(DEFAULT_FARE["per_km_rate"])),
            "per_minute_rate": _round(_d(DEFAULT_FARE["per_minute_rate"])),
            "minimum_fare": _round(_d(DEFAULT_FARE["minimum_fare"])),
            "booking_fee": _round(_d(DEFAULT_FARE["booking_fee"])),
            "surge_multiplier": _round(_d(surge)),
        }
        for vt in vehicle_types
    ]


def find_service_area_for_point(areas: List[Dict[str, Any]], lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """
    Return the first service area whose polygon contains (lat, lng), or None.

    Pure function — useful in tests with hand-constructed area dicts.
    """
    for area in areas:
        poly = get_service_area_polygon(area)
        if poly and point_in_polygon(lat, lng, poly):
            return area
    return None


def merge_fare_configs_with_vehicle_types(
    fare_configs: List[Dict[str, Any]],
    vehicle_types: List[Dict[str, Any]],
    surge: Decimal,
) -> List[Dict[str, Any]]:
    """
    Join fare_configs to vehicle_types and apply surge.

    Returns one entry per fare_config that matches a vehicle type.
    Pure function — no I/O.
    """
    vt_map = {vt["id"]: vt for vt in vehicle_types}
    result = []
    for fare in fare_configs:
        vt = vt_map.get(fare["vehicle_type_id"])
        if vt:
            result.append(
                {
                    "vehicle_type": vt,
                    "base_fare": _round(_d(fare["base_fare"])),
                    "per_km_rate": _round(_d(fare["per_km_rate"])),
                    "per_minute_rate": _round(_d(fare["per_minute_rate"])),
                    "minimum_fare": _round(_d(fare["minimum_fare"])),
                    "booking_fee": _round(_d(fare["booking_fee"])),
                    "surge_multiplier": _round(_d(surge)),
                }
            )
    return result


class FareService:
    """
    Pricing logic that depends on the database.

    Routes should instantiate this with the shared `db` and call methods on it.
    All public methods are async and return plain dicts/lists ready for JSON
    serialization.
    """

    def __init__(self, db):
        self.db = db

    async def list_active_vehicle_types(self) -> List[Dict[str, Any]]:
        """Fetch all active vehicle types."""
        return await self.db.get_rows("vehicle_types", {"is_active": True}, limit=100)

    async def fares_for_location(self, lat: float, lng: float) -> List[Dict[str, Any]]:
        """
        Compute the fare list for a given (lat, lng).

        Resolution order:
          1. If no active vehicle types: return []
          2. If no matching service area: return defaults
          3. If matching area but no fare_configs: return defaults with area surge
          4. If matching area + fare_configs: return merged result
          5. If merge yields nothing (vehicle types changed): return defaults with surge
        """
        vehicle_types = await self.list_active_vehicle_types()
        if not vehicle_types:
            return []

        all_areas = await self.db.get_rows("service_areas", {"is_active": True}, limit=100)
        matching_area = find_service_area_for_point(all_areas, lat, lng)

        if not matching_area:
            return build_default_fares(vehicle_types)

        surge = min(_d(matching_area.get("surge_multiplier", 1)), _d(SURGE_CAP))
        fare_configs = await self.db.get_rows(
            "fare_configs",
            {"service_area_id": matching_area["id"], "is_active": True},
            limit=100,
        )

        if not fare_configs:
            return build_default_fares(vehicle_types, surge)

        merged = merge_fare_configs_with_vehicle_types(fare_configs, vehicle_types, surge)
        if not merged:
            return build_default_fares(vehicle_types, surge)

        return merged
