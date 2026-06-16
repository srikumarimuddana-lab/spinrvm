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

    # Build path from phase_polylines or route_polyline as (lat, lng) floats.
    trail: list[tuple[float, float]] = []

    trip_trail = _extract_trail((phase_polylines or {}).get("trip_in_progress"))

    # The snapshot shows the travelled pickup→dropoff route ONLY. The
    # navigating_to_pickup leg is deliberately excluded: drawn alongside the
    # trip leg it reads as a second route on the receipt map (admin tooling
    # replays per-phase trails from ride_routes instead).
    #
    # Require at least 10 trip-leg points before trusting the raw trail —
    # gated on the trip leg ALONE, not the combined phase count: a dense
    # pickup leg used to carry a sparse trip leg over the threshold, and the
    # Static Maps API then drew straight chords between the few trip points
    # (the reported "straight line between P and D" next to the real path).
    # Fall through to route_polyline in that case.
    if len(trip_trail) >= 10:
        trail = trip_trail
    elif route_polyline:
        for pt in route_polyline:
            try:
                trail.append((float(pt[0]), float(pt[1])))
            except (TypeError, ValueError, IndexError):
                continue

    logger.info(
        "render_ride_snapshot_google: trail_points=%d, route_polyline=%s (len=%d), phase_polylines keys=%s",
        len(trail),
        type(route_polyline).__name__ if route_polyline else "None",
        len(route_polyline) if isinstance(route_polyline, list) else 0,
        list((phase_polylines or {}).keys()),
    )

    if trail:
        # Sample down to keep URL under ~8192 chars.
        if len(trail) > 80:
            step = max(1, len(trail) // 80)
            sampled = trail[::step]
            if sampled[-1] != trail[-1]:
                sampled.append(trail[-1])
            trail = sampled

        # Break the trail wherever one hop is a far outlier vs the typical
        # spacing (GPS dropout, OSRM multi-matching jump). Without this, the
        # Static Maps API connects the two sides with a straight chord that
        # reads as a SECOND red line running across the map next to the real
        # route — the "duplicate route line" artifact. Each gap-free run is
        # drawn on its own; we never draw the bridging chord.
        runs = _split_on_gaps(trail)

        # Colour every run by its GLOBAL position along the trail so the
        # gradient (orange #FF9500 → red #EE2B2B) stays continuous across the
        # break, matching the app's MapView gradient polyline.
        SEGS = 10
        total = len(trail)
        chunk = max(2, total // SEGS)
        global_idx = 0
        for run in runs:
            n = len(run)
            for i in range(0, n - 1, chunk):
                end = min(i + chunk + 1, n)
                t = (global_idx + i) / max(total - 1, 1)
                r = int(255 + (238 - 255) * t)
                g = int(149 + (43 - 149) * t)
                b = int(0 + (43 - 0) * t)
                seg = run[i:end]
                if len(seg) >= 2:
                    seg_str = "|".join(f"{lat},{lng}" for lat, lng in seg)
                    params.append(f"path=color:0x{r:02X}{g:02X}{b:02X}FF|weight:4|{seg_str}")
            global_idx += n

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


def _extract_trail(raw: Optional[list]) -> list[tuple[float, float]]:
    """Convert phase_polylines entry [lat, lng, ts] to (lat, lng) float tuples."""
    if not raw:
        return []
    out: list[tuple[float, float]] = []
    for pt in raw:
        try:
            out.append((float(pt[0]), float(pt[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


# A single hop longer than this AND more than _GAP_FACTOR× the trail's median
# hop is treated as a discontinuity (GPS dropout / OSRM matching jump) rather
# than a real road segment, and the bridging chord is not drawn. The relative
# test is what does the work; the absolute floor stops a dense urban trail
# (tiny median) from fragmenting on ordinary block-to-block spacing.
_GAP_MIN_KM = 0.75
_GAP_FACTOR = 6.0


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math

    lat1, lng1 = a
    lat2, lng2 = b
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _split_on_gaps(coords: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Split a polyline into contiguous runs, breaking at outlier hops.

    Returns the list of runs (each with >= 2 points). A trail with no outlier
    hop comes back as a single run, so normal routes are unaffected. Trails of
    fewer than 3 points can't have an outlier to compare against and pass
    through unchanged.
    """
    if len(coords) < 3:
        return [coords] if len(coords) >= 2 else []
    hops = [_haversine_km(coords[i - 1], coords[i]) for i in range(1, len(coords))]
    ordered = sorted(hops)
    median = ordered[len(ordered) // 2]
    threshold = max(_GAP_MIN_KM, _GAP_FACTOR * median) if median > 0 else _GAP_MIN_KM
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = [coords[0]]
    for i, hop in enumerate(hops, start=1):
        if hop > threshold:
            runs.append(current)
            current = [coords[i]]
        else:
            current.append(coords[i])
    runs.append(current)
    return [r for r in runs if len(r) >= 2]


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

    # Trip leg only — see render_ride_snapshot_google for why the
    # navigating_to_pickup leg is excluded from the snapshot.
    trip_trail = _coerce_polyline((phase_polylines or {}).get("trip_in_progress"))

    legacy_trail: list[tuple[float, float]] = []
    if not trip_trail and route_polyline:
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

        # Same gap guard as the Google renderer: draw each contiguous run on
        # its own so a GPS dropout / matching jump doesn't get bridged by a
        # straight chord that reads as a second route. staticmap uses
        # (lng, lat); _split_on_gaps works in (lat, lng), so convert in/out.
        def _add_split_line(coords_lnglat: list[tuple[float, float]]) -> None:
            latlng = [(la, ln) for ln, la in coords_lnglat]
            for run in _split_on_gaps(latlng):
                m.add_line(Line([(ln, la) for la, ln in run], "#3b82f6", 4))

        if trip_trail:
            _add_split_line(trip_trail)
        if legacy_trail:
            _add_split_line(legacy_trail)

        image = m.render()
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Ride snapshot render failed: {exc}")
        return None
