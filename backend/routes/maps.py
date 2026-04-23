"""Authenticated Google Maps proxy endpoints backed by Redis caching.

Every endpoint hides our API key (clients never see it) and collapses
duplicate requests through `utils.google_maps_cache`. All routes require
a logged-in rider/driver/admin so the proxy can't be abused as a free
public gateway for arbitrary Maps calls.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

try:
    from ..dependencies import get_current_user
    from ..utils import google_maps_cache
except ImportError:
    from dependencies import get_current_user  # type: ignore
    from utils import google_maps_cache  # type: ignore


api_router = APIRouter(prefix="/maps", tags=["Maps"])


def _handle_runtime(exc: RuntimeError) -> HTTPException:
    """Translate the 'no API key configured' RuntimeError into 503."""
    return HTTPException(status_code=503, detail=str(exc))


@api_router.get("/directions")
async def get_directions(
    origin_lat: float = Query(..., description="Origin latitude"),
    origin_lng: float = Query(..., description="Origin longitude"),
    dest_lat: float = Query(..., description="Destination latitude"),
    dest_lng: float = Query(..., description="Destination longitude"),
    mode: str = Query("driving"),
    language: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Cached Directions passthrough. Returns Google's JSON as-is."""
    try:
        return await google_maps_cache.get_directions(
            origin_lat, origin_lng, dest_lat, dest_lng,
            mode=mode, language=language,
        )
    except RuntimeError as e:
        raise _handle_runtime(e)
    except Exception as e:
        logger.error(f"/maps/directions failed: {e}")
        raise HTTPException(status_code=502, detail="Directions upstream failed")


@api_router.get("/geocode")
async def get_geocode(
    address: str = Query(..., min_length=1),
    language: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Cached forward-geocoding."""
    try:
        return await google_maps_cache.geocode(address, language=language)
    except RuntimeError as e:
        raise _handle_runtime(e)
    except Exception as e:
        logger.error(f"/maps/geocode failed: {e}")
        raise HTTPException(status_code=502, detail="Geocoding upstream failed")


@api_router.get("/reverse-geocode")
async def get_reverse_geocode(
    lat: float = Query(...),
    lng: float = Query(...),
    language: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Cached reverse-geocoding; rounds coords for cache equality."""
    try:
        return await google_maps_cache.reverse_geocode(lat, lng, language=language)
    except RuntimeError as e:
        raise _handle_runtime(e)
    except Exception as e:
        logger.error(f"/maps/reverse-geocode failed: {e}")
        raise HTTPException(status_code=502, detail="Reverse geocoding upstream failed")


@api_router.get("/place/autocomplete")
async def place_autocomplete(
    query: str = Query(..., min_length=1),
    session_token: str = Query(..., description="UUID v4 reused across one search session"),
    location_bias: Optional[str] = Query(None),
    components: str = Query("country:ca"),
    language: str = Query("en"),
    current_user: dict = Depends(get_current_user),
):
    """Cached Places Autocomplete — session_token is billing-relevant."""
    try:
        return await google_maps_cache.places_autocomplete(
            query, session_token,
            location_bias=location_bias,
            components=components,
            language=language,
        )
    except RuntimeError as e:
        raise _handle_runtime(e)
    except Exception as e:
        logger.error(f"/maps/place/autocomplete failed: {e}")
        raise HTTPException(status_code=502, detail="Autocomplete upstream failed")


@api_router.get("/place/details")
async def place_details(
    place_id: str = Query(..., min_length=1),
    session_token: Optional[str] = Query(None),
    fields: str = Query("geometry,formatted_address"),
    language: str = Query("en"),
    current_user: dict = Depends(get_current_user),
):
    """Cached Place Details — same session_token closes the billing session."""
    try:
        return await google_maps_cache.place_details(
            place_id,
            session_token=session_token,
            fields=fields,
            language=language,
        )
    except RuntimeError as e:
        raise _handle_runtime(e)
    except Exception as e:
        logger.error(f"/maps/place/details failed: {e}")
        raise HTTPException(status_code=502, detail="Place details upstream failed")
