"""Render a PNG snapshot of a completed ride's route.

The admin drawer and the rider's email invoice both need a static image
of the ride path. We already store the driver's real GPS breadcrumbs
per phase in ``rides.phase_polylines`` (migration 39) — no external
routing service needed, just composite those polylines onto an
OSM-tile basemap.

``staticmap`` pulls raster tiles from OpenStreetMap, draws our markers
and lines, and returns a PIL Image. We render to PNG bytes and the
caller uploads to Cloudinary for permanent CDN hosting.

This runs synchronously inside a thread-pool executor (called from
asyncio in ``complete_ride``) so the driver's "Complete" request
doesn't block on the tile fetches — see backend/routes/drivers.py.
"""

from __future__ import annotations

import io
import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# OSM tile URL template. Low-volume admin usage fits OSM's fair-use
# policy; if we start generating thousands of snapshots a day we should
# switch to a paid tile provider or self-host.
_DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

# Render target — 2:1 aspect matches the drawer slot and most email
# clients without looking squished.
_WIDTH = 640
_HEIGHT = 320


def _coerce_polyline(raw: Iterable) -> list[tuple[float, float]]:
    """Normalise a ``phase_polylines`` entry to ``[(lng, lat), ...]``.

    phase_polylines stores ``[lat, lng, iso_ts]`` tuples but staticmap
    expects ``(lng, lat)``. Bad/short inputs return an empty list.
    """
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
    """Return PNG bytes for a ride's route, or None if rendering failed.

    Prefers ``phase_polylines`` (Phase 2 amber, Phase 3 blue) and falls
    back to the legacy combined ``route_polyline`` (single blue line) for
    rides that predate migration 39. If neither trail is available we
    still emit a usable snapshot with just the pickup/dropoff markers.
    """
    # Import inside the function so the module can load in environments
    # where staticmap isn't installed (tests, stub environments). The
    # caller catches the ImportError path via the returned None.
    try:
        from staticmap import CircleMarker, Line, StaticMap  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"staticmap not available; skipping snapshot: {exc}")
        return None

    pickup_trail = _coerce_polyline((phase_polylines or {}).get("navigating_to_pickup"))
    trip_trail = _coerce_polyline((phase_polylines or {}).get("trip_in_progress"))
    has_phase_trails = bool(pickup_trail) or bool(trip_trail)

    # Legacy combined polyline → single blue line when phase trails are absent.
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
        m = StaticMap(_WIDTH, _HEIGHT, url_template=_DEFAULT_TILE_URL, padding_x=30, padding_y=30)

        # Pickup = green, Dropoff = red. Match the colour convention the
        # drawer uses so operators see the same visual cues in the PNG
        # and in the live MapLibre view.
        m.add_marker(CircleMarker((pickup_lng, pickup_lat), "#ffffff", 14))
        m.add_marker(CircleMarker((pickup_lng, pickup_lat), "#10b981", 10))
        m.add_marker(CircleMarker((dropoff_lng, dropoff_lat), "#ffffff", 14))
        m.add_marker(CircleMarker((dropoff_lng, dropoff_lat), "#ef4444", 10))

        if pickup_trail:
            # Phase 2 (driver → pickup) — amber.
            m.add_line(Line(pickup_trail, "#f59e0b", 4))
        if trip_trail:
            # Phase 3 (pickup → dropoff) — blue. Drawn last so it sits
            # on top when the two phases overlap.
            m.add_line(Line(trip_trail, "#3b82f6", 4))
        if legacy_trail:
            m.add_line(Line(legacy_trail, "#3b82f6", 4))

        image = m.render()
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        # Common failures: tile-server 429, connection timeout, PIL
        # install mismatch. We never want these to block ride
        # completion, so return None and let the caller move on.
        logger.warning(f"Ride snapshot render failed: {exc}")
        return None
