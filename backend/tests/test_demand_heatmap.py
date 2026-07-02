"""Unit tests for GET /drivers/demand-heatmap (routes/drivers.py).

Covers the per-service-area config added in migration 202:
  - admin gating via show_demand_heatmap
  - data window (heatmap_data_window_hours) driving the created_at cutoff
  - data source (heatmap_data_source) driving the status filter
  - refresh_seconds contract returned to the driver app
  - ~110 m coordinate bucketing with count weights (PIPEDA data minimization)
  - k-anonymity floor suppressing cells below _HEATMAP_MIN_CELL_COUNT
  - defensive clamping of out-of-range config values
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

USER_ID = "user_hm"
DRIVER_ID = "driver_hm"
AREA_ID = "area_hm"


def _resolve_inner(fn):
    """slowapi's @limiter.limit wraps the coroutine in async_wrapper without
    setting __wrapped__. Walk __closure__ cells to pull out the original
    so we can call the handler without tripping rate-limit state."""
    while True:
        nxt = getattr(fn, "__wrapped__", None)
        if nxt is None:
            closure = getattr(fn, "__closure__", None) or ()
            for cell in closure:
                val = cell.cell_contents
                if callable(val) and getattr(val, "__code__", None) is not None:
                    if val is fn:
                        continue
                    return val
            return fn
        fn = nxt


def _driver(**extra):
    return {"id": DRIVER_ID, "user_id": USER_ID, "service_area_id": AREA_ID, **extra}


def _area(**extra):
    return {"id": AREA_ID, "show_demand_heatmap": True, **extra}


def _ride(lat, lng):
    return {"pickup_lat": lat, "pickup_lng": lng}


def _run(driver_rows, area_rows, ride_rows, captured=None):
    """Invoke the endpoint with a table-keyed get_rows mock."""
    from backend.routes import drivers as drv

    async def get_rows_mock(table, filters=None, **kw):
        if table == "drivers":
            return driver_rows
        if table == "service_areas":
            return area_rows
        if table == "rides":
            if captured is not None:
                captured["filters"] = filters
            return ride_rows
        raise AssertionError(f"unexpected table {table}")

    handler = _resolve_inner(drv.get_demand_heatmap)
    with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_mock)):
        return asyncio.run(handler(request=MagicMock(), current_user={"id": USER_ID}))


class TestGating:
    def test_disabled_area_returns_disabled_with_refresh_contract(self):
        result = _run([_driver()], [_area(show_demand_heatmap=False)], [])
        assert result["enabled"] is False
        assert result["points"] == []
        # Disabled response still tells the app how often to re-check.
        assert result["refresh_seconds"] == 300

    def test_no_driver_row_returns_disabled(self):
        result = _run([], [], [])
        assert result["enabled"] is False

    def test_driver_without_service_area_returns_disabled(self):
        result = _run([_driver(service_area_id=None)], [], [])
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
        result = _run([_driver()], [_area()], rides)
        assert result["enabled"] is True
        assert result["total_rides"] == 4
        points = {(p[0], p[1]): p[2] for p in result["points"]}
        assert points == {(52.130, -106.670): 3}

    def test_exact_coordinates_never_leave_the_endpoint(self):
        rides = [_ride(52.1301234, -106.6702345)] * 3  # clears the k-floor
        result = _run([_driver()], [_area()], rides)
        (lat, lng, _w) = result["points"][0]
        assert lat == round(52.1301234, 3)
        assert lng == round(-106.6702345, 3)

    def test_low_count_cells_are_suppressed(self):
        # k-anonymity floor: a cell with fewer than _HEATMAP_MIN_CELL_COUNT
        # requests must never leave the endpoint — a weight-1 cell in a rural
        # area is re-identifiable with local knowledge.
        from backend.routes.drivers import _HEATMAP_MIN_CELL_COUNT

        rides = [_ride(52.13, -106.67)] * (_HEATMAP_MIN_CELL_COUNT - 1)
        result = _run([_driver()], [_area()], rides)
        assert result["points"] == []
        assert result["total_rides"] == _HEATMAP_MIN_CELL_COUNT - 1

    def test_rides_with_missing_coordinates_are_skipped(self):
        rides = [_ride(None, -106.67), _ride(52.13, None)] + [_ride(52.13, -106.67)] * 3
        result = _run([_driver()], [_area()], rides)
        assert len(result["points"]) == 1
        # total_rides reflects rows returned, points only mappable ones
        assert result["total_rides"] == 5


class TestConfig:
    def test_defaults_when_columns_absent(self):
        # Pre-migration-202 row shape: only show_demand_heatmap present.
        captured: dict = {}
        result = _run([_driver()], [_area()], [], captured)
        assert result["refresh_seconds"] == 300
        assert result["data_window_hours"] == 168
        assert result["data_source"] == "all_requests"
        assert "status" not in captured["filters"]

    def test_window_hours_drives_cutoff(self):
        captured: dict = {}
        before = datetime.now(timezone.utc)
        _run([_driver()], [_area(heatmap_data_window_hours=24)], [], captured)
        cutoff = datetime.fromisoformat(captured["filters"]["created_at"]["$gte"])
        expected = before - timedelta(hours=24)
        assert abs((cutoff - expected).total_seconds()) < 60

    def test_completed_rides_source_filters_status(self):
        captured: dict = {}
        result = _run([_driver()], [_area(heatmap_data_source="completed_rides")], [], captured)
        assert captured["filters"]["status"] == {"$in": ["completed"]}
        assert result["data_source"] == "completed_rides"

    def test_missed_rides_source_filters_status(self):
        captured: dict = {}
        result = _run([_driver()], [_area(heatmap_data_source="missed_rides")], [], captured)
        assert captured["filters"]["status"] == {"$in": ["cancelled"]}
        assert result["data_source"] == "missed_rides"

    def test_unknown_source_falls_back_to_all_requests(self):
        captured: dict = {}
        result = _run([_driver()], [_area(heatmap_data_source="everything")], [], captured)
        assert result["data_source"] == "all_requests"
        assert "status" not in captured["filters"]

    def test_out_of_range_values_are_clamped(self):
        area = _area(heatmap_refresh_seconds=5, heatmap_data_window_hours=100_000)
        result = _run([_driver()], [area], [])
        assert result["refresh_seconds"] == 30
        assert result["data_window_hours"] == 720

    def test_garbage_values_fall_back_to_defaults(self):
        area = _area(heatmap_refresh_seconds="soon", heatmap_data_window_hours=None)
        result = _run([_driver()], [area], [])
        assert result["refresh_seconds"] == 300
        assert result["data_window_hours"] == 168
