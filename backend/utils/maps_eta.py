"""Road-network ETA helper (OSRM-first, Google Distance Matrix fallback).

Computes the road-network ETA from a driver's current location to a destination
(pickup point only — NOT dropoff). Results are cached in Redis for 15 seconds so
the hot GPS-ping loop triggers at most ~4 routing calls per minute per active ride.

Provider order: OSRM /route (self-hosted or the public fallback configured in
core/config.py — zero metered cost) → Google Distance Matrix (traffic-aware,
metered) → haversine at 30 km/h. OSRM lacks live-traffic awareness, which is an
acceptable trade for Saskatchewan-scale cities in exchange for eliminating the
per-ping Distance Matrix spend.

IMPORTANT — call scope:
  This helper must only be called during the pre-pickup phases:
  ``driver_assigned``, ``driver_accepted``, ``driver_arrived``.
  The gating set ``_ETA_PICKUP_STATUSES`` in ``websocket.py`` enforces this.
  During ``in_progress`` the rider app computes ETA client-side via haversine
  so we do not re-call Maps on every GPS ping for the full trip duration.

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


async def _osrm_eta_seconds(driver_lat: float, driver_lng: float, dest_lat: float, dest_lng: float) -> int | None:
    """ETA via OSRM /route (overview=false — no geometry, just duration).

    Returns None when no OSRM URL resolves (self-hosted + public fallback both
    unset) or on any error, letting the caller fall through to Google.
    """
    try:
        from ..settings_loader import get_app_settings
        from .route_distance import _live_osrm_url
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
        from utils.route_distance import _live_osrm_url  # type: ignore

    try:
        app_settings = await get_app_settings() or {}
    except Exception:
        app_settings = {}
    osrm_url = _live_osrm_url(app_settings)
    if not osrm_url:
        return None

    url = f"{osrm_url.rstrip('/')}/route/v1/driving/{driver_lng},{driver_lat};{dest_lng},{dest_lat}"
    try:
        async with httpx.AsyncClient(timeout=_MAPS_TIMEOUT) as client:
            resp = await client.get(url, params={"overview": "false", "alternatives": "false", "steps": "false"})
            if resp.status_code != 200:
                logger.warning("[ETA] OSRM /route returned %d — trying Google fallback", resp.status_code)
                return None
            data = resp.json()
    except Exception:
        logger.warning("[ETA] OSRM /route call failed — trying Google fallback", exc_info=False)
        return None

    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    try:
        return int(round(float(data["routes"][0]["duration"])))
    except (KeyError, TypeError, ValueError):
        return None


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
    GPS pings from the same driver/ride pair don't re-hit the routing provider.
    Provider order: OSRM /route (free) → Google Distance Matrix → haversine.
    """
    cache_key = f"eta:{driver_id}:{ride_id}"

    # ── Cache check ────────────────────────────────────────────────────────
    try:
        cached = await redis_get(cache_key)
        if cached is not None:
            return int(cached)
    except Exception:
        logger.warning("[ETA] Redis get failed — cache miss, will call routing provider", exc_info=False)

    # ── OSRM first (no metered cost) ────────────────────────────────────────
    eta_seconds = await _osrm_eta_seconds(driver_lat, driver_lng, dest_lat, dest_lng)
    if eta_seconds is not None:
        try:
            await redis_set(cache_key, str(eta_seconds), ttl=_ETA_CACHE_TTL)
        except Exception:
            logger.warning("[ETA] Redis set failed — ETA won't be cached", exc_info=False)
        return eta_seconds

    # ── Google Distance Matrix fallback ─────────────────────────────────────
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
            logger.error("[ETA] Maps API call failed — using haversine fallback", exc_info=True)
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


async def batch_get_etas(
    drivers: list[dict],
    dest_lat: float,
    dest_lng: float,
    maps_api_key: str,
) -> dict[str, int]:
    """Return {driver_id: eta_seconds} for multiple drivers in one API call.

    Google Distance Matrix accepts up to 25 pipe-separated origins per
    request.  Each driver dict must have 'id', 'lat', 'lng'.
    """
    if not drivers:
        return {}

    result: dict[str, int] = {}

    if not maps_api_key:
        for d in drivers:
            result[d["id"]] = _haversine_eta_seconds(d["lat"], d["lng"], dest_lat, dest_lng)
        return result

    origins = "|".join(f"{d['lat']},{d['lng']}" for d in drivers[:25])
    try:
        params = {
            "origins": origins,
            "destinations": f"{dest_lat},{dest_lng}",
            "departure_time": "now",
            "traffic_model": "best_guess",
            "key": maps_api_key,
        }
        async with httpx.AsyncClient(timeout=_MAPS_TIMEOUT) as client:
            resp = await client.get(_MAPS_URL, params=params)
            resp.raise_for_status()
            body = resp.json()

        for i, d in enumerate(drivers[:25]):
            element = body["rows"][i]["elements"][0]
            if element.get("status") == "OK":
                duration = element.get("duration_in_traffic") or element.get("duration")
                result[d["id"]] = int(duration["value"])
            else:
                result[d["id"]] = _haversine_eta_seconds(d["lat"], d["lng"], dest_lat, dest_lng)

    except Exception:
        logger.error("[ETA] Batch Maps API call failed — haversine fallback for all", exc_info=True)
        for d in drivers[:25]:
            result[d["id"]] = _haversine_eta_seconds(d["lat"], d["lng"], dest_lat, dest_lng)

    # Drivers beyond the 25-origin limit get haversine only
    for d in drivers[25:]:
        result[d["id"]] = _haversine_eta_seconds(d["lat"], d["lng"], dest_lat, dest_lng)

    return result
