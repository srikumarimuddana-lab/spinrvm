"""Unit tests for utils/driver_statement.py — period math + aggregation."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from utils import driver_statement as stmt

# ---------------------------------------------------------------------------
# Period arithmetic
# ---------------------------------------------------------------------------


def test_latest_completed_weekly_is_prior_mon_to_sun():
    # Wednesday 2026-07-29 → prior full week Mon 07-20 .. Sun 07-26.
    assert stmt.latest_completed_weekly(date(2026, 7, 29)) == (date(2026, 7, 20), date(2026, 7, 26))
    # A Monday returns the week that just ended yesterday.
    assert stmt.latest_completed_weekly(date(2026, 7, 27)) == (date(2026, 7, 20), date(2026, 7, 26))


def test_latest_completed_monthly_is_prior_calendar_month():
    assert stmt.latest_completed_monthly(date(2026, 7, 30)) == (date(2026, 6, 1), date(2026, 6, 30))
    assert stmt.latest_completed_monthly(date(2026, 1, 1)) == (date(2025, 12, 1), date(2025, 12, 31))


def test_period_bounds_validation_and_month_lengths():
    assert stmt.period_bounds("weekly", date(2026, 7, 20)) == (date(2026, 7, 20), date(2026, 7, 26))
    assert stmt.period_bounds("monthly", date(2024, 2, 1)) == (date(2024, 2, 1), date(2024, 2, 29))  # leap
    with pytest.raises(ValueError):
        stmt.period_bounds("weekly", date(2026, 7, 21))  # not a Monday
    with pytest.raises(ValueError):
        stmt.period_bounds("monthly", date(2026, 7, 2))  # not the 1st
    with pytest.raises(ValueError):
        stmt.period_bounds("daily", date(2026, 7, 20))


def test_period_window_utc_uses_regina_offset():
    # Saskatchewan is fixed UTC-6 (no DST): local midnight = 06:00Z.
    gte, lt = stmt.period_window_utc(date(2026, 7, 20), date(2026, 7, 26))
    assert gte == "2026-07-20T00:00:00-06:00"
    assert lt == "2026-07-27T00:00:00-06:00"


def test_period_label_formats():
    assert stmt.period_label("monthly", date(2026, 6, 1), date(2026, 6, 30)) == "June 2026"
    assert stmt.period_label("weekly", date(2026, 7, 20), date(2026, 7, 26)) == "Jul 20 - 26, 2026"
    assert stmt.period_label("weekly", date(2026, 6, 29), date(2026, 7, 5)) == "Jun 29 - Jul 05, 2026"


# ---------------------------------------------------------------------------
# build_statement aggregation
# ---------------------------------------------------------------------------


def _tables(rides=(), cancelled=(), bonuses=(), payouts=(), claims=()):
    async def _get_rows(table, filters=None, **kw):
        if table == "rides":
            return list(cancelled) if filters.get("status") == "cancelled" else list(rides)
        if table == "driver_bonuses":
            return list(bonuses)
        if table == "payouts":
            return list(payouts)
        if table == "ride_incentive_claims":
            return list(claims)
        return []

    return AsyncMock(side_effect=_get_rows)


@pytest.mark.asyncio
async def test_build_statement_aggregates_all_income_sources():
    rides = [
        {"id": "r1", "driver_earnings": "20.00", "tip_amount": "2.00", "tax_amount": "1.10", "distance_km": 5.5, "duration_minutes": 12},
        {"id": "r2", "driver_earnings": "35.50", "tip_amount": 0, "tax_amount": 0, "distance_km": 10.0, "duration_minutes": 20},
    ]
    cancelled = [{"cancellation_fee_driver": "5.00"}]
    bonuses = [{"amount": "10.00", "kind": "quest"}]
    claims = [{"ride_id": "r1", "bonus_amount": "3.25"}]
    payouts = [
        {"created_at": "2026-07-21T10:00:00+00:00", "amount": 40.00, "fee": 0, "status": "completed", "payout_type": "standard"},
        {"created_at": "2026-07-23T10:00:00+00:00", "amount": 20.00, "fee": 0.50, "net_amount": 19.50, "status": "completed", "payout_type": "instant"},
        {"created_at": "2026-07-24T10:00:00+00:00", "amount": 99.00, "fee": 0, "status": "reversed", "payout_type": "standard"},
    ]
    with patch.object(stmt, "db_supabase") as db:
        db.get_rows = _tables(rides, cancelled, bonuses, payouts, claims)
        out = await stmt.build_statement({"id": "d1", "name": "Test Driver"}, "weekly", date(2026, 7, 20))

    e = out["earnings"]
    assert e["ride_earnings"] == "55.50"
    assert e["tips_included"] == "2.00"
    assert e["incentives"] == "3.25"
    assert e["bonuses"] == "10.00"
    assert e["cancellation_fees"] == "5.00"
    assert e["tax_collected"] == "1.10"
    # 55.50 + 3.25 + 10.00 + 5.00 + 1.10
    assert e["total"] == "74.85"
    assert out["trips"] == 2
    assert out["distance_km"] == 15.5
    assert out["period_label"] == "Jul 20 - 26, 2026"
    # Reversed payout listed but not counted in the total paid out.
    assert out["payouts_total"] == "60.00"
    assert len(out["payouts"]) == 3
    instant = out["payouts"][1]
    assert instant["fee"] == "0.50"
    assert instant["net"] == "19.50"
    assert instant["label"] == "Instant payout"
    assert out["has_activity"] is True


@pytest.mark.asyncio
async def test_build_statement_tax_snapshot_fallback():
    rides = [
        {
            "id": "r1",
            "driver_earnings": "10.00",
            "tax_amount": 0,
            "fare_breakdown_snapshot": {"lines": [{"type": "gst", "amount": "0.50"}, {"type": "pst", "amount": "0.60"}]},
        }
    ]
    with patch.object(stmt, "db_supabase") as db:
        db.get_rows = _tables(rides=rides)
        out = await stmt.build_statement({"id": "d1"}, "weekly", date(2026, 7, 20))
    assert out["earnings"]["tax_collected"] == "1.10"


@pytest.mark.asyncio
async def test_build_statement_no_activity():
    with patch.object(stmt, "db_supabase") as db:
        db.get_rows = _tables()
        out = await stmt.build_statement({"id": "d1"}, "monthly", date(2026, 6, 1))
    assert out["has_activity"] is False
    assert out["earnings"]["total"] == "0.00"
    assert out["payouts"] == []
    assert out["period_label"] == "June 2026"


@pytest.mark.asyncio
async def test_build_custom_statement_range_validation_and_label():
    with patch.object(stmt, "db_supabase") as db:
        db.get_rows = _tables(rides=[{"id": "r1", "driver_earnings": "10.00"}])
        out = await stmt.build_custom_statement({"id": "d1"}, date(2026, 7, 1), date(2026, 7, 15))
    assert out["period_type"] == "custom"
    assert out["period_start"] == "2026-07-01"
    assert out["period_end"] == "2026-07-15"
    assert out["period_label"] == "Jul 01 - 15, 2026"
    assert out["earnings"]["ride_earnings"] == "10.00"

    with pytest.raises(ValueError):
        await stmt.build_custom_statement({"id": "d1"}, date(2026, 7, 15), date(2026, 7, 1))
    with pytest.raises(ValueError):
        await stmt.build_custom_statement({"id": "d1"}, date(2025, 1, 1), date(2026, 7, 15))


@pytest.mark.asyncio
async def test_build_statement_legacy_fare_component_fallback():
    """Rows predating driver_earnings fall back to fare components + tip —
    same rule as routes/drivers/_shared._ride_income."""
    rides = [{"id": "r1", "driver_earnings": None, "base_fare": "5.00", "distance_fare": "3.00", "time_fare": "1.00", "tip_amount": "1.50"}]
    with patch.object(stmt, "db_supabase") as db:
        db.get_rows = _tables(rides=rides)
        out = await stmt.build_statement({"id": "d1"}, "weekly", date(2026, 7, 20))
    assert out["earnings"]["ride_earnings"] == "10.50"


@pytest.mark.asyncio
async def test_build_statement_splits_previous_app_payouts():
    """One era per number: stripe_sync rows (previous-app transfers) are
    summed apart from Spinr payouts, and a period whose only money is
    previous-app is flagged so the PDF can explain the $0.00 earnings
    instead of leaving it beside a real paid-out figure."""
    payouts = [
        {"created_at": "2026-06-07T10:00:00+00:00", "amount": 20.77, "fee": 0, "status": "completed", "payout_type": "stripe_sync"},
        {"created_at": "2026-06-11T10:00:00+00:00", "amount": 10.00, "fee": 0, "status": "completed", "payout_type": "stripe_sync"},
        {"created_at": "2026-06-15T10:00:00+00:00", "amount": 12.00, "fee": 0, "status": "completed", "payout_type": "standard"},
    ]
    rides = [{"id": "r1", "driver_earnings": "12.00", "tip_amount": 0, "tax_amount": 0, "distance_km": 1, "duration_minutes": 5}]
    with patch.object(stmt, "db_supabase") as db:
        db.get_rows = _tables(rides, (), (), payouts, ())
        out = await stmt.build_statement({"id": "d1"}, "monthly", date(2026, 6, 1))

    assert out["payouts_total"] == "42.77"
    assert out["payouts_spinr_total"] == "12.00"
    assert out["payouts_previous_app_total"] == "30.77"
    assert out["previous_app_only"] is False
    assert out["payouts"][0]["label"] == "Previous app payout"


@pytest.mark.asyncio
async def test_build_statement_flags_previous_app_only_period():
    """The June-2026 production case: two previous-app transfers, zero Spinr
    activity — '$0.00 earned · $30.77 paid out' with no explanation."""
    payouts = [
        {"created_at": "2026-06-07T10:00:00+00:00", "amount": 20.77, "fee": 0, "status": "completed", "payout_type": "stripe_sync"},
        {"created_at": "2026-06-11T10:00:00+00:00", "amount": 10.00, "fee": 0, "status": "completed", "payout_type": "stripe_sync"},
    ]
    with patch.object(stmt, "db_supabase") as db:
        db.get_rows = _tables((), (), (), payouts, ())
        out = await stmt.build_statement({"id": "d1"}, "monthly", date(2026, 6, 1))

    assert out["earnings"]["total"] == "0.00"
    assert out["payouts_previous_app_total"] == "30.77"
    assert out["payouts_spinr_total"] == "0.00"
    assert out["previous_app_only"] is True
