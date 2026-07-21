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

import json
import logging
import math
from datetime import datetime, timezone
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

try:
    from .maps_budget import check_budget, record_call
    from .redis_client import redis_get, redis_set
except ImportError:
    from utils.maps_budget import check_budget, record_call  # type: ignore
    from utils.redis_client import redis_get, redis_set  # type: ignore

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
_MAX_COMPLETED_ENDPOINT_SNAP_M = 75.0

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


def _osrm_timestamp(point: dict) -> Optional[int]:
    """Return a Unix timestamp for OSRM /match, or None when unavailable."""
    # v2 breadcrumbs retain immutable device capture time. Prefer it over
    # server receipt time so OSRM's speed plausibility follows actual travel.
    ts = point.get("captured_at") or point.get("timestamp") or point.get("recorded_at") or point.get("created_at")
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # Expo Location timestamps are milliseconds; server rows are usually seconds/ISO.
        return int(ts / 1000) if ts > 10_000_000_000 else int(ts)
    if isinstance(ts, str):
        try:
            return int(float(ts))
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp())
        except ValueError:
            return None
    return None


def _osrm_bearing(point: dict) -> str:
    """Per-point heading constraint for OSRM /match.

    Empty string means "no bearing constraint" for that coordinate. When the
    phone provides heading/course, giving OSRM a directional hint helps avoid
    snapping a noisy fix to the opposite carriageway on divided roads.
    """
    bearing = point.get("heading")
    if bearing is None:
        bearing = point.get("bearing")
    if bearing is None:
        bearing = point.get("course")
    try:
        return f"{int(round(float(bearing))) % 360},45"
    except (TypeError, ValueError):
        return ""


async def _compute_osrm_chunk_matchings(trip_points: list[dict], osrm_url: str) -> Optional[List[RoadMatch]]:
    """Return each OSRM matching independently for one bounded input chunk.

    A matching boundary is evidence of an OSRM discontinuity. The segmented
    finalizer must retain that boundary; only the legacy single-route adapter
    below flattens these results for backwards compatibility.
    """
    coords = ";".join(f"{p['lng']},{p['lat']}" for p in trip_points)
    radiuses = ";".join(_osrm_radius(p) for p in trip_points)
    # OSRM timestamps are speed constraints, so send them only for v2 rows
    # carrying immutable device capture time. Legacy `timestamp` values were
    # assigned by the server while flushing a buffer; they preserve order but
    # can be microseconds apart and make a valid trace look impossible.
    timestamps = [_osrm_timestamp(p) if p.get("captured_at") is not None else None for p in trip_points]
    bearings = [_osrm_bearing(p) for p in trip_points]
    url = f"{osrm_url.rstrip('/')}/match/v1/driving/{coords}"
    params = {
        "overview": "full",  # return the snapped road geometry, not just distance
        "geometries": "geojson",  # [[lng,lat],...] — no polyline decoder needed
        "steps": "false",
        "radiuses": radiuses,
        "gaps": "split",
        "tidy": "true",
    }
    if all(ts is not None for ts in timestamps):
        params["timestamps"] = ";".join(str(ts) for ts in timestamps)
    if any(bearings):
        params["bearings"] = ";".join(bearings)

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

    matchings: List[RoadMatch] = []
    for m in data.get("matchings") or []:
        d = m.get("distance")
        if not isinstance(d, (int, float)) or d <= 0:
            continue
        # geometry.coordinates is [[lng, lat], ...] for geojson. De-duplicate
        # only inside this matching; never join separate matchings here.
        polyline: List[List[float]] = []
        for lng, lat in (m.get("geometry") or {}).get("coordinates") or []:
            point = [round(float(lat), 6), round(float(lng), 6)]
            if not polyline or polyline[-1] != point:
                polyline.append(point)
        if len(polyline) >= 2:
            matchings.append((round(float(d) / 1000.0, 3), _cap_polyline(polyline, _MAX_ROAD_POLYLINE_POINTS)))

    return matchings or None


async def _compute_via_osrm(trip_points: list[dict], osrm_url: str) -> Optional[RoadMatch]:
    """Legacy single-route adapter that flattens bounded OSRM matchings."""
    sampled = _downsample(trip_points, _OSRM_MAX_POINTS)
    matchings = await _compute_osrm_chunk_matchings(sampled, osrm_url)
    if not matchings:
        return None
    total_km = sum(distance_km for distance_km, _ in matchings)
    polyline: List[List[float]] = []
    for _, matching_geometry in matchings:
        for point in matching_geometry:
            if not polyline or polyline[-1] != point:
                polyline.append(point)
    return round(total_km, 3), _cap_polyline(polyline, _MAX_ROAD_POLYLINE_POINTS)


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


def _overlapping_chunks(points: list[dict], size: int = 90, overlap: int = 10):
    """Yield provider-safe chunks while retaining context at internal edges."""
    if size < 2 or overlap < 0 or overlap >= size:
        raise ValueError("chunk overlap must be non-negative and smaller than the chunk size")
    start = 0
    while start < len(points):
        yield points[start : start + size]
        if start + size >= len(points):
            break
        start += size - overlap


def _observed_segment_points(segment: Any) -> list[dict]:
    """Accept route-segment dataclasses, dict projections, or raw point lists."""
    if hasattr(segment, "points"):
        raw_points = segment.points
    elif isinstance(segment, dict):
        raw_points = segment.get("points") or segment.get("observed_points") or []
    else:
        raw_points = segment
    return [
        point
        for point in (raw_points or [])
        if isinstance(point, dict) and point.get("lat") is not None and point.get("lng") is not None
    ]


async def compute_segmented_road_route(observed_segments: list[Any]) -> dict:
    """Map-match each observed segment independently with bounded chunks.

    The returned ``matched_segments`` arrays are intentionally nested by
    observed segment and provider matching. Callers must render/snapshot each
    array separately, which prevents a straight chord across missing GPS data.
    """
    app_settings = await get_app_settings() or {}
    osrm_url = (app_settings.get("osrm_url") or settings.OSRM_URL or "").strip()
    google_api_key = (app_settings.get("google_maps_api_key") or "").strip()
    completed_segments: List[dict] = []
    failures: List[dict] = []
    providers = set()
    total_distance_km = 0.0

    for segment_index, observed_segment in enumerate(observed_segments):
        points = _observed_segment_points(observed_segment)
        matched_segments: List[dict] = []
        segment_distance_km = 0.0
        if len(points) < 2:
            failures.append({"segment_index": segment_index, "reason": "insufficient_points"})
            completed_segments.append(
                {"segment_index": segment_index, "distance_km": 0.0, "matched_segments": matched_segments}
            )
            continue

        for chunk_index, chunk in enumerate(_overlapping_chunks(points)):
            matchings: Optional[List[RoadMatch]] = None
            provider: Optional[str] = None
            if osrm_url:
                matchings = await _compute_osrm_chunk_matchings(chunk, osrm_url)
                if matchings:
                    provider = "osrm_match"
            if not matchings and google_api_key:
                google_match = await _compute_via_google_roads(chunk, google_api_key)
                if google_match:
                    matchings = [google_match]
                    provider = "google_roads"
            if not matchings or provider is None:
                failures.append(
                    {"segment_index": segment_index, "chunk_index": chunk_index, "reason": "provider_unavailable"}
                )
                continue

            for distance_km, polyline in matchings:
                # A provider must return an actual geometry; distance without
                # geometry cannot truthfully appear in an actual-route display.
                if distance_km <= 0 or len(polyline) < 2:
                    failures.append(
                        {
                            "segment_index": segment_index,
                            "chunk_index": chunk_index,
                            "reason": "invalid_provider_geometry",
                        }
                    )
                    continue
                matched_segments.append(
                    {
                        "chunk_index": chunk_index,
                        "provider": provider,
                        "distance_km": distance_km,
                        "polyline": [list(point) for point in polyline],
                    }
                )
                providers.add(provider)
                segment_distance_km += distance_km

        completed_segments.append(
            {
                "segment_index": segment_index,
                "distance_km": round(segment_distance_km, 3),
                "matched_segments": matched_segments,
            }
        )
        total_distance_km += segment_distance_km

    return {
        "segments": completed_segments,
        "distance_km": round(total_distance_km, 3),
        "provider": next(iter(providers)) if len(providers) == 1 else "mixed" if providers else None,
        "failures": failures,
    }


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
    """Live driver→destination route: OSRM /route first, Google Directions fallback.

    For the live trip map: returns the optimal road route + ETA between the
    driver's current position and the destination (pickup or dropoff) as
    ``{"polyline": [[lat,lng],...], "eta_seconds": int, "distance_km": float}``.
    Provider chain: OSRM via _live_osrm_url (self-hosted, then the public demo
    fallback when none is configured); if OSRM is unavailable or fails, the
    Google Directions API (budget-gated + cached) so the rider's route line
    keeps following the driver instead of freezing on the planned polyline.
    Returns None only when every provider fails — the client then keeps its
    saved line. This is /route (point-to-point optimal path), distinct from
    /match (snap a driven trace) used for billing.
    """
    app_settings = await get_app_settings() or {}
    osrm_url = _live_osrm_url(app_settings)
    if osrm_url:
        result = await _compute_route_via_osrm(from_lat, from_lng, to_lat, to_lng, osrm_url)
        if result is not None:
            return result
        logger.info("[route_distance] OSRM /route produced no value; trying Google Directions fallback")

    api_key = (app_settings.get("google_maps_api_key") or "").strip()
    if not api_key:
        return None
    return await _compute_route_via_google(from_lat, from_lng, to_lat, to_lng, api_key)


async def _compute_route_via_osrm(
    from_lat: float, from_lng: float, to_lat: float, to_lng: float, osrm_url: str
) -> Optional[dict]:
    """Point-to-point route via OSRM /route. None on any failure."""
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


async def snap_endpoint_via_osrm(point: dict, osrm_url: str) -> Optional[List[float]]:
    """Snap one completed-route guardrail to OSRM within 75 metres."""
    try:
        lat = float(point["lat"])
        lng = float(point["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    if not osrm_url or not math.isfinite(lat) or not math.isfinite(lng):
        return None

    url = f"{osrm_url.rstrip('/')}/nearest/v1/driving/{lng},{lat}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(url, params={"number": 1})
            if resp.status_code != 200:
                logger.warning("[route_distance] completed-route OSRM /nearest returned %d", resp.status_code)
                return None
            data = resp.json()
    except Exception:
        logger.warning("[route_distance] completed-route OSRM /nearest failed", exc_info=True)
        return None

    waypoints = data.get("waypoints") if data.get("code") == "Ok" else None
    waypoint = waypoints[0] if isinstance(waypoints, list) and waypoints else None
    if not isinstance(waypoint, dict):
        return None
    try:
        distance_m = float(waypoint["distance"])
        snapped_lng, snapped_lat = waypoint["location"]
        snapped_lat = float(snapped_lat)
        snapped_lng = float(snapped_lng)
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not all(math.isfinite(value) for value in (distance_m, snapped_lat, snapped_lng))
        or distance_m < 0
        or distance_m > _MAX_COMPLETED_ENDPOINT_SNAP_M
    ):
        return None
    return [round(snapped_lat, 6), round(snapped_lng, 6)]


async def compute_gap_route_via_osrm(start: List[float], end: List[float], osrm_url: str) -> Optional[RoadMatch]:
    """Route one missing completed-trip interval without using OSRM Trip."""
    if len(start) < 2 or len(end) < 2 or not osrm_url:
        return None
    try:
        start_lat, start_lng = float(start[0]), float(start[1])
        end_lat, end_lng = float(end[0]), float(end[1])
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (start_lat, start_lng, end_lat, end_lng)):
        return None

    routed = await _compute_route_via_osrm(start_lat, start_lng, end_lat, end_lng, osrm_url)
    if not routed:
        return None
    direct_km = _haversine_km(start_lat, start_lng, end_lat, end_lng)
    distance_km = float(routed.get("distance_km") or 0)
    maximum_km = max(direct_km * 5.0, direct_km + 2.0)
    if distance_km < direct_km or distance_km > maximum_km:
        return None

    polyline: List[List[float]] = []
    for coordinate in routed.get("polyline") or []:
        if len(coordinate) < 2:
            continue
        normalized = [round(float(coordinate[0]), 6), round(float(coordinate[1]), 6)]
        if not polyline or polyline[-1] != normalized:
            polyline.append(normalized)
    if len(polyline) < 2:
        return None
    return round(distance_km, 3), polyline


# Google Directions API (fallback for the live route line when OSRM is down).
_DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
# Clients poll the live route every ~20 s and a shared-trip page may poll the
# same ride concurrently. Caching on a ~110 m origin grid (3 decimals) dedupes
# concurrent viewers and stationary drivers without hiding real movement.
# Must exceed the ~20 s client poll interval, or a stationary driver's next
# poll always misses the expired entry and pays for a fresh Directions call.
_LIVE_ROUTE_CACHE_TTL_S = 30


def _decode_encoded_polyline(encoded: str) -> List[List[float]]:
    """Decode a Google encoded polyline (1e-5 precision) into [[lat, lng], ...]."""
    coords: List[List[float]] = []
    index = lat = lng = 0
    while index < len(encoded):
        deltas: List[int] = []
        for _ in range(2):
            shift = result = 0
            while True:
                if index >= len(encoded):  # truncated input — keep what decoded cleanly
                    return coords
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lng += deltas[1]
        coords.append([round(lat * 1e-5, 6), round(lng * 1e-5, 6)])
    return coords


async def _compute_route_via_google(
    from_lat: float, from_lng: float, to_lat: float, to_lng: float, api_key: str
) -> Optional[dict]:
    """Point-to-point route via Google Directions. Budget-gated and Redis-cached.

    No departure_time param on purpose: traffic-aware requests bill at the
    Advanced SKU; the fallback only needs road geometry + a reasonable ETA.
    """
    cache_key = f"live_route:google:{round(from_lat, 3)},{round(from_lng, 3)}:{round(to_lat, 5)},{round(to_lng, 5)}"
    try:
        cached = await redis_get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.warning("[route_distance] live-route cache get failed", exc_info=False)

    allowed, spent, budget = await check_budget()
    if not allowed:
        logger.warning(
            "[route_distance] Maps daily budget reached (%.2f/%.2f USD) — skipping Directions fallback",
            spent,
            budget,
        )
        return None

    params = {
        "origin": f"{from_lat},{from_lng}",
        "destination": f"{to_lat},{to_lng}",
        "mode": "driving",
        "key": api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(_DIRECTIONS_URL, params=params)
        await record_call("directions")
        if resp.status_code != 200:
            logger.warning("[route_distance] Directions API returned %d", resp.status_code)
            return None
        data = resp.json()
    except Exception as e:
        logger.warning("[route_distance] Directions API call failed: %s", e)
        return None

    routes = data.get("routes") or []
    if data.get("status") != "OK" or not routes:
        logger.warning("[route_distance] Directions status=%s", data.get("status"))
        return None
    r0 = routes[0]
    polyline = _decode_encoded_polyline(((r0.get("overview_polyline") or {}).get("points")) or "")
    if len(polyline) < 2:
        return None
    legs = r0.get("legs") or []
    duration_s = sum(_num_or_zero((leg.get("duration") or {}).get("value")) for leg in legs)
    distance_m = sum(_num_or_zero((leg.get("distance") or {}).get("value")) for leg in legs)
    result = {
        "polyline": _cap_polyline(polyline, _MAX_ROAD_POLYLINE_POINTS),
        "eta_seconds": int(round(duration_s)),
        "distance_km": round(distance_m / 1000.0, 3),
    }
    try:
        await redis_set(cache_key, json.dumps(result), ttl=_LIVE_ROUTE_CACHE_TTL_S)
    except Exception:
        logger.warning("[route_distance] live-route cache set failed", exc_info=False)
    return result


async def snap_to_road(lat: float, lng: float) -> Optional[Tuple[float, float]]:
    """Snap a single (lat,lng) to the nearest drivable road point.

    Riders sometimes drop the pickup pin inside a building/mall where no car can
    stop. This returns a coordinate on the road network the driver can actually
    reach. OSRM /nearest first (self-hosted, free), Google Roads nearestRoads
    fallback. Returns (lat, lng) or None to keep the original (no provider
    configured, the nearest road is implausibly far, or an error).
    """
    app_settings = await get_app_settings() or {}

    # 1) OSRM /nearest — single-point snap; public-OSRM fallback applies
    #    when no self-hosted instance is configured (see _live_osrm_url).
    osrm_url = _live_osrm_url(app_settings)
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


def _live_osrm_url(app_settings: dict) -> str:
    """OSRM base URL for LIGHT live calls (/route line+ETA, /nearest snap).

    Resolution: app_settings ``osrm_url`` (admin-rotatable) → OSRM_URL env →
    OSRM_FALLBACK_URL (public demo server; see core/config.py for the
    fair-use caveat). The /match billing path deliberately does NOT use the
    fallback — billable distance only comes from explicitly configured infra.
    """
    return (
        (app_settings.get("osrm_url") or "").strip()
        or settings.OSRM_URL.strip()
        or getattr(settings, "OSRM_FALLBACK_URL", "").strip()
    )


def _num_or_zero(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
