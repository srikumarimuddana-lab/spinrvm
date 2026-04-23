"""Server-side cache for Google Maps calls.

Why this exists
---------------

Directions, Geocoding, and Place Details are billed per request. Every
client-direct call is a full-price API hit. Routing these through the
backend lets us collapse duplicate requests across users and across time
into a single billable call, and lets us swap providers later without
touching the apps.

Foundation status
-----------------

This module is the foundation only. The rider and driver apps currently
call `maps.googleapis.com` directly (`MapViewDirections` component and
bare `fetch()` calls in `saved-places.tsx`, `search-destination.tsx`,
`pick-on-map.tsx`, `driver/addresses.tsx`). The cache has zero hit rate
until those call sites migrate to the `/maps/*` proxy endpoints. That
migration is per-screen and can land incrementally.

Cache keys
----------

All keys live under the `gmaps:` prefix so admin monitoring can surface
them:

    gmaps:dir:<hash>       directions (120s TTL — traffic changes fast)
    gmaps:geo:<hash>       forward geocoding (7d TTL — addresses stable)
    gmaps:geo:rev:<hash>   reverse geocoding (7d TTL)
    gmaps:place:ac:<hash>  place autocomplete (60s TTL — query-specific)
    gmaps:place:det:<hash> place details (7d TTL — place_id is stable)

Keys hash the normalized input so rounded-equivalent coordinates collide
intentionally. 4-decimal rounding ≈ 11m, which is well under Google's
own snap-to-road tolerance for directions.

Observability
-------------

Each call emits either a `gmaps_cache_hits_total{op=...}` or
`gmaps_cache_misses_total{op=...}` counter. Errors increment
`gmaps_api_errors_total{op=...}`. These surface on /metrics and on the
admin Redis page via `KNOWN_KEY_PREFIXES`.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

import httpx
from loguru import logger

try:
    from ..settings_loader import get_app_settings
    from ..utils import metrics
    from ..utils.redis_client import redis_get, redis_set
except ImportError:
    from settings_loader import get_app_settings  # type: ignore
    from utils import metrics  # type: ignore
    from utils.redis_client import redis_get, redis_set  # type: ignore


_GMAPS_BASE = "https://maps.googleapis.com/maps/api"

# TTLs chosen per endpoint semantics, not per cost:
#   Directions — traffic-aware, drifts within minutes.
#   Geocoding — address→coord is effectively immutable for our horizon.
#   Autocomplete — the user types fast, same prefix rarely seen twice
#     from the same session, but cross-user collisions ("airport") are
#     worth the short window.
#   Place details — place_id is stable for years.
_TTL_DIRECTIONS = 120
_TTL_GEOCODE = 7 * 24 * 3600
_TTL_AUTOCOMPLETE = 60
_TTL_PLACE_DETAILS = 7 * 24 * 3600


def _hash_key(prefix: str, payload: Dict[str, Any]) -> str:
    """Stable cache key: sha1 over sorted-json payload → short hex."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}{digest}"


def _round_coord(v: float, places: int = 4) -> float:
    return round(float(v), places)


async def _get_api_key() -> Optional[str]:
    settings = await get_app_settings()
    key = (settings or {}).get("google_maps_api_key") or ""
    return key or None


async def _cached_get(
    op: str,
    cache_key: str,
    ttl: int,
    url: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Generic read-through helper. Logs hit/miss; never caches errors."""
    hit = await redis_get(cache_key)
    if hit:
        metrics.inc("gmaps_cache_hits_total", {"op": op})
        try:
            return json.loads(hit)
        except json.JSONDecodeError:
            # Corrupted entry — fall through to refetch.
            pass

    metrics.inc("gmaps_cache_misses_total", {"op": op})
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            body = resp.json()
    except Exception as e:
        metrics.inc("gmaps_api_errors_total", {"op": op})
        logger.error(f"google_maps {op} failed: {e}")
        raise

    # Only cache success states — Google returns 200 with status=ZERO_RESULTS
    # on perfectly valid-but-empty queries; those ARE cacheable (same input
    # will stay empty). We skip caching the "we couldn't reach the API"
    # shape (already raised above) and the API quota errors so a quota
    # blip doesn't pin failure for 7 days.
    status = body.get("status") if isinstance(body, dict) else None
    if status in ("OVER_QUERY_LIMIT", "REQUEST_DENIED", "UNKNOWN_ERROR"):
        metrics.inc("gmaps_api_errors_total", {"op": op, "status": status})
        logger.warning(f"google_maps {op} non-retryable status: {status}")
        # Still return body so caller sees the real response; just don't poison cache.
        return body

    try:
        await redis_set(cache_key, json.dumps(body), ttl=ttl)
    except Exception as e:
        logger.warning(f"google_maps cache write failed for {op}: {e}")
    return body


# ── Public surface ────────────────────────────────────────────────────────────


async def get_directions(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    mode: str = "driving",
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """Cached Directions API call. Rounds coords to 4 decimals (~11m).

    Return shape matches Google's response — the caller can use
    `routes[0].overview_polyline.points` for map rendering and
    `routes[0].legs[0].duration.value` for ETA seconds.
    """
    api_key = await _get_api_key()
    if not api_key:
        raise RuntimeError("Google Maps API key not configured in app_settings")

    key_payload = {
        "o": (_round_coord(origin_lat), _round_coord(origin_lng)),
        "d": (_round_coord(dest_lat), _round_coord(dest_lng)),
        "mode": mode,
        "lang": language or "",
    }
    cache_key = _hash_key("gmaps:dir:", key_payload)

    params = {
        "origin": f"{origin_lat},{origin_lng}",
        "destination": f"{dest_lat},{dest_lng}",
        "mode": mode,
        "key": api_key,
    }
    if language:
        params["language"] = language

    return await _cached_get(
        "directions", cache_key, _TTL_DIRECTIONS,
        f"{_GMAPS_BASE}/directions/json", params,
    )


async def geocode(address: str, language: Optional[str] = None) -> Dict[str, Any]:
    """Cached forward-geocoding. Normalizes address for key equality."""
    api_key = await _get_api_key()
    if not api_key:
        raise RuntimeError("Google Maps API key not configured in app_settings")

    normalized = " ".join(address.strip().lower().split())
    key_payload = {"q": normalized, "lang": language or ""}
    cache_key = _hash_key("gmaps:geo:", key_payload)

    params = {"address": address, "key": api_key}
    if language:
        params["language"] = language

    return await _cached_get(
        "geocode", cache_key, _TTL_GEOCODE,
        f"{_GMAPS_BASE}/geocode/json", params,
    )


async def reverse_geocode(
    lat: float, lng: float, language: Optional[str] = None
) -> Dict[str, Any]:
    """Cached reverse-geocoding. Rounds to 4 decimals so pan-jitter collides."""
    api_key = await _get_api_key()
    if not api_key:
        raise RuntimeError("Google Maps API key not configured in app_settings")

    key_payload = {
        "lat": _round_coord(lat),
        "lng": _round_coord(lng),
        "lang": language or "",
    }
    cache_key = _hash_key("gmaps:geo:rev:", key_payload)

    params = {"latlng": f"{lat},{lng}", "key": api_key}
    if language:
        params["language"] = language

    return await _cached_get(
        "reverse_geocode", cache_key, _TTL_GEOCODE,
        f"{_GMAPS_BASE}/geocode/json", params,
    )


async def places_autocomplete(
    query: str,
    session_token: str,
    location_bias: Optional[str] = None,
    components: str = "country:ca",
    language: str = "en",
) -> Dict[str, Any]:
    """Cached Places Autocomplete.

    The `session_token` is part of Google's billing model — all
    autocomplete requests sharing a token bill at a flat rate together
    with the eventual Place Details request. The client MUST generate a
    UUID v4 per search session and reuse it until the user picks a
    result; we include it in the cache key so tokens don't cross-pollute.
    """
    api_key = await _get_api_key()
    if not api_key:
        raise RuntimeError("Google Maps API key not configured in app_settings")

    normalized = " ".join(query.strip().lower().split())
    key_payload = {
        "q": normalized,
        "token": session_token,
        "bias": location_bias or "",
        "comp": components,
        "lang": language,
    }
    cache_key = _hash_key("gmaps:place:ac:", key_payload)

    params = {
        "input": query,
        "key": api_key,
        "sessiontoken": session_token,
        "components": components,
        "language": language,
    }
    if location_bias:
        # Google accepts `location=lat,lng&radius=m` or `locationbias=...`;
        # we pass through whatever the client gave us.
        params["locationbias"] = location_bias

    return await _cached_get(
        "place_autocomplete", cache_key, _TTL_AUTOCOMPLETE,
        f"{_GMAPS_BASE}/place/autocomplete/json", params,
    )


async def place_details(
    place_id: str,
    session_token: Optional[str] = None,
    fields: str = "geometry,formatted_address",
    language: str = "en",
) -> Dict[str, Any]:
    """Cached Place Details. Stable across weeks — long TTL is safe."""
    api_key = await _get_api_key()
    if not api_key:
        raise RuntimeError("Google Maps API key not configured in app_settings")

    key_payload = {"pid": place_id, "fields": fields, "lang": language}
    cache_key = _hash_key("gmaps:place:det:", key_payload)

    params = {
        "place_id": place_id,
        "key": api_key,
        "fields": fields,
        "language": language,
    }
    if session_token:
        params["sessiontoken"] = session_token

    return await _cached_get(
        "place_details", cache_key, _TTL_PLACE_DETAILS,
        f"{_GMAPS_BASE}/place/details/json", params,
    )
