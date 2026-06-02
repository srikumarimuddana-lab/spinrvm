"""Road-snapped trip distance for billing (Feature P2 — billable distance).

After the haversine sum (with the spike filter from drivers.complete_ride),
this module recomputes the trip distance by snapping the GPS breadcrumbs to
the road network, so the billed distance respects the actual route the driver
drove (detours, turns, road curvature) instead of straight-line GPS hops.

Two providers, in priority order:
  1. Self-hosted OSRM /match (preferred when OSRM_URL is configured). Map-
     matching snaps the whole noisy trace onto roads and returns the matched
     route distance directly. No per-call cost, runs on our own infra.
  2. Google Roads API snapToRoads (fallback). Same idea, metered per request.

Failure modes are soft: any provider error, missing config, or insufficient
points returns None, and the caller falls back to the haversine value computed
in complete_ride. The caller stores BOTH values in ride_metrics so ops can
compare distributions before flipping fully to road-snapped billing, and it
applies a 1/3×–3× sanity gate against the haversine baseline — so a misbehaving
provider can never corrupt a fare.

Why map-matching (OSRM /match) and not routing (OSRM /route or Directions)?
  * /route returns the *optimal* path between endpoints, not the path the
    driver actually drove — wrong for billing when the driver detoured.
  * /match snaps the actual GPS trace onto the road network and returns the
    matched route, respecting the driven path while staying road-aligned.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import httpx

try:
    from ..settings_loader import get_app_settings
except ImportError:
    from settings_loader import get_app_settings  # type: ignore

try:
    from ..core.config import settings
except ImportError:
    from core.config import settings  # type: ignore

logger = logging.getLogger(__name__)

# Trip-end SLA is < 1 s; the snap call is on the hot completion path so we cap
# aggressively. Self-hosted OSRM on the same network usually answers in tens of
# ms; Google Roads in 200–500 ms. Anything slower falls back to the haversine-
# filtered value (already spike-protected by the P0 filter in complete_ride).
_TIMEOUT_S = 2.0
_MIN_POINTS = 5  # below this, snap-to-road isn't reliable

# Google Roads snapToRoads
_SNAP_TO_ROAD_URL = "https://roads.googleapis.com/v1/snapToRoads"
_GOOGLE_MAX_POINTS = 100  # Google's hard limit

# OSRM /match — default server max-matching-size is 100 coordinates/request.
_OSRM_MAX_POINTS = 100
_OSRM_RADIUS_MIN_M = 10
_OSRM_RADIUS_MAX_M = 50
_OSRM_RADIUS_DEFAULT_M = 20


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _downsample(points: list[dict], max_count: int) -> list[dict]:
    """Evenly downsample to at most max_count points, always including the last.

    Reserves one slot for the trailing dropoff point so the total never
    exceeds max_count. Earlier versions built max_count points and then
    appended the last one, producing max_count+1 — long trips would exceed the
    provider's coordinate cap and silently fall back to the haversine result,
    defeating the road-snap recompute for exactly the trips most likely to need
    it.
    """
    if len(points) <= max_count:
        return points
    if max_count < 2:
        return [points[-1]]
    # Pick max_count - 1 evenly spaced points across the input, then always
    # append the actual last point. End-to-end length is at most max_count.
    step = len(points) / (max_count - 1)
    sampled = [points[int(i * step)] for i in range(max_count - 1)]
    if sampled[-1] is not points[-1]:
        sampled.append(points[-1])
    return sampled


def _osrm_radius(point: dict) -> str:
    """Per-point match search radius (m), from GPS accuracy, clamped.

    OSRM rejects a coordinate it can't match within its radius, so we floor it
    generously for noisy fixes and cap it so a wildly inaccurate fix can't snap
    to the wrong road.
    """
    acc = point.get("accuracy")
    try:
        r = float(acc) if acc is not None else float(_OSRM_RADIUS_DEFAULT_M)
    except (TypeError, ValueError):
        r = float(_OSRM_RADIUS_DEFAULT_M)
    r = max(_OSRM_RADIUS_MIN_M, min(r, _OSRM_RADIUS_MAX_M))
    return str(int(r))


async def _compute_via_osrm(trip_points: list[dict], osrm_url: str) -> Optional[float]:
    """Map-match the trace with OSRM /match; return the matched distance (km).

    OSRM expects {lng},{lat} order. We sum every matching's distance because a
    sparse/messy trace can be split into several matchings; gaps=ignore keeps a
    momentary signal loss from fragmenting the trip and tidy=true drops
    outliers before matching.
    """
    sampled = _downsample(trip_points, _OSRM_MAX_POINTS)
    coords = ";".join(f"{p['lng']},{p['lat']}" for p in sampled)
    radiuses = ";".join(_osrm_radius(p) for p in sampled)
    url = f"{osrm_url.rstrip('/')}/match/v1/driving/{coords}"
    params = {
        "overview": "false",  # we only need distances, not geometry
        "steps": "false",
        "geometries": "polyline",
        "radiuses": radiuses,
        "gaps": "ignore",
        "tidy": "true",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning("[route_distance] OSRM returned %d", resp.status_code)
                return None
            data = resp.json()
    except Exception as e:
        # Any network / timeout / parse failure: silent None, caller falls back.
        logger.warning("[route_distance] OSRM call failed: %s", e)
        return None

    if data.get("code") != "Ok":
        logger.warning("[route_distance] OSRM code=%s", data.get("code"))
        return None

    total_m = 0.0
    for m in data.get("matchings") or []:
        d = m.get("distance")
        if isinstance(d, (int, float)) and d > 0:
            total_m += float(d)
    if total_m <= 0:
        return None
    return round(total_m / 1000.0, 3)


async def _compute_via_google_roads(trip_points: list[dict], api_key: str) -> Optional[float]:
    """Snap the trace with Google Roads snapToRoads; return the distance (km)."""
    sampled = _downsample(trip_points, _GOOGLE_MAX_POINTS)
    path = "|".join(f"{p['lat']},{p['lng']}" for p in sampled)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                _SNAP_TO_ROAD_URL,
                params={
                    # interpolate=true returns extra snapped points filling the
                    # gaps along the road network between our samples — that's
                    # the polyline we sum for the billable distance.
                    "path": path,
                    "interpolate": "true",
                    "key": api_key,
                },
            )
            if resp.status_code != 200:
                logger.warning("[route_distance] Roads API returned %d", resp.status_code)
                return None
            data = resp.json()
    except Exception as e:
        logger.warning("[route_distance] Roads API call failed: %s", e)
        return None

    snapped = data.get("snappedPoints") or []
    if len(snapped) < 2:
        return None

    # Sum haversine between consecutive snapped points. Each snapped point is
    # already on a road, so this approximates the driven road distance very
    # closely (much better than summing raw GPS, even after the spike filter).
    total_km = 0.0
    for i in range(1, len(snapped)):
        a = snapped[i - 1].get("location") or {}
        b = snapped[i].get("location") or {}
        lat1, lng1 = a.get("latitude"), a.get("longitude")
        lat2, lng2 = b.get("latitude"), b.get("longitude")
        if None in (lat1, lng1, lat2, lng2):
            continue
        total_km += _haversine_km(lat1, lng1, lat2, lng2)

    if total_km <= 0:
        return None
    return round(total_km, 3)


async def compute_road_distance_km(breadcrumbs: list[dict]) -> Optional[float]:
    """Recompute trip distance by snapping breadcrumbs to the road network.

    Prefers self-hosted OSRM /match when OSRM_URL (or the app_settings
    ``osrm_url`` override) is set; otherwise Google Roads snapToRoads. Returns
    km rounded to 3 dp, or None if no provider could compute a value — the
    caller MUST treat None as "keep the haversine-filtered value already
    computed".

    Args:
      breadcrumbs: ordered list of {lat, lng, tracking_phase, ...} dicts from
                   driver_location_history for a single ride.
    """
    if not breadcrumbs:
        return None

    trip_points = [
        b
        for b in breadcrumbs
        if b.get("tracking_phase") == "trip_in_progress" and b.get("lat") is not None and b.get("lng") is not None
    ]
    if len(trip_points) < _MIN_POINTS:
        return None

    app_settings = await get_app_settings() or {}

    # OSRM first when configured. DB override (rotatable via admin) wins over the
    # OSRM_URL env var.
    osrm_url = (app_settings.get("osrm_url") or settings.OSRM_URL or "").strip()
    if osrm_url:
        km = await _compute_via_osrm(trip_points, osrm_url)
        if km is not None:
            return km
        logger.info("[route_distance] OSRM produced no value; trying Google Roads fallback")

    api_key = (app_settings.get("google_maps_api_key") or "").strip()
    if api_key:
        return await _compute_via_google_roads(trip_points, api_key)

    return None
