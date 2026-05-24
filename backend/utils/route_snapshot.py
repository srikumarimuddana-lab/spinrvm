"""Render a PNG snapshot of a ride's route via Google Static Maps API.

Used for:
- Email invoice route image
- Admin dashboard ride drawer
- Driver/rider app ride detail (snapshot-first, MapView fallback)

The snapshot is generated at ride creation (planned route) and again at
ride completion (actual GPS path). Uploaded to Supabase Storage as a
public PNG for permanent CDN hosting.

This runs inside an async context — the Google Static Maps call is a
single HTTP fetch, no PIL/staticmap dependency needed.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_WIDTH = 640
_HEIGHT = 320
_STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"


async def render_ride_snapshot_google(
    *,
    api_key: str,
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    phase_polylines: Optional[dict] = None,
    route_polyline: Optional[list] = None,
) -> Optional[bytes]:
    """Fetch a PNG from Google Static Maps API with route drawn.

    Returns PNG bytes or None on failure. Never raises.
    """
    params: list[str] = [
        f"size={_WIDTH}x{_HEIGHT}",
        "maptype=roadmap",
        f"markers=color:green|label:P|{pickup_lat},{pickup_lng}",
        f"markers=color:red|label:D|{dropoff_lat},{dropoff_lng}",
    ]

    # Build path from phase_polylines or route_polyline
    trail_points: list[str] = []

    pickup_trail = _extract_trail((phase_polylines or {}).get("navigating_to_pickup"))
    trip_trail = _extract_trail((phase_polylines or {}).get("trip_in_progress"))

    if pickup_trail or trip_trail:
        trail_points = pickup_trail + trip_trail
    elif route_polyline:
        for pt in route_polyline:
            try:
                lat = float(pt[0])
                lng = float(pt[1])
                trail_points.append(f"{lat},{lng}")
            except (TypeError, ValueError, IndexError):
                continue

    if trail_points:
        # Google Static Maps URL limit is ~8192 chars. Sample if needed.
        if len(trail_points) > 80:
            step = max(1, len(trail_points) // 80)
            sampled = trail_points[::step]
            if sampled[-1] != trail_points[-1]:
                sampled.append(trail_points[-1])
            trail_points = sampled

        path_str = "|".join(trail_points)
        params.append(f"path=color:0x3B82F6FF|weight:4|{path_str}")

    params.append(f"key={api_key}")
    url = f"{_STATIC_MAPS_URL}?{'&'.join(params)}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(
                "Google Static Maps returned %s: %s",
                resp.status_code,
                resp.text[:200],
            )
            return None
        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type:
            logger.warning("Google Static Maps returned non-image content-type: %s", content_type)
            return None
        return resp.content
    except Exception as exc:
        logger.warning("Google Static Maps fetch failed: %s", exc)
        return None


def _extract_trail(raw: Optional[list]) -> list[str]:
    """Convert phase_polylines entry [lat, lng, ts] to 'lat,lng' strings."""
    if not raw:
        return []
    out: list[str] = []
    for pt in raw:
        try:
            lat = float(pt[0])
            lng = float(pt[1])
            out.append(f"{lat},{lng}")
        except (TypeError, ValueError, IndexError):
            continue
    return out


# Keep the old OSM-based renderer as fallback if Google API key is unavailable.
def _coerce_polyline(raw) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if not raw:
        return out
    for pt in raw:
        try:
            lat = float(pt[0])
            lng = float(pt[1])
        except (TypeError, ValueError, IndexError):
            continue
        out.append((lng, lat))
    return out


def render_ride_snapshot(
    *,
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    phase_polylines: Optional[dict] = None,
    route_polyline: Optional[list] = None,
) -> Optional[bytes]:
    """OSM/staticmap fallback — used only when Google API key is unavailable."""
    try:
        from staticmap import CircleMarker, Line, StaticMap  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"staticmap not available; skipping snapshot: {exc}")
        return None

    import io

    pickup_trail = _coerce_polyline((phase_polylines or {}).get("navigating_to_pickup"))
    trip_trail = _coerce_polyline((phase_polylines or {}).get("trip_in_progress"))
    has_phase_trails = bool(pickup_trail) or bool(trip_trail)

    legacy_trail: list[tuple[float, float]] = []
    if not has_phase_trails and route_polyline:
        for pt in route_polyline:
            try:
                lat = float(pt[0])
                lng = float(pt[1])
            except (TypeError, ValueError, IndexError):
                continue
            legacy_trail.append((lng, lat))

    try:
        _OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        m = StaticMap(640, 320, url_template=_OSM_TILE_URL, padding_x=30, padding_y=30)
        m.add_marker(CircleMarker((pickup_lng, pickup_lat), "#ffffff", 14))
        m.add_marker(CircleMarker((pickup_lng, pickup_lat), "#10b981", 10))
        m.add_marker(CircleMarker((dropoff_lng, dropoff_lat), "#ffffff", 14))
        m.add_marker(CircleMarker((dropoff_lng, dropoff_lat), "#ef4444", 10))

        if pickup_trail:
            m.add_line(Line(pickup_trail, "#f59e0b", 4))
        if trip_trail:
            m.add_line(Line(trip_trail, "#3b82f6", 4))
        if legacy_trail:
            m.add_line(Line(legacy_trail, "#3b82f6", 4))

        image = m.render()
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Ride snapshot render failed: {exc}")
        return None
