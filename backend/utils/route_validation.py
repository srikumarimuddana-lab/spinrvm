"""Post-trip GPS route validation via OSRM /match, then Google Roads fallback.

After a ride completes, we snap the driver's GPS breadcrumbs to the road
network and measure match coverage/deviation. A legitimate trip has high road
match coverage (or low Google snap deviation). A spoofed trip tends to have
large unmatched/deviating stretches because fake coordinates rarely align with
actual roads.

This is how Uber/Lyft catch fake trips after the fact — even if the spoofer
bypasses all client-side checks, the server-side route doesn't match reality.

Usage:
    score = await validate_trip_route(ride_id)
    if score and score["deviation_pct"] > 30:
        # Flag for manual review — >30% of points far from any road
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

_SNAP_TO_ROAD_URL = "https://roads.googleapis.com/v1/snapToRoads"
_MAX_POINTS_PER_REQUEST = 100  # API limit
_DEVIATION_THRESHOLD_METERS = 50  # beyond 50m from nearest road = suspicious
_OSRM_RADIUS_MIN_M = 10
_OSRM_RADIUS_MAX_M = 50
_OSRM_RADIUS_DEFAULT_M = 20
_TIMEOUT_S = 10.0


def _haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _downsample(points: list[dict], max_count: int) -> list[dict]:
    """Evenly downsample points to fit API limit."""
    if len(points) <= max_count:
        return points
    step = len(points) / max_count
    return [points[int(i * step)] for i in range(max_count)]


def _osrm_radius(point: dict) -> str:
    try:
        accuracy = float(point.get("accuracy")) if point.get("accuracy") is not None else _OSRM_RADIUS_DEFAULT_M
    except (TypeError, ValueError):
        accuracy = _OSRM_RADIUS_DEFAULT_M
    return str(int(max(_OSRM_RADIUS_MIN_M, min(accuracy, _OSRM_RADIUS_MAX_M))))


async def _validate_via_osrm(trip_points: list[dict], osrm_url: str) -> Optional[dict]:
    """Validate a trace with OSRM /match and tracepoint match coverage."""
    sampled = _downsample(trip_points, _MAX_POINTS_PER_REQUEST)
    coords = ";".join(f"{p['lng']},{p['lat']}" for p in sampled)
    url = f"{osrm_url.rstrip('/')}/match/v1/driving/{coords}"
    params = {
        "overview": "false",
        "geometries": "geojson",
        "steps": "false",
        "radiuses": ";".join(_osrm_radius(p) for p in sampled),
        "gaps": "ignore",
        "tidy": "true",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning("[route_validation] OSRM returned %d", resp.status_code)
                return None
            data = resp.json()
    except Exception as exc:
        logger.warning("[route_validation] OSRM call failed: %s", exc)
        return None

    if data.get("code") != "Ok":
        logger.warning("[route_validation] OSRM code=%s", data.get("code"))
        return None

    tracepoints = data.get("tracepoints") or []
    total = len(sampled)
    matched = sum(1 for tp in tracepoints if tp)
    unmatched = max(0, total - matched)
    deviation_pct = (unmatched / total) * 100 if total else 0.0
    confidences = [float(m.get("confidence")) for m in (data.get("matchings") or []) if m.get("confidence") is not None]
    avg_confidence = sum(confidences) / len(confidences) if confidences else None

    if deviation_pct > 50 or (avg_confidence is not None and avg_confidence < 0.3):
        verdict = "likely_spoofed"
    elif deviation_pct > 20 or (avg_confidence is not None and avg_confidence < 0.6):
        verdict = "suspicious"
    else:
        verdict = "clean"

    return {
        "provider": "osrm_match",
        "total_points": total,
        "snapped_points": matched,
        "deviation_pct": round(deviation_pct, 1),
        "avg_deviation_m": None,
        "max_deviation_m": None,
        "match_confidence": round(avg_confidence, 3) if avg_confidence is not None else None,
        "verdict": verdict,
    }


async def validate_trip_route(
    breadcrumbs: list[dict],
) -> Optional[dict]:
    """Validate GPS breadcrumbs against road network.

    Returns:
        {
            "total_points": int,
            "snapped_points": int,
            "deviation_pct": float,  # % of points > 50m from nearest road
            "avg_deviation_m": float,
            "max_deviation_m": float,
            "verdict": "clean" | "suspicious" | "likely_spoofed"
        }
        or None if validation couldn't run (no API key, API error, etc.)
    """
    if not breadcrumbs or len(breadcrumbs) < 5:
        return None

    app_settings = await get_app_settings() or {}

    # Filter to trip_in_progress phase only (the actual ride)
    trip_points = [
        b
        for b in breadcrumbs
        if b.get("tracking_phase") == "trip_in_progress" and b.get("lat") is not None and b.get("lng") is not None
    ]
    if len(trip_points) < 5:
        return None

    osrm_url = (app_settings.get("osrm_url") or settings.OSRM_URL or "").strip()
    if osrm_url:
        osrm_result = await _validate_via_osrm(trip_points, osrm_url)
        if osrm_result is not None:
            return osrm_result

    api_key = (app_settings.get("google_maps_api_key") or "").strip()
    if not api_key:
        return None

    sampled = _downsample(trip_points, _MAX_POINTS_PER_REQUEST)

    path_str = "|".join(f"{p['lat']},{p['lng']}" for p in sampled)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                _SNAP_TO_ROAD_URL,
                params={
                    "path": path_str,
                    "interpolate": "false",
                    "key": api_key,
                },
            )
            if resp.status_code != 200:
                logger.warning("[route_validation] Roads API returned %d", resp.status_code)
                return None
            data = resp.json()
    except Exception as e:
        logger.warning("[route_validation] Roads API call failed: %s", e)
        return None

    snapped_points = data.get("snappedPoints", [])
    if not snapped_points:
        return {
            "provider": "google_roads",
            "total_points": len(sampled),
            "snapped_points": 0,
            "deviation_pct": 100.0,
            "avg_deviation_m": 999.0,
            "max_deviation_m": 999.0,
            "verdict": "likely_spoofed",
        }

    # Build index of snapped locations by originalIndex
    snapped_by_idx: dict[int, dict] = {}
    for sp in snapped_points:
        idx = sp.get("originalIndex")
        if idx is not None:
            loc = sp.get("location", {})
            snapped_by_idx[idx] = {
                "lat": loc.get("latitude"),
                "lng": loc.get("longitude"),
            }

    # Calculate deviation for each original point
    deviations: list[float] = []
    far_count = 0

    for i, original in enumerate(sampled):
        if i in snapped_by_idx:
            snapped = snapped_by_idx[i]
            dev = _haversine_meters(
                original["lat"],
                original["lng"],
                snapped["lat"],
                snapped["lng"],
            )
            deviations.append(dev)
            if dev > _DEVIATION_THRESHOLD_METERS:
                far_count += 1
        else:
            # Point wasn't snapped — no road nearby
            deviations.append(999.0)
            far_count += 1

    total = len(sampled)
    deviation_pct = (far_count / total) * 100 if total > 0 else 0
    avg_dev = sum(deviations) / len(deviations) if deviations else 0
    max_dev = max(deviations) if deviations else 0

    if deviation_pct > 50:
        verdict = "likely_spoofed"
    elif deviation_pct > 20:
        verdict = "suspicious"
    else:
        verdict = "clean"

    return {
        "provider": "google_roads",
        "total_points": total,
        "snapped_points": len(snapped_by_idx),
        "deviation_pct": round(deviation_pct, 1),
        "avg_deviation_m": round(avg_dev, 1),
        "max_deviation_m": round(max_dev, 1),
        "verdict": verdict,
    }
