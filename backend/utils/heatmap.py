"""Demand-heatmap configuration bounds, data-source semantics, and
privacy-preserving aggregation.

Single source of truth shared by the driver endpoint
(routes/drivers.py::get_demand_heatmap) and the admin service-area API
(routes/admin/service_areas.py). The CHECK constraints in migration 202
mirror these bounds; if either side changes, change both.

The grid size and k-anonymity floor are PIPEDA mechanics, not tuning knobs:
any surface that serves ride-location aggregates to a wider audience than
the rider must go through bucket_ride_points() (or justify, in writing, why
not). Admin copy that states the grid size / floor lives in
admin-dashboard/src/app/dashboard/service-areas/page.tsx — keep it in sync.
"""

from typing import Any, Dict, List, Optional

HEATMAP_WINDOW_HOURS_MIN = 1
HEATMAP_WINDOW_HOURS_MAX = 720  # 30 days
HEATMAP_WINDOW_HOURS_DEFAULT = 168  # 7 days — pre-migration-202 behavior

HEATMAP_REFRESH_SECONDS_MIN = 30
HEATMAP_REFRESH_SECONDS_MAX = 3600
HEATMAP_REFRESH_SECONDS_DEFAULT = 300

# Which rides feed the map. Values are extra filters merged into the rides
# query (base filters: service_area_id + created_at window).
#   all_requests    — every ride request regardless of outcome
#   completed_rides — fulfilled demand only
#   missed_rides    — genuinely unmet demand: the system cancelled because no
#                     driver was found (migration 38's cancellation_type).
#                     Deliberately NOT status='cancelled' — that would count
#                     rider changed-my-mind cancels (the dominant cancel
#                     volume, KPI target ≤8%) as "missed" and steer drivers
#                     toward exactly the wrong blocks.
HEATMAP_SOURCE_FILTERS: Dict[str, Dict[str, Any]] = {
    "all_requests": {},
    "completed_rides": {"status": {"$in": ["completed"]}},
    "missed_rides": {"cancellation_type": {"$in": ["no_drivers_found"]}},
}
HEATMAP_DATA_SOURCES = tuple(HEATMAP_SOURCE_FILTERS)
HEATMAP_DATA_SOURCE_DEFAULT = "all_requests"
# For pydantic Field(pattern=...) in the admin request models.
HEATMAP_DATA_SOURCE_PATTERN = f"^({'|'.join(HEATMAP_DATA_SOURCES)})$"

# Curated color themes for the driver-app overlay (migration 204). Names
# only — the actual ramps live in the driver app
# (driver-app/components/dashboard/demandHeatmap.ts), each pre-validated for
# CVD legibility and lightness monotonicity. A curated enum (not free-form
# colors) so an admin can't configure a ramp that's illegible to red-green
# colorblind drivers; the app falls back to the default on unknown names, so
# themes can be removed later without breaking old rows.
HEATMAP_COLOR_THEMES = ("inferno", "ocean", "viridis")
HEATMAP_COLOR_THEME_DEFAULT = "inferno"
HEATMAP_COLOR_THEME_PATTERN = f"^({'|'.join(HEATMAP_COLOR_THEMES)})$"

# Grid size for pickup-coordinate bucketing: 3 decimal places ≈ 110 m.
# Riders' exact pickup coordinates must never be shipped to every driver in
# the area (PIPEDA data minimization); the heatmap only needs block-level
# density.
HEATMAP_GRID_DECIMALS = 3
# k-anonymity floor: cells with fewer requests than this are suppressed. A
# weight-1 cell in a low-density area still says "someone requested a ride
# in this specific ~110 m box", which local knowledge can re-identify
# (single farmhouse on a rural road). Aggregation alone is not anonymization.
HEATMAP_MIN_CELL_COUNT = 3


def clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    """Coerce value to an int within [lo, hi]; default on garbage input."""
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def bucket_ride_points(rides: List[Dict[str, Any]]) -> List[List[float]]:
    """Aggregate ride pickups into [[lat, lng, weight], ...] heatmap cells.

    Applies the ~110 m grid AND the k-anonymity floor. Rows with missing
    coordinates are skipped.
    """
    buckets: Dict[tuple, int] = {}
    for r in rides:
        lat = r.get("pickup_lat")
        lng = r.get("pickup_lng")
        if lat is None or lng is None:
            continue
        cell = (round(float(lat), HEATMAP_GRID_DECIMALS), round(float(lng), HEATMAP_GRID_DECIMALS))
        buckets[cell] = buckets.get(cell, 0) + 1
    return [
        [cell_lat, cell_lng, weight]
        for (cell_lat, cell_lng), weight in buckets.items()
        if weight >= HEATMAP_MIN_CELL_COUNT
    ]


def resolve_heatmap_config(service_area: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Clamped per-area heatmap config with defaults for pre-202 rows."""
    area = service_area or {}
    data_source = area.get("heatmap_data_source") or HEATMAP_DATA_SOURCE_DEFAULT
    if data_source not in HEATMAP_SOURCE_FILTERS:
        data_source = HEATMAP_DATA_SOURCE_DEFAULT
    color_theme = area.get("heatmap_color_theme") or HEATMAP_COLOR_THEME_DEFAULT
    if color_theme not in HEATMAP_COLOR_THEMES:
        color_theme = HEATMAP_COLOR_THEME_DEFAULT
    return {
        "color_theme": color_theme,
        "window_hours": clamp_int(
            area.get("heatmap_data_window_hours"),
            HEATMAP_WINDOW_HOURS_MIN,
            HEATMAP_WINDOW_HOURS_MAX,
            HEATMAP_WINDOW_HOURS_DEFAULT,
        ),
        "refresh_seconds": clamp_int(
            area.get("heatmap_refresh_seconds"),
            HEATMAP_REFRESH_SECONDS_MIN,
            HEATMAP_REFRESH_SECONDS_MAX,
            HEATMAP_REFRESH_SECONDS_DEFAULT,
        ),
        "data_source": data_source,
    }
