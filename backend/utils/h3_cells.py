"""Pure H3 cell helpers for dispatch lookup and heatmap aggregation.

Uber's H3 library (https://h3geo.org) indexes the globe as hexagons. Spinr
uses resolutions 7–9 only:

- res 7 (~5.2 km²) — coarse ring for large search radii
- res 8 (~0.74 km²) — default dispatch lookup
- res 9 (~0.11 km²) — heatmap cells (k-anonymity still applied on counts)

This module does not talk to Redis or Postgres. Invalid coordinates raise
ValueError so a bad GPS fix cannot silently land in the wrong cell.
"""

from __future__ import annotations

import math
from typing import Iterable

import h3

ALLOWED_RESOLUTIONS = frozenset({7, 8, 9})
DEFAULT_DISPATCH_RESOLUTION = 8
DEFAULT_HEATMAP_RESOLUTION = 9
# SUNION of a filled disk this large is a matching-path timeout. Larger
# radii must raise so H3 failovers to PostGIS / legacy instead of scanning
# thousands of Redis keys on the hail.
MAX_DISK_CELLS = 512
# SUNION/ZRANGE of a 10 km res-8 disk is ~1657 keys; Redis round-trips and
# key fan-out blow up past a few hundred. Query auto-drops to a coarser
# stored resolution; if even res 7 is too big, matching must fail over.
MAX_H3_QUERY_CELLS = 400


class H3DiskTooLargeError(ValueError):
    """Search disk still exceeds MAX_H3_QUERY_CELLS after dropping resolution."""

    def __init__(self, radius_km: float, res: int, n_cells: int):
        super().__init__(f"h3_disk_too_large radius_km={radius_km} res={res} cells={n_cells}")
        self.radius_km = radius_km
        self.res = res
        self.n_cells = n_cells


# Mean hexagon edge length in km (Uber H3 table). Used to size the k-ring
# so a search radius is covered with one extra ring of padding.
_EDGE_KM = {7: 1.220629759, 8: 0.461354684, 9: 0.174375668}


def validate_resolution(res: int) -> int:
    if res not in ALLOWED_RESOLUTIONS:
        raise ValueError(f"H3 resolution must be one of {sorted(ALLOWED_RESOLUTIONS)}, got {res!r}")
    return res


def validate_latlng(lat: float, lng: float) -> tuple[float, float]:
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"lat/lng must be numeric, got {lat!r},{lng!r}") from exc
    if not math.isfinite(lat_f) or not math.isfinite(lng_f):
        raise ValueError("lat/lng must be finite")
    if lat_f < -90 or lat_f > 90 or lng_f < -180 or lng_f > 180:
        raise ValueError(f"lat/lng out of range: {lat_f},{lng_f}")
    return lat_f, lng_f


def cell_for(lat: float, lng: float, res: int = DEFAULT_DISPATCH_RESOLUTION) -> str:
    """Return the H3 index for a WGS84 point at ``res``."""
    res = validate_resolution(res)
    lat_f, lng_f = validate_latlng(lat, lng)
    return h3.latlng_to_cell(lat_f, lng_f, res)


def k_ring_size(radius_km: float, res: int = DEFAULT_DISPATCH_RESOLUTION) -> int:
    """Grid-disk k that covers ``radius_km`` plus one ring of padding."""
    res = validate_resolution(res)
    try:
        radius = float(radius_km)
    except (TypeError, ValueError):
        radius = 0.0
    if not math.isfinite(radius) or radius <= 0:
        return 1
    edge = _EDGE_KM[res]
    return max(1, math.ceil(radius / edge) + 1)


def disk_cell_count(k: int) -> int:
    """Hex count in a filled k-disk: 1 + 3k(k+1)."""
    k_int = max(0, int(k))
    return 1 + 3 * k_int * (k_int + 1)


def resolution_for_query(
    radius_km: float,
    preferred_res: int = DEFAULT_DISPATCH_RESOLUTION,
    *,
    max_cells: int = MAX_H3_QUERY_CELLS,
) -> int:
    """Finest stored resolution whose k-disk fits ``max_cells``.

    Index writes always populate res 7/8/9, so dropping from 8→7 is a
    read-side choice, not a rebuild. Raises :class:`H3DiskTooLargeError`
    if even the coarsest allowed resolution is still too wide.
    """
    preferred_res = validate_resolution(preferred_res)
    last_n = 0
    last_res = preferred_res
    for res in sorted((r for r in ALLOWED_RESOLUTIONS if r <= preferred_res), reverse=True):
        n = disk_cell_count(k_ring_size(radius_km, res))
        last_n, last_res = n, res
        if n <= max_cells:
            return res
    raise H3DiskTooLargeError(radius_km, last_res, last_n)


def disk(cell: str, k: int) -> list[str]:
    """Inclusive k-ring around ``cell``. k=0 returns [cell]."""
    if not cell or not h3.is_valid_cell(cell):
        raise ValueError(f"invalid H3 cell: {cell!r}")
    k_int = max(0, int(k))
    count = disk_cell_count(k_int)
    if count > MAX_DISK_CELLS:
        raise ValueError(f"h3_disk_too_large:k={k_int}:cells={count}:cap={MAX_DISK_CELLS}")
    return list(h3.grid_disk(cell, k_int))


def cells_covering(lat: float, lng: float, radius_km: float, res: int = DEFAULT_DISPATCH_RESOLUTION) -> list[str]:
    """H3 cells whose union covers the search circle around pickup."""
    origin = cell_for(lat, lng, res)
    return disk(origin, k_ring_size(radius_km, res))


def centroid(cell: str) -> tuple[float, float]:
    """(lat, lng) of the hexagon centre — safe to ship as a heatmap point."""
    if not cell or not h3.is_valid_cell(cell):
        raise ValueError(f"invalid H3 cell: {cell!r}")
    lat, lng = h3.cell_to_latlng(cell)
    return float(lat), float(lng)


def boundary(cell: str) -> list[list[float]]:
    """Closed ring of [lat, lng] vertices for drawing a hex on a map."""
    if not cell or not h3.is_valid_cell(cell):
        raise ValueError(f"invalid H3 cell: {cell!r}")
    verts = [list(pair) for pair in h3.cell_to_boundary(cell)]
    if verts and verts[0] != verts[-1]:
        verts.append(verts[0])
    return verts


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km. Post-filter after an H3 ring query."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def filter_ids_within_radius(
    candidates: Iterable[tuple[str, float, float]],
    pickup_lat: float,
    pickup_lng: float,
    radius_km: float,
) -> list[str]:
    """Keep driver IDs whose stored lat/lng is inside the search circle.

    H3 rings are a superset of the circle (hexes stick out past the radius).
    Matching still runs ``filter_and_rank_drivers`` haversine; this is an
    optional tighter pre-filter for ID-only index hits.
    """
    out: list[str] = []
    for driver_id, lat, lng in candidates:
        try:
            if haversine_km(pickup_lat, pickup_lng, float(lat), float(lng)) <= radius_km:
                out.append(driver_id)
        except (TypeError, ValueError):
            continue
    return out


def cells_for_all_resolutions(lat: float, lng: float) -> dict[int, str]:
    """Reverse-index payload: the driver's cell at every supported res."""
    lat_f, lng_f = validate_latlng(lat, lng)
    return {res: h3.latlng_to_cell(lat_f, lng_f, res) for res in sorted(ALLOWED_RESOLUTIONS)}
