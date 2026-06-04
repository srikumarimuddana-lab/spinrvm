"""Road-snapped trip distance + route geometry for billing and SGI map review.

After the haversine sum (with the spike filter from drivers.complete_ride),
this module recomputes the trip distance by snapping the GPS breadcrumbs to
the road network, so the billed distance respects the actual route the driver
drove (detours, turns, road curvature) instead of straight-line GPS hops. It
also returns the **road-matched geometry** so the saved trip map follows the
roads (raw GPS zig-zags; the snapped line is what an SGI/dispute map review
should see).

Two providers, in priority order:
  1. Self-hosted OSRM /match (preferred when OSRM_URL is configured). Map-
     matching snaps the whole noisy trace onto roads and returns both the
     matched route distance and its geometry. No per-call cost, our own infra.
  2. Google Roads API snapToRoads (fallback). The snapped points are both the
     distance basis and the geometry. Metered per request.

Failure modes are soft: any provider error, missing config, or insufficient
points returns None, and the caller falls back to the haversine value computed
in complete_ride (and keeps the raw GPS polyline). The caller also applies a
1/3x-3x sanity gate against the haversine baseline before trusting the distance
OR saving the road geometry, so a misbehaving provider can never corrupt a fare
or persist a bogus map.

Why map-matching (OSRM /match) and not routing (OSRM /route or Directions)?
  * /route returns the *optimal* path between endpoints, not the path the
    driver actually drove — wrong for billing when the driver detoured.
  * /match snaps the actual GPS trace onto the road network and returns the
    matched route, respecting the driven path while staying road-aligned.
"""

from __future__ import annotations

import logging
import math
from typing import Any, List, Optional, Tuple

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
# ms; Google Roads in 200-500 ms. Anything slower falls back to the haversine-
# filtered value (already spike-protected by the P0 filter in complete_ride).
_TIMEOUT_S = 2.0
_MIN_POINTS = 5  # below this, snap-to-road isn't reliable

# Google Roads snapToRoads
_SNAP_TO_ROAD_URL = "https://roads.googleapis.com/v1/snapToRoads"
# Google Roads nearestRoads — single-point snap for pickup coordinates.
_NEAREST_ROADS_URL = "https://roads.googleapis.com/v1/nearestRoads"
# If the nearest road is farther than this from the dropped pin, the snap is
# probably wrong (huge lot, bad geocode) — keep the original coordinate.
_MAX_SNAP_MOVE_M = 500.0
_GOOGLE_MAX_POINTS = 100  # Google's hard limit

# OSRM /match — default server max-matching-size is 100 coordinates/request.
_OSRM_MAX_POINTS = 100
_OSRM_RADIUS_MIN_M = 10
_OSRM_RADIUS_MAX_M = 50
_OSRM_RADIUS_DEFAULT_M = 20

# Cap the saved road geometry so the rides row stays bounded. The matched route
# can contain hundreds of vertices; ~300 is plenty for a faithful map replay.
_MAX_ROAD_POLYLINE_POINTS = 300

# A road match: (distance_km, polyline) where polyline is [[lat, lng], ...].
RoadMatch = Tuple[float, List[List[float]]]


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _downsample(points: list[dict], max_count: int) -> list[dict]:
    """Evenly downsample dict points to at most max_count, always keeping the last."""
    if len(points) <= max_count:
        return points
    if max_count < 2:
        return [points[-1]]
    step = len(points) / (max_count - 1)
    sampled = [points[int(i * step)] for i in range(max_count - 1)]
    if sampled[-1] is not points[-1]:
        sampled.append(points[-1])
    return sampled


def _cap_polyline(coords: List[List[float]], max_count: int) -> List[List[float]]:
    """Evenly downsample a [[lat,lng],...] line to at most max_count, keeping the last."""
    if len(coords) <= max_count:
        return coords
    if max_count < 2:
        return [coords[-1]]
    step = len(coords) / (max_count - 1)
    out = [coords[int(i * step)] for i in range(max_count - 1)]
    if out[-1] is not coords[-1]:
        out.append(coords[-1])
    return out


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


async def _compute_via_osrm(trip_points: list[dict], osrm_url: str) -> Optional[RoadMatch]:
    """Map-match the trace with OSRM /match; return (distance_km, [[lat,lng],...]).

    OSRM expects {lng},{lat} order. We sum every matching's distance and
    concatenate their geometries because a sparse/messy trace can be split into
    several matchings; gaps=ignore keeps a momentary signal loss from
    fragmenting the trip and tidy=true drops outliers before matching.
    overview=full + geometries=geojson returns the snapped road geometry.
    """
    sampled = _downsample(trip_points, _OSRM_MAX_POINTS)
    coords = ";".join(f"{p['lng']},{p['lat']}" for p in sampled)
    radiuses = ";".join(_osrm_radius(p) for p in sampled)
    url = f"{osrm_url.rstrip('/')}/match/v1/driving/{coords}"
    params = {
        "overview": "full",  # return the snapped road geometry, not just distance
        "geometries": "geojson",  # [[lng,lat],...] — no polyline decoder needed
        "steps": "false",
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
    polyline: List[List[float]] = []
    for m in data.get("matchings") or []:
        d = m.get("distance")
        if isinstance(d, (int, float)) and d > 0:
            total_m += float(d)
        # geometry.coordinates is [[lng, lat], ...] for geojson.
        for lng, lat in (m.get("geometry") or {}).get("coordinates") or []:
            polyline.append([round(float(lat), 6), round(float(lng), 6)])

    if total_m <= 0:
        return None
    return round(total_m / 1000.0, 3), _cap_polyline(polyline, _MAX_ROAD_POLYLINE_POINTS)


async def _compute_via_google_roads(trip_points: list[dict], api_key: str) -> Optional[RoadMatch]:
    """Snap the trace with Google Roads snapToRoads; return (distance_km, [[lat,lng],...])."""
    sampled = _downsample(trip_points, _GOOGLE_MAX_POINTS)
    path = "|".join(f"{p['lat']},{p['lng']}" for p in sampled)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                _SNAP_TO_ROAD_URL,
                params={
                    # interpolate=true returns extra snapped points filling the
                    # gaps along the road network between our samples — that's
                    # the polyline we sum AND the geometry we save.
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

    # Each snapped point is already on a road. Sum haversine between consecutive
    # snapped points for distance, and collect them as the road geometry.
    polyline: List[List[float]] = []
    total_km = 0.0
    prev: Optional[Tuple[float, float]] = None
    for sp in snapped:
        loc = sp.get("location") or {}
        lat, lng = loc.get("latitude"), loc.get("longitude")
        if lat is None or lng is None:
            continue
        polyline.append([round(float(lat), 6), round(float(lng), 6)])
        if prev is not None:
            total_km += _haversine_km(prev[0], prev[1], lat, lng)
        prev = (lat, lng)

    if total_km <= 0:
        return None
    return round(total_km, 3), _cap_polyline(polyline, _MAX_ROAD_POLYLINE_POINTS)


async def compute_road_route(breadcrumbs: list[dict], phase: str = "trip_in_progress") -> Optional[dict]:
    """Road-snap a single ride phase and return {"distance_km": float, "polyline": [[lat,lng],...]}.

    Prefers self-hosted OSRM /match when OSRM_URL (or the app_settings
    ``osrm_url`` override) is set; otherwise Google Roads snapToRoads. Returns
    None if no provider could compute a value — the caller MUST treat None as
    "keep the haversine-filtered distance and the raw GPS polyline".

    Args:
      breadcrumbs: ordered list of {lat, lng, tracking_phase, ...} dicts from
                   driver_location_history for a single ride.
      phase: which insurance/tracking phase to road-snap. Defaults to
             ``trip_in_progress`` (Phase 3) for back-compat; pass
             ``navigating_to_pickup`` to snap the driver→pickup leg (Phase 2).
    """
    if not breadcrumbs:
        return None

    trip_points = [
        b
        for b in breadcrumbs
        if b.get("tracking_phase") == phase and b.get("lat") is not None and b.get("lng") is not None
    ]
    if len(trip_points) < _MIN_POINTS:
        return None

    app_settings = await get_app_settings() or {}

    match: Optional[RoadMatch] = None
    provider: Optional[str] = None
    # OSRM first when configured. DB override (rotatable via admin) wins over env.
    osrm_url = (app_settings.get("osrm_url") or settings.OSRM_URL or "").strip()
    if osrm_url:
        match = await _compute_via_osrm(trip_points, osrm_url)
        if match is not None:
            provider = "osrm_match"
        else:
            logger.info("[route_distance] OSRM produced no value; trying Google Roads fallback")

    if match is None:
        api_key = (app_settings.get("google_maps_api_key") or "").strip()
        if api_key:
            match = await _compute_via_google_roads(trip_points, api_key)
            if match is not None:
                provider = "google_roads"

    if match is None:
        return None
    distance_km, polyline = match
    return {
        "distance_km": distance_km,
        "polyline": polyline,
        "provider": provider or "unknown",
        "input_points_count": len(trip_points),
    }


async def compute_road_distance_km(breadcrumbs: list[dict]) -> Optional[float]:
    """Back-compat shim: road-snapped distance (km) only, or None to fall back.

    Prefer compute_road_route() when you also need the geometry.
    """
    result = await compute_road_route(breadcrumbs)
    return result["distance_km"] if result else None


async def compute_route(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> Optional[dict]:
    """Live driver→destination route via OSRM /route (NOT /match).

    For the live share map: returns the optimal road route + ETA between the
    driver's current position and the destination (pickup or dropoff). Returns
    ``{"polyline": [[lat,lng],...], "eta_seconds": int, "distance_km": float}``
    or None when OSRM is unavailable (caller can fall back to a straight line /
    Google). This is /route (point-to-point optimal path), distinct from /match
    (snap a driven trace) used for billing.
    """
    app_settings = await get_app_settings() or {}
    osrm_url = (app_settings.get("osrm_url") or settings.OSRM_URL or "").strip()
    if not osrm_url:
        return None

    coords = f"{from_lng},{from_lat};{to_lng},{to_lat}"  # OSRM is lng,lat
    url = f"{osrm_url.rstrip('/')}/route/v1/driving/{coords}"
    params = {"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "false"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning("[route_distance] OSRM /route returned %d", resp.status_code)
                return None
            data = resp.json()
    except Exception as e:
        logger.warning("[route_distance] OSRM /route call failed: %s", e)
        return None

    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    r0 = data["routes"][0]
    polyline = [
        [round(float(lat), 6), round(float(lng), 6)]
        for lng, lat in ((r0.get("geometry") or {}).get("coordinates") or [])
    ]
    if len(polyline) < 2:
        return None
    return {
        "polyline": _cap_polyline(polyline, _MAX_ROAD_POLYLINE_POINTS),
        "eta_seconds": int(round(_num_or_zero(r0.get("duration")))),
        "distance_km": round(_num_or_zero(r0.get("distance")) / 1000.0, 3),
    }


async def snap_to_road(lat: float, lng: float) -> Optional[Tuple[float, float]]:
    """Snap a single (lat,lng) to the nearest drivable road point.

    Riders sometimes drop the pickup pin inside a building/mall where no car can
    stop. This returns a coordinate on the road network the driver can actually
    reach. OSRM /nearest first (self-hosted, free), Google Roads nearestRoads
    fallback. Returns (lat, lng) or None to keep the original (no provider
    configured, the nearest road is implausibly far, or an error).
    """
    app_settings = await get_app_settings() or {}

    # 1) OSRM /nearest — single-point snap, very fast on self-hosted infra.
    osrm_url = (app_settings.get("osrm_url") or settings.OSRM_URL or "").strip()
    if osrm_url:
        try:
            url = f"{osrm_url.rstrip('/')}/nearest/v1/driving/{lng},{lat}"  # OSRM is lng,lat
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.get(url, params={"number": 1})
            if resp.status_code == 200:
                data = resp.json()
                wps = data.get("waypoints") or []
                if data.get("code") == "Ok" and wps:
                    loc = wps[0].get("location") or []
                    moved_m = _num_or_zero(wps[0].get("distance"))
                    if len(loc) == 2 and moved_m <= _MAX_SNAP_MOVE_M:
                        return round(float(loc[1]), 6), round(float(loc[0]), 6)
        except Exception as e:
            logger.warning("[route_distance] OSRM /nearest failed: %s", e)

    # 2) Google Roads nearestRoads fallback.
    api_key = (app_settings.get("google_maps_api_key") or "").strip()
    if api_key:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.get(_NEAREST_ROADS_URL, params={"points": f"{lat},{lng}", "key": api_key})
            if resp.status_code == 200:
                pts = (resp.json() or {}).get("snappedPoints") or []
                if pts:
                    loc = pts[0].get("location") or {}
                    slat, slng = loc.get("latitude"), loc.get("longitude")
                    if slat is not None and slng is not None:
                        moved_m = _haversine_km(lat, lng, float(slat), float(slng)) * 1000.0
                        if moved_m <= _MAX_SNAP_MOVE_M:
                            return round(float(slat), 6), round(float(slng), 6)
        except Exception as e:
            logger.warning("[route_distance] Google nearestRoads failed: %s", e)

    return None


def _num_or_zero(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
