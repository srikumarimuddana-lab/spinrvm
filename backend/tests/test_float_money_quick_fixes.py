"""Regression tests for ROADMAP N19 float-money quick fixes.

MONEY-006 (features.compute_fare_estimate grand_total) and MONEY-001
(driver tip totals in routes/drivers/earnings.py), per
docs/audit/clean-sheet/02-findings/money-cra.md and
docs/change-log/2026-09-25-float-money-writes.md.

Rows are served through the conftest ``mock_supabase_client`` (the real
``db_supabase.get_rows`` -> ``repositories._base`` path runs; only the
supabase-py client underneath is fake), with inputs chosen so binary-float
accumulation drifts (0.1-cent shapes) and the assertions pin the exact
Decimal result.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.anyio


class _Query:
    """Chainable stand-in for a supabase-py query builder: every builder
    method returns itself, execute() returns the table's canned rows."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self):
        return SimpleNamespace(data=[dict(r) for r in self._rows], count=len(self._rows))

    def __getattr__(self, _name):
        return lambda *a, **k: self


def _serve(mock_supabase_client, rows_by_table):
    mock_supabase_client.table.side_effect = lambda name: _Query(rows_by_table.get(name, []))


# ---------------------------------------------------------------------------
# MONEY-006 — compute_fare_estimate grand_total
# ---------------------------------------------------------------------------

_SQUARE = [
    {"lat": 52.0, "lng": -107.0},
    {"lat": 52.0, "lng": -106.0},
    {"lat": 53.0, "lng": -106.0},
    {"lat": 53.0, "lng": -107.0},
]


def _fare_config():
    return {
        "vehicle_type_id": "vt-1",
        "base_fare": "3.10",
        "per_km_rate": "1.10",
        "per_minute_rate": "0.20",
        "booking_fee": "2.20",
        "minimum_fare": "5.00",
    }


async def test_grand_total_is_exact_decimal_sum_of_fare_fees_and_tax(mock_supabase_client):
    from features import compute_fare_estimate

    area = {
        "id": "sa-1",
        "name": "Test Area",
        "is_active": True,
        "polygon": _SQUARE,
        "gst_enabled": True,
        "gst_rate": 5.0,
        "surge_enabled": False,
    }
    # 0.1 + 0.2 shaped flat fees — the classic non-representable pair.
    fees = [
        {"id": "f1", "fee_name": "Fee A", "fee_type": "custom", "calc_mode": "flat", "amount": 0.1},
        {"id": "f2", "fee_name": "Fee B", "fee_type": "custom", "calc_mode": "flat", "amount": 0.2},
    ]
    _serve(
        mock_supabase_client,
        {"fare_configs": [_fare_config()], "service_areas": [area], "area_fees": fees},
    )

    result = await compute_fare_estimate(
        pickup_lat=52.5,
        pickup_lng=-106.5,
        dropoff_lat=52.6,
        dropoff_lng=-106.6,
        distance_km=7.3,
        duration_minutes=13,
        vehicle_type_id="vt-1",
    )

    # Expected, computed independently in Decimal:
    # 3.10 + 1.10*7.3 (=8.03) + 0.20*13 (=2.60) + 2.20 = 15.93 fare
    # fees 0.10 + 0.20 = 0.30; GST 5% of 16.23 = 0.8115 -> 0.81 (HALF_UP)
    subtotal = Decimal("15.93")
    fees_total = Decimal("0.30")
    gst = ((subtotal + fees_total) * Decimal("0.05")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    expected = subtotal + fees_total + gst

    assert result["subtotal"] == float(subtotal)
    assert result["area_fees_total"] == float(fees_total)
    assert result["tax_amount"] == float(gst)
    assert expected == Decimal("17.04")
    assert result["grand_total"] == float(expected)
    # Boundary shape unchanged: still a JSON float, not a Decimal/str.
    assert type(result["grand_total"]) is float
    # Reconciles to the cent against its own parts, in Decimal.
    parts = sum(
        (Decimal(str(result[k])) for k in ("subtotal", "area_fees_total", "tax_amount")),
        Decimal("0"),
    )
    assert Decimal(str(result["grand_total"])) == parts


async def test_grand_total_with_no_matched_area_is_the_fare(mock_supabase_client):
    """No service area -> no fees, no tax; grand_total == subtotal exactly."""
    from features import compute_fare_estimate

    _serve(mock_supabase_client, {"fare_configs": [_fare_config()], "service_areas": []})

    result = await compute_fare_estimate(
        pickup_lat=10.0,
        pickup_lng=10.0,
        dropoff_lat=10.1,
        dropoff_lng=10.1,
        distance_km=7.3,
        duration_minutes=13,
        vehicle_type_id="vt-1",
    )

    assert result["area_fees_total"] == 0.0
    assert result["tax_amount"] == 0.0
    assert result["grand_total"] == 15.93
    assert type(result["grand_total"]) is float


# ---------------------------------------------------------------------------
# MONEY-001 — driver tip totals (routes/drivers/earnings.py)
# ---------------------------------------------------------------------------

_USER_ID = "user_n19_tips"
_DRIVER = {"id": "driver_n19_tips", "user_id": _USER_ID}

# Ten $0.10 tips: a plain float `+=` loop gives 0.9999999999999999 (and so
# does sum() before Python 3.12); the exact answer is 1.00.
_TEN_DIME_TIPS = [Decimal("0.10")] * 10


def _tip_rides(tips):
    when = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    return [
        {
            "id": f"ride-{i}",
            "driver_id": _DRIVER["id"],
            "status": "completed",
            "base_fare": 10.0,
            "distance_fare": 0.0,
            "time_fare": 0.0,
            "tip_amount": float(t),
            "driver_earnings": 10.0,
            "tax_amount": 0.0,
            "ride_completed_at": when,
            "distance_km": 1.0,
            "duration_minutes": 5,
        }
        for i, t in enumerate(tips)
    ]


def _serve_driver_rides(mock_supabase_client, rides):
    # driver_daily_stats deliberately empty so weekly/monthly take the
    # rides-table path where the tip accumulation lives.
    _serve(mock_supabase_client, {"drivers": [_DRIVER], "rides": rides})


async def test_daily_tips_are_exact_decimal_sum(mock_supabase_client):
    from backend.routes.drivers import get_driver_daily_earnings

    _serve_driver_rides(mock_supabase_client, _tip_rides(_TEN_DIME_TIPS))
    result = await get_driver_daily_earnings(days=7, current_user={"id": _USER_ID})

    assert len(result) == 1
    assert result[0]["tips"] == 1.0
    assert type(result[0]["tips"]) is float  # boundary shape unchanged


async def test_weekly_tips_are_exact_decimal_sum(mock_supabase_client):
    from backend.routes.drivers import get_driver_weekly_earnings

    _serve_driver_rides(mock_supabase_client, _tip_rides(_TEN_DIME_TIPS))
    result = await get_driver_weekly_earnings(weeks=4, current_user={"id": _USER_ID})

    assert len(result) == 1
    assert result[0]["tips"] == 1.0
    assert type(result[0]["tips"]) is float


async def test_monthly_tips_are_exact_decimal_sum(mock_supabase_client):
    from backend.routes.drivers import get_driver_monthly_earnings

    _serve_driver_rides(mock_supabase_client, _tip_rides(_TEN_DIME_TIPS))
    result = await get_driver_monthly_earnings(months=6, current_user={"id": _USER_ID})

    assert len(result) == 1
    assert result[0]["tips"] == 1.0
    assert type(result[0]["tips"]) is float


async def test_comparison_tips_are_exact_decimal_sum(mock_supabase_client):
    from backend.routes.drivers import get_driver_earnings_comparison

    _serve_driver_rides(mock_supabase_client, _tip_rides(_TEN_DIME_TIPS))
    result = await get_driver_earnings_comparison(period="week", current_user={"id": _USER_ID})

    assert result["current"]["tips"] == 1.0
    assert type(result["current"]["tips"]) is float
    assert result["previous"]["tips"] == 0.0
    assert result["change_pct"]["tips"] == 100.0


async def test_earnings_total_tips_is_exact_decimal_string(mock_supabase_client):
    from backend.routes.drivers import get_driver_earnings

    tips = [Decimal("1.10"), Decimal("2.20"), Decimal("3.30")] + _TEN_DIME_TIPS
    _serve_driver_rides(mock_supabase_client, _tip_rides(tips))
    result = await get_driver_earnings(period="week", current_user={"id": _USER_ID})

    assert sum(tips, Decimal("0")) == Decimal("7.60")
    assert result["total_tips"] == "7.60"


async def test_earnings_total_tips_accepts_non_float_numerics(mock_supabase_client):
    """The tip sum now goes through _d() like every sibling money field, so a
    str-typed tip value sums correctly instead of raising inside the try and
    turning the whole summary into a 503. Not a symptom seen in production
    today (PostgREST returns NUMERIC as JSON numbers); it pins the _d() path."""
    from backend.routes.drivers import get_driver_earnings

    rides = _tip_rides([Decimal("0")] * 3)
    for r, t in zip(rides, ("1.10", "2.20", "3.30")):
        r["tip_amount"] = t
    _serve_driver_rides(mock_supabase_client, rides)
    result = await get_driver_earnings(period="week", current_user={"id": _USER_ID})

    assert result["total_tips"] == "6.60"
