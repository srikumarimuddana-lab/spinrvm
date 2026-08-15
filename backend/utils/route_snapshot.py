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

# Uniform route styling — mirrors shared/constants/routeMapStyle.ts so the receipt
# PNG reads the same as every in-app map: the REAL route trail coloured as ONE
# orange→red gradient along its length (orange at the start, red at the end).
_ROUTE_STROKE_WIDTH = 4
_GRADIENT_START_RGB = (255, 149, 0)  # #FF9500
_GRADIENT_END_RGB = (238, 43, 43)  # #EE2B2B
_GRADIENT_TOTAL = 24  # total colour sub-segments across the whole route (URL budget)


def normalize_polyline_points(value: object) -> Optional[list[list[float]]]:
    """Coerce a stored `planned_route_polyline` to `[[lat, lng], …]`.

    The column's contract (migration 100) is a decoded `[[lat, lng], …]`, but
    the legacy-import backfill briefly wrote `{"lat": …, "lng": …}` objects
    instead (repaired by migration 313). Accept either so a snapshot render
    never silently drops the route on a row that hasn't been converted yet.
    Returns None when nothing usable is left.
    """
    if not isinstance(value, list):
        return None
    points: list[list[float]] = []
    for p in value:
        if isinstance(p, dict):
            lat, lng = p.get("lat"), p.get("lng")
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            lat, lng = p[0], p[1]
        else:
            continue
        if isinstance(lat, bool) or isinstance(lng, bool):
            continue
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            points.append([float(lat), float(lng)])
    return points or None


def _gradient_rgb(t: float) -> tuple[int, int, int]:
    t = 0.0 if t != t else max(0.0, min(1.0, t))  # NaN → 0
    return tuple(round(_GRADIENT_START_RGB[i] + (_GRADIENT_END_RGB[i] - _GRADIENT_START_RGB[i]) * t) for i in range(3))  # type: ignore[return-value]


def _gradient_runs(trails: list[list[tuple[float, float]]]) -> tuple[list[list[tuple[float, float]]], int]:
    """Sample + gap-split the real route into drawable runs and the total segment
    count, with a bounded sample budget so the gradient stays cheap."""
    runs = [t for t in trails if len(t) >= 2]
    if not runs:
        return [], 0
    per = max(2, _GRADIENT_TOTAL // len(runs) + 1)
    pieces: list[list[tuple[float, float]]] = []
    for trail in runs:
        for run in _split_on_gaps(_sample_trail(trail, maximum=per)):
            if len(run) >= 2:
                pieces.append(run)
    total = sum(len(p) - 1 for p in pieces)
    return pieces, total


async def render_ride_snapshot_google(
    *,
    api_key: str,
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    phase_polylines: Optional[dict] = None,
    route_polyline: Optional[list] = None,
    route_segments: Optional[list] = None,
    completion_point: Optional[dict] = None,
    route_quality: Optional[dict] = None,
) -> Optional[bytes]:
    """Fetch a PNG from Google Static Maps API with a gap-safe route drawn.

    ``route_segments`` is the v2 source of truth. Each segment is emitted as
    an independent Static Maps ``path`` so the provider never draws a chord
    across a capture gap. Legacy inputs retain their previous behavior.
    Returns PNG bytes or None on failure. Never raises.
    """
    params: list[str] = [
        f"size={_WIDTH}x{_HEIGHT}",
        "maptype=roadmap",
        f"markers=color:green|label:P|{pickup_lat},{pickup_lng}",
        f"markers=color:red|label:D|{dropoff_lat},{dropoff_lng}",
    ]
    completion = _coerce_coordinate(completion_point)
    if completion:
        params.append(f"markers=color:orange|label:C|{completion[0]},{completion[1]}")

    # The REAL route trail, coloured as one orange→red gradient along its length
    # (uniform with every in-app map). Segment boundaries / gaps are still
    # preserved — never flattened into a chord — but every drawn segment is now
    # coloured by its position, not a two-tone by index.
    if route_segments is not None:
        trails = _extract_segment_trails(route_segments)
    else:
        legacy = _extract_trail((phase_polylines or {}).get("trip_in_progress"))
        if len(legacy) < 10:
            legacy = _extract_trail(route_polyline)
        trails = [legacy] if len(legacy) >= 2 else []

    pieces, total = _gradient_runs(trails)
    idx = 0
    for run in pieces:
        for i in range(len(run) - 1):
            r, g, b = _gradient_rgb((idx + 0.5) / total) if total else _GRADIENT_START_RGB
            params.append(
                f"path=color:0x{r:02X}{g:02X}{b:02X}FF|weight:{_ROUTE_STROKE_WIDTH}|"
                f"{run[i][0]},{run[i][1]}|{run[i + 1][0]},{run[i + 1][1]}"
            )
            idx += 1

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
        return _add_route_quality_banner(resp.content, route_quality) if _is_incomplete(route_quality) else resp.content
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


def _coerce_coordinate(raw: Optional[dict]) -> Optional[tuple[float, float]]:
    if not isinstance(raw, dict):
        return None
    try:
        return float(raw["lat"]), float(raw["lng"])
    except (KeyError, TypeError, ValueError):
        return None


def _extract_segment_trails(raw_segments: Optional[list]) -> list[list[tuple[float, float]]]:
    """Return each v2 segment independently; no segment is ever concatenated."""
    trails: list[list[tuple[float, float]]] = []
    for segment in raw_segments or []:
        raw_points = segment.get("coordinates") or segment.get("points") if isinstance(segment, dict) else segment
        trail = _extract_trail(raw_points if isinstance(raw_points, list) else None)
        if len(trail) >= 2:
            trails.append(trail)
    return trails


def _sample_trail(trail: list[tuple[float, float]], maximum: int = 80) -> list[tuple[float, float]]:
    if len(trail) <= maximum:
        return trail
    step = max(1, len(trail) // maximum)
    sampled = trail[::step]
    if sampled[-1] != trail[-1]:
        sampled.append(trail[-1])
    return sampled


def _append_path(params: list[str], trail: list[tuple[float, float]], color: str = "0xFF9500FF") -> None:
    for run in _split_on_gaps(_sample_trail(trail)):
        if len(run) >= 2:
            coord_string = "|".join(f"{lat},{lng}" for lat, lng in run)
            params.append(f"path=color:{color}|weight:4|{coord_string}")


def _append_segmented_paths(params: list[str], trails: list[list[tuple[float, float]]]) -> None:
    for index, trail in enumerate(trails):
        # Keep the visual progression while ensuring each durable segment has
        # its own `path=` argument. Static Maps cannot bridge between them.
        color = "0xFF9500FF" if index == 0 else "0xEE2B2BFF"
        _append_path(params, trail, color)


def _append_legacy_paths(params: list[str], phase_polylines: Optional[dict], route_polyline: Optional[list]) -> None:
    """Legacy fallback: preserve old trail selection plus its gap guard."""
    trip_trail = _extract_trail((phase_polylines or {}).get("trip_in_progress"))
    if len(trip_trail) >= 10:
        _append_path(params, trip_trail)
        return
    _append_path(params, _extract_trail(route_polyline))


def _is_incomplete(route_quality: Optional[dict]) -> bool:
    if not isinstance(route_quality, dict):
        return False
    return bool(route_quality.get("missing_tail") or route_quality.get("incomplete_reason"))


def _add_route_quality_banner(png_bytes: bytes, quality: dict) -> bytes:
    """Overlay a truthful coverage note without failing the receipt pipeline."""
    try:
        import io

        from PIL import Image, ImageDraw, ImageFont

        image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        draw = ImageDraw.Draw(image, "RGBA")
        coverage = quality.get("coverage_ratio")
        coverage_text = (
            f"{round(float(coverage) * 100)}% GPS coverage" if coverage is not None else "GPS coverage incomplete"
        )
        message = f"Route incomplete — {coverage_text}"
        font = ImageFont.load_default()
        left, top, right, bottom = draw.textbbox((0, 0), message, font=font)
        padding = 10
        y = image.height - (bottom - top) - 2 * padding
        draw.rounded_rectangle((0, y, image.width, image.height), radius=0, fill=(127, 29, 29, 230))
        draw.text((padding, y + padding), message, fill="white", font=font)
        output = io.BytesIO()
        image.convert("RGB").save(output, format="PNG", optimize=True)
        return output.getvalue()
    except Exception:
        logger.warning("route quality banner failed; retaining unannotated snapshot", exc_info=True)
        return png_bytes


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
    route_segments: Optional[list] = None,
    completion_point: Optional[dict] = None,
    route_quality: Optional[dict] = None,
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

    # Build the route_polyline fallback UNCONDITIONALLY so a sparse trip trail can
    # defer to it (mirrors the Google renderer's < 10-point density check) — a
    # 2-point trip trail must not be drawn as one long chord when a usable
    # route_polyline exists.
    legacy_trail: list[tuple[float, float]] = []
    if route_polyline:
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
        completion = _coerce_coordinate(completion_point)
        if completion:
            completion_lat, completion_lng = completion
            m.add_marker(CircleMarker((completion_lng, completion_lat), "#ffffff", 14))
            m.add_marker(CircleMarker((completion_lng, completion_lat), "#f59e0b", 10))

        # The real route trail coloured as one orange→red gradient (uniform with
        # the Google renderer + every in-app map). Gap-split runs are preserved
        # (never chorded); staticmap uses (lng, lat). trip_trail/legacy_trail are
        # (lng, lat) — convert to (lat, lng) for the shared gradient helpers.
        if route_segments is not None:
            trails = _extract_segment_trails(route_segments)
        elif len(trip_trail) >= 10:
            trails = [[(la, ln) for ln, la in trip_trail]]
        elif legacy_trail:
            trails = [[(la, ln) for ln, la in legacy_trail]]
        elif trip_trail:  # sparse, but the only geometry available
            trails = [[(la, ln) for ln, la in trip_trail]]
        else:
            trails = []

        pieces, total = _gradient_runs(trails)
        idx = 0
        for run in pieces:
            for i in range(len(run) - 1):
                r, g, b = _gradient_rgb((idx + 0.5) / total) if total else _GRADIENT_START_RGB
                (a_lat, a_lng), (b_lat, b_lng) = run[i], run[i + 1]
                m.add_line(Line([(a_lng, a_lat), (b_lng, b_lat)], f"#{r:02x}{g:02x}{b:02x}", _ROUTE_STROKE_WIDTH))
                idx += 1

        image = m.render()
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()
        return _add_route_quality_banner(png_bytes, route_quality) if _is_incomplete(route_quality) else png_bytes
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Ride snapshot render failed: {exc}")
        return None
