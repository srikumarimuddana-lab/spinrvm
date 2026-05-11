"""Rider-facing Google Maps Platform proxy.

Hides the Maps API key behind the backend, applies session tokens for Places
billing, caches reverse-geocode lookups in Redis, and enforces a daily-spend
circuit breaker. Mirrors the admin Places proxy in ``routes/admin/rides.py``
but with rider-appropriate rate limits and an additional geocode endpoint.

Endpoints (mounted at ``/api/v1/maps/*``):

- ``GET /maps/places/autocomplete?input=&session_token=`` — rider typeahead
- ``GET /maps/places/details?place_id=&session_token=`` — finalise a session
- ``GET /maps/reverse-geocode?lat=&lng=`` — drop-pin → address

Cost shape: with session tokens, N autocomplete + 1 details = $0.017 flat.
Reverse-geocode hits a 24h Redis cache keyed by 4-decimal lat/lng (~11m).

All endpoints require an authenticated rider/driver. The Maps key is read
from ``app_settings.google_maps_api_key`` so it can be rotated without
redeploy.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

try:
    from ..dependencies import get_current_user
    from ..settings_loader import get_app_settings
    from ..utils.maps_budget import check_budget, record_call
    from ..utils.rate_limiter import default_limiter as limiter
    from ..utils.redis_client import redis_get, redis_set
except ImportError:  # pragma: no cover - dual import path
    from dependencies import get_current_user  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.maps_budget import check_budget, record_call  # type: ignore
    from utils.rate_limiter import default_limiter as limiter  # type: ignore
    from utils.redis_client import redis_get, redis_set  # type: ignore

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/maps", tags=["Maps"])

_HTTP_TIMEOUT = 5.0
_REVERSE_GEOCODE_TTL = 24 * 3600  # 24 h — addresses are stable
_GEOCODE_CACHE_PRECISION = 4  # decimals — ~11 m grid


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
    """Proxy Places Autocomplete. Pass session_token to bundle billing."""
    await _ensure_budget()
    api_key = await _maps_key()

    params: dict = {
        "input": input,
        "key": api_key,
        "language": "en",
        "components": "country:ca",
    }
    if session_token:
        params["sessiontoken"] = session_token
    if location:
        params["location"] = location
        params["radius"] = str(radius)

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/place/autocomplete/json",
                params=params,
            )
            data = resp.json()
    except Exception as e:
        logger.error("[maps_proxy] autocomplete request failed: %s", e)
        raise HTTPException(status_code=502, detail="Failed to call Places API") from e

    status = data.get("status")
    if status not in ("OK", "ZERO_RESULTS"):
        logger.error("[maps_proxy] autocomplete API error: %s", status)
        raise HTTPException(status_code=502, detail="Places API error")

    # Bill as session-priced when client opted in, per-keystroke otherwise.
    await record_call("autocomplete_session" if session_token else "autocomplete")

    return {"predictions": data.get("predictions", [])}


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

    params: dict = {
        "place_id": place_id,
        "fields": "geometry,formatted_address",
        "key": api_key,
    }
    if session_token:
        params["sessiontoken"] = session_token

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/place/details/json",
                params=params,
            )
            data = resp.json()
    except Exception as e:
        logger.error("[maps_proxy] details request failed: %s", e)
        raise HTTPException(status_code=502, detail="Failed to call Places API") from e

    if data.get("status") != "OK":
        logger.error("[maps_proxy] details API error: %s", data.get("status"))
        raise HTTPException(status_code=502, detail="Places API error")

    # When session_token is set, billing is bundled into the autocomplete_session
    # tier already recorded — no further record needed. Without a session token
    # this is a per-call Place Details bill.
    if not session_token:
        await record_call("details")

    loc = data.get("result", {}).get("geometry", {}).get("location", {})
    return {
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "formatted_address": data.get("result", {}).get("formatted_address"),
    }


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
