"""Best-effort address <-> coordinate consistency check (B9).

Used by ``POST /addresses`` and ``POST /favorites`` to catch the class of
bug the Glide Crescent wrong-pin incident exposed: a client sends an
address label that doesn't match the coordinate it's paired with, and the
backend persists the mismatched pair verbatim — a saved address or
favorite then replays that bad pin forever.

Fails OPEN (returns "cannot verify, allow the save") whenever:
  - No Google Maps API key configured, or the daily Maps budget is
    exhausted.
  - The address text doesn't resolve to a precise (ROOFTOP /
    RANGE_INTERPOLATED) geocode — most saved-address labels are informal
    ("Home", "Mom's house", a business name) and would otherwise
    false-positive-reject on every legitimate save. An APPROXIMATE/partial
    match means Google itself isn't confident, so it can't be used to
    reject a pin either.
  - The Maps API call itself fails or times out.

Only rejects (returns ``ok=False``) when a PRECISE geocode of the address
text resolves more than ``MISMATCH_KM`` away from the supplied
coordinate — the exact "text says one place, pin says another" bug class,
with high confidence there's a real mismatch rather than an ambiguous one.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

try:
    from .geo_utils import calculate_distance
except ImportError:
    from geo_utils import calculate_distance

try:
    from ..settings_loader import get_app_settings
    from ..utils.maps_budget import check_budget, record_call
except ImportError:
    from settings_loader import get_app_settings
    from utils.maps_budget import check_budget, record_call

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_HTTP_TIMEOUT = 3.0

# Same threshold the incident writeup (B9) and the AI booking tool's own
# reconcile guard use — a coordinate this close to its own address is
# treated as "the same place", not a mismatch worth blocking a save over.
MISMATCH_KM = 1.0

# Only these Geocoding location_type values pin an actual building; see the
# identical convention (and incident) documented in ai/tools_booking.py.
_PRECISE_LOCATION_TYPES = frozenset({"ROOFTOP", "RANGE_INTERPOLATED"})


async def verify_address_matches_coordinate(
    address: str, lat: float, lng: float
) -> tuple[bool, Optional[str], Optional[str]]:
    """Best-effort check that ``address`` and ``(lat, lng)`` describe the
    same place. Returns ``(ok, reason, place_id)`` — ``ok`` is False only
    when a precise geocode confidently disagrees with the supplied
    coordinate; every ambiguous or unavailable case fails open
    (``ok=True``). ``place_id`` is the resolved Google Places place_id
    whenever a geocode result was actually returned (B9 enhancement —
    already fetched as part of this same call, free to capture for the
    caller to persist alongside a saved address); ``None`` when no result
    was returned at all (no API key, budget exhausted, call failed, no
    match).
    """
    if not address or not address.strip():
        return True, None, None

    try:
        settings = await get_app_settings()
        api_key = (settings or {}).get("google_maps_api_key") or ""
    except Exception as exc:
        logger.warning("address_verification: settings lookup failed, failing open: %s", exc)
        return True, None, None
    if not api_key:
        return True, None, None

    try:
        within_budget, _spent, _budget = await check_budget()
    except Exception as exc:
        logger.warning("address_verification: budget check failed, failing open: %s", exc)
        return True, None, None
    if not within_budget:
        return True, None, None

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                _GEOCODE_URL,
                params={"address": address, "region": "ca", "key": api_key, "language": "en"},
            )
            data = resp.json()
        await record_call("geocode")
    except Exception as exc:
        logger.warning("address_verification: geocode call failed, failing open: %s", exc)
        return True, None, None

    if data.get("status") != "OK" or not data.get("results"):
        return True, None, None

    result = data["results"][0]
    place_id = result.get("place_id")
    if result.get("partial_match"):
        return True, None, place_id
    geometry = result.get("geometry") or {}
    if geometry.get("location_type") not in _PRECISE_LOCATION_TYPES:
        return True, None, place_id

    loc = geometry.get("location") or {}
    geo_lat, geo_lng = loc.get("lat"), loc.get("lng")
    if geo_lat is None or geo_lng is None:
        return True, None, place_id

    distance_km = calculate_distance(lat, lng, geo_lat, geo_lng)
    if distance_km > MISMATCH_KM:
        return False, f"'{address}' geocodes {distance_km:.1f} km from the supplied location", place_id
    return True, None, place_id
