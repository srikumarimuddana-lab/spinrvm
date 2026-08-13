"""Coverage-closure tests for routes/drivers/earnings.py (A1c Sub-tier A).

get_driver_balance already has decent coverage from test_drivers_extended.py
(happy path, all-money-out-payouts-deducted regression, stripe-sync exclusion,
DB-error 503, driver-not-found). This file adds the one isolated branch that
was still missing there (the driver_bonuses fetch failing) plus the whole of
the remaining, previously-untested endpoints in this module:
  - get_driver_bonuses (entirely untested)
  - get_driver_earnings: service-area timezone lookup, incentive-claims
    lookup failure, fare_breakdown_snapshot tax fallback, rides-fetch 503,
    bonus-fetch degrade
  - get_driver_daily_earnings: date-less row skip, DB-error 503
  - get_driver_trip_earnings (entirely untested)
  - get_driver_weekly_earnings / get_driver_monthly_earnings: pre-aggregated
    driver_daily_stats path, RPC/lookup-failure fallback to the rides table,
    and the rides-fallback DB-error 503
  - get_driver_earnings_comparison (entirely untested)
  - get_driver_earnings_forecast: happy path, rides-fetch 503, and the
    outer computation-exception fallback to the all-zero response

Patch-target conventions (matching test_subscriptions_coverage.py /
test_drivers_extended.py):
  - `db_supabase` is a module reference shared by every importer;
    `patch("backend.db_supabase.<fn>")` covers both direct
    `db_supabase.<fn>(...)` calls in earnings.py AND `_deps.db.<fn>(...)`
    calls (`db = db_supabase` is a module alias, not a copy).
  - `db_supabase.supabase` (the raw supabase-py client) is used directly
    (not via run_sync) for the ride_incentive_claims lookup; patch
    `backend.db_supabase.supabase`.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.anyio

USER_ID = "user_earnings_cov"
DRIVER_ID = "driver_earnings_cov"


def _driver(**extra) -> dict:
    return {"id": DRIVER_ID, "user_id": USER_ID, **extra}


def _ride(**extra) -> dict:
    base = {
        "id": "ride-1",
        "driver_id": DRIVER_ID,
        "status": "completed",
        "base_fare": 10.00,
        "distance_fare": 5.00,
        "time_fare": 2.00,
        "tip_amount": 3.00,
        "driver_earnings": 15.00,
        "ride_completed_at": "2026-08-01T12:00:00+00:00",
        "distance_km": 4.2,
        "duration_minutes": 12,
    }
    base.update(extra)
    return base


# ============================================================
# get_driver_balance: the one isolated branch test_drivers_extended.py
# doesn't already cover (driver_bonuses fetch failure)
# ============================================================


class TestGetDriverBalanceBonusFetchFailure:
    async def test_bonus_fetch_error_raises_503(self):
        from backend.routes.drivers import get_driver_balance

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [_ride()]
            if table == "payouts":
                return []
            if table == "driver_bonuses":
                raise Exception("driver_bonuses table unreachable")
            return []

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            with pytest.raises(HTTPException) as exc:
                await get_driver_balance(current_user={"id": USER_ID})
        assert exc.value.status_code == 503


# ============================================================
# get_driver_bonuses
# ============================================================


class TestGetDriverBonuses:
    async def test_driver_not_found_404(self):
        from backend.routes.drivers import get_driver_bonuses

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await get_driver_bonuses(current_user={"id": "ghost"})
        assert exc.value.status_code == 404

    async def test_returns_bonuses_with_total(self):
        from backend.routes.drivers import get_driver_bonuses

        bonuses = [
            {"id": "b1", "amount": "10.00", "kind": "quest", "description": "Weekend quest", "created_at": "t1"},
            {"id": "b2", "amount": "5.50", "kind": "referral", "description": "Referral", "created_at": "t2"},
        ]

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "driver_bonuses":
                return bonuses
            return []

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = await get_driver_bonuses(current_user={"id": USER_ID})

        assert len(result["bonuses"]) == 2
        assert result["total"] == "15.50"
        assert result["bonuses"][0]["kind"] == "quest"

    async def test_db_error_raises_503(self):
        from backend.routes.drivers import get_driver_bonuses

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            raise Exception("db down")

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            with pytest.raises(HTTPException) as exc:
                await get_driver_bonuses(current_user={"id": USER_ID})
        assert exc.value.status_code == 503


# ============================================================
# get_driver_earnings (period summary)
# ============================================================


class TestGetDriverEarnings:
    async def test_driver_not_found_404(self):
        from backend.routes.drivers import get_driver_earnings

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await get_driver_earnings(period="week", current_user={"id": "ghost"})
        assert exc.value.status_code == 404

    async def test_uses_driver_service_area_timezone(self):
        """A driver with service_area_id set must resolve the area's
        timezone (instead of the America/Regina default) without erroring."""
        from backend.routes.drivers import get_driver_earnings

        driver = _driver(service_area_id="area-yyz")
        sa_calls = []

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                sa_calls.append(filters)
                return [{"id": "area-yyz", "timezone": "America/Toronto"}]
            if table == "rides":
                return [_ride()]
            return []

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = await get_driver_earnings(period="week", current_user={"id": USER_ID})

        assert sa_calls and sa_calls[0] == {"id": "area-yyz"}
        assert result["total_rides"] == 1

    async def test_incentive_claims_lookup_failure_degrades_to_zero(self):
        from backend.routes.drivers import get_driver_earnings

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [_ride()]
            return []

        fake_supabase = MagicMock()
        fake_supabase.table.return_value.select.return_value.in_.return_value.execute.side_effect = Exception(
            "ride_incentive_claims unreachable"
        )

        with (
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.db_supabase.supabase", fake_supabase),
        ):
            result = await get_driver_earnings(period="week", current_user={"id": USER_ID})

        assert result["total_incentives"] == "0.00"

    async def test_tax_fallback_from_fare_breakdown_snapshot(self):
        """A ride with tax_amount == 0 but a fare_breakdown_snapshot carrying
        gst/pst line items must have those summed into total_tax."""
        from backend.routes.drivers import get_driver_earnings

        ride = _ride(
            tax_amount=0,
            fare_breakdown_snapshot={
                "lines": [
                    {"type": "gst", "amount": "0.75"},
                    {"type": "pst", "amount": "0.90"},
                    {"type": "base", "amount": "10.00"},  # not a tax line -> ignored
                ]
            },
        )

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [ride]
            return []

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = await get_driver_earnings(period="week", current_user={"id": USER_ID})

        assert result["total_tax"] == "1.65"

    async def test_rides_fetch_failure_raises_503(self):
        from backend.routes.drivers import get_driver_earnings

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            raise Exception("rides table unreachable")

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            with pytest.raises(HTTPException) as exc:
                await get_driver_earnings(period="week", current_user={"id": USER_ID})
        assert exc.value.status_code == 503

    async def test_bonus_fetch_failure_degrades_but_still_returns_ride_earnings(self):
        from backend.routes.drivers import get_driver_earnings

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [_ride()]
            if table == "driver_bonuses":
                raise Exception("driver_bonuses down")
            return []

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = await get_driver_earnings(period="week", current_user={"id": USER_ID})

        assert result["total_bonuses"] == "0.00"
        assert Decimal(result["total_earnings"]) > Decimal("0")

    async def test_all_period_skips_date_filter(self):
        from backend.routes.drivers import get_driver_earnings

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                assert "ride_completed_at" not in (filters or {})
                return [_ride()]
            return []

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = await get_driver_earnings(period="all", current_user={"id": USER_ID})
        assert result["period"] == "all"

    async def test_average_per_ride_zero_when_no_rides(self):
        from backend.routes.drivers import get_driver_earnings

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            return []

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = await get_driver_earnings(period="week", current_user={"id": USER_ID})
        assert result["average_per_ride"] == "0.00"


class TestGetDriverEarningsLegacyActivityStats:
    """Regression for the bug reported against a migrated driver's Activity
    screen: "All Time" showed 17 real rides in the list below a stat block
    reading Total Earned $0.00 / 0 Total Trips / 0.0 KM Driven / 0h Online
    Time — even though the rides existed. Root cause: total_rides/
    total_distance_km/total_duration_minutes were summed over the SAME
    EXCLUDE_LEGACY_RIDES-filtered `rides` list used for money, so a driver
    whose completed rides in the period are entirely legacy-imported got 0
    for all three despite utils/legacy_rides being explicit that the
    exclusion "only governs money math" and imported rides "remain fully
    visible in ride history"."""

    def _get_rows_legacy_and_real(self, legacy_rides, real_rides):
        """Mimics EXCLUDE_LEGACY_RIDES's PostgREST predicate: a filters dict
        carrying that key returns only non-legacy rows; without it, both."""

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                if filters and filters.get("legacy_import_metadata") == {"$eq": {}}:
                    return list(real_rides)
                if filters and filters.get("status") == "cancelled":
                    return []
                return list(legacy_rides) + list(real_rides)
            return []

        return get_rows

    async def test_all_legacy_rides_report_real_trip_count_but_zero_money(self):
        from backend.routes.drivers import get_driver_earnings

        legacy_rides = [
            _ride(
                id=f"legacy-{i}",
                legacy_import_metadata={"source": "previous_app"},
                distance_km=5.0,
                duration_minutes=10,
            )
            for i in range(3)
        ]

        with patch(
            "backend.db_supabase.get_rows",
            AsyncMock(side_effect=self._get_rows_legacy_and_real(legacy_rides, [])),
        ):
            result = await get_driver_earnings(period="all", current_user={"id": USER_ID})

        # Activity stats reflect all completed rides, legacy included.
        assert result["total_rides"] == 3
        assert result["total_distance_km"] == 15.0
        assert result["total_duration_minutes"] == 30
        # Money stays legacy-excluded — those dollars were already paid out
        # by the previous app (Finding 3, A30).
        assert result["total_earnings"] == "0.00"
        assert result["average_per_ride"] == "0.00"

    async def test_mixed_legacy_and_real_rides_split_correctly(self):
        from backend.routes.drivers import get_driver_earnings

        legacy_rides = [
            _ride(
                id="legacy-1", legacy_import_metadata={"source": "previous_app"}, distance_km=8.0, duration_minutes=20
            )
        ]
        real_rides = [_ride(id="real-1", distance_km=4.2, duration_minutes=12)]

        with patch(
            "backend.db_supabase.get_rows",
            AsyncMock(side_effect=self._get_rows_legacy_and_real(legacy_rides, real_rides)),
        ):
            result = await get_driver_earnings(period="all", current_user={"id": USER_ID})

        # Trip count/distance/duration include the legacy ride.
        assert result["total_rides"] == 2
        assert result["total_distance_km"] == pytest.approx(12.2)
        assert result["total_duration_minutes"] == 32
        # Average is over the one real, earning ride only — not diluted by
        # the legacy ride's $0 contribution.
        assert Decimal(result["total_earnings"]) > Decimal("0")
        assert result["average_per_ride"] == result["total_earnings"]


# ============================================================
# get_driver_daily_earnings
# ============================================================


class TestGetDriverDailyEarnings:
    async def test_skips_rides_missing_completed_at(self):
        from backend.routes.drivers import get_driver_daily_earnings

        rides = [_ride(ride_completed_at=None), _ride(ride_completed_at="2026-08-01T00:00:00+00:00")]

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return rides
            return []

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = await get_driver_daily_earnings(days=7, current_user={"id": USER_ID})

        assert len(result) == 1
        assert result[0]["date"] == "2026-08-01"

    async def test_db_error_raises_503(self):
        from backend.routes.drivers import get_driver_daily_earnings

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            raise Exception("db down")

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            with pytest.raises(HTTPException) as exc:
                await get_driver_daily_earnings(days=7, current_user={"id": USER_ID})
        assert exc.value.status_code == 503

    async def test_earnings_accumulate_via_decimal_not_float(self):
        """A26-adjacent P2 finding (docs/audit/2026-08-11-driver-rider-migration-audit.md):
        raw float() accumulation over many rides drifts off the exact cent
        value. 10 rides at $0.10+$0.10+$0.10+$0.10 each sum to exactly $4.00
        with Decimal; the old raw-float accumulation gave 3.9999999999999996."""
        from backend.routes.drivers import get_driver_daily_earnings

        rides = [
            _ride(
                base_fare=0.1,
                distance_fare=0.1,
                time_fare=0.1,
                tip_amount=0.1,
                ride_completed_at="2026-08-01T12:00:00+00:00",
            )
            for _ in range(10)
        ]

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return rides
            return []

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = await get_driver_daily_earnings(days=7, current_user={"id": USER_ID})

        assert len(result) == 1
        assert result[0]["earnings"] == 4.0


# ============================================================
# get_driver_trip_earnings (previously entirely untested)
# ============================================================


class TestGetDriverTripEarnings:
    async def test_days_over_365_raises_422(self):
        from backend.routes.drivers import get_driver_trip_earnings

        with pytest.raises(HTTPException) as exc:
            await get_driver_trip_earnings(limit=20, offset=0, days=400, current_user={"id": USER_ID})
        assert exc.value.status_code == 422

    async def test_driver_not_found_404(self):
        from backend.routes.drivers import get_driver_trip_earnings

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await get_driver_trip_earnings(limit=20, offset=0, days=None, current_user={"id": "ghost"})
        assert exc.value.status_code == 404

    async def test_returns_trip_list(self):
        from backend.routes.drivers import get_driver_trip_earnings

        ride = _ride(pickup_address="123 Main St", dropoff_address="456 Elm St", rider_rating=5)

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [ride]
            return []

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = await get_driver_trip_earnings(limit=20, offset=0, days=30, current_user={"id": USER_ID})

        assert result["limit"] == 20
        assert result["offset"] == 0
        assert len(result["trips"]) == 1
        assert result["trips"][0]["ride_id"] == "ride-1"
        assert result["trips"][0]["pickup_address"] == "123 Main St"

    async def test_no_days_restriction_omits_date_filter(self):
        from backend.routes.drivers import get_driver_trip_earnings

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                assert "ride_completed_at" not in (filters or {})
                return [_ride()]
            return []

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = await get_driver_trip_earnings(limit=20, offset=0, days=None, current_user={"id": USER_ID})
        assert len(result["trips"]) == 1

    async def test_db_error_raises_503(self):
        from backend.routes.drivers import get_driver_trip_earnings

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            raise Exception("db down")

        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            with pytest.raises(HTTPException) as exc:
                await get_driver_trip_earnings(limit=20, offset=0, days=None, current_user={"id": USER_ID})
        assert exc.value.status_code == 503


# ============================================================
# get_driver_weekly_earnings (previously entirely untested)
# ============================================================


class TestGetDriverWeeklyEarnings:
    async def test_driver_not_found_404(self):
        from backend.routes.drivers import get_driver_weekly_earnings

        with patch("backend.db_supabase.find_one", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await get_driver_weekly_earnings(weeks=4, current_user={"id": "ghost"})
        assert exc.value.status_code == 404

    async def test_uses_pre_aggregated_daily_stats(self):
        from backend.routes.drivers import get_driver_weekly_earnings

        stats = [
            {
                "stat_date": "2026-07-27",
                "total_earnings": 100.0,
                "total_tips": 10.0,
                "rides_completed": 5,
                "online_minutes": 300,
                "total_km": 40.0,
            }
        ]

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=stats)),
        ):
            result = await get_driver_weekly_earnings(weeks=4, current_user={"id": USER_ID})

        assert len(result) == 1
        assert result[0]["earnings"] == 100.0
        assert result[0]["rides"] == 5
        assert result[0]["online_hours"] == 5.0

    async def test_daily_stats_lookup_failure_falls_back_to_rides(self):
        from backend.routes.drivers import get_driver_weekly_earnings

        def get_rows(table, filters=None, **kw):
            if table == "driver_daily_stats":
                raise Exception("rpc unavailable")
            if table == "rides":
                return [_ride()]
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        ):
            result = await get_driver_weekly_earnings(weeks=4, current_user={"id": USER_ID})

        assert len(result) == 1
        assert result[0]["rides"] == 1

    async def test_no_daily_stats_rows_falls_back_to_rides(self):
        from backend.routes.drivers import get_driver_weekly_earnings

        def get_rows(table, filters=None, **kw):
            if table == "driver_daily_stats":
                return []
            if table == "rides":
                return [_ride()]
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        ):
            result = await get_driver_weekly_earnings(weeks=4, current_user={"id": USER_ID})

        assert len(result) == 1
        assert result[0]["earnings"] == pytest.approx(20.0)

    async def test_rides_fallback_earnings_accumulate_via_decimal(self):
        """See TestGetDriverDailyEarnings.test_earnings_accumulate_via_decimal_not_float —
        same fix, same drift-prone inputs, in the rides-table fallback path."""
        from backend.routes.drivers import get_driver_weekly_earnings

        rides = [
            _ride(
                base_fare=0.1,
                distance_fare=0.1,
                time_fare=0.1,
                tip_amount=0.1,
                ride_completed_at="2026-08-01T12:00:00+00:00",
            )
            for _ in range(10)
        ]

        def get_rows(table, filters=None, **kw):
            if table == "driver_daily_stats":
                return []
            if table == "rides":
                return rides
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        ):
            result = await get_driver_weekly_earnings(weeks=4, current_user={"id": USER_ID})

        assert len(result) == 1
        assert result[0]["earnings"] == 4.0

    async def test_rides_fallback_db_error_raises_503(self):
        from backend.routes.drivers import get_driver_weekly_earnings

        def get_rows(table, filters=None, **kw):
            if table == "driver_daily_stats":
                return []
            raise Exception("rides table down")

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_driver_weekly_earnings(weeks=4, current_user={"id": USER_ID})
        assert exc.value.status_code == 503


# ============================================================
# get_driver_monthly_earnings (previously entirely untested)
# ============================================================


class TestGetDriverMonthlyEarnings:
    async def test_driver_not_found_404(self):
        from backend.routes.drivers import get_driver_monthly_earnings

        with patch("backend.db_supabase.find_one", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await get_driver_monthly_earnings(months=6, current_user={"id": "ghost"})
        assert exc.value.status_code == 404

    async def test_uses_pre_aggregated_daily_stats(self):
        from backend.routes.drivers import get_driver_monthly_earnings

        stats = [
            {
                "stat_date": "2026-07-15",
                "total_earnings": 200.0,
                "total_tips": 20.0,
                "rides_completed": 8,
                "online_minutes": 600,
                "total_km": 80.0,
            }
        ]

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=stats)),
        ):
            result = await get_driver_monthly_earnings(months=6, current_user={"id": USER_ID})

        assert len(result) == 1
        assert result[0]["month"] == "2026-07"
        assert result[0]["rides"] == 8

    async def test_daily_stats_lookup_failure_falls_back_to_rides(self):
        from backend.routes.drivers import get_driver_monthly_earnings

        def get_rows(table, filters=None, **kw):
            if table == "driver_daily_stats":
                raise Exception("rpc unavailable")
            if table == "rides":
                return [_ride()]
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        ):
            result = await get_driver_monthly_earnings(months=6, current_user={"id": USER_ID})

        assert len(result) == 1
        assert result[0]["rides"] == 1

    async def test_rides_fallback_earnings_accumulate_via_decimal(self):
        """See TestGetDriverDailyEarnings.test_earnings_accumulate_via_decimal_not_float —
        same fix, same drift-prone inputs, in the rides-table fallback path."""
        from backend.routes.drivers import get_driver_monthly_earnings

        rides = [
            _ride(
                base_fare=0.1,
                distance_fare=0.1,
                time_fare=0.1,
                tip_amount=0.1,
                ride_completed_at="2026-08-01T12:00:00+00:00",
            )
            for _ in range(10)
        ]

        def get_rows(table, filters=None, **kw):
            if table == "driver_daily_stats":
                return []
            if table == "rides":
                return rides
            return []

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        ):
            result = await get_driver_monthly_earnings(months=6, current_user={"id": USER_ID})

        assert len(result) == 1
        assert result[0]["earnings"] == 4.0

    async def test_rides_fallback_db_error_raises_503(self):
        from backend.routes.drivers import get_driver_monthly_earnings

        def get_rows(table, filters=None, **kw):
            if table == "driver_daily_stats":
                return []
            raise Exception("rides table down")

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_driver_monthly_earnings(months=6, current_user={"id": USER_ID})
        assert exc.value.status_code == 503


# ============================================================
# get_driver_earnings_comparison (previously entirely untested)
# ============================================================


class TestGetDriverEarningsComparison:
    async def test_driver_not_found_404(self):
        from backend.routes.drivers import get_driver_earnings_comparison

        with patch("backend.db_supabase.find_one", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await get_driver_earnings_comparison(period="week", current_user={"id": "ghost"})
        assert exc.value.status_code == 404

    async def test_earnings_accumulate_via_decimal_not_float(self):
        """See TestGetDriverDailyEarnings.test_earnings_accumulate_via_decimal_not_float —
        same fix, same drift-prone inputs, applied to summarize()'s current-period sum."""
        from backend.routes.drivers import get_driver_earnings_comparison

        rides = [
            _ride(
                base_fare=0.1,
                distance_fare=0.1,
                time_fare=0.1,
                tip_amount=0.1,
                ride_completed_at="2026-08-01T00:00:00+00:00",
            )
            for _ in range(10)
        ]

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=rides)),
        ):
            result = await get_driver_earnings_comparison(period="week", current_user={"id": USER_ID})

        assert result["current"]["earnings"] == 4.0

    async def test_week_period_computes_pct_change(self):
        from backend.routes.drivers import get_driver_earnings_comparison

        current_ride = _ride(ride_completed_at="2026-08-01T00:00:00+00:00")
        previous_ride = _ride(ride_completed_at="2026-07-20T00:00:00+00:00", tip_amount=1.00, driver_earnings=10.00)
        all_rides = [current_ride, previous_ride]

        def get_rows(table, filters=None, **kw):
            if table != "rides":
                return []
            gte = (filters or {}).get("ride_completed_at", {}).get("$gte", "")
            # "Current period" call passes a >=7d-ago start; "all_rides" call
            # passes a >=14d-ago start and includes both rows.
            if gte >= "2026-07-25":
                return [current_ride]
            return all_rides

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        ):
            result = await get_driver_earnings_comparison(period="week", current_user={"id": USER_ID})

        assert result["current"]["rides"] == 1
        assert result["previous"]["rides"] == 1
        assert result["change_pct"]["rides"] == 0.0

    async def test_month_period_zero_previous_gives_100pct_or_0pct(self):
        from backend.routes.drivers import get_driver_earnings_comparison

        current_ride = _ride(ride_completed_at="2026-08-01T00:00:00+00:00")

        def get_rows(table, filters=None, **kw):
            if table != "rides":
                return []
            gte = (filters or {}).get("ride_completed_at", {}).get("$gte", "")
            # Current-period query (>= ~30d ago) sees the ride; the all_rides
            # query (>= ~60d ago) also sees it but the previous window
            # (30-60d ago) then filters it out entirely -> previous == 0.
            return [current_ride]

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        ):
            result = await get_driver_earnings_comparison(period="month", current_user={"id": USER_ID})

        assert result["previous"]["rides"] == 0
        assert result["change_pct"]["rides"] == 100.0

    async def test_db_error_raises_503(self):
        from backend.routes.drivers import get_driver_earnings_comparison

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=Exception("db down"))),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_driver_earnings_comparison(period="week", current_user={"id": USER_ID})
        assert exc.value.status_code == 503


# ============================================================
# get_driver_earnings_forecast (previously almost entirely untested)
# ============================================================


class TestGetDriverEarningsForecast:
    async def test_driver_not_found_404(self):
        from backend.routes.drivers import get_driver_earnings_forecast

        with patch("backend.db_supabase.find_one", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await get_driver_earnings_forecast(current_user={"id": "ghost"})
        assert exc.value.status_code == 404

    async def test_computes_projection_from_recent_rides(self):
        from backend.routes.drivers import get_driver_earnings_forecast

        rides = [_ride(ride_completed_at="2026-07-05T00:00:00+00:00", driver_earnings=28.00)]

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=rides)),
        ):
            result = await get_driver_earnings_forecast(current_user={"id": USER_ID})

        assert "this_week_earnings" in result
        assert "projected_weekly_total" in result
        assert Decimal(result["daily_avg_last_28d"]) >= Decimal("0")

    async def test_recent_rides_fetch_failure_raises_503(self):
        from backend.routes.drivers import get_driver_earnings_forecast

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=Exception("db down"))),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_driver_earnings_forecast(current_user={"id": USER_ID})
        assert exc.value.status_code == 503

    async def test_computation_exception_falls_back_to_zero_response(self):
        """A failure inside the (already-fetched) computation block must
        degrade to the all-zero forecast rather than 500ing a motivational
        home-screen widget."""
        from backend.routes.drivers import get_driver_earnings_forecast

        rides = [_ride(ride_completed_at="2026-07-05T00:00:00+00:00")]

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=_driver())),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=rides)),
            patch("backend.routes.drivers.earnings._money_str", side_effect=Exception("formatting blew up")),
        ):
            result = await get_driver_earnings_forecast(current_user={"id": USER_ID})

        assert result["this_week_earnings"] == "0.00"
        assert result["projected_weekly_total"] == "0.00"
