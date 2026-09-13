"""Rider-facing Google Maps Platform proxy.

Hides the Maps API key behind the backend, applies session tokens for Places
billing, caches reverse-geocode lookups in Redis, and enforces a daily-spend
circuit breaker. Mirrors the admin Places proxy in ``routes/admin/rides.py``
but with rider-appropriate rate limits and an additional geocode endpoint.

Endpoints (mounted at ``/api/v1/maps/*``):

- ``GET /maps/places/autocomplete?input=&session_token=`` — rider typeahead
- ``GET /maps/places/details?place_id=&session_token=`` — finalise a session
- ``GET /maps/reverse-geocode?lat=&lng=`` — drop-pin → address
- ``GET /maps/directions?origin=&destination=&waypoints=`` — road route for
  the client-side map-line fallback (R7, docs/audit/ride-experience/
  ROADMAP.md). Dark-launched behind ``app_settings.directions_proxy_enabled``
  — callers decide whether to use this or call Google directly; this route
  does not itself gate on the flag.

Cost shape: Places API (New) bills autocomplete requests and the final
Place Details Essentials call separately when a user selects a prediction.
Reverse-geocode hits a 24h Redis cache keyed by 4-decimal lat/lng (~11m).
Directions is billed per call, same SKU as ``routes/rides/_shared.py``'s
fare-estimate Directions call and ``utils/route_distance.py``'s live-route
fallback — this proxy shares the same ``"directions"`` budget bucket.

All endpoints require an authenticated rider/driver. The Maps key is read
from ``app_settings.google_maps_api_key`` so it can be rotated without
redeploy.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

try:
    from .. import db_supabase
    from ..dependencies import get_current_user
    from ..settings_loader import get_app_settings
    from ..utils.google_places_new import (
        PLACES_NEW_AUTOCOMPLETE_FIELD_MASK,
        PLACES_NEW_AUTOCOMPLETE_URL,
        PLACES_NEW_DETAILS_FIELD_MASK,
        build_autocomplete_payload,
        legacy_details_from_new_response,
        legacy_predictions_from_new_response,
        places_new_details_url,
        places_new_headers,
    )
    from ..utils.maps_budget import check_budget, record_call
    from ..utils.polyline import decode_polyline
    from ..utils.rate_limiter import default_limiter as limiter
    from ..utils.rate_limiter import get_user_or_ip_key
    from ..utils.redis_client import redis_get, redis_set
except ImportError:  # pragma: no cover - dual import path
    import db_supabase  # type: ignore
    from dependencies import get_current_user  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.google_places_new import (  # type: ignore
        PLACES_NEW_AUTOCOMPLETE_FIELD_MASK,
        PLACES_NEW_AUTOCOMPLETE_URL,
        PLACES_NEW_DETAILS_FIELD_MASK,
        build_autocomplete_payload,
        legacy_details_from_new_response,
        legacy_predictions_from_new_response,
        places_new_details_url,
        places_new_headers,
    )
    from utils.maps_budget import check_budget, record_call  # type: ignore
    from utils.polyline import decode_polyline  # type: ignore
    from utils.rate_limiter import default_limiter as limiter  # type: ignore
    from utils.rate_limiter import get_user_or_ip_key  # type: ignore
    from utils.redis_client import redis_get, redis_set  # type: ignore

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/maps", tags=["Maps"])

_HTTP_TIMEOUT = 5.0
_REVERSE_GEOCODE_TTL = 24 * 3600  # 24 h — addresses are stable
_GEOCODE_CACHE_PRECISION = 4  # decimals — ~11 m grid

# C105 (ACTION_ITEMS.md): dedupe concurrent viewers of the same leg, mirroring
# route_distance.py's _LIVE_ROUTE_CACHE_TTL_S. Uniform fine precision on
# every coordinate -- same scheme as routes/rides/_shared.py's
# _fare_directions_cache_key, NOT route_distance.py's coarse-origin scheme.
# route_distance.py can safely coarsen its origin because its only caller is
# a live driver GPS position; this endpoint has no such guarantee. Checked
# against all 5 real client call sites (rider-app + driver-app) before
# picking this: driver-arriving.tsx and the driver dashboard do pass a
# moving position as origin, but ride-options.tsx, ride-in-progress.tsx and
# driver-arrived.tsx all pass a FIXED, per-ride pickup as "origin" paired
# with a FIXED dropoff. Coarsening the origin there would let two different
# bookings' distinct-but-nearby pickups (e.g. two entrances on the same
# block) collide in the cache whenever their dropoffs also round together
# (common for a popular shared destination, e.g. an airport) -- silently
# serving one rider's confirmed route to another. Fine precision on the
# origin costs some cache-hit rate for the two genuinely-moving-origin call
# sites (a jittery GPS ping less often re-rounds to the same bucket) but
# that is a pure efficiency trade-off, not a correctness one.
_DIRECTIONS_CACHE_TTL_S = 30
_DIRECTIONS_CACHE_PRECISION = 5  # decimals — ~1 m grid, every coordinate


async def _ensure_budget() -> None:
    allowed, spent, budget = await check_budget()
    if not allowed:
        logger.error(
            "[maps_proxy] daily budget exceeded — refusing call",
            extra={"spent_usd": round(spent, 4), "budget_usd": budget},
        )
        raise HTTPException(
            status_code=503,
            detail="Maps service temporarily unavailable (daily budget reached)",
        )


async def _maps_key() -> str:
    settings_row = await get_app_settings()
    api_key = (settings_row or {}).get("google_maps_api_key") or ""
    if not api_key:
        raise HTTPException(status_code=503, detail="Google Maps API key not configured")
    return api_key


@api_router.get("/places/autocomplete")
@limiter.limit("120/minute")
async def places_autocomplete(
    request: Request,
    input: str = Query(..., min_length=1, max_length=200),
    session_token: Optional[str] = Query(default=None, max_length=64),
    location: Optional[str] = Query(default=None, max_length=50),
    radius: int = Query(default=50000, ge=1000, le=100000),
    current_user: dict = Depends(get_current_user),
):
    """Proxy Places API (New) Autocomplete using session_token when present."""
    await _ensure_budget()
    api_key = await _maps_key()

    payload = build_autocomplete_payload(input, session_token, location, radius)

    logger.info(
        "[maps_proxy] autocomplete(new) input=%r location=%s radius=%s restricted=%s",
        input,
        location or "(none)",
        radius if location else "(none)",
        bool(location),
    )

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                PLACES_NEW_AUTOCOMPLETE_URL,
                headers=places_new_headers(api_key, PLACES_NEW_AUTOCOMPLETE_FIELD_MASK),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            "[maps_proxy] autocomplete(new) API error: %s",
            e.response.text[:500],
        )
        raise HTTPException(status_code=502, detail="Places API error") from e
    except Exception as e:
        logger.error("[maps_proxy] autocomplete(new) request failed: %s", e)
        raise HTTPException(status_code=502, detail="Failed to call Places API") from e

    # Places API (New) bills autocomplete requests individually for sessions
    # ending in Place Details Essentials, so record each proxied request.
    await record_call("autocomplete")

    predictions = legacy_predictions_from_new_response(data)

    logger.info(
        "[maps_proxy] autocomplete results=%d top=%s",
        len(predictions),
        predictions[0].get("description", "")[:60] if predictions else "(none)",
    )

    # Sort by distance from the rider when origin was provided. Google's
    # autocomplete sorts by relevance by default — a distant well-known
    # Walmart can outrank the closer one even with strict bounds. Re-sort
    # by `distance_meters` (populated when `origin` is sent) so the rider
    # always sees the closest match first.
    if location and predictions:
        predictions = sorted(
            predictions,
            key=lambda p: p.get("distance_meters", 10_000_000),
        )

    return {"predictions": predictions}


@api_router.get("/places/details")
@limiter.limit("120/minute")
async def places_details(
    request: Request,
    place_id: str = Query(..., min_length=1, max_length=255),
    session_token: Optional[str] = Query(default=None, max_length=64),
    current_user: dict = Depends(get_current_user),
):
    """Proxy Place Details. Same session_token closes the billing session."""
    await _ensure_budget()
    api_key = await _maps_key()

    params: dict = {}
    if session_token:
        params["sessionToken"] = session_token

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                places_new_details_url(place_id),
                headers=places_new_headers(api_key, PLACES_NEW_DETAILS_FIELD_MASK),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("[maps_proxy] details(new) API error: %s", e.response.text[:500])
        raise HTTPException(status_code=502, detail="Places API error") from e
    except Exception as e:
        logger.error("[maps_proxy] details(new) request failed: %s", e)
        raise HTTPException(status_code=502, detail="Failed to call Places API") from e

    await record_call("details")

    return legacy_details_from_new_response(data)


@api_router.get("/reverse-geocode")
@limiter.limit("120/minute")
async def reverse_geocode(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    current_user: dict = Depends(get_current_user),
):
    """Proxy Geocoding API for lat/lng → address. 24 h Redis cache on a ~11 m grid."""
    cache_lat = round(lat, _GEOCODE_CACHE_PRECISION)
    cache_lng = round(lng, _GEOCODE_CACHE_PRECISION)
    cache_key = f"maps:revgeo:{cache_lat}:{cache_lng}"

    try:
        cached = await redis_get(cache_key)
    except Exception:
        cached = None
    if cached:
        return {"formatted_address": cached, "cached": True}

    await _ensure_budget()
    api_key = await _maps_key()

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"latlng": f"{lat},{lng}", "key": api_key, "language": "en"},
            )
            data = resp.json()
    except Exception as e:
        logger.error("[maps_proxy] reverse-geocode request failed: %s", e)
        raise HTTPException(status_code=502, detail="Failed to call Geocoding API") from e

    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        logger.error("[maps_proxy] reverse-geocode API error: %s", data.get("status"))
        raise HTTPException(status_code=502, detail="Geocoding API error")

    await record_call("geocode")

    results = data.get("results", [])
    formatted = results[0]["formatted_address"] if results else f"{cache_lat}, {cache_lng}"

    try:
        await redis_set(cache_key, formatted, ttl=_REVERSE_GEOCODE_TTL)
    except Exception:
        logger.warning("[maps_proxy] failed to cache reverse-geocode result", exc_info=False)

    return {"formatted_address": formatted, "cached": False}


def _parse_latlng(raw: str, field: str) -> tuple:
    """Parse a required 'lat,lng' query param. Raises 400 on any bad input."""
    try:
        lat_str, lng_str = raw.split(",")
        lat, lng = float(lat_str), float(lng_str)
    except (ValueError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"{field} must be 'lat,lng'") from e
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(status_code=400, detail=f"{field} out of range")
    return lat, lng


def _directions_cache_key(o_lat: float, o_lng: float, d_lat: float, d_lng: float, stops: list) -> str:
    """Cache key for get_directions — uniform fine precision on every
    coordinate (see the _DIRECTIONS_CACHE_PRECISION comment above for why).
    Waypoints are included in order: order is never optimized, so a
    different stop sequence for the same origin/destination is a different
    route and must not collide.
    """
    p = _DIRECTIONS_CACHE_PRECISION
    key = f"maps_directions:{round(o_lat, p)},{round(o_lng, p)}:{round(d_lat, p)},{round(d_lng, p)}"
    if stops:
        wp = "|".join(f"{round(lat, p)},{round(lng, p)}" for lat, lng in stops)
        key += f":wp={wp}"
    return key


@api_router.get("/directions")
@limiter.limit("60/minute", key_func=get_user_or_ip_key)
async def get_directions(
    request: Request,
    origin: str = Query(..., min_length=1, max_length=64, description="'lat,lng'"),
    destination: str = Query(..., min_length=1, max_length=64, description="'lat,lng'"),
    waypoints: Optional[str] = Query(
        default=None, max_length=512, description="'lat,lng|lat,lng|...' stop order, never optimized"
    ),
    current_user: dict = Depends(get_current_user),
):
    """Proxy Google Directions for the client-side map-line fallback (R7).

    Rider-app/driver-app screens that render a `MapViewDirections` fallback
    line (when the backend didn't already supply a route polyline) call this
    instead of Google directly, once each call site is migrated and
    ``app_settings.directions_proxy_enabled`` is on — the server key never
    reaches the device for those calls. This route itself does not check
    that flag; it is a plain, always-available proxy like this file's
    sibling autocomplete/details/reverse-geocode endpoints, and the flag is
    purely a client-side rollout switch (see the module docstring).

    Waypoint order is preserved exactly as given — never optimized. A
    caller relying on a specific stop sequence (e.g. rider-entered stops
    priced and dispatched in that order) must not have this endpoint
    silently reorder them.

    Returns ``{"coordinates": [[lat, lng], ...], "distance_km": float|None,
    "duration_minutes": float|None, "cached": bool}`` — the same information a
    `MapViewDirections.onReady` callback provides, pre-decoded so the client
    doesn't need its own polyline decoder for this path.

    Redis-cached for ``_DIRECTIONS_CACHE_TTL_S`` seconds (C105,
    ACTION_ITEMS.md) to dedupe concurrent viewers of the same leg — e.g. the
    driver dashboard's origin→pickup fetch and the rider's driver-arriving
    fetch requesting near-identical coordinates around the same ride. A
    Redis outage never breaks this endpoint: cache get/set failures are
    caught and logged, falling through to (or skipping) the direct Google
    call exactly as if there were no cache at all. A malformed/degenerate
    result (no billable distance, or no decoded coordinates) is never
    cached, so a bad response can't keep being served for the TTL window.
    """
    o_lat, o_lng = _parse_latlng(origin, "origin")
    d_lat, d_lng = _parse_latlng(destination, "destination")
    stops = [_parse_latlng(pair, "waypoints") for pair in waypoints.split("|")] if waypoints else []

    cache_key = _directions_cache_key(o_lat, o_lng, d_lat, d_lng, stops)
    try:
        cached = await redis_get(cache_key)
        if cached:
            return {**json.loads(cached), "cached": True}
    except Exception:
        logger.warning("[maps_proxy] directions cache get failed", exc_info=False)

    await _ensure_budget()
    api_key = await _maps_key()

    params: dict = {
        "origin": f"{o_lat},{o_lng}",
        "destination": f"{d_lat},{d_lng}",
        "key": api_key,
    }
    if stops:
        params["waypoints"] = "|".join(f"{lat},{lng}" for lat, lng in stops)

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get("https://maps.googleapis.com/maps/api/directions/json", params=params)
            data = resp.json()
    except Exception as e:
        logger.error("[maps_proxy] directions request failed: %s", e)
        raise HTTPException(status_code=502, detail="Failed to call Directions API") from e

    await record_call("directions")

    if data.get("status") != "OK" or not data.get("routes"):
        logger.warning("[maps_proxy] directions API non-OK status: %s", data.get("status"))
        raise HTTPException(status_code=502, detail="No route found")

    route = data["routes"][0]
    legs = route.get("legs", [])
    distance_m = sum(leg.get("distance", {}).get("value", 0) for leg in legs)
    duration_s = sum(leg.get("duration", {}).get("value", 0) for leg in legs)
    polyline_str = route.get("overview_polyline", {}).get("points", "")

    try:
        coordinates = decode_polyline(polyline_str) if polyline_str else []
    except ValueError as e:
        logger.error("[maps_proxy] directions polyline decode failed: %s", e)
        raise HTTPException(status_code=502, detail="Malformed route from Directions API") from e

    result = {
        "coordinates": coordinates,
        "distance_km": round(distance_m / 1000, 2) if distance_m else None,
        "duration_minutes": round(duration_s / 60, 1) if duration_s else None,
    }

    # Never cache a malformed/degenerate result — a stale bad entry would
    # keep returning it for the whole TTL window (same rule as _shared.py's
    # _fetch_directions_route).
    if result["distance_km"] is not None and coordinates:
        try:
            await redis_set(cache_key, json.dumps(result), ttl=_DIRECTIONS_CACHE_TTL_S)
        except Exception:
            logger.warning("[maps_proxy] directions cache set failed", exc_info=False)

    return {**result, "cached": False}


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    import math

    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@api_router.get("/pickup-points")
@limiter.limit("120/minute")
async def pickup_points(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    current_user: dict = Depends(get_current_user),
):
    """Curated meeting points for a venue containing (lat,lng).

    If the dropped pickup pin falls within an active venue's detection radius,
    return that venue's named, driver-reachable pickup points so the rider can
    pick where to meet (vs a pin a car can't reach, e.g. inside a mall).
    Returns ``{"venue": null, "pickup_points": []}`` when no venue matches.
    """
    try:
        venues = await db_supabase.get_rows("venues", {"is_active": True}, limit=2000)
    except Exception as e:
        logger.warning("[maps_proxy] pickup-points venue lookup failed: %s", e)
        return {"venue": None, "pickup_points": []}

    best = None
    best_d = None
    for v in venues or []:
        clat, clng = v.get("center_lat"), v.get("center_lng")
        if clat is None or clng is None:
            continue
        d = _haversine_m(lat, lng, float(clat), float(clng))
        if d <= float(v.get("radius_m") or 150) and (best_d is None or d < best_d):
            best, best_d = v, d

    if not best:
        return {"venue": None, "pickup_points": []}

    pts = [
        {"name": p.get("name"), "lat": p.get("lat"), "lng": p.get("lng")}
        for p in (best.get("pickup_points") or [])
        if p.get("lat") is not None and p.get("lng") is not None
    ]
    return {"venue": {"id": best.get("id"), "name": best.get("name")}, "pickup_points": pts}
