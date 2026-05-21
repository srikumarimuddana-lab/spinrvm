"""Road-snapped trip distance for billing (Feature P2 — billable distance).

After the haversine sum (with the spike filter from drivers.complete_ride),
this module recomputes the trip distance by snapping the GPS breadcrumbs
to the road network via Google's Roads API. The snapped polyline respects
the actual route the driver took (detours included), so it's more
accurate than raw GPS sum AND respects road geometry (turns, curves)
that haversine straight-line distance underestimates.

Failure modes are soft: any API error, missing key, or insufficient
points returns None, and the caller falls back to the haversine value
computed in complete_ride. The caller stores BOTH values in ride_metrics
so ops can compare distributions before flipping fully to road-snapped
billing.

Why snapToRoads with interpolate=true (not Directions API)?
  * Directions returns Google's optimal route, not the route the driver
    actually drove — bad for billing when the driver detoured.
  * snapToRoads with interpolate=true respects the actual driven path
    while still being road-aligned (it returns intermediate snapped
    points along the road network connecting our sample).
  * 100 points/request (vs Directions' 25 waypoints) means we can keep
    higher polyline fidelity in one call.
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

logger = logging.getLogger(__name__)

_SNAP_TO_ROAD_URL = "https://roads.googleapis.com/v1/snapToRoads"
_MAX_POINTS_PER_REQUEST = 100  # Google's hard limit
# Trip-end SLA is < 1 s; the snap call is on the hot completion path so we
# cap aggressively. Most successful calls finish in 200–500 ms; anything
# slower falls back to the haversine-filtered value (which is already
# spike-protected by the P0 filter in complete_ride).
_TIMEOUT_S = 2.0
_MIN_POINTS = 5  # below this, snap-to-road isn't reliable


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
    appended the last one, producing max_count+1 — long trips would send
    101 points to Roads API (which caps at 100) and silently fall back to
    the haversine result, defeating the road-snap recompute for exactly
    the trips most likely to need it.
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


async def compute_road_distance_km(breadcrumbs: list[dict]) -> Optional[float]:
    """Recompute trip distance by snapping breadcrumbs to the road network.

    Args:
      breadcrumbs: ordered list of {lat, lng, tracking_phase, ...} dicts
                   from driver_location_history for a single ride.

    Returns:
      km as a float, rounded to 3 decimals, OR None if the API was unable
      to compute a value (no key, < _MIN_POINTS in trip phase, HTTP error,
      empty response). The caller MUST treat None as "fall back to the
      haversine-filtered value already computed".
    """
    if not breadcrumbs:
        return None

    settings = await get_app_settings()
    api_key = (settings or {}).get("google_maps_api_key", "")
    if not api_key:
        return None

    trip_points = [
        b
        for b in breadcrumbs
        if b.get("tracking_phase") == "trip_in_progress" and b.get("lat") is not None and b.get("lng") is not None
    ]
    if len(trip_points) < _MIN_POINTS:
        return None

    sampled = _downsample(trip_points, _MAX_POINTS_PER_REQUEST)
    path = "|".join(f"{p['lat']},{p['lng']}" for p in sampled)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                _SNAP_TO_ROAD_URL,
                params={
                    # interpolate=true returns extra snapped points filling
                    # the gaps along the road network between our samples
                    # — that's the polyline we sum for the billable distance.
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
        # Any network / timeout / parse failure: silent None, caller falls back.
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
