"""Demand forecasting — heuristic-based prediction engine.

Uses historical ride data to predict demand by hour-of-day and day-of-week.
Falls back to sensible defaults when insufficient data is available.

This is a Phase 3 lightweight approach; a future iteration could use
Prophet/statsmodels for time-series ML forecasting.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    from ..db import db
except ImportError:
    from db import db

logger = logging.getLogger(__name__)

# Default demand patterns when no historical data is available.
# Represents typical urban rideshare demand shape (normalised 0-1).
DEFAULT_HOURLY_PATTERN = {
    0: 0.15,
    1: 0.08,
    2: 0.05,
    3: 0.03,
    4: 0.03,
    5: 0.05,
    6: 0.15,
    7: 0.45,
    8: 0.70,
    9: 0.55,
    10: 0.40,
    11: 0.45,
    12: 0.55,
    13: 0.50,
    14: 0.45,
    15: 0.50,
    16: 0.65,
    17: 0.85,
    18: 0.90,
    19: 0.80,
    20: 0.65,
    21: 0.55,
    22: 0.45,
    23: 0.30,
}

# Weekend multipliers (Fri/Sat evenings spike, Sun/Mon mornings dip)
DAY_MULTIPLIERS = {
    0: 0.85,  # Monday
    1: 0.90,  # Tuesday
    2: 0.95,  # Wednesday
    3: 1.00,  # Thursday
    4: 1.15,  # Friday
    5: 1.25,  # Saturday
    6: 0.80,  # Sunday
}


async def _get_historical_hourly_demand(
    area_id: Optional[str] = None,
    lookback_days: int = 28,
) -> Dict[int, Dict[int, float]]:
    """Build an average demand matrix [day_of_week][hour] from historical rides.

    Returns a nested dict: {day_of_week: {hour: avg_ride_count}}.
    """
    start = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()

    try:
        filters: Dict[str, Any] = {"status": "completed"}
        rides = await db.get_rows("rides", filters, limit=10000, order="created_at")
    except Exception as e:
        logger.error(f"Forecast: failed to fetch rides: {e}")
        return {}

    # Filter by date and optionally area
    buckets: Dict[int, Dict[int, List[str]]] = defaultdict(lambda: defaultdict(list))

    for r in rides:
        created = r.get("created_at", "")
        if not isinstance(created, str) or created < start:
            continue
        if area_id and r.get("service_area_id") != area_id:
            continue

        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00").replace("+00:00", ""))
            day = dt.weekday()
            hour = dt.hour
            date_key = dt.strftime("%Y-%m-%d")
            buckets[day][hour].append(date_key)
        except (ValueError, TypeError):
            continue

    # Average per unique day (not per ride)
    result: Dict[int, Dict[int, float]] = {}
    for day in range(7):
        result[day] = {}
        for hour in range(24):
            dates = buckets[day][hour]
            if dates:
                unique_days = len(set(dates))
                result[day][hour] = len(dates) / max(unique_days, 1)
            else:
                result[day][hour] = 0
    return result


async def forecast_demand(
    area_id: Optional[str] = None,
    hours_ahead: int = 24,
    lookback_days: int = 28,
) -> List[Dict[str, Any]]:
    """Generate an hourly demand forecast for the next N hours.

    Uses historical averages by day-of-week/hour when data is available,
    otherwise falls back to the default demand pattern.

    Returns list of {hour, day_name, predicted_rides, confidence, is_peak}.
    """
    historical = await _get_historical_hourly_demand(area_id, lookback_days)
    has_data = any(historical.get(d, {}).get(h, 0) > 0 for d in range(7) for h in range(24))

    now = datetime.utcnow()
    forecasts = []

    # Compute the max value for peak detection
    all_values = []
    for offset in range(hours_ahead):
        t = now + timedelta(hours=offset)
        day = t.weekday()
        hour = t.hour
        if has_data:
            val = historical.get(day, {}).get(hour, 0)
        else:
            val = DEFAULT_HOURLY_PATTERN.get(hour, 0.3) * DAY_MULTIPLIERS.get(day, 1.0) * 10
        all_values.append(val)

    max_val = max(all_values) if all_values else 1
    peak_threshold = max_val * 0.75

    for offset in range(hours_ahead):
        t = now + timedelta(hours=offset)
        day = t.weekday()
        hour = t.hour
        day_name = t.strftime("%a")

        if has_data:
            predicted = historical.get(day, {}).get(hour, 0)
            confidence = "high" if lookback_days >= 14 else "medium"
        else:
            predicted = DEFAULT_HOURLY_PATTERN.get(hour, 0.3) * DAY_MULTIPLIERS.get(day, 1.0) * 10
            confidence = "low"

        is_peak = predicted >= peak_threshold and predicted > 0

        forecasts.append(
            {
                "timestamp": t.isoformat(),
                "hour": hour,
                "day_name": day_name,
                "predicted_rides": round(predicted, 1),
                "confidence": confidence,
                "is_peak": is_peak,
            }
        )

    return forecasts


async def get_forecast_summary(
    area_id: Optional[str] = None,
    lookback_days: int = 28,
) -> Dict[str, Any]:
    """High-level forecast summary for dashboard display."""
    forecast = await forecast_demand(area_id, hours_ahead=24, lookback_days=lookback_days)

    if not forecast:
        return {"available": False}

    peak_hours = [f for f in forecast if f["is_peak"]]
    next_peak = peak_hours[0] if peak_hours else None
    total_predicted = sum(f["predicted_rides"] for f in forecast)
    avg_hourly = round(total_predicted / len(forecast), 1) if forecast else 0

    # Current hour context
    current = forecast[0] if forecast else None

    return {
        "available": True,
        "current_hour": current,
        "next_peak": next_peak,
        "total_predicted_24h": round(total_predicted, 0),
        "avg_hourly": avg_hourly,
        "peak_hours_count": len(peak_hours),
        "confidence": current["confidence"] if current else "low",
        "forecast": forecast,
    }
