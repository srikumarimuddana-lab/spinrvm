import decimal
import json
import logging
import os
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from fastapi import APIRouter, Query

try:
    from .. import db_supabase
    from ..geo_utils import get_service_area_polygon, point_in_polygon
    from ..services.fare_service import DEFAULT_FARE
    from ..utils.redis_client import redis_delete_pattern, redis_get, redis_set
    from ..utils.surge_engine import SURGE_CAP
except ImportError:
    import db_supabase
    from geo_utils import get_service_area_polygon, point_in_polygon
    from services.fare_service import DEFAULT_FARE
    from utils.redis_client import redis_delete_pattern, redis_get, redis_set
    from utils.surge_engine import SURGE_CAP

db = db_supabase  # legacy alias
logger = logging.getLogger(__name__)
api_router = APIRouter(tags=["Fares"])

# PERF-001: Fare cache TTL in seconds (default 5 min).
# WARNING: cached fares embed the surge multiplier at cache-fill time.
# If surge changes within the TTL window the rider sees a stale estimate;
# payments.py re-validates the fare at settlement and will reject a mismatch.
# When surge is active the effective TTL is capped at 60 s to reduce staleness.
# Set FARE_CACHE_TTL_SECONDS=60 in production to tighten the window during
# surge events without fully disabling the cache.
_FARE_CACHE_TTL = int(os.environ.get("FARE_CACHE_TTL_SECONDS", "300"))
if _FARE_CACHE_TTL <= 0:
    logger.warning(
        "FARE_CACHE_TTL_SECONDS is %d — fare caching is effectively disabled",
        _FARE_CACHE_TTL,
    )

# ── Decimal helpers (CQ-009) ──────────────────────────────────────────
_TWO_PLACES = Decimal("0.01")


def _fd(v) -> float:
    """Round a raw DB float to 2 decimal places via Decimal to avoid float drift."""
    try:
        return float(Decimal(str(v)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    except (TypeError, ValueError, decimal.InvalidOperation):
        return 0.0


def _money_str(v) -> str:
    """Serialise a money value as an exact 2-dp Decimal string (never float)."""
    try:
        return str(Decimal(str(v)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    except (TypeError, ValueError, decimal.InvalidOperation):
        return "0.00"


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
    # Rider/driver apps key the car illustration off `image_url`, but the DB
    # column added in migration 83 is `illustration_url`. Surface both keys
    # so neither side has to care which name "won".
    for vt in types or []:
        if isinstance(vt, dict) and vt.get("illustration_url") and not vt.get("image_url"):
            vt["image_url"] = vt["illustration_url"]
    return serialize_doc(types)


@api_router.get("/service-areas")
async def get_public_service_areas():
    """Active service areas (public).

    Driver onboarding (become-driver, profile-setup) and the rider app both
    need a list of operating regions without admin auth. Returns only
    active areas with the minimum fields the UI needs; admins use
    /admin/service-areas for the full record including surge config.
    """
    areas = await db_supabase.get_rows("service_areas", {"is_active": True}, order="name", limit=500)
    return [
        {
            "id": a.get("id"),
            "name": a.get("name"),
            "city": a.get("city"),
            "province": a.get("province"),
            "country": a.get("country"),
            "parent_service_area_id": a.get("parent_service_area_id"),
        }
        for a in areas
    ]


def _build_default_fares(vt_list, surge=1.0):
    """Default fare rows when no service area / fare_configs apply.

    Values sourced from fare_service.DEFAULT_FARE so there is exactly one
    place to update platform defaults.
    """
    return [
        serialize_doc(
            {
                "vehicle_type": vt,
                "base_fare": _money_str(DEFAULT_FARE["base_fare"]),
                "per_km_rate": _money_str(DEFAULT_FARE["per_km_rate"]),
                "per_minute_rate": _money_str(DEFAULT_FARE["per_minute_rate"]),
                "minimum_fare": _money_str(DEFAULT_FARE["minimum_fare"]),
                "booking_fee": _money_str(DEFAULT_FARE["booking_fee"]),
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

    Source-of-truth priority for base/per-km/per-min/min/booking fee:

      1. ``service_areas.vehicle_pricing`` JSONB  — what the Service
         Areas admin editor writes to (keyed by vehicle type NAME).
         This is the canonical per-area override operators configure.
      2. ``fare_configs`` table                   — legacy rows keyed
         by vehicle_type_id. Used when the area has no vehicle_pricing
         entry for a given vehicle type.
      3. _build_default_fares()                   — platform defaults
         when neither source has anything.

    All three paths merge into the same output shape downstream code
    (rides.py fare calc, rider-app estimates) already expects.
    Extracted from ``get_fares_for_location`` so callers that already
    resolved the area (e.g. ``create_ride``) can skip the second
    ``service_areas`` fetch. ``matched_area=None`` → defaults.
    """
    if not vehicle_types:
        return []

    if not matched_area:
        return _build_default_fares(vehicle_types)

    # Surge is gated on surge_active so an operator can park a
    # multiplier > 1.0 without having it silently applied when the
    # toggle is off. Matches surge_engine.py's convention (only
    # writes multiplier when active) and the admin UI toggle under
    # Service Areas → General.
    surge = (
        min(matched_area.get("surge_multiplier", 1.0), SURGE_CAP) if matched_area.get("surge_active", False) else 1.0
    )

    # Name → pricing row from vehicle_pricing JSONB (source of truth).
    # Field names: vehicle_type (NAME), base_fare, per_km, per_min,
    # min_fare, booking_fee.
    vp_raw = matched_area.get("vehicle_pricing") or []
    vp_by_name: dict = {}
    if isinstance(vp_raw, list):
        for row in vp_raw:
            if isinstance(row, dict) and row.get("vehicle_type"):
                vp_by_name[row["vehicle_type"]] = row

    # fare_configs fallback, indexed by vehicle_type_id.
    fc_rows = await db_supabase.get_rows(
        "fare_configs",
        {"service_area_id": matched_area["id"], "is_active": True},
        limit=100,
    )
    fc_by_vt_id: dict = {r.get("vehicle_type_id"): r for r in (fc_rows or []) if r.get("vehicle_type_id")}

    def _pick(vt):
        """Return a pricing dict for this vehicle type or None."""
        # Prefer JSONB-by-name (canonical admin-editor source).
        vp = vp_by_name.get(vt.get("name"))
        if vp:
            return {
                "base_fare": vp.get("base_fare", 0),
                "per_km_rate": vp.get("per_km", vp.get("per_km_rate", 0)),
                "per_minute_rate": vp.get("per_min", vp.get("per_minute_rate", 0)),
                "minimum_fare": vp.get("min_fare", vp.get("minimum_fare", 0)),
                "booking_fee": vp.get("booking_fee", 0),
            }
        # Fall back to fare_configs legacy row.
        fc = fc_by_vt_id.get(vt.get("id"))
        if fc:
            return {
                "base_fare": fc.get("base_fare", 0),
                "per_km_rate": fc.get("per_km_rate", 0),
                "per_minute_rate": fc.get("per_minute_rate", 0),
                "minimum_fare": fc.get("minimum_fare", 0),
                "booking_fee": fc.get("booking_fee", 0),
            }
        return None

    result = []
    for vt in vehicle_types:
        pricing = _pick(vt) or {
            "base_fare": DEFAULT_FARE["base_fare"],
            "per_km_rate": DEFAULT_FARE["per_km_rate"],
            "per_minute_rate": DEFAULT_FARE["per_minute_rate"],
            "minimum_fare": DEFAULT_FARE["minimum_fare"],
            "booking_fee": DEFAULT_FARE["booking_fee"],
        }
        # Normalise all monetary values from DB through _money_str() so they
        # serialise as exact Decimal strings; surge_multiplier stays float.
        result.append(
            {
                "vehicle_type": serialize_doc(vt),
                "base_fare": _money_str(pricing["base_fare"]),
                "per_km_rate": _money_str(pricing["per_km_rate"]),
                "per_minute_rate": _money_str(pricing["per_minute_rate"]),
                "minimum_fare": _money_str(pricing["minimum_fare"]),
                "booking_fee": _money_str(pricing["booking_fee"]),
                "platform_fee": _money_str(matched_area.get("platform_fee", 0)),
                "city_fee": _money_str(matched_area.get("city_fee", 0)),
                "surge_multiplier": _fd(surge),
            }
        )

    logger.info(
        f"Fares: Returning {len(result)} fare estimates "
        f"(vehicle_pricing={len(vp_by_name)}, fare_configs={len(fc_by_vt_id)})"
    )
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
    # Migration 83 added illustration_url; rider/driver apps key the car
    # image off `image_url`. Mirror the value here so embedded
    # vehicle_type objects in the estimate response carry both fields.
    for vt in vehicle_types or []:
        if isinstance(vt, dict) and vt.get("illustration_url") and not vt.get("image_url"):
            vt["image_url"] = vt["illustration_url"]
    logger.info(f"Fares: Found {len(vehicle_types)} active vehicle types")

    if not vehicle_types:
        logger.error("Fares: No active vehicle types found in database!")
        return []

    matching_area = await resolve_service_area_for_point(lat, lng, all_areas=all_areas)
    if not matching_area:
        logger.info("Fares: No matching service area for location, using defaults")
        return _build_default_fares(vehicle_types)

    logger.info(f"Fares: Matched service area '{matching_area.get('name', matching_area['id'])}'")
    return await build_fares_for_area(matching_area, vehicle_types)


@api_router.get("/fares")
async def get_fares_for_location(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lng: float = Query(..., ge=-180.0, le=180.0),
):
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

    # Cache the computed result.
    # Cap TTL at 60 s when surge is active so stale multipliers expire quickly.
    try:
        surge_multiplier = result[0].get("surge_multiplier", 1.0) if result else 1.0
        effective_ttl = min(_FARE_CACHE_TTL, 60) if surge_multiplier > 1.0 else _FARE_CACHE_TTL
        await redis_set(cache_key, json.dumps(result), ttl=effective_ttl)
        logger.debug(f"Fare cache SET for key {cache_key} (TTL={effective_ttl}s, surge={surge_multiplier})")
    except Exception as e:
        logger.warning(f"Could not cache fare result: {e}")

    return result
