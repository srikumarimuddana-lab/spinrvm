"""Google Maps Distance Matrix ETA helper (Feature A — P3).

Computes the road-network ETA from a driver's current location to a destination
(pickup or dropoff point). Results are cached in Redis for 15 seconds so the
hot GPS-ping loop (60 pings/min/driver) triggers at most ~4 Maps API calls per
minute per active ride.

Failure modes are soft:
  - Redis unavailable → no caching, Maps called every ping (acceptable for
    small fleets; log a warning so ops can fix Redis)
  - Maps API key absent or request fails → haversine fallback at 30 km/h
  - Maps returns no route (very rare) → haversine fallback

The caller (websocket.py) always gets an int or None:
  - int  → seconds to destination via road network (or haversine estimate)
  - None → Maps AND haversine both failed; omit eta_seconds from WS event
"""

import logging
import math

import httpx

logger = logging.getLogger(__name__)

# Redis TTL for ETA cache entries.  15 s gives a smooth UI update cadence
# without hammering the Maps API on every GPS ping.
_ETA_CACHE_TTL = 15

# Assumed average speed for haversine fallback (km/h).  30 km/h accounts
# for urban driving with turns and traffic lights.
_FALLBACK_SPEED_KMH = 30

# Google Maps Distance Matrix endpoint.
_MAPS_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

# Hard timeout for the Maps HTTP call.  The WS fan-out path must stay fast;
# if Maps is slow we'd rather serve a slightly stale cached value than block.
_MAPS_TIMEOUT = 3.0


try:
    from .redis_client import redis_get, redis_set
except ImportError:
    from utils.redis_client import redis_get, redis_set  # type: ignore


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _haversine_eta_seconds(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    km = _haversine_km(lat1, lng1, lat2, lng2)
    return max(60, int(km / _FALLBACK_SPEED_KMH * 3600))


async def get_ride_eta_seconds(
    driver_lat: float,
    driver_lng: float,
    dest_lat: float,
    dest_lng: float,
    maps_api_key: str,
    driver_id: str,
    ride_id: str,
) -> int | None:
    """Return ETA in seconds from driver to destination, or None on total failure.

    Uses Redis to cache the result for _ETA_CACHE_TTL seconds so back-to-back
    GPS pings from the same driver/ride pair don't re-hit the Maps API.
    """
    cache_key = f"eta:{driver_id}:{ride_id}"

    # ── Cache check ────────────────────────────────────────────────────────
    try:
        cached = await redis_get(cache_key)
        if cached is not None:
            return int(cached)
    except Exception:
        logger.warning("[ETA] Redis get failed — cache miss, will call Maps", exc_info=False)

    # ── Maps API call ──────────────────────────────────────────────────────
    if maps_api_key:
        try:
            params = {
                "origins": f"{driver_lat},{driver_lng}",
                "destinations": f"{dest_lat},{dest_lng}",
                "departure_time": "now",
                "traffic_model": "best_guess",
                "key": maps_api_key,
            }
            async with httpx.AsyncClient(timeout=_MAPS_TIMEOUT) as client:
                resp = await client.get(_MAPS_URL, params=params)
                resp.raise_for_status()
                body = resp.json()

            element = body["rows"][0]["elements"][0]
            if element.get("status") == "OK":
                # Prefer traffic-aware duration when available
                duration = element.get("duration_in_traffic") or element.get("duration")
                eta_seconds = int(duration["value"])
            else:
                logger.warning(f"[ETA] Maps element status={element.get('status')} — falling back to haversine")
                eta_seconds = _haversine_eta_seconds(driver_lat, driver_lng, dest_lat, dest_lng)

        except Exception:
            logger.warning("[ETA] Maps API call failed — using haversine fallback", exc_info=False)
            eta_seconds = _haversine_eta_seconds(driver_lat, driver_lng, dest_lat, dest_lng)
    else:
        # No API key configured — haversine only
        eta_seconds = _haversine_eta_seconds(driver_lat, driver_lng, dest_lat, dest_lng)

    # ── Cache result ───────────────────────────────────────────────────────
    try:
        await redis_set(cache_key, str(eta_seconds), ttl=_ETA_CACHE_TTL)
    except Exception:
        logger.warning("[ETA] Redis set failed — ETA won't be cached", exc_info=False)

    return eta_seconds
