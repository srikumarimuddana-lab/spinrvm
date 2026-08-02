"""Corporate + admin portal review, Admin #3: demand_forecast used to label
its output "confidence": "high"/"medium"/"low" — implying statistical
rigor from a trained model, when the module is actually a plain
historical-average lookup (per its own module docstring). Relabeled to
"data_basis" with values describing what the number is grounded in
(historical_average / limited_history / default_pattern), not a fake
confidence interval.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils.demand_forecast import forecast_demand, get_forecast_summary


def _completed_ride(days_ago: int, hour: int) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return {"status": "completed", "created_at": ts.isoformat(), "service_area_id": None}


@pytest.mark.asyncio
async def test_forecast_uses_historical_average_label_with_sufficient_data():
    """lookback_days >= 14 (the default) with real historical rides →
    data_basis is "historical_average", not "confidence": "high"."""
    now_hour = datetime.now(timezone.utc).hour
    rides = [_completed_ride(days_ago=d, hour=now_hour) for d in range(1, 20)]
    with patch("backend.utils.demand_forecast.db.get_rows", AsyncMock(return_value=rides)):
        forecast = await forecast_demand(hours_ahead=1)
    assert forecast[0]["data_basis"] == "historical_average"
    assert "confidence" not in forecast[0]


@pytest.mark.asyncio
async def test_forecast_uses_limited_history_label_with_short_lookback():
    """Historical data exists, but the caller passed a lookback under 14
    days → data_basis is "limited_history", not "confidence": "medium"."""
    now_hour = datetime.now(timezone.utc).hour
    rides = [_completed_ride(days_ago=1, hour=now_hour)]
    with patch("backend.utils.demand_forecast.db.get_rows", AsyncMock(return_value=rides)):
        forecast = await forecast_demand(hours_ahead=1, lookback_days=7)
    assert forecast[0]["data_basis"] == "limited_history"
    assert "confidence" not in forecast[0]


@pytest.mark.asyncio
async def test_forecast_uses_default_pattern_label_with_no_data():
    """No historical rides at all → data_basis is "default_pattern", not
    "confidence": "low" — this is the case the old labeling most
    overstated: zero real data, dressed up as a "confidence" level."""
    with patch("backend.utils.demand_forecast.db.get_rows", AsyncMock(return_value=[])):
        forecast = await forecast_demand(hours_ahead=3)
    assert all(f["data_basis"] == "default_pattern" for f in forecast)
    assert all("confidence" not in f for f in forecast)


@pytest.mark.asyncio
async def test_forecast_summary_data_basis_matches_current_hour():
    with patch("backend.utils.demand_forecast.db.get_rows", AsyncMock(return_value=[])):
        summary = await get_forecast_summary()
    assert summary["available"] is True
    assert summary["data_basis"] == summary["current_hour"]["data_basis"] == "default_pattern"
    assert "confidence" not in summary


@pytest.mark.asyncio
async def test_forecast_summary_empty_forecast_returns_unavailable():
    """An empty forecast list short-circuits to {"available": False}
    before data_basis is ever computed — unchanged by the relabeling,
    confirmed so the rename didn't disturb this early-return path."""
    with patch("backend.utils.demand_forecast.forecast_demand", AsyncMock(return_value=[])):
        summary = await get_forecast_summary()
    assert summary == {"available": False}
