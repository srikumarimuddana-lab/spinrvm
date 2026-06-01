"""Rider-facing Google Maps Platform proxy.

Hides the Maps API key behind the backend, applies session tokens for Places
billing, caches reverse-geocode lookups in Redis, and enforces a daily-spend
circuit breaker. Mirrors the admin Places proxy in ``routes/admin/rides.py``
but with rider-appropriate rate limits and an additional geocode endpoint.

Endpoints (mounted at ``/api/v1/maps/*``):

- ``GET /maps/places/autocomplete?input=&session_token=`` — rider typeahead
- ``GET /maps/places/details?place_id=&session_token=`` — finalise a session
- ``GET /maps/reverse-geocode?lat=&lng=`` — drop-pin → address

Cost shape: Places API (New) bills autocomplete requests and the final
Place Details Essentials call separately when a user selects a prediction.
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
    from ..utils.maps_budget import (
        check_budget,
        close_autocomplete_session,
        record_autocomplete_request,
        record_call,
    )
    from ..utils.rate_limiter import default_limiter as limiter
    from ..utils.redis_client import redis_get, redis_set
except ImportError:  # pragma: no cover - dual import path
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
    from utils.maps_budget import (  # type: ignore
        check_budget,
        close_autocomplete_session,
        record_autocomplete_request,
        record_call,
    )
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
        raise HTTPException(
            status_code=503, detail="Google Maps API key not configured"
        )
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

    await record_autocomplete_request(session_token)

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
    await close_autocomplete_session(session_token)

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
        raise HTTPException(
            status_code=502, detail="Failed to call Geocoding API"
        ) from e

    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        logger.error("[maps_proxy] reverse-geocode API error: %s", data.get("status"))
        raise HTTPException(status_code=502, detail="Geocoding API error")

    await record_call("geocode")

    results = data.get("results", [])
    formatted = (
        results[0]["formatted_address"] if results else f"{cache_lat}, {cache_lng}"
    )

    try:
        await redis_set(cache_key, formatted, ttl=_REVERSE_GEOCODE_TTL)
    except Exception:
        logger.warning(
            "[maps_proxy] failed to cache reverse-geocode result", exc_info=False
        )

    return {"formatted_address": formatted, "cached": False}
