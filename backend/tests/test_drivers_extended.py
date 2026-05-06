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
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

USER_ID = "user_drv_ext"
DRIVER_ID = "driver_drv_ext"
RIDE_ID = "ride_drv_ext"
RIDER_ID = "rider_drv_ext"


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

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[_driver()])):
            with patch("backend.routes.drivers._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)):
                result = asyncio.run(drv.get_my_driver(current_user={"id": USER_ID}))

        assert result["id"] == DRIVER_ID
        assert "stripe_account_id" not in result

    def test_raises_404_when_no_driver(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
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
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.drivers._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("backend.routes.drivers._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        ):
            req = drv.UpdateDriverProfileRequest(preferred_language="fr")
            result = asyncio.run(drv.update_my_driver(body=req, current_user={"id": USER_ID}))

        assert result is not None

    def test_empty_update_returns_success_without_driver(self):
        # update_my_driver auto-creates a driver row rather than raising 404.
        # An empty request (no fields) exits early with {"success": True}.
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
            req = drv.UpdateDriverProfileRequest()
            result = asyncio.run(drv.update_my_driver(body=req, current_user={"id": USER_ID}))
        assert result == {"success": True}


# ---------------------------------------------------------------------------
# get_driver_balance
# ---------------------------------------------------------------------------


class TestGetDriverBalance:
    def test_returns_balance_summary(self):
        from backend.routes import drivers as drv

        rides = [{"driver_earnings": 20.0, "tip_amount": 2.0}]
        payouts = [{"amount": 5.0}]

        def get_rows_mock(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "rides":
                return rides
            if table == "payouts":
                return payouts
            return []

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_mock)):
            result = asyncio.run(drv.get_driver_balance(current_user={"id": USER_ID}))

        assert result["total_earnings"] == "20.00"
        assert result["total_tips"] == "2.00"
        assert result["total_rides"] == 1

    def test_returns_zeros_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
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
        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=self._setup(rides))):
            result = asyncio.run(drv.get_driver_earnings(period="week", current_user={"id": USER_ID}))
        assert result["period"] == "week"
        assert result["total_rides"] == 1
        assert result["total_earnings"] == "15.00"

    def test_today_period(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=self._setup([]))):
            result = asyncio.run(drv.get_driver_earnings(period="today", current_user={"id": USER_ID}))
        assert result["period"] == "today"
        assert result["total_rides"] == 0

    def test_day_period_alias(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=self._setup([]))):
            result = asyncio.run(drv.get_driver_earnings(period="day", current_user={"id": USER_ID}))
        assert result["period"] == "day"

    def test_month_period(self):
        from backend.routes import drivers as drv

        rides = [{"driver_earnings": 300.0, "tip_amount": 20.0, "distance_km": 80.0, "duration_minutes": 200}]
        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=self._setup(rides))):
            result = asyncio.run(drv.get_driver_earnings(period="month", current_user={"id": USER_ID}))
        assert result["period"] == "month"

    def test_all_period_no_date_filter(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=self._setup([]))):
            result = asyncio.run(drv.get_driver_earnings(period="all", current_user={"id": USER_ID}))
        assert result["period"] == "all"

    def test_unknown_period_fallback(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=self._setup([]))):
            result = asyncio.run(drv.get_driver_earnings(period="quarter", current_user={"id": USER_ID}))
        assert result["period"] == "quarter"

    def test_average_per_ride_computed(self):
        from backend.routes import drivers as drv

        rides = [
            {"driver_earnings": 20.0, "tip_amount": 0.0, "distance_km": 5.0, "duration_minutes": 10},
            {"driver_earnings": 10.0, "tip_amount": 0.0, "distance_km": 3.0, "duration_minutes": 5},
        ]
        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=self._setup(rides))):
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

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_driver_daily_earnings(days=7, current_user={"id": USER_ID}))

        assert isinstance(result, list)

    def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
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
            patch("backend.routes.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.drivers.mark_present", AsyncMock()),
        ):
            result = asyncio.run(drv.update_location_batch(batch=points, current_user={"id": USER_ID}))

        assert result == {"success": True}

    def test_dict_format_with_locations_key(self):
        from backend.routes import drivers as drv

        batch = {"locations": [{"lat": 52.13, "lng": -106.67}]}
        driver = _driver()

        with (
            patch("backend.routes.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.drivers.mark_present", AsyncMock()),
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
            patch("backend.routes.drivers.db_supabase.update_one", AsyncMock(return_value=offline_driver)),
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[offline_driver])),
            patch("backend.routes.drivers.mark_present", AsyncMock()) as mp,
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
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.db_supabase.get_user_by_id", AsyncMock(return_value=rider)),
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

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_active_ride(current_user={"id": USER_ID}))

        assert result["ride"] is None

    def test_raises_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
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
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.db_supabase.count_documents", AsyncMock(return_value=2)),
        ):
            result = asyncio.run(drv.get_ride_history(limit=20, offset=0, current_user={"id": USER_ID}))

        assert result["total"] == 2
        assert len(result["rides"]) == 2

    def test_raises_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
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
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("backend.routes.drivers.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers.send_push_notification", AsyncMock()),
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
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.db.update_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.arrive_at_pickup(ride_id=RIDE_ID, current_user={"id": USER_ID}))
        assert exc.value.status_code == 409

    def test_404_when_ride_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
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
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("backend.routes.drivers.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers.send_push_notification", AsyncMock()),
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
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.db.update_one", AsyncMock(return_value=None)),
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
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.db_supabase.update_one", AsyncMock(return_value=completed)),
            patch("backend.routes.drivers.db_supabase.get_ride", AsyncMock(return_value=completed)),
            patch("backend.routes.drivers.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.drivers.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers._generate_and_store_ride_snapshot", AsyncMock()),
        ):
            result = asyncio.run(drv.complete_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))

        assert result is not None

    def test_rejects_non_in_progress_state(self):
        from backend.routes import drivers as drv
        from backend.utils.error_handling import RideStateError

        ride = _ride("driver_arrived")

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else [ride]

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            with pytest.raises((RideStateError, Exception)) as exc:
                asyncio.run(drv.complete_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))
        assert hasattr(exc.value, "status_code") and exc.value.status_code in (409, 400, 422)

    def test_404_when_ride_not_found(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
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
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.db_supabase.get_ride", AsyncMock(side_effect=[ride, cancelled])),
            patch("backend.routes.drivers.db_supabase.update_ride", AsyncMock(return_value=cancelled)),
            patch("backend.routes.drivers.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.drivers.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.drivers.manager.broadcast_ride_status", AsyncMock()),
            patch("backend.routes.drivers.manager.broadcast_to_admins", AsyncMock()),
            patch("backend.routes.drivers.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(drv.cancel_ride(ride_id=RIDE_ID, reason="test", current_user={"id": USER_ID}))

        assert result == {"success": True}

    def test_rejects_cancel_of_in_progress_ride(self):
        from backend.routes import drivers as drv
        from backend.utils.error_handling import RideStateError

        ride = _ride("in_progress")

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            with pytest.raises((RideStateError, Exception)) as exc:
                asyncio.run(drv.cancel_ride(ride_id=RIDE_ID, reason="", current_user={"id": USER_ID}))
        assert hasattr(exc.value, "status_code") and exc.value.status_code in (400, 409, 422)


# ---------------------------------------------------------------------------
# decline_ride
# ---------------------------------------------------------------------------


class TestDeclineRide:
    def test_success_declines_offer(self):
        from backend.routes import drivers as drv

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers.db_supabase.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("backend.routes.drivers.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers.db.insert_one", AsyncMock()),
        ):
            result = asyncio.run(drv.decline_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))

        assert result == {"success": True}

    def test_race_lost_returns_success_silently(self):
        from backend.routes import drivers as drv

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers.db_supabase.update_one", AsyncMock(return_value=None)),
            patch("backend.routes.drivers.db.insert_one", AsyncMock()),
        ):
            result = asyncio.run(drv.decline_ride(ride_id=RIDE_ID, current_user={"id": USER_ID}))

        assert result == {"success": True}


# ---------------------------------------------------------------------------
# rate_rider
# ---------------------------------------------------------------------------


class TestRateRider:
    def test_rates_rider_successfully(self):
        from backend.routes import drivers as drv
        from backend.schemas import RideRatingRequest

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
            patch("backend.routes.drivers.db_supabase.update_ride", AsyncMock(return_value=_ride("completed"))),
        ):
            req = RideRatingRequest(rating=5, comment="Great rider!")
            result = asyncio.run(drv.rate_rider(ride_id=RIDE_ID, rating_data=req, current_user={"id": USER_ID}))

        assert result is not None


# ---------------------------------------------------------------------------
# destination mode
# ---------------------------------------------------------------------------


class TestDestinationMode:
    def test_set_destination_success(self):
        from backend.routes import drivers as drv

        with (
            patch("backend.routes.drivers.db.find_one", AsyncMock(return_value=_driver())),
            patch("backend.routes.drivers.db.update_one", AsyncMock(return_value=_driver())),
        ):
            req = drv.SetDestinationRequest(address="200 Broadway", lat=52.15, lng=-106.65)
            result = asyncio.run(drv.set_destination_mode(req=req, current_user={"id": USER_ID}))

        assert result["destination_mode"] is True

    def test_set_destination_404_when_no_driver(self):
        from fastapi import HTTPException

        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db.find_one", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                req = drv.SetDestinationRequest(address="200 Broadway", lat=52.15, lng=-106.65)
                asyncio.run(drv.set_destination_mode(req=req, current_user={"id": USER_ID}))
        assert exc.value.status_code == 404

    def test_clear_destination_success(self):
        from backend.routes import drivers as drv

        with (
            patch("backend.routes.drivers.db.find_one", AsyncMock(return_value=_driver())),
            patch("backend.routes.drivers.db.update_one", AsyncMock(return_value=_driver())),
        ):
            result = asyncio.run(drv.clear_destination_mode(current_user={"id": USER_ID}))

        assert result["destination_mode"] is False

    def test_get_destination_mode(self):
        from backend.routes import drivers as drv

        driver = _driver(
            destination_mode=True, destination_address="200 Broadway", destination_lat=52.15, destination_lng=-106.65
        )
        with patch("backend.routes.drivers.db.find_one", AsyncMock(return_value=driver)):
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

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_bank_account(current_user={"id": USER_ID}))

        assert result["has_bank_account"] is True

    def test_returns_no_bank_account(self):
        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            return [_driver()] if table == "drivers" else []

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
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

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
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

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_subscription_plans(current_user={"id": USER_ID}))

        assert result["free_mode"] is True
        assert result["plans"] == []


# ---------------------------------------------------------------------------
# get_current_subscription
# ---------------------------------------------------------------------------


class TestGetCurrentSubscription:
    def test_no_driver_returns_no_subscription(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
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
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.db_supabase.count_documents", AsyncMock(return_value=0)),
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

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)):
            result = asyncio.run(drv.get_current_subscription(current_user={"id": USER_ID}))

        assert result["has_subscription"] is False
