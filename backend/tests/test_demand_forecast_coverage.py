"""Coverage for utils/demand_forecast.py (A1c, Sub-tier B).

Heuristic demand-forecast engine backing the admin heatmap/dashboard. Had
no dedicated test file; only 18.52% coverage as an incidental side effect.

`_get_historical_hourly_demand` hits the DB via `db.get_rows`; every other
function is pure computation over its return value, so only that one seam
needs mocking.

Test-only change — no application code modified.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


def _ride(created_at: str, service_area_id: str | None = None):
    row = {"created_at": created_at, "status": "completed"}
    if service_area_id is not None:
        row["service_area_id"] = service_area_id
    return row


class TestGetHistoricalHourlyDemand:
    @pytest.mark.anyio
    async def test_db_error_returns_empty_dict(self, monkeypatch):
        from backend.utils import demand_forecast

        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(side_effect=ConnectionError("db down")))
        result = await demand_forecast._get_historical_hourly_demand()
        assert result == {}

    @pytest.mark.anyio
    async def test_no_rides_returns_all_zero_matrix(self, monkeypatch):
        from backend.utils import demand_forecast

        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=[]))
        result = await demand_forecast._get_historical_hourly_demand()
        assert len(result) == 7
        assert all(result[d][h] == 0 for d in range(7) for h in range(24))

    @pytest.mark.anyio
    async def test_averages_by_unique_calendar_day_not_ride_count(self, monkeypatch):
        """The source computes `len(dates) / unique_days` — rides-per-day,
        not a flat occurrence count. Two rides on the SAME calendar date
        (1 unique day) average to 2.0 rides/day, not 1.0."""
        from backend.utils import demand_forecast

        # A Wednesday (weekday()==2) at 08:00 UTC, two rides same date.
        rows = [
            _ride("2026-07-01T08:15:00Z"),
            _ride("2026-07-01T08:45:00Z"),
        ]
        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=rows))
        result = await demand_forecast._get_historical_hourly_demand(lookback_days=365)
        assert result[2][8] == 2.0  # 2 rides / 1 unique day

    @pytest.mark.anyio
    async def test_two_different_dates_same_weekday_hour_average_correctly(self, monkeypatch):
        """Two rides on DIFFERENT calendar dates (2 unique days) average to
        1.0 ride/day — the per-day rate, not the raw ride count."""
        from backend.utils import demand_forecast

        # Two different Wednesdays, both at 08:xx UTC.
        rows = [
            _ride("2026-07-01T08:15:00Z"),
            _ride("2026-07-08T08:45:00Z"),
        ]
        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=rows))
        result = await demand_forecast._get_historical_hourly_demand(lookback_days=365)
        assert result[2][8] == 1.0  # 2 rides / 2 unique days

    @pytest.mark.anyio
    async def test_rides_before_lookback_window_are_excluded(self, monkeypatch):
        from backend.utils import demand_forecast

        old_date = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=[_ride(old_date)]))
        result = await demand_forecast._get_historical_hourly_demand(lookback_days=28)
        assert all(result[d][h] == 0 for d in range(7) for h in range(24))

    @pytest.mark.anyio
    async def test_area_filter_excludes_non_matching_rows(self, monkeypatch):
        from backend.utils import demand_forecast

        rows = [
            _ride("2026-07-01T08:15:00Z", service_area_id="area-a"),
            _ride("2026-07-01T08:20:00Z", service_area_id="area-b"),
        ]
        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=rows))
        result = await demand_forecast._get_historical_hourly_demand(area_id="area-a", lookback_days=365)
        assert result[2][8] == 1.0

    @pytest.mark.anyio
    async def test_non_string_created_at_is_skipped(self, monkeypatch):
        from backend.utils import demand_forecast

        rows = [{"created_at": None, "status": "completed"}]
        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=rows))
        result = await demand_forecast._get_historical_hourly_demand()
        assert all(result[d][h] == 0 for d in range(7) for h in range(24))

    @pytest.mark.anyio
    async def test_unparseable_timestamp_is_skipped(self, monkeypatch):
        from backend.utils import demand_forecast

        rows = [_ride("not-a-real-timestamp")]
        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=rows))
        result = await demand_forecast._get_historical_hourly_demand()
        assert all(result[d][h] == 0 for d in range(7) for h in range(24))


class TestForecastDemand:
    @pytest.mark.anyio
    async def test_uses_default_pattern_when_no_historical_data(self, monkeypatch):
        from backend.utils import demand_forecast

        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=[]))
        forecast = await demand_forecast.forecast_demand(hours_ahead=3)
        assert len(forecast) == 3
        assert all(f["data_basis"] == "default_pattern" for f in forecast)

    @pytest.mark.anyio
    async def test_uses_historical_data_with_historical_average_basis_when_lookback_14_plus(self, monkeypatch):
        """Seed a ride at exactly `now`'s truncated hour so has_data is
        guaranteed True for the very first forecast entry (offset=0), then
        confirm lookback_days>=14 yields data_basis='historical_average'."""
        from backend.utils import demand_forecast

        now = datetime.now(timezone.utc)
        rows = [_ride(now.replace(minute=0, second=0, microsecond=0).isoformat())]
        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=rows))
        forecast = await demand_forecast.forecast_demand(hours_ahead=1, lookback_days=28)
        assert forecast[0]["data_basis"] == "historical_average"

    @pytest.mark.anyio
    async def test_limited_history_basis_when_lookback_under_14_days(self, monkeypatch):
        from backend.utils import demand_forecast

        now = datetime.now(timezone.utc)
        rows = [_ride(now.replace(minute=0, second=0, microsecond=0).isoformat())]
        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=rows))
        forecast = await demand_forecast.forecast_demand(hours_ahead=1, lookback_days=7)
        assert forecast[0]["data_basis"] == "limited_history"

    @pytest.mark.anyio
    async def test_zero_hours_ahead_returns_empty_list(self, monkeypatch):
        from backend.utils import demand_forecast

        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=[]))
        forecast = await demand_forecast.forecast_demand(hours_ahead=0)
        assert forecast == []

    @pytest.mark.anyio
    async def test_forecast_entries_have_expected_shape(self, monkeypatch):
        from backend.utils import demand_forecast

        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=[]))
        forecast = await demand_forecast.forecast_demand(hours_ahead=2)
        for entry in forecast:
            assert set(entry.keys()) == {
                "timestamp",
                "hour",
                "day_name",
                "predicted_rides",
                "data_basis",
                "is_peak",
            }

    @pytest.mark.anyio
    async def test_peak_detection_flags_highest_value_hours(self, monkeypatch):
        """Hour 18 (0.90) in the default pattern is always >= 75% of the
        24h max — must be flagged as a peak."""
        from backend.utils import demand_forecast

        monkeypatch.setattr(demand_forecast.db, "get_rows", AsyncMock(return_value=[]))
        forecast = await demand_forecast.forecast_demand(hours_ahead=24)
        peak_hours = {f["hour"] for f in forecast if f["is_peak"]}
        assert 18 in peak_hours  # DEFAULT_HOURLY_PATTERN's global max


class TestGetForecastSummary:
    @pytest.mark.anyio
    async def test_empty_forecast_returns_unavailable(self, monkeypatch):
        from backend.utils import demand_forecast

        monkeypatch.setattr(demand_forecast, "forecast_demand", AsyncMock(return_value=[]))
        result = await demand_forecast.get_forecast_summary()
        assert result == {"available": False}

    @pytest.mark.anyio
    async def test_summary_aggregates_forecast_correctly(self, monkeypatch):
        from backend.utils import demand_forecast

        fake_forecast = [
            {"hour": 0, "predicted_rides": 1.0, "data_basis": "default_pattern", "is_peak": False},
            {"hour": 1, "predicted_rides": 9.0, "data_basis": "default_pattern", "is_peak": True},
            {"hour": 2, "predicted_rides": 2.0, "data_basis": "default_pattern", "is_peak": False},
        ]
        monkeypatch.setattr(demand_forecast, "forecast_demand", AsyncMock(return_value=fake_forecast))

        result = await demand_forecast.get_forecast_summary()
        assert result["available"] is True
        assert result["current_hour"] == fake_forecast[0]
        assert result["next_peak"] == fake_forecast[1]
        assert result["total_predicted_24h"] == 12.0
        assert result["avg_hourly"] == 4.0
        assert result["peak_hours_count"] == 1
        assert result["data_basis"] == "default_pattern"

    @pytest.mark.anyio
    async def test_summary_with_no_peak_hours_next_peak_is_none(self, monkeypatch):
        from backend.utils import demand_forecast

        fake_forecast = [{"hour": 0, "predicted_rides": 1.0, "data_basis": "default_pattern", "is_peak": False}]
        monkeypatch.setattr(demand_forecast, "forecast_demand", AsyncMock(return_value=fake_forecast))

        result = await demand_forecast.get_forecast_summary()
        assert result["next_peak"] is None
