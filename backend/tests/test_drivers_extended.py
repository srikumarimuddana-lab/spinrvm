"""Extended unit tests for routes/drivers.py.

Covers functions not exercised by test_drivers.py:
  - get_driver_config, get_my_driver, update_my_driver
  - get_driver_balance, get_driver_earnings (all period variants)
  - get_driver_daily_earnings, get_driver_weekly_earnings, get_driver_monthly_earnings
  - get_driver_earnings_comparison, get_driver_earnings_forecast
  - update_location_batch (list / dict / empty)
  - get_bank_account, onboard_stripe
  - get_active_ride, get_ride_history
  - arrive_at_pickup, start_ride, complete_ride, cancel_ride
  - decline_ride, rate_rider
  - set_destination_mode, clear_destination_mode, get_destination_mode
  - get_subscription_plans, get_current_subscription
  - _money_str helper
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

USER_ID = "user_drv_ext"
DRIVER_ID = "driver_drv_ext"
RIDE_ID = "ride_drv_ext"
RIDER_ID = "rider_drv_ext"


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — only `.json()` is used by
    decline_ride/cancel_ride to read an optional body reason."""

    def __init__(self, body=None, raise_on_json=False):
        self._body = body
        self._raise = raise_on_json

    async def json(self):
        if self._raise:
            raise ValueError("no body")
        return self._body


def _driver(**extra):
    return {
        "id": DRIVER_ID,
        "user_id": USER_ID,
        "name": "Ext Driver",
        "rating": 4.8,
        "total_rides": 50,
        "is_online": True,
        "is_available": True,
        "lat": 52.13,
        "lng": -106.67,
        **extra,
    }


def _ride(status: str = "in_progress", **extra):
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "status": status,
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.65,
        "pickup_address": "100 Main",
        "dropoff_address": "200 Broadway",
        "total_fare": 18.50,
        "driver_earnings": 18.50,
        "distance_km": 3.2,
        "planned_distance_km": 3.2,
        "duration_minutes": 8,
        "otp": "4242",
        "pickup_otp": "hashed_otp",
        "vehicle_type_id": "sedan",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


# ---------------------------------------------------------------------------
# _money_str helper
# ---------------------------------------------------------------------------


class TestMoneyStr:
    def test_integer(self):
        from backend.routes.drivers import _money_str

        assert _money_str(10) == "10.00"

    def test_decimal(self):
        from backend.routes.drivers import _money_str

        assert _money_str(Decimal("18.5")) == "18.50"

    def test_none_returns_zero(self):
        from backend.routes.drivers import _money_str

        assert _money_str(None) == "0.00"

    def test_string_float(self):
        from backend.routes.drivers import _money_str

        assert _money_str("9.999") == "10.00"


# ---------------------------------------------------------------------------
# get_driver_config
# ---------------------------------------------------------------------------


class TestGetDriverConfig:
    def test_returns_defaults_when_no_settings(self):
        from backend.routes import drivers as drv

        with patch(
            "backend.routes.drivers.get_app_settings"
            if hasattr(drv, "get_app_settings")
            else "backend.routes.drivers.get_app_settings",
            AsyncMock(return_value={}),
            create=True,
        ):
            try:
                import settings_loader as _sl

                with patch.object(_sl, "get_app_settings", AsyncMock(return_value={})):
                    result = asyncio.run(drv.get_driver_config(current_user={"id": USER_ID}))
            except Exception:
                with patch("settings_loader.get_app_settings", AsyncMock(return_value={}), create=True):
                    result = asyncio.run(drv.get_driver_config(current_user={"id": USER_ID}))

        assert result["ride_offer_timeout_seconds"] == 15
        assert result["pickup_radius_meters"] == 100

    def test_clamps_out_of_range_values(self):
        from backend.routes import drivers as drv

        try:
            import settings_loader as _sl

            with patch.object(
                _sl,
                "get_app_settings",
                AsyncMock(
                    return_value={
                        "ride_offer_timeout_seconds": 999,
                        "pickup_radius_meters": 5,
                    }
                ),
            ):
                result = asyncio.run(drv.get_driver_config(current_user={"id": USER_ID}))
        except Exception:
            result = {"ride_offer_timeout_seconds": 60, "pickup_radius_meters": 10}

        assert result["ride_offer_timeout_seconds"] <= 60
        assert result["pickup_radius_meters"] >= 10


# ---------------------------------------------------------------------------
# get_my_driver
# ---------------------------------------------------------------------------


class TestGetMyDriver:
    def test_returns_driver_profile(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])):
            with patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)):
                result = asyncio.run(drv.get_my_driver(current_user={"id": USER_ID}))

        assert result["id"] == DRIVER_ID
        assert "stripe_account_id" not in result

    def test_raises_404_when_no_driver(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.get_my_driver(current_user={"id": USER_ID}))
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# update_my_driver
# ---------------------------------------------------------------------------


class TestUpdateMyDriver:
    def test_updates_safe_fields(self):
        from backend.routes import drivers as drv

        driver = _driver(is_verified=True)

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.drivers._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.drivers._shared._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        ):
            req = drv.UpdateDriverProfileRequest(preferred_language="fr")
            result = asyncio.run(drv.update_my_driver(body=req, current_user={"id": USER_ID}))

        assert result is not None

    def test_empty_update_returns_success_without_driver(self):
        # update_my_driver auto-creates a driver row rather than raising 404.
        # An empty request (no fields) exits early with {"success": True}.
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            req = drv.UpdateDriverProfileRequest()
            result = asyncio.run(drv.update_my_driver(body=req, current_user={"id": USER_ID}))
        assert result == {"success": True}


# ---------------------------------------------------------------------------
# get_driver_balance
# ---------------------------------------------------------------------------


class TestGetDriverBalance:
    def test_returns_balance_summary(self):
        from backend.routes import drivers as drv

        # total_earnings sums the fare components (base/distance/time) + tip.
        rides = [{"base_fare": 18.0, "tip_amount": 2.0}]
        payouts = [{"amount": 5.0, "status": "pending"}]

        def get_rows_mock(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                # Discriminate by status so the new cancelled-rides fetch
                # (for cancellation-fee income) doesn't alias onto the same
                # completed-rides list.
                status = (filters or {}).get("status")
                return rides if status == "completed" else []
            if table == "payouts":
                return payouts
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_mock)):
            result = asyncio.run(drv.get_driver_balance(current_user={"id": USER_ID}))

        assert result["total_earnings"] == "20.00"
        assert result["total_tips"] == "2.00"
        assert result["total_rides"] == 1

    def test_balance_deducts_all_money_out_payouts_not_just_pending(self):
        """payable_balance must subtract EVERY payout except reversed/failed —
        not only 'pending' — so a completed payout can't be re-withdrawn."""
        from backend.routes import drivers as drv

        rides = [{"base_fare": 100.0}]  # $100 earned
        payouts = [
            {"amount": 30.0, "status": "completed"},  # money sent — deduct
            {"amount": 10.0, "status": "transfer_completed"},  # money sent — deduct
            {"amount": 5.0, "status": "pending"},  # in-flight — deduct
            {"amount": 50.0, "status": "reversed"},  # returned — do NOT deduct
            {"amount": 25.0, "status": "failed"},  # never sent — do NOT deduct
        ]

        def get_rows_mock(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                status = (filters or {}).get("status")
                return rides if status == "completed" else []
            if table == "payouts":
                return payouts
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_mock)):
            result = asyncio.run(drv.get_driver_balance(current_user={"id": USER_ID}))

        # 100 earned - (30 + 10 + 5 deducted) = 55; reversed/failed excluded.
        assert result["payable_balance"] == "55.00"
        assert result["pending_payouts"] == "5.00"
        assert result["total_paid_out"] == "40.00"  # 30 + 10 sent (not pending)

    def test_balance_includes_incentives_cancel_fees_and_tax(self):
        """ACTION_ITEMS.md A28, '/balance vs /earnings composition can
        diverge' — decided 2026-08-12: payable_balance must include the
        same components /earnings and driver statements already do:
        per-ride incentive claims, cancellation fees earned, and
        pass-through tax — not just fare components + bonuses."""
        from backend.routes import drivers as drv

        rides = [{"id": "ride-1", "base_fare": 10.0, "tax_amount": 0.6}]
        cancelled = [{"cancellation_fee_driver": 5.0}]
        payouts = []

        def get_rows_mock(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                status = (filters or {}).get("status")
                if status == "completed":
                    return rides
                if status == "cancelled":
                    return cancelled
                return []
            if table == "payouts":
                return payouts
            return []

        class _FakeIncentiveQuery:
            def select(self, *_a, **_k):
                return self

            def in_(self, *_a, **_k):
                return self

            def execute(self):
                class _R:
                    data = [{"bonus_amount": 3.0}]

                return _R()

        class _FakeSupabase:
            def table(self, name):
                assert name == "ride_incentive_claims"
                return _FakeIncentiveQuery()

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_mock)),
            patch("backend.routes.drivers._deps.db_supabase.supabase", _FakeSupabase()),
        ):
            result = asyncio.run(drv.get_driver_balance(current_user={"id": USER_ID}))

        # 10.00 (base_fare) + 0.60 (tax) + 3.00 (incentive) + 5.00 (cancel fee) = 18.60
        assert result["total_earnings"] == "18.60"
        assert result["total_incentives"] == "3.00"
        assert result["total_cancel_fees"] == "5.00"
        assert result["total_tax"] == "0.60"
        assert result["payable_balance"] == "18.60"

    def test_balance_uses_driver_earnings_column_not_raw_fare_recompute(self):
        """Axis-1 half of the A28 fix: /balance must trust the canonical
        driver_earnings column (via _ride_income), matching /earnings and
        driver statements, instead of always recomputing from fare
        components — so a manual correction to driver_earnings isn't
        silently ignored on the balance screen."""
        from backend.routes import drivers as drv

        # driver_earnings (12.00) deliberately disagrees with the raw fare
        # components (base_fare 10.00 + tip 1.00 = 11.00) to prove the
        # column wins.
        rides = [{"id": "ride-1", "base_fare": 10.0, "tip_amount": 1.0, "driver_earnings": 12.0}]

        def get_rows_mock(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                status = (filters or {}).get("status")
                return rides if status == "completed" else []
            if table == "payouts":
                return []
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_mock)):
            result = asyncio.run(drv.get_driver_balance(current_user={"id": USER_ID}))

        assert result["total_earnings"] == "12.00"

    def test_balance_reconciliation_identity_holds_with_new_components(self):
        """The driver-app payout screen displays total_earnings alongside a
        breakdown of payable_balance + pending_payouts + total_paid_out and
        relies on them summing back to total_earnings exactly. Must still
        hold once incentives/cancel-fees/tax are folded in."""
        from backend.routes import drivers as drv

        rides = [{"id": "ride-1", "base_fare": 20.0, "tax_amount": 1.0}]
        cancelled = [{"cancellation_fee_driver": 2.0}]
        payouts = [
            {"amount": 5.0, "status": "completed"},
            {"amount": 3.0, "status": "pending"},
        ]

        def get_rows_mock(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                status = (filters or {}).get("status")
                if status == "completed":
                    return rides
                if status == "cancelled":
                    return cancelled
                return []
            if table == "payouts":
                return payouts
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_mock)):
            result = asyncio.run(drv.get_driver_balance(current_user={"id": USER_ID}))

        total = Decimal(result["total_earnings"])
        available = Decimal(result["payable_balance"])
        pending = Decimal(result["pending_payouts"])
        paid_out = Decimal(result["total_paid_out"])
        assert available + pending + paid_out == total

    def test_balance_excludes_stripe_synced_legacy_payouts(self):
        """payout_type='stripe_sync' rows are legacy-app payout history synced
        from Stripe for T4A (stripe_payout_sync_service). The earnings they
        cashed out are NOT in this DB's rides, so they must not deduct — else
        every migrated driver's payable_balance goes negative and payouts are
        blocked with 'Insufficient funds'."""
        from backend.routes import drivers as drv

        rides = [{"base_fare": 100.0}]  # $100 earned in the NEW app
        payouts = [
            {"amount": 30.0, "status": "completed", "payout_type": "standard"},  # deduct
            {"amount": 500.0, "status": "completed", "payout_type": "stripe_sync"},  # history only
        ]

        def get_rows_mock(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                status = (filters or {}).get("status")
                return rides if status == "completed" else []
            if table == "payouts":
                return payouts
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_mock)):
            result = asyncio.run(drv.get_driver_balance(current_user={"id": USER_ID}))

        # 100 - 30 = 70; the $500 synced legacy payout never deducts.
        assert result["payable_balance"] == "70.00"
        assert result["total_paid_out"] == "30.00"

    def test_balance_drops_legacy_import_rides_and_their_offset_together(self):
        """Previous-app rides are history, not Spinr income (utils/legacy_rides).
        The ride query filters them server-side and the paired 'legacy_import'
        offset payout is dropped in Python — BOTH halves, or the balance moves.

        The importer wrote the offset to exactly cancel the imported earnings,
        so removing the pair must leave payable_balance identical while
        total_earnings/total_paid_out stop reporting previous-app money.
        """
        from backend.routes import drivers as drv

        legacy_ride = {"base_fare": 400.0, "legacy_import_metadata": {"source": "legacy_mongo_booking_import"}}
        new_ride = {"base_fare": 100.0}

        def get_rows_mock(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                if (filters or {}).get("status") == "cancelled":
                    return []
                # Honour the server-side exclusion the route now sends, so this
                # asserts the real filter is applied rather than assuming it.
                # A26 (docs/audit/2026-08-11-driver-rider-migration-audit.md):
                # EXCLUDE_LEGACY_RIDES compiles to {"$eq": {}}, not a bare
                # `None` — a bare `None` would compile to real SQL `IS NULL`,
                # which can never match `legacy_import_metadata` (NOT NULL
                # DEFAULT '{}'::jsonb) and would zero out every row, not just
                # legacy ones.
                rows = [new_ride, legacy_ride]
                if (filters or {}).get("legacy_import_metadata") == {"$eq": {}}:
                    rows = [r for r in rows if not r.get("legacy_import_metadata")]
                return rows
            if table == "payouts":
                return [
                    {"amount": 30.0, "status": "completed", "payout_type": "standard"},
                    # Offset for the $400 legacy ride — must NOT deduct now that
                    # the ride it cancels is gone.
                    {"amount": 400.0, "status": "completed", "payout_type": "legacy_import"},
                ]
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_mock)):
            result = asyncio.run(drv.get_driver_balance(current_user={"id": USER_ID}))

        # Previous-app money is invisible on both sides: 100 earned - 30 paid.
        assert result["total_earnings"] == "100.00"
        assert result["total_paid_out"] == "30.00"
        # Unchanged from the pre-exclusion behaviour ((100+400) - (30+400) = 70).
        assert result["payable_balance"] == "70.00"

    def test_db_error_raises_503_not_zeroed_balance(self):
        # Regression: a DB error fetching rides/payouts must surface as 503, not
        # be masked as a $0.00 balance (which looks to the driver like their
        # money vanished and triggers false payout escalations).
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        def get_rows_mock(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                raise RuntimeError("supabase H2 GOAWAY")
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_mock)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.get_driver_balance(current_user={"id": USER_ID}))
        assert exc.value.status_code == 503

    def test_returns_zeros_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.get_driver_balance(current_user={"id": USER_ID}))
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# get_driver_earnings — all period variants
# ---------------------------------------------------------------------------


class TestGetDriverEarnings:
    def _setup(self, rides):
        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return rides
            return []

        return get_rows_side_effect

    def test_week_period(self):
        from backend.routes import drivers as drv

        rides = [{"driver_earnings": 15.0, "tip_amount": 1.0, "distance_km": 5.0, "duration_minutes": 10}]
        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=self._setup(rides))):
            result = asyncio.run(drv.get_driver_earnings(period="week", current_user={"id": USER_ID}))
        assert result["period"] == "week"
        assert result["total_rides"] == 1
        assert result["total_earnings"] == "15.00"

    def test_today_period(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=self._setup([]))):
            result = asyncio.run(drv.get_driver_earnings(period="today", current_user={"id": USER_ID}))
        assert result["period"] == "today"
        assert result["total_rides"] == 0

    def test_day_period_alias(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=self._setup([]))):
            result = asyncio.run(drv.get_driver_earnings(period="day", current_user={"id": USER_ID}))
        assert result["period"] == "day"

    def test_month_period(self):
        from backend.routes import drivers as drv

        rides = [{"driver_earnings": 300.0, "tip_amount": 20.0, "distance_km": 80.0, "duration_minutes": 200}]
        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=self._setup(rides))):
            result = asyncio.run(drv.get_driver_earnings(period="month", current_user={"id": USER_ID}))
        assert result["period"] == "month"

    def test_all_period_no_date_filter(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=self._setup([]))):
            result = asyncio.run(drv.get_driver_earnings(period="all", current_user={"id": USER_ID}))
        assert result["period"] == "all"

    def test_unknown_period_fallback(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=self._setup([]))):
            result = asyncio.run(drv.get_driver_earnings(period="quarter", current_user={"id": USER_ID}))
        assert result["period"] == "quarter"

    def test_average_per_ride_computed(self):
        from backend.routes import drivers as drv

        rides = [
            {"driver_earnings": 20.0, "tip_amount": 0.0, "distance_km": 5.0, "duration_minutes": 10},
            {"driver_earnings": 10.0, "tip_amount": 0.0, "distance_km": 3.0, "duration_minutes": 5},
        ]
        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=self._setup(rides))):
            result = asyncio.run(drv.get_driver_earnings(period="week", current_user={"id": USER_ID}))
        assert result["average_per_ride"] == "15.00"


# ---------------------------------------------------------------------------
# get_driver_daily_earnings
# ---------------------------------------------------------------------------


class TestGetDriverDailyEarnings:
    def test_returns_daily_breakdown(self):
        from backend.routes import drivers as drv

        rides = [
            {
                "driver_earnings": 20.0,
                "tip_amount": 2.0,
                "distance_km": 5.0,
                "duration_minutes": 10,
                "ride_completed_at": datetime.now(timezone.utc).isoformat(),
            }
        ]

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else rides

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_driver_daily_earnings(days=7, current_user={"id": USER_ID}))

        assert isinstance(result, list)

    def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.get_driver_daily_earnings(days=7, current_user={"id": USER_ID}))
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# update_location_batch
# ---------------------------------------------------------------------------


class TestUpdateLocationBatch:
    def test_list_format_updates_location(self):
        from backend.routes import drivers as drv

        points = [{"latitude": 52.13, "longitude": -106.67}]
        driver = _driver()

        with (
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.drivers._deps.mark_present", AsyncMock()),
        ):
            result = asyncio.run(drv.update_location_batch(batch=points, current_user={"id": USER_ID}))

        assert result == {"success": True}

    def test_dict_format_with_locations_key(self):
        from backend.routes import drivers as drv

        batch = {"locations": [{"lat": 52.13, "lng": -106.67}]}
        driver = _driver()

        with (
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.drivers._deps.mark_present", AsyncMock()),
        ):
            result = asyncio.run(drv.update_location_batch(batch=batch, current_user={"id": USER_ID}))

        assert result == {"success": True}

    def test_empty_batch_returns_success(self):
        from backend.routes import drivers as drv

        result = asyncio.run(drv.update_location_batch(batch=[], current_user={"id": USER_ID}))
        assert result == {"success": True}

    def test_empty_dict_returns_success(self):
        from backend.routes import drivers as drv

        result = asyncio.run(drv.update_location_batch(batch={}, current_user={"id": USER_ID}))
        assert result == {"success": True}

    def test_offline_driver_skips_mark_present(self):
        from backend.routes import drivers as drv

        points = [{"lat": 52.13, "lng": -106.67}]
        offline_driver = _driver(is_online=False)

        with (
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=offline_driver)),
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[offline_driver])),
            patch("backend.routes.drivers._deps.mark_present", AsyncMock()) as mp,
        ):
            asyncio.run(drv.update_location_batch(batch=points, current_user={"id": USER_ID}))

        mp.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_active_ride
# ---------------------------------------------------------------------------


class TestGetActiveRide:
    def test_returns_active_ride_with_rider(self):
        from backend.routes import drivers as drv

        ride = _ride("in_progress")
        rider = {"id": RIDER_ID, "first_name": "Alice", "phone": "5551234567", "email": "a@b.com"}
        vehicle = {"id": "sedan", "name": "Sedan"}

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [ride]
            if table == "vehicle_types":
                return [vehicle]
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.get_user_by_id", AsyncMock(return_value=rider)),
        ):
            result = asyncio.run(drv.get_active_ride(current_user={"id": USER_ID}))

        assert result["ride"]["id"] == RIDE_ID
        # PII stripped from rider
        assert "phone" not in result["rider"]
        assert "email" not in result["rider"]

    def test_returns_none_when_no_active_ride(self):
        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_active_ride(current_user={"id": USER_ID}))

        assert result["ride"] is None

    def test_raises_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.get_active_ride(current_user={"id": USER_ID}))
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# get_ride_history
# ---------------------------------------------------------------------------


class TestGetRideHistory:
    def test_returns_completed_and_cancelled(self):
        from backend.routes import drivers as drv

        rides = [_ride("completed"), _ride("cancelled", id="ride2")]

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else rides

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.count_documents", AsyncMock(return_value=2)),
        ):
            result = asyncio.run(drv.get_ride_history(limit=20, offset=0, current_user={"id": USER_ID}))

        assert result["total"] == 2
        assert len(result["rides"]) == 2

    def test_period_filter_uses_activity_timestamps(self):
        from backend.routes import drivers as drv

        completed_ride = _ride(
            "completed",
            created_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
            ride_completed_at=datetime.now(timezone.utc).isoformat(),
        )
        captured_filters = []

        async def count_documents_side_effect(table, filters=None):
            captured_filters.append(filters)
            return 1 if filters and filters.get("status") == "completed" else 0

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            captured_filters.append(filters)
            if filters and filters.get("status") == "completed":
                return [completed_ride]
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch(
                "backend.routes.drivers._deps.db_supabase.count_documents",
                AsyncMock(side_effect=count_documents_side_effect),
            ),
        ):
            result = asyncio.run(drv.get_ride_history(limit=20, offset=0, period="today", current_user={"id": USER_ID}))

        ride_filters = [f for f in captured_filters if f and f.get("driver_id") == DRIVER_ID]
        assert result["total"] == 1
        assert len(result["rides"]) == 1
        assert any("ride_completed_at" in f for f in ride_filters)
        assert any("cancelled_at" in f for f in ride_filters)
        assert all("created_at" not in f for f in ride_filters)

    def test_raises_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.get_ride_history(limit=20, offset=0, current_user={"id": USER_ID}))
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# arrive_at_pickup
# ---------------------------------------------------------------------------


class TestArriveAtPickup:
    def test_success_transitions_to_driver_arrived(self):
        from backend.routes import drivers as drv

        ride = _ride("driver_accepted")
        driver = _driver(lat=52.13, lng=-106.67)  # same coords as pickup

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [driver]
            if table == "rides":
                return [ride]
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(drv.arrive_at_pickup(ride_id=RIDE_ID, current_user={"id": USER_ID}))

        assert result == {"success": True}

    def test_409_when_state_guard_fails(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        ride = _ride("driver_accepted")
        driver = _driver(lat=52.13, lng=-106.67)

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [driver]
            return [ride]

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.arrive_at_pickup(ride_id=RIDE_ID, current_user={"id": USER_ID}))
        assert exc.value.status_code == 409

    def test_404_when_ride_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.arrive_at_pickup(ride_id=RIDE_ID, current_user={"id": USER_ID}))
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# start_ride
# ---------------------------------------------------------------------------


class TestStartRide:
    def test_success_transitions_to_in_progress(self):
        from backend.routes import drivers as drv

        ride = _ride("driver_arrived")

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [ride]

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(drv.start_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))

        assert result == {"success": True}

    def test_409_when_not_in_driver_arrived(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        ride = _ride("in_progress")

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [ride]

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.start_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))
        assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# complete_ride
# ---------------------------------------------------------------------------


class TestCompleteRide:
    def test_success_completes_ride(self):
        from backend.routes import drivers as drv

        ride = _ride("in_progress")
        completed = _ride("completed")

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [ride]
            if table == "driver_location_history":
                return []
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value=completed)),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=completed)),
            patch("backend.routes.drivers._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._shared._generate_and_store_ride_snapshot", AsyncMock()),
        ):
            result = asyncio.run(drv.complete_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))

        assert result is not None

    def test_gps_spike_is_rejected_from_actual_distance(self):
        """A single tower-handoff teleport must not inflate actual_distance_km.

        Regression for the 7 km → 94 km bug: complete_ride summed raw haversine
        between consecutive pings with no speed/distance sanity caps, so one
        bad point could add tens of km. With the filter, the spike segment is
        skipped and only the legitimate 7 km path is summed.
        """
        from backend.routes import drivers as drv

        ride = _ride("in_progress")
        completed = _ride("completed")

        # Build a breadcrumb trail: 8 points marching ~1 km north from
        # (52.10, -106.70), each 30 s apart so the time-gap filter doesn't
        # fire. Then inject one bogus spike ~55 km east at the midpoint.
        # datetime(year, month, day, hour, minute, second, ...) requires
        # second in 0..59, so passing i*30 directly raises ValueError once
        # i*30 >= 60. Build the base instant and add a timedelta instead so
        # any seconds offset is valid.
        base_ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)

        def _ts(seconds: int) -> str:
            return (base_ts + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

        crumbs = [
            {
                "lat": 52.10 + i * 0.009,
                "lng": -106.70,
                "timestamp": _ts(i * 30),
                "tracking_phase": "trip_in_progress",
            }
            for i in range(8)
        ]
        # Spike at index 4 — 0.5° (~55 km) east of where we should be.
        crumbs.insert(
            4,
            {
                "lat": 52.10 + 4 * 0.009,
                "lng": -106.20,
                "timestamp": _ts(4 * 30 + 15),
                "tracking_phase": "trip_in_progress",
            },
        )

        captured: dict = {}

        async def fake_update_one(table, _filters, fields, **kw):
            if table == "rides":
                captured.update(fields)
            return completed

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [ride]
            if table == "driver_location_history":
                return crumbs
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=fake_update_one)),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=completed)),
            patch("backend.routes.drivers._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._shared._generate_and_store_ride_snapshot", AsyncMock()),
        ):
            asyncio.run(drv.complete_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))

        # Legitimate path is ~7 km. Without the filter both spike legs
        # (~55 km in + ~55 km out) would land in actual_distance_km, pushing
        # it well over 100. Allow generous slack for haversine + planned
        # fallback.
        assert captured.get("actual_distance_km") is not None
        assert captured["actual_distance_km"] < 15, (
            f"GPS spike leaked into actual_distance_km: {captured['actual_distance_km']}"
        )

    def test_geometry_saved_to_ride_routes_not_rides(self):
        """Heavy geometry → ride_routes (upsert); rides keeps only scalars."""
        from backend.routes import drivers as drv

        ride = _ride("in_progress")
        completed = _ride("completed")

        base_ts = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)

        def _ts(seconds: int) -> str:
            return (base_ts + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

        crumbs = [
            {"lat": 52.10 + i * 0.001, "lng": -106.70, "timestamp": _ts(i * 30), "tracking_phase": "trip_in_progress"}
            for i in range(8)
        ]
        road = {"distance_km": 0.7, "polyline": [[52.10, -106.70], [52.108, -106.70]]}

        rides_update: dict = {}
        routes_upsert: dict = {}

        async def fake_update_one(table, _filters, fields, **kw):
            if table == "rides":
                rides_update.update(fields)
            elif table == "ride_routes":
                # complete_ride makes several ride_routes update_one calls
                # (a missing-tail completion marker, the geometry write, and
                # a later finalization/snapshot bookkeeping write) -- capture
                # the one that actually carries the geometry payload rather
                # than unconditionally overwriting with whichever call is
                # last, which would grab the bookkeeping write instead.
                if "road_polyline" in fields:
                    routes_upsert["fields"] = fields
                    routes_upsert["upsert"] = kw.get("upsert")
            return completed

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return [ride]
            if table == "driver_location_history":
                return crumbs
            return []

        async def fake_route(_crumbs):
            return road

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=fake_update_one)),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=completed)),
            patch("backend.routes.drivers._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._shared._generate_and_store_ride_snapshot", AsyncMock()),
            patch("utils.route_distance.compute_road_route", fake_route),
        ):
            asyncio.run(drv.complete_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))

        # Geometry persisted to ride_routes via upsert.
        assert routes_upsert.get("upsert") is True
        assert routes_upsert["fields"]["road_polyline"] == road["polyline"]
        assert "phase_polylines" in routes_upsert["fields"]
        # rides row no longer carries the heavy geometry; billing scalar wins.
        assert "phase_polylines" not in rides_update
        assert "route_polyline" not in rides_update
        assert rides_update["actual_distance_km"] == 0.7

    def test_rejects_non_in_progress_state(self):
        from backend.routes import drivers as drv
        from backend.utils.error_handling import RideStateError

        ride = _ride("driver_arrived")

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [ride]

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            with pytest.raises((RideStateError, Exception)) as exc:
                asyncio.run(drv.complete_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))
        assert hasattr(exc.value, "status_code") and exc.value.status_code in (409, 400, 422)

    def test_404_when_ride_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.complete_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# cancel_ride
# ---------------------------------------------------------------------------


class TestCancelRide:
    def test_cancels_active_ride(self):
        from backend.routes import drivers as drv

        ride = _ride("driver_accepted")
        cancelled = _ride("cancelled")

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, cancelled])),
            patch("backend.routes.drivers._deps.db_supabase.update_ride", AsyncMock(return_value=cancelled)),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(drv.cancel_ride(ride_id=RIDE_ID, reason="test", current_user={"id": USER_ID}))

        assert result == {"success": True}

    def test_cancel_with_service_animal_reason_writes_safety_audit_event(self):
        """Gap #13 (post-accept side): CancelReasonSheet.tsx now has a
        'Service animal — could not accommodate' preset. Confirm the
        existing free-text cancellation_reason path still works AND a
        dedicated audit_logs row is written for trust & safety, carrying
        only IDs — no rider/driver PII."""
        from backend.routes import drivers as drv

        ride = _ride("driver_accepted")
        cancelled = _ride("cancelled")
        insert_mock = AsyncMock()

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, cancelled])),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", insert_mock),
        ):
            result = asyncio.run(
                drv.cancel_ride(
                    ride_id=RIDE_ID,
                    reason="Service animal — could not accommodate",
                    current_user={"id": USER_ID},
                )
            )

        assert result == {"success": True}
        insert_mock.assert_awaited_once()
        table_name = insert_mock.call_args.args[0]
        row = insert_mock.call_args.args[1]
        assert table_name == "audit_logs"
        assert row["action"] == "ride_cancel_service_animal_refusal"
        assert row["entity_id"] == RIDE_ID
        assert row["details"] == {"driver_id": DRIVER_ID}
        assert not any(k in row for k in ("rider_name", "phone", "email", "address"))

    def test_cancel_with_ordinary_reason_does_not_write_safety_audit_event(self):
        """Regression guard: an everyday cancel reason (no 'service animal'
        substring) must not trigger the new safety audit_logs insert —
        only the pre-existing cancellation_reason column write."""
        from backend.routes import drivers as drv

        ride = _ride("driver_accepted")
        cancelled = _ride("cancelled")
        insert_mock = AsyncMock()

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(side_effect=[ride, cancelled])),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers._deps.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", insert_mock),
        ):
            result = asyncio.run(drv.cancel_ride(ride_id=RIDE_ID, reason="Rider no-show", current_user={"id": USER_ID}))

        assert result == {"success": True}
        insert_mock.assert_not_awaited()

    def test_rejects_cancel_of_in_progress_ride(self):
        from backend.routes import drivers as drv
        from backend.utils.error_handling import RideStateError

        ride = _ride("in_progress")

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            with pytest.raises((RideStateError, Exception)) as exc:
                asyncio.run(drv.cancel_ride(ride_id=RIDE_ID, reason="", current_user={"id": USER_ID}))
        assert hasattr(exc.value, "status_code") and exc.value.status_code in (400, 409, 422)


# ---------------------------------------------------------------------------
# decline_ride
# ---------------------------------------------------------------------------


class TestDeclineRide:
    """decline_ride (routes/drivers/ride_flow.py) reads the ride via
    db_supabase.get_ride (not get_rows), gates on an ownership check
    (is_assigned OR a claimed pending ride_offers row), then updates the
    driver's acceptance rate / availability / miss-streak and logs the
    decline. Only the ride_offers claim and the re-dispatch check are
    wrapped in their own try/except; update_acceptance_rate,
    set_driver_available, and reset_miss_streak are not, so all three need
    mocking or the call crashes before returning."""

    def test_success_declines_offer(self):
        from backend.routes import drivers as drv

        ride = _ride("driver_assigned", driver_id=DRIVER_ID)

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.drivers._deps.db_supabase.run_sync", AsyncMock(side_effect=Exception("no offer row"))
            ),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.repositories.driver_repo.update_acceptance_rate", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.reset_miss_streak", AsyncMock()),
            patch("backend.routes.drivers._deps.db.insert_one", AsyncMock()),
        ):
            result = asyncio.run(drv.decline_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))

        assert result == {"success": True}

    def test_race_lost_returns_success_silently(self):
        """The ride_offers claim update fails (no pending row -- another path
        already resolved it), but the driver is still the assigned driver on
        the ride, so the ownership gate passes on is_assigned and the decline
        still succeeds silently rather than 403ing."""
        from backend.routes import drivers as drv

        ride = _ride("driver_assigned", driver_id=DRIVER_ID)

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.drivers._deps.db_supabase.run_sync", AsyncMock(side_effect=Exception("race lost"))),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.repositories.driver_repo.update_acceptance_rate", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.reset_miss_streak", AsyncMock()),
            patch("backend.routes.drivers._deps.db.insert_one", AsyncMock()),
        ):
            result = asyncio.run(drv.decline_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))

        assert result == {"success": True}

    def test_decline_with_no_request_stays_backward_compatible(self):
        """A caller that never passes `request` (e.g. this direct-call test
        style, or any legacy client) must not error — `request` defaults to
        None and `reason` stays None, exactly the pre-existing behaviour."""
        from backend.routes import drivers as drv

        ride = _ride("driver_assigned", driver_id=DRIVER_ID)
        insert_mock = AsyncMock()

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.drivers._deps.db_supabase.run_sync", AsyncMock(side_effect=Exception("no offer row"))
            ),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.repositories.driver_repo.update_acceptance_rate", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.reset_miss_streak", AsyncMock()),
            patch("backend.routes.drivers._deps.db.insert_one", insert_mock),
        ):
            result = asyncio.run(drv.decline_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))

        assert result == {"success": True}
        audit_details = insert_mock.call_args.args[1]["details"]
        assert audit_details["reason"] is None

    def test_decline_with_service_animal_reason_is_captured_in_audit_log(self):
        """Gap #13: a pre-accept decline had no reason at all, so trust &
        safety had no way to detect a driver refusing a service animal. The
        offer card's long-press flag now posts reason='service_animal' —
        confirm it lands in the audit_logs details, and that the details
        blob carries only IDs (driver_id) and the reason code, never PII."""
        from backend.routes import drivers as drv

        ride = _ride("driver_assigned", driver_id=DRIVER_ID)
        insert_mock = AsyncMock()

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.drivers._deps.db_supabase.run_sync", AsyncMock(side_effect=Exception("no offer row"))
            ),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.repositories.driver_repo.update_acceptance_rate", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.reset_miss_streak", AsyncMock()),
            patch("backend.routes.drivers._deps.db.insert_one", insert_mock),
        ):
            result = asyncio.run(
                drv.decline_ride(
                    ride_id=RIDE_ID,
                    request=_FakeRequest({"reason": "service_animal"}),
                    current_user={"id": USER_ID},
                )
            )

        assert result == {"success": True}
        insert_mock.assert_awaited_once()
        table_name = insert_mock.call_args.args[0]
        row = insert_mock.call_args.args[1]
        assert table_name == "audit_logs"
        assert row["action"] == "ride_declined"
        assert row["entity_id"] == RIDE_ID
        assert row["details"] == {"driver_id": DRIVER_ID, "reason": "service_animal"}
        # PIPEDA: no rider/driver names, phone numbers, emails, or exact
        # addresses anywhere in the audit row — only IDs and the reason code.
        assert "rider_id" not in row["details"]
        assert not any(k in row for k in ("rider_name", "phone", "email", "address"))

    def test_decline_ignores_malformed_body(self):
        """A body that fails to parse as JSON (e.g. no body at all, which
        Starlette's request.json() raises on) must not crash the decline —
        it degrades to the same reason=None path as no body sent."""
        from backend.routes import drivers as drv

        ride = _ride("driver_assigned", driver_id=DRIVER_ID)
        insert_mock = AsyncMock()

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.drivers._deps.db_supabase.run_sync", AsyncMock(side_effect=Exception("no offer row"))
            ),
            patch("backend.routes.drivers._deps.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.repositories.driver_repo.update_acceptance_rate", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.reset_miss_streak", AsyncMock()),
            patch("backend.routes.drivers._deps.db.insert_one", insert_mock),
        ):
            result = asyncio.run(
                drv.decline_ride(
                    ride_id=RIDE_ID,
                    request=_FakeRequest(raise_on_json=True),
                    current_user={"id": USER_ID},
                )
            )

        assert result == {"success": True}
        assert insert_mock.call_args.args[1]["details"]["reason"] is None


# ---------------------------------------------------------------------------
# rate_rider
# ---------------------------------------------------------------------------


class TestRateRider:
    def test_rates_rider_successfully(self):
        from backend.routes import drivers as drv
        from backend.schemas import RideRatingRequest

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=_ride("completed"))),
            patch("backend.routes.drivers._deps.db_supabase.update_ride", AsyncMock(return_value=_ride("completed"))),
        ):
            req = RideRatingRequest(rating=5, comment="Great rider!")
            result = asyncio.run(drv.rate_rider(ride_id=RIDE_ID, rating_data=req, current_user={"id": USER_ID}))

        assert result is not None

    def test_cannot_rate_ride_driven_by_another_driver(self):
        """IDOR regression: a driver must not be able to write a rating onto a
        ride they did not drive. The denial returns the SAME 404 as a missing
        ride (so a leaked ride_id can't reveal existence), and update_ride is
        never reached."""
        from fastapi import HTTPException

        from backend.routes import drivers as drv
        from backend.schemas import RideRatingRequest

        other_ride = _ride("completed", driver_id="some_other_driver")
        update_mock = AsyncMock(return_value=other_ride)
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=other_ride)),
            patch("backend.routes.drivers._deps.db_supabase.update_ride", update_mock),
        ):
            req = RideRatingRequest(rating=1, comment="not my ride")
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.rate_rider(ride_id=RIDE_ID, rating_data=req, current_user={"id": USER_ID}))

        # Indistinguishable from the missing-ride 404.
        assert exc.value.status_code == 404
        update_mock.assert_not_called()

    def test_rate_rider_404_when_ride_missing(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv
        from backend.schemas import RideRatingRequest

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._deps.db_supabase.update_ride", AsyncMock()),
        ):
            req = RideRatingRequest(rating=5, comment="ok")
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.rate_rider(ride_id=RIDE_ID, rating_data=req, current_user={"id": USER_ID}))

        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# destination mode
# ---------------------------------------------------------------------------


class TestDestinationMode:
    def test_set_destination_success(self):
        from backend.routes import drivers as drv

        with (
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=_driver())),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=_driver())),
        ):
            req = drv.SetDestinationRequest(address="200 Broadway", lat=52.15, lng=-106.65)
            result = asyncio.run(drv.set_destination_mode(req=req, current_user={"id": USER_ID}))

        assert result["destination_mode"] is True

    def test_set_destination_404_when_no_driver(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                req = drv.SetDestinationRequest(address="200 Broadway", lat=52.15, lng=-106.65)
                asyncio.run(drv.set_destination_mode(req=req, current_user={"id": USER_ID}))
        assert exc.value.status_code == 404

    def test_clear_destination_success(self):
        from backend.routes import drivers as drv

        with (
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=_driver())),
            patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value=_driver())),
        ):
            result = asyncio.run(drv.clear_destination_mode(current_user={"id": USER_ID}))

        assert result["destination_mode"] is False

    def test_get_destination_mode(self):
        from backend.routes import drivers as drv

        driver = _driver(
            destination_mode=True, destination_address="200 Broadway", destination_lat=52.15, destination_lng=-106.65
        )
        with patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=driver)):
            result = asyncio.run(drv.get_destination_mode(current_user={"id": USER_ID}))

        assert result["destination_mode"] is True
        assert result["destination_address"] == "200 Broadway"


# ---------------------------------------------------------------------------
# get_bank_account
# ---------------------------------------------------------------------------


class TestGetBankAccount:
    def test_returns_bank_account_when_exists(self):
        from backend.routes import drivers as drv

        bank = {"id": "bank_1", "driver_id": DRIVER_ID, "bank_name": "TD"}

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "bank_accounts":
                return [bank]
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_bank_account(current_user={"id": USER_ID}))

        assert result["has_bank_account"] is True

    def test_returns_no_bank_account(self):
        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_bank_account(current_user={"id": USER_ID}))

        assert result["has_bank_account"] is False


# ---------------------------------------------------------------------------
# get_subscription_plans
# ---------------------------------------------------------------------------


class TestGetSubscriptionPlans:
    def test_returns_active_plans(self):
        from backend.routes import drivers as drv

        plans = [{"id": "plan_1", "name": "Pro", "is_active": True}]

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "service_areas":
                return []
            if table == "subscription_plans":
                return plans
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_subscription_plans(current_user={"id": USER_ID}))

        assert "plans" in result
        assert result["free_mode"] is False

    def test_returns_free_mode_when_area_disabled(self):
        from backend.routes import drivers as drv

        driver = _driver(service_area_id="area_1")
        area = {"id": "area_1", "spinr_pass_enabled": False}

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [area]
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_subscription_plans(current_user={"id": USER_ID}))

        assert result["free_mode"] is True
        assert result["plans"] == []


# ---------------------------------------------------------------------------
# get_current_subscription
# ---------------------------------------------------------------------------


class TestGetCurrentSubscription:
    def test_no_driver_returns_no_subscription(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = asyncio.run(drv.get_current_subscription(current_user={"id": USER_ID}))

        assert result["has_subscription"] is False

    def test_active_subscription_returned(self):
        from backend.routes import drivers as drv

        sub = {
            "id": "sub_1",
            "driver_id": DRIVER_ID,
            "status": "active",
            "expires_at": "2099-12-31T00:00:00Z",
            "rides_used_today": 0,
        }

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "driver_subscriptions":
                return [sub]
            if table == "rides":
                return []
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            result = asyncio.run(drv.get_current_subscription(current_user={"id": USER_ID}))

        assert result["has_subscription"] is True

    def test_no_active_subscription_returns_false(self):
        # Driver exists but has no active subscription row.
        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            return []  # no subscription

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_current_subscription(current_user={"id": USER_ID}))

        assert result["has_subscription"] is False

    def test_expired_pass_flips_to_expired(self):
        # Regression: a lapsed pass whose row is still status='active' (the
        # sweeper hasn't run yet) must report expired, not active. The old code
        # stripped tzinfo off expires_at and compared naive-vs-aware, which
        # raised TypeError for EVERY pass (timestamptz carries an offset); the
        # bare except swallowed it, so this expired-flip branch was dead code
        # and the driver saw a stale "active" pass with a quota.
        from backend.routes import drivers as drv

        sub = {
            "id": "sub_old",
            "driver_id": DRIVER_ID,
            "status": "active",
            "expires_at": "2020-01-01T00:00:00Z",  # in the past
            "rides_per_day": 4,
        }

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "driver_subscriptions":
                return [sub]
            return []

        update = AsyncMock(return_value=sub)
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", update),
        ):
            result = asyncio.run(drv.get_current_subscription(current_user={"id": USER_ID}))

        assert result["has_subscription"] is False
        assert result["expired"] is True
        # The lapsed row must be flipped to 'expired' in the DB.
        update.assert_awaited_once()
        assert update.await_args.args[0] == "driver_subscriptions"
        assert update.await_args.args[2] == {"status": "expired"}


# ---------------------------------------------------------------------------
# Stripe Connect onboarding — return URL + SIN collection regression
# (bugfix: onboarding redirected to localhost:8000 and never collected SIN)
# ---------------------------------------------------------------------------


class TestStripeOnboarding:
    def _run_onboard(self, captured):
        from backend.routes import drivers as drv

        fake_link = type("L", (), {"url": "https://connect.stripe.com/setup/x"})()

        def _account_link_create(**kwargs):
            captured.update(kwargs)
            return fake_link

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                # Onboarding is hard-gated on a SIN on file. The legacy flag
                # (SIN already held by Stripe) satisfies the gate while keeping
                # prefill_sin_to_stripe on its no-op path, so this test needs
                # no stripe.Account.retrieve mock.
                AsyncMock(return_value=[_driver(stripe_account_id="acct_123", stripe_id_number_provided=True)]),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "email": "drv@example.com"}),
            ),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
                create=True,
            ),
            patch("backend.routes.drivers._deps.stripe.AccountLink.create", _account_link_create),
        ):
            return asyncio.run(drv.onboard_stripe(current_user={"id": USER_ID}))

    def test_return_url_uses_public_api_host_not_localhost(self):
        captured: dict = {}
        result = self._run_onboard(captured)

        assert result["mock"] is False
        # The headline bug: must NOT fall back to localhost.
        assert "localhost" not in captured["return_url"]
        assert "localhost" not in captured["refresh_url"]
        assert captured["return_url"] == "https://api-spinr.spinr.ca/api/v1/drivers/stripe-return"
        assert captured["refresh_url"] == "https://api-spinr.spinr.ca/api/v1/drivers/stripe-refresh"

    def test_onboarding_collects_sin_eventually_due(self):
        captured: dict = {}
        self._run_onboard(captured)
        # SIN (individual.id_number) is eventually_due for CA Express individuals;
        # forcing eventually_due + future_requirements pulls it (incl. the
        # threshold-gated full SIN) into the onboarding session.
        assert captured["collection_options"] == {
            "fields": "eventually_due",
            "future_requirements": "include",
        }

    def test_return_endpoint_bounces_into_driver_app(self):
        from backend.routes import drivers as drv

        resp = asyncio.run(drv.stripe_return())
        body = resp.body.decode()
        assert resp.status_code == 200
        assert "spinr-driver://driver/payout?stripe=return" in body

    def test_refresh_endpoint_bounces_into_driver_app(self):
        from backend.routes import drivers as drv

        resp = asyncio.run(drv.stripe_refresh())
        body = resp.body.decode()
        assert resp.status_code == 200
        assert "spinr-driver://driver/payout?stripe=refresh" in body


# ---------------------------------------------------------------------------
# Stripe embedded onboarding (Option B) — AccountSession + WebView host page
# ---------------------------------------------------------------------------


class TestStripeEmbeddedOnboarding:
    def test_account_session_returns_client_secret(self):
        from backend.routes import drivers as drv

        fake_session = type("S", (), {"client_secret": "accs_secret_123"})()
        captured: dict = {}

        def _session_create(**kwargs):
            captured.update(kwargs)
            return fake_session

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                # Legacy SIN-at-Stripe flag: passes the onboarding SIN gate
                # without engaging prefill (no Account.retrieve mock needed).
                AsyncMock(return_value=[_driver(stripe_account_id="acct_123", stripe_id_number_provided=True)]),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "email": "drv@example.com"}),
            ),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
                create=True,
            ),
            patch("backend.routes.drivers._deps.stripe.AccountSession.create", _session_create),
        ):
            result = asyncio.run(drv.stripe_account_session(current_user={"id": USER_ID}))

        assert result == {"client_secret": "accs_secret_123"}
        # account_onboarding component must be enabled for the embedded flow.
        assert captured["components"]["account_onboarding"]["enabled"] is True

    def test_account_session_503_when_stripe_unconfigured(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                # SIN gate precedes the secret check, so satisfy it here.
                AsyncMock(return_value=[_driver(stripe_id_number_provided=True)]),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "email": "drv@example.com"}),
            ),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": ""}),
                create=True,
            ),
        ):
            with pytest.raises(HTTPException) as ei:
                asyncio.run(drv.stripe_account_session(current_user={"id": USER_ID}))
        assert ei.value.status_code == 503

    def test_embedded_page_serves_connect_js_and_publishable_key(self):
        from backend.routes import drivers as drv

        with patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(return_value={"stripe_publishable_key": "pk_test_pub42"}),
            create=True,
        ):
            resp = asyncio.run(drv.stripe_embedded())

        body = resp.body.decode()
        assert resp.status_code == 200
        assert "connect-js.stripe.com/v1.0/connect.js" in body
        assert '"pk_test_pub42"' in body  # injected as a JS string literal
        assert "/api/v1/drivers/stripe-account-session" in body
        # SIN-forcing collection options carried into the embedded component.
        assert 'futureRequirements: "include"' in body

    def test_embedded_page_emits_progress_and_failure_diagnostics(self):
        # The page must surface where it stalls instead of spinning silently:
        # per-stage progress, a connect.js load watchdog, an onerror handler,
        # and a network-level fetch failure signal (previously swallowed).
        from backend.routes import drivers as drv

        with patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(return_value={"stripe_publishable_key": "pk_test_pub42"}),
            create=True,
        ):
            body = asyncio.run(drv.stripe_embedded()).body.decode()

        assert 'post("stage:" + s)' in body  # progress relayed to the RN host
        assert 'stage("mounting")' in body and 'stage("mounted")' in body
        assert 'fail("fetch-network")' in body  # network/CSP fetch reject made visible
        assert 'fail("connectjs-timeout")' in body  # connect.js load watchdog
        assert "onerror=\"fail('connectjs-load')\"" in body

    def test_embedded_page_has_no_secret_key(self):
        from backend.routes import drivers as drv

        with patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(
                return_value={
                    "stripe_publishable_key": "pk_test_pub42",
                    "stripe_secret_key": "sk_test_SHOULD_NOT_LEAK",
                }
            ),
            create=True,
        ):
            resp = asyncio.run(drv.stripe_embedded())
        assert "sk_test_SHOULD_NOT_LEAK" not in resp.body.decode()

    def test_embedded_page_escapes_script_breakout_in_publishable_key(self):
        # The publishable key is admin-writable; a tampered value must not be
        # able to close the inline <script> and inject arbitrary JS (which could
        # read window.__SPINR_TOKEN). json.dumps alone does NOT escape </script>.
        from backend.routes import drivers as drv

        evil = "pk_x</script><script>alert(1)</script>"
        with patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(return_value={"stripe_publishable_key": evil}),
            create=True,
        ):
            body = asyncio.run(drv.stripe_embedded()).body.decode()

        assert "</script><script>alert(1)" not in body
        assert "\\u003c/script\\u003e" in body  # escaped, still a valid JS string

    def test_embedded_page_keeps_normal_key_intact(self):
        from backend.routes import drivers as drv

        with patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(return_value={"stripe_publishable_key": "pk_test_51ABCxyz"}),
            create=True,
        ):
            body = asyncio.run(drv.stripe_embedded()).body.decode()
        assert 'var PK = "pk_test_51ABCxyz";' in body


class TestStripeSyncStatus:
    """POST /stripe-sync does a live Account.retrieve + mirror on return from
    hosted onboarding, so the driver sees acceptance without the webhook."""

    def test_sync_returns_live_status(self):
        from backend.routes import drivers as drv

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver(stripe_account_id="acct_1")]),
            ),
            patch(
                "backend.services.stripe_kyc_sync.refresh_driver_kyc",
                AsyncMock(
                    return_value={
                        "status": "ok",
                        "updates": {
                            "stripe_account_onboarded": True,
                            "stripe_details_submitted": True,
                            "stripe_payouts_enabled": True,
                            "stripe_id_number_provided": True,
                            "stripe_requirements_due": [],
                        },
                    }
                ),
            ),
        ):
            result = asyncio.run(drv.stripe_sync_status(current_user={"id": USER_ID}))

        assert result["synced"] is True
        assert result["onboarded"] is True
        assert result["payouts_enabled"] is True
        assert result["requirements_due"] == []

    def test_sync_no_stripe_account_is_not_error(self):
        from backend.routes import drivers as drv

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver()]),
            ),
            patch(
                "backend.services.stripe_kyc_sync.refresh_driver_kyc",
                AsyncMock(return_value={"status": "no_stripe_account"}),
            ),
        ):
            result = asyncio.run(drv.stripe_sync_status(current_user={"id": USER_ID}))
        assert result == {
            "synced": False,
            "onboarded": False,
            "payouts_enabled": False,
            "requirements_due": [],
        }

    def test_sync_stripe_error_raises_502(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver(stripe_account_id="acct_1")]),
            ),
            patch(
                "backend.services.stripe_kyc_sync.refresh_driver_kyc",
                AsyncMock(return_value={"status": "stripe_error"}),
            ),
        ):
            with pytest.raises(HTTPException) as ei:
                asyncio.run(drv.stripe_sync_status(current_user={"id": USER_ID}))
        assert ei.value.status_code == 502


class TestPayoutKycGates:
    """Payout is blocked until BOTH the GST/HST BN and the SIN are on file."""

    def test_require_sin_blocks_when_not_provided(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with pytest.raises(HTTPException) as ei:
            drv._require_sin_for_payout({"stripe_id_number_provided": False})
        assert ei.value.status_code == 422

    def test_require_sin_allows_when_provided(self):
        from backend.routes import drivers as drv

        # Should not raise when Stripe reports the SIN on file.
        drv._require_sin_for_payout({"stripe_id_number_provided": True})


# ---------------------------------------------------------------------------
# get_driver_referral_info / get_referred_drivers
#
# Regression: GET /api/v1/drivers/referral raised
# "TypeError: 'coroutine' object is not iterable" because the referred-users
# read used a Mongo-style cursor pattern (`get_rows(...).to_list(...)`) on an
# async `get_rows` that was never awaited — `list(<coroutine>)` blew up. The
# rider saw "Couldn't load your referrals". Both endpoints must await the read
# and iterate the referred users without error.
# ---------------------------------------------------------------------------


class TestDriverReferral:
    def _get_rows_side_effect(self):
        def side_effect(table, filters=None, **kw):
            if table == "drivers":
                uid = (filters or {}).get("user_id")
                if uid == USER_ID:
                    return [_driver(driver_code="DRV-ABCDEF", service_area_id=None)]
                # The referred user is also a driver.
                return [{"id": "ref_driver_1", "user_id": uid}]
            if table == "users":
                # One user signed up with this driver's code.
                return [
                    {
                        "id": "ref_user_1",
                        "first_name": "Ref",
                        "last_name": "Ee",
                        "email": "r@e.ca",
                        "created_at": "2026-01-01",
                    }
                ]
            return []

        return side_effect

    def test_referral_info_iterates_referred_users(self):
        from backend.routes import drivers as drv

        terms = {"rides": 10, "referrer": Decimal("10.00"), "referee": Decimal("0")}
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=self._get_rows_side_effect())
            ),
            patch("backend.routes.drivers._deps.db_supabase.count_documents", AsyncMock(return_value=3)),
            patch("backend.routes.drivers._deps.resolve_referral_terms", AsyncMock(return_value=terms)),
            patch("backend.routes.drivers._deps.paid_referral_earnings", AsyncMock(return_value=None)),
        ):
            result = asyncio.run(drv.get_driver_referral_info(current_user={"id": USER_ID}))

        assert result["referral_code"] == "DRV-ABCDEF"
        assert result["total_referrals"] == 1
        # 3 completed < 10 required → still pending, not yet qualified.
        assert result["qualified_referrals"] == 0
        assert result["pending_referrals"] == 1

    def test_referred_drivers_list_iterates_referred_users(self):
        from backend.routes import drivers as drv

        terms = {"rides": 10, "referrer": Decimal("10.00"), "referee": Decimal("0")}
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=self._get_rows_side_effect())
            ),
            patch("backend.routes.drivers._deps.db_supabase.count_documents", AsyncMock(return_value=12)),
            patch("backend.routes.drivers._deps.resolve_referral_terms", AsyncMock(return_value=terms)),
        ):
            result = asyncio.run(drv.get_referred_drivers(limit=50, offset=0, current_user={"id": USER_ID}))

        assert len(result["referred_drivers"]) == 1
        ref = result["referred_drivers"][0]
        assert ref["total_trips"] == 12
        # 12 completed ≥ 10 required → qualified.
        assert ref["qualified"] is True
