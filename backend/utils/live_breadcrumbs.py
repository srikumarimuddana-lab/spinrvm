"""Gap-filled breadcrumb trail for live trip map.

Reuses the 4-tier gap fill strategy from route_reconstruction.py:
  Tier 1: Haversine (tiny gaps < 30m, < 30s)
  Tier 2: OSRM /route (road-following, gaps 30m–2km)
  Tier 3: Google Directions (fallback when OSRM unavailable)
  Tier 4: Haversine interpolation (always works, max connectors exceeded)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from ..core.config import settings
    from ..db_supabase import get_rows
    from ..settings_loader import get_app_settings
except ImportError:
    from core.config import settings  # type: ignore
    from db_supabase import get_rows  # type: ignore
    from settings_loader import get_app_settings  # type: ignore

try:
    from .datetime_utils import parse_iso_utc
    from .route_distance import (
        compute_gap_route_via_google,
        compute_gap_route_via_osrm,
    )
    from .route_reconstruction import (
        CONTINUITY_TOLERANCE_M,
        MAX_INFERRED_CONNECTORS,
    )
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.route_distance import (  # type: ignore
        compute_gap_route_via_google,
        compute_gap_route_via_osrm,
    )
    from utils.route_reconstruction import (  # type: ignore
        CONTINUITY_TOLERANCE_M,
        MAX_INFERRED_CONNECTORS,
    )

import math

logger = logging.getLogger(__name__)

# Performance bounds
MAX_BREADCRUMB_POINTS = 200
MAX_BREADCRUMB_AGE_SECONDS = 30 * 60  # 30 minutes


def _haversine_m(a: List[float], b: List[float]) -> float:
    """Haversine distance in meters between two [lat, lng] points."""
    R = 6_371_000.0
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    s = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(s), math.sqrt(1 - s))


def _time_gap_seconds(p1: Dict[str, Any], p2: Dict[str, Any]) -> int:
    t1 = parse_iso_utc(p1.get("captured_at"))
    t2 = parse_iso_utc(p2.get("captured_at"))
    if t1 and t2:
        return int((t2 - t1).total_seconds())
    return 0


def _interpolate_haversine(start: List[float], end: List[float], segments: int) -> List[List[float]]:
    """Linear interpolation on sphere. Returns intermediate points (excludes start)."""
    if segments <= 1:
        return [end]
    points = []
    for i in range(1, segments + 1):
        f = i / segments
        lat = start[0] + (end[0] - start[0]) * f
        lng = start[1] + (end[1] - start[1]) * f
        points.append([round(lat, 6), round(lng, 6)])
    return points


async def get_gap_filled_breadcrumbs(ride_id: str) -> List[List[float]]:
    """Return gap-filled breadcrumb polyline [[lat, lng], ...] for live map.

    Fetches recent raw GPS points and fills gaps between consecutive points
    using the same 4-tier strategy as post-trip reconstruction.
    """
    # Fetch recent raw GPS points for the trip_in_progress phase
    points = await get_rows(
        "driver_location_history",
        {"ride_id": ride_id, "tracking_phase": "trip_in_progress"},
        order="captured_at",
        limit=MAX_BREADCRUMB_POINTS,
    )

    if len(points) < 2:
        return []

    # Extract valid coordinates in chronological order
    coords: List[List[float]] = []
    valid_points: List[Dict[str, Any]] = []

    for p in points:
        lat, lng = p.get("lat"), p.get("lng")
        if lat is not None and lng is not None:
            coords.append([float(lat), float(lng)])
            valid_points.append(p)

    if len(coords) < 2:
        return []

    # Get provider configs
    app_settings = await get_app_settings() or {}
    osrm_url = (app_settings.get("osrm_url") or settings.OSRM_URL or "").strip()
    google_api_key = (app_settings.get("google_maps_api_key") or "").strip()

    # Gap-fill between consecutive points
    filled: List[List[float]] = [coords[0]]
    connector_attempts = 0

    for i in range(1, len(coords)):
        start = coords[i - 1]
        end = coords[i]
        gap_m = _haversine_m(start, end)
        gap_s = _time_gap_seconds(valid_points[i - 1], valid_points[i])

        # Skip fill if gap is tiny (continuity)
        if gap_m <= CONTINUITY_TOLERANCE_M and gap_s <= 30:
            filled.append(end)
            continue

        # Fill the gap
        connector_attempts += 1
        if connector_attempts > MAX_INFERRED_CONNECTORS:
            # Too many gaps — straight-line interpolate the rest
            filled.extend(_interpolate_haversine(start, end, max(2, int(gap_m / 50))))
            continue

        # Tier 1: OSRM /route (road-following)
        connector: Optional[tuple[float, List[List[float]]]] = None
        if osrm_url and gap_m > CONTINUITY_TOLERANCE_M:
            connector = await compute_gap_route_via_osrm(start, end, osrm_url)

        # Tier 2: Google Directions fallback
        if not connector and google_api_key and gap_m > CONTINUITY_TOLERANCE_M:
            connector = await compute_gap_route_via_google(start, end, google_api_key)

        if connector:
            _, connector_coords = connector
            # Append connector (skip first point to avoid duplicate)
            filled.extend(connector_coords[1:])
        else:
            # Tier 3/4: Haversine interpolation
            logger.debug(
                "live breadcrumb gap fill fell to haversine: gap=%.0fm, time=%ds, attempt=%d",
                gap_m,
                gap_s,
                connector_attempts,
            )
            filled.extend(_interpolate_haversine(start, end, max(2, int(gap_m / 50))))

    return filled


async def build_breadcrumb_trail(
    ride_id: str,
    current_driver_pos: List[float],
    destination: List[float],
) -> List[List[float]]:
    """Build complete breadcrumb trail for live map.

    Returns gap-filled historical path + straight line from last known position
    to current driver position (if different).
    """
    trail = await get_gap_filled_breadcrumbs(ride_id)

    if not trail:
        return []

    last_known = trail[-1]
    # If driver has moved significantly from last breadcrumb, add connector
    if _haversine_m(last_known, current_driver_pos) > CONTINUITY_TOLERANCE_M:
        # Try road-following connector to current position
        app_settings = await get_app_settings() or {}
        osrm_url = (app_settings.get("osrm_url") or settings.OSRM_URL or "").strip()
        google_api_key = (app_settings.get("google_maps_api_key") or "").strip()

        connector = None
        if osrm_url:
            connector = await compute_gap_route_via_osrm(last_known, current_driver_pos, osrm_url)
        if not connector and google_api_key:
            connector = await compute_gap_route_via_google(last_known, current_driver_pos, google_api_key)

        if connector:
            _, connector_coords = connector
            trail.extend(connector_coords[1:])
        else:
            # Straight line to current position
            trail.extend(_interpolate_haversine(last_known, current_driver_pos, 5))

    # Cap total points for performance
    if len(trail) > MAX_BREADCRUMB_POINTS:
        # Keep start, end, and evenly sample middle
        step = len(trail) / (MAX_BREADCRUMB_POINTS - 1)
        sampled = [trail[int(i * step)] for i in range(MAX_BREADCRUMB_POINTS - 1)]
        sampled.append(trail[-1])
        return sampled

    return trail
