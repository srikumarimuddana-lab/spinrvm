"""Unit tests for GET /drivers/demand-heatmap (routes/drivers.py).

Covers the per-service-area config added in migration 202:
  - admin gating via show_demand_heatmap
  - data window (heatmap_data_window_hours) driving the created_at cutoff
  - data source (heatmap_data_source) driving the rides filter — in
    particular that missed_rides means "no driver found", never rider cancels
  - refresh_seconds contract returned to the driver app (also on the
    disabled path, honoring the per-area setting)
  - ~110 m coordinate bucketing with count weights (PIPEDA data minimization)
  - k-anonymity floor suppressing cells below HEATMAP_MIN_CELL_COUNT
  - defensive clamping of out-of-range config values
  - per-area payload caching (cache hit skips the rides scan)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tests._factories import resolve_inner

pytestmark = pytest.mark.unit

USER_ID = "user_hm"
DRIVER_ID = "driver_hm"
AREA_ID = "area_hm"


def _driver(**extra):
    return {"id": DRIVER_ID, "user_id": USER_ID, "service_area_id": AREA_ID, **extra}


def _area(**extra):
    return {"id": AREA_ID, "show_demand_heatmap": True, **extra}


def _ride(lat, lng):
    return {"pickup_lat": lat, "pickup_lng": lng}


def _run(driver, area, ride_rows, captured=None, cached_payload=None):
    """Invoke the endpoint with the DB + cache layers mocked.

    captured (optional dict) records the rides-query filters and any
    redis_set writes. cached_payload (optional str) is what redis_get
    returns — simulating a warm per-area cache.
    """
    from backend.routes import drivers as drv

    async def get_rows_mock(table, filters=None, **kw):
        assert table == "rides", f"unexpected get_rows table {table}"
        if captured is not None:
            captured["filters"] = filters
        return ride_rows

    async def redis_get_mock(key):
        if captured is not None:
            captured["cache_key"] = key
        return cached_payload

    async def redis_set_mock(key, value, ttl=None):
        if captured is not None:
            captured["cache_write"] = {"key": key, "value": value, "ttl": ttl}

    handler = resolve_inner(drv.get_demand_heatmap)
    with (
        patch(
            "backend.routes.drivers.db_supabase.get_driver_by_user_id_cached",
            AsyncMock(return_value=driver),
        ),
        patch("backend.routes.drivers.db_supabase.find_one", AsyncMock(return_value=area)),
        patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_mock)),
        patch("backend.utils.redis_client.redis_get", AsyncMock(side_effect=redis_get_mock)),
        patch("backend.utils.redis_client.redis_set", AsyncMock(side_effect=redis_set_mock)),
    ):
        return asyncio.run(handler(request=MagicMock(), current_user={"id": USER_ID}))


class TestGating:
    def test_disabled_area_returns_disabled_with_refresh_contract(self):
        result = _run(_driver(), _area(show_demand_heatmap=False), [])
        assert result["enabled"] is False
        assert result["points"] == []
        assert result["refresh_seconds"] == 300

    def test_disabled_area_honors_configured_refresh(self):
        # An admin who raised the cadence to shed load keeps that protection
        # while the overlay is off (the app still polls to see it turn on).
        area = _area(show_demand_heatmap=False, heatmap_refresh_seconds=3600)
        result = _run(_driver(), area, [])
        assert result["enabled"] is False
        assert result["refresh_seconds"] == 3600

    def test_no_driver_row_returns_disabled(self):
        result = _run(None, None, [])
        assert result["enabled"] is False

    def test_driver_without_service_area_returns_disabled(self):
        result = _run(_driver(service_area_id=None), None, [])
        assert result["enabled"] is False


class TestBucketing:
    def test_points_are_bucketed_with_count_weights(self):
        # Three pickups within the same ~110 m cell collapse into one weighted
        # point; a lone pickup elsewhere is suppressed by the k-floor.
        rides = [
            _ride(52.13011, -106.67022),
            _ride(52.13049, -106.67013),  # same cell after 3-decimal rounding
            _ride(52.13021, -106.67041),  # same cell again → weight 3
            _ride(52.20000, -106.70000),  # weight 1 → suppressed
        ]
        result = _run(_driver(), _area(), rides)
        assert result["enabled"] is True
        assert result["total_rides"] == 4
        points = {(p[0], p[1]): p[2] for p in result["points"]}
        assert points == {(52.130, -106.670): 3}

    def test_exact_coordinates_never_leave_the_endpoint(self):
        rides = [_ride(52.1301234, -106.6702345)] * 3  # clears the k-floor
        result = _run(_driver(), _area(), rides)
        (lat, lng, _w) = result["points"][0]
        assert lat == round(52.1301234, 3)
        assert lng == round(-106.6702345, 3)

    def test_low_count_cells_are_suppressed(self):
        # k-anonymity floor: a cell with fewer than HEATMAP_MIN_CELL_COUNT
        # requests must never leave the endpoint — a weight-1 cell in a rural
        # area is re-identifiable with local knowledge.
        from backend.utils.heatmap import HEATMAP_MIN_CELL_COUNT

        rides = [_ride(52.13, -106.67)] * (HEATMAP_MIN_CELL_COUNT - 1)
        result = _run(_driver(), _area(), rides)
        assert result["points"] == []
        assert result["total_rides"] == HEATMAP_MIN_CELL_COUNT - 1

    def test_rides_with_missing_coordinates_are_skipped(self):
        rides = [_ride(None, -106.67), _ride(52.13, None)] + [_ride(52.13, -106.67)] * 3
        result = _run(_driver(), _area(), rides)
        assert len(result["points"]) == 1
        # total_rides reflects rows returned, points only mappable ones
        assert result["total_rides"] == 5


class TestConfig:
    def test_defaults_when_columns_absent(self):
        # Pre-migration-202 row shape: only show_demand_heatmap present.
        captured: dict = {}
        result = _run(_driver(), _area(), [], captured)
        assert result["refresh_seconds"] == 300
        assert result["data_window_hours"] == 168
        assert result["data_source"] == "all_requests"
        assert "status" not in captured["filters"]
        assert "cancellation_type" not in captured["filters"]

    def test_window_hours_drives_cutoff(self):
        captured: dict = {}
        before = datetime.now(timezone.utc)
        _run(_driver(), _area(heatmap_data_window_hours=24), [], captured)
        cutoff = datetime.fromisoformat(captured["filters"]["created_at"]["$gte"])
        expected = before - timedelta(hours=24)
        assert abs((cutoff - expected).total_seconds()) < 60

    def test_completed_rides_source_filters_status(self):
        captured: dict = {}
        result = _run(_driver(), _area(heatmap_data_source="completed_rides"), [], captured)
        assert captured["filters"]["status"] == {"$in": ["completed"]}
        assert result["data_source"] == "completed_rides"

    def test_missed_rides_means_no_driver_found_not_rider_cancels(self):
        # 'missed_rides' must isolate genuinely unmet demand. Filtering on
        # status='cancelled' would count rider changed-my-mind cancels (the
        # dominant cancel volume) and steer drivers toward the wrong blocks.
        captured: dict = {}
        result = _run(_driver(), _area(heatmap_data_source="missed_rides"), [], captured)
        assert captured["filters"]["cancellation_type"] == {"$in": ["no_drivers_found"]}
        assert "status" not in captured["filters"]
        assert result["data_source"] == "missed_rides"

    def test_unknown_source_falls_back_to_all_requests(self):
        captured: dict = {}
        result = _run(_driver(), _area(heatmap_data_source="everything"), [], captured)
        assert result["data_source"] == "all_requests"
        assert "status" not in captured["filters"]

    def test_out_of_range_values_are_clamped(self):
        area = _area(heatmap_refresh_seconds=5, heatmap_data_window_hours=100_000)
        result = _run(_driver(), area, [])
        assert result["refresh_seconds"] == 30
        assert result["data_window_hours"] == 720

    def test_garbage_values_fall_back_to_defaults(self):
        area = _area(heatmap_refresh_seconds="soon", heatmap_data_window_hours=None)
        result = _run(_driver(), area, [])
        assert result["refresh_seconds"] == 300
        assert result["data_window_hours"] == 168

    def test_color_theme_defaults_to_inferno(self):
        # Pre-migration-204 rows (column absent) and unknown names both map
        # to the default so a removed theme can never break old rows.
        assert _run(_driver(), _area(), [])["color_theme"] == "inferno"
        assert _run(_driver(), _area(heatmap_color_theme="rainbow"), [])["color_theme"] == "inferno"

    def test_configured_color_theme_is_returned(self):
        result = _run(_driver(), _area(heatmap_color_theme="ocean"), [])
        assert result["color_theme"] == "ocean"

    def test_color_theme_rides_along_on_cache_hits(self):
        cached = json.dumps({"points": [[52.13, -106.67, 5]], "total_rides": 5})
        result = _run(_driver(), _area(heatmap_color_theme="viridis"), None, cached_payload=cached)
        assert result["color_theme"] == "viridis"


class TestCaching:
    def test_result_is_written_to_the_per_area_cache(self):
        captured: dict = {}
        rides = [_ride(52.13, -106.67)] * 3
        _run(_driver(), _area(heatmap_refresh_seconds=60), rides, captured)
        write = captured["cache_write"]
        assert write["key"] == f"heatmap:v1:{AREA_ID}:all_requests:168"
        assert write["ttl"] == 60  # min(refresh_seconds, 300)
        assert json.loads(write["value"])["total_rides"] == 3

    def test_cache_hit_skips_the_rides_scan(self):
        captured: dict = {}
        cached = json.dumps({"points": [[52.13, -106.67, 5]], "total_rides": 5})
        result = _run(_driver(), _area(), None, captured, cached_payload=cached)
        assert result["points"] == [[52.13, -106.67, 5]]
        assert result["total_rides"] == 5
        # get_rows was never called: ride_rows=None would have raised inside
        # the endpoint's bucketing if the scan had run.
        assert "filters" not in captured

    def test_corrupt_cache_entry_falls_through_to_recompute(self):
        captured: dict = {}
        rides = [_ride(52.13, -106.67)] * 3
        result = _run(_driver(), _area(), rides, captured, cached_payload="{not json")
        assert result["total_rides"] == 3
        assert "filters" in captured  # scan ran
