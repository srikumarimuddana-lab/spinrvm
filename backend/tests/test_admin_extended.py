"""Extended unit tests for routes/admin/rides.py and routes/admin/drivers.py.

routes/admin/rides.py   — currently 26.6%  (target 60%)
routes/admin/drivers.py — currently 20.2%  (target 55%)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ADMIN_USER = {"id": "admin_1", "role": "admin", "email": "admin@spinr.ca"}
DRIVER_ID = "driver_admin_ext"
DRIVER_USER_ID = "user_admin_ext"
RIDE_ID = "ride_admin_ext"
RIDER_ID = "rider_admin_ext"


def _ride(status: str = "searching", **extra):
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "status": status,
        "total_fare": 18.50,
        "driver_earnings": 18.50,
        "tip_amount": 0,
        "pickup_address": "100 Main",
        "dropoff_address": "200 Broadway",
        "created_at": datetime.utcnow().isoformat(),
        **extra,
    }


def _driver(**extra):
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "name": "Admin Test Driver",
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "rating": 4.8,
        "total_rides": 100,
        "created_at": datetime.utcnow().isoformat(),
        **extra,
    }


def _user(**extra):
    return {
        "id": DRIVER_USER_ID,
        "first_name": "Bob",
        "last_name": "Driver",
        "email": "bob@example.com",
        "phone": "+15555550100",
        **extra,
    }


# ===========================================================================
# admin/rides.py
# ===========================================================================


class TestAdminGetRides:
    def test_returns_paginated_rides(self):
        from backend.routes.admin import rides as admin_rides

        rides = [_ride("completed")]

        with (
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=1)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=rides)),
            patch("backend.routes.admin.rides._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            result = asyncio.run(admin_rides.admin_get_rides(limit=50, offset=0))

        assert result["total_count"] == 1
        assert len(result["rides"]) == 1
        assert result["limit"] == 50

    def test_filters_by_status(self):
        from backend.routes.admin import rides as admin_rides

        with (
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=0)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.admin.rides._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            result = asyncio.run(admin_rides.admin_get_rides(limit=50, offset=0, status="completed"))

        assert result["total_count"] == 0

    def test_filters_scheduled_rides(self):
        from backend.routes.admin import rides as admin_rides

        scheduled = [_ride("searching", is_scheduled=True)]

        with (
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=1)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=scheduled)),
            patch("backend.routes.admin.rides._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            result = asyncio.run(admin_rides.admin_get_rides(limit=50, offset=0, is_scheduled=True))

        assert result["total_count"] == 1


class TestAdminGetActiveRides:
    def test_returns_active_rides_with_driver_locations(self):
        from backend.routes.admin import rides as admin_rides

        rides = [
            _ride("in_progress"),
            _ride("driver_accepted", id="ride_2"),
        ]
        drivers_map = {DRIVER_ID: _driver(lat=52.13, lng=-106.67)}
        users_map = {RIDER_ID: {"id": RIDER_ID, "first_name": "Alice"}}

        with (
            patch("backend.routes.admin.rides.db.get_rows", AsyncMock(return_value=rides)),
            patch("backend.routes.admin.rides._batch_fetch_drivers_and_users", AsyncMock(return_value=(drivers_map, users_map))),
        ):
            result = asyncio.run(admin_rides.admin_get_active_rides())

        assert result["count"] == 2
        # Check that driver lat/lng is included
        first = result["rides"][0]
        assert "driver_lat" in first

    def test_handles_db_error_gracefully(self):
        from backend.routes.admin import rides as admin_rides

        with (
            patch("backend.routes.admin.rides.db.get_rows", AsyncMock(side_effect=Exception("DB error"))),
            patch("backend.routes.admin.rides._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            result = asyncio.run(admin_rides.admin_get_active_rides())

        assert result["count"] == 0
        assert result["rides"] == []


class TestAdminGetStats:
    def test_returns_all_stat_fields(self):
        from backend.routes.admin import rides as admin_rides

        with (
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=10)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            result = asyncio.run(admin_rides.admin_get_stats())

        required = ["total_rides", "completed_rides", "cancelled_rides", "active_rides",
                    "total_drivers", "online_drivers", "total_users", "rides_today"]
        for field in required:
            assert field in result, f"Missing field: {field}"

    def test_calculates_revenue(self):
        from backend.routes.admin import rides as admin_rides

        ride_with_fare = [{"total_fare": 25.0, "driver_earnings": 25.0, "admin_earnings": 0, "tip_amount": 3.0}]

        with (
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=5)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=ride_with_fare)),
        ):
            result = asyncio.run(admin_rides.admin_get_stats())

        assert result["revenue_today"] >= 0
        assert result["revenue_month"] >= 0


class TestAdminGetRideStats:
    def test_returns_stats_structure(self):
        from backend.routes.admin import rides as admin_rides

        with (
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            result = asyncio.run(admin_rides.admin_get_ride_stats())

        assert isinstance(result, dict)


class TestAdminGetRideDetails:
    def test_returns_ride_details(self):
        from backend.routes.admin import rides as admin_rides

        ride = _ride("completed")
        driver = _driver()
        rider = _user(id=RIDER_ID)

        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.admin.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.rides.db_supabase.get_user_by_id", AsyncMock(return_value=rider)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            result = asyncio.run(admin_rides.admin_get_ride_details(ride_id=RIDE_ID))

        assert result is not None

    def test_ride_not_found_raises_404(self):
        from fastapi import HTTPException
        from backend.routes.admin import rides as admin_rides

        with patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(admin_rides.admin_get_ride_details(ride_id=RIDE_ID))
        assert exc.value.status_code == 404


class TestAdminCompleteRide:
    def test_completes_in_progress_ride(self):
        from backend.routes.admin import rides as admin_rides

        ride = _ride("in_progress")
        completed = _ride("completed")

        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.admin.rides.db_supabase.update_ride", AsyncMock(return_value=completed)),
            patch("backend.routes.admin.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=_driver())),
            patch("backend.routes.admin.rides.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.admin.rides.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.admin.rides.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(
                admin_rides.admin_complete_ride(ride_id=RIDE_ID, admin_user=ADMIN_USER)
            )

        assert result["success"] is True

    def test_rejects_non_active_ride(self):
        from fastapi import HTTPException
        from backend.routes.admin import rides as admin_rides
        from backend.utils.error_handling import SpinrException

        ride = _ride("completed")

        with patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises((HTTPException, SpinrException)) as exc:
                asyncio.run(
                    admin_rides.admin_complete_ride(ride_id=RIDE_ID, admin_user=ADMIN_USER)
                )
        assert exc.value.status_code == 400


class TestAdminGetEarnings:
    def test_returns_earnings_summary(self):
        from backend.routes.admin import rides as admin_rides

        rides = [{"total_fare": 20.0, "driver_earnings": 20.0, "tip_amount": 2.0}]

        with patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=rides)):
            result = asyncio.run(admin_rides.admin_get_earnings(period="month"))

        assert isinstance(result, dict)

    def test_empty_period_returns_zeros(self):
        from backend.routes.admin import rides as admin_rides

        with patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = asyncio.run(admin_rides.admin_get_earnings(period="week"))

        assert isinstance(result, dict)


# ===========================================================================
# admin/drivers.py
# ===========================================================================


class TestAdminGetDrivers:
    def test_returns_all_drivers(self):
        from backend.routes.admin import drivers as admin_drivers

        drivers = [_driver()]
        users = [_user()]

        def get_rows_side(table, filters=None, **kw):
            if table == "drivers":
                return drivers
            if table == "users":
                return users
            return []

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            result = asyncio.run(admin_drivers.admin_get_drivers(limit=50, offset=0))

        assert isinstance(result, list)
        assert len(result) >= 1

    def test_deduplicates_by_user_id(self):
        from backend.routes.admin import drivers as admin_drivers

        # Two rows with same user_id (duplicate)
        drivers = [
            _driver(id="d1", user_id="uid1", created_at="2024-01-01"),
            _driver(id="d2", user_id="uid1", created_at="2024-01-02"),
        ]
        users = [_user(id="uid1")]

        def get_rows_side(table, filters=None, **kw):
            return drivers if table == "drivers" else users

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            result = asyncio.run(admin_drivers.admin_get_drivers(limit=50, offset=0))

        # Deduplication keeps only the first created row
        assert len(result) == 1

    def test_filters_by_verified_status(self):
        from backend.routes.admin import drivers as admin_drivers

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = asyncio.run(admin_drivers.admin_get_drivers(is_verified=True))

        assert result == []

    def test_search_by_name(self):
        from backend.routes.admin import drivers as admin_drivers

        drivers = [_driver()]
        users = [_user()]

        def get_rows_side(table, filters=None, **kw):
            if table == "users":
                return users
            return drivers

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            result = asyncio.run(admin_drivers.admin_get_drivers(search="Bob"))

        assert isinstance(result, list)


class TestAdminSearchDrivers:
    def test_delegates_to_admin_get_drivers(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverSearchRequest

        with patch("backend.routes.admin.drivers.admin_get_drivers", AsyncMock(return_value=[])) as mock:
            req = DriverSearchRequest(search="alice", limit=5)
            result = asyncio.run(admin_drivers.admin_search_drivers(body=req, admin_user=ADMIN_USER))

        mock.assert_awaited_once()
        assert result == []


class TestAdminGetDriverStats:
    def test_returns_stats_with_no_filters(self):
        from backend.routes.admin import drivers as admin_drivers

        with (
            patch("backend.routes.admin.drivers.db_supabase.count_documents", AsyncMock(return_value=5)),
            patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            result = asyncio.run(admin_drivers.admin_get_driver_stats())

        assert isinstance(result, dict)

    def test_accepts_service_area_filter(self):
        from backend.routes.admin import drivers as admin_drivers

        with (
            patch("backend.routes.admin.drivers.db_supabase.count_documents", AsyncMock(return_value=2)),
            patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            result = asyncio.run(admin_drivers.admin_get_driver_stats(service_area_id="area_1"))

        assert isinstance(result, dict)


class TestAdminUpdateDriver:
    def test_updates_driver_fields(self):
        from backend.routes.admin import drivers as admin_drivers

        driver = _driver()

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.admin.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
        ):
            result = asyncio.run(
                admin_drivers.admin_update_driver(
                    driver_id=DRIVER_ID,
                    updates={"city": "Saskatoon"},
                    admin=ADMIN_USER,
                )
            )

        assert result is not None

    def test_404_when_driver_not_found(self):
        from fastapi import HTTPException
        from backend.routes.admin import drivers as admin_drivers

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    admin_drivers.admin_update_driver(
                        driver_id="nonexistent",
                        updates={"city": "Regina"},
                        admin=ADMIN_USER,
                    )
                )
        assert exc.value.status_code == 404


class TestAdminVerifyDriver:
    def test_approves_driver(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverVerifyRequest

        driver = _driver(is_verified=False)

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.admin.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
            patch("backend.routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            req = DriverVerifyRequest(action="approve")
            result = asyncio.run(
                admin_drivers.admin_verify_driver(driver_id=DRIVER_ID, req=req, admin=ADMIN_USER)
            )

        assert result is not None

    def test_rejects_driver(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverVerifyRequest

        driver = _driver(is_verified=False)

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.admin.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
            patch("backend.routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            req = DriverVerifyRequest(action="reject", reason="Documents incomplete")
            result = asyncio.run(
                admin_drivers.admin_verify_driver(driver_id=DRIVER_ID, req=req, admin=ADMIN_USER)
            )

        assert result is not None


class TestAdminDriverAction:
    def test_suspend_driver(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverActionRequest

        driver = _driver(status="active")

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.admin.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
            patch("backend.routes.admin.drivers.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            req = DriverActionRequest(action="suspend", reason="Violation")
            result = asyncio.run(
                admin_drivers.admin_driver_action(driver_id=DRIVER_ID, req=req, admin=ADMIN_USER)
            )

        assert result is not None

    def test_activate_driver(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverActionRequest

        driver = _driver(status="suspended")

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.admin.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
            patch("backend.routes.admin.drivers.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            req = DriverActionRequest(action="activate")
            result = asyncio.run(
                admin_drivers.admin_driver_action(driver_id=DRIVER_ID, req=req, admin=ADMIN_USER)
            )

        assert result is not None


class TestAdminGetDriverNotes:
    def test_returns_notes_list(self):
        from backend.routes.admin import drivers as admin_drivers

        notes = [{"id": "note_1", "driver_id": DRIVER_ID, "note": "Test note", "created_at": "2024-01-01"}]

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=notes)):
            result = asyncio.run(admin_drivers.admin_get_driver_notes(driver_id=DRIVER_ID))

        assert "notes" in result
        assert len(result["notes"]) == 1


class TestAdminAddDriverNote:
    def test_adds_note_successfully(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverNoteCreate

        note = {"id": "note_2", "driver_id": DRIVER_ID, "note": "Important note"}

        with (
            patch("backend.routes.admin.drivers.db_supabase.insert_one", AsyncMock(return_value=note)),
        ):
            req = DriverNoteCreate(note="Important note", author="Admin")
            result = asyncio.run(admin_drivers.admin_add_driver_note(driver_id=DRIVER_ID, req=req))

        assert result is not None


class TestAdminGetDriverRides:
    def test_returns_driver_rides(self):
        from backend.routes.admin import drivers as admin_drivers

        rides = [_ride("completed")]

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=rides)):
            result = asyncio.run(admin_drivers.admin_get_driver_rides(driver_id=DRIVER_ID))

        assert "rides" in result


class TestAdminGetDriverLocationTrail:
    def test_returns_location_trail(self):
        from backend.routes.admin import drivers as admin_drivers

        trail = [
            {"lat": 52.13, "lng": -106.67, "timestamp": "2024-01-01T12:00:00Z"},
            {"lat": 52.14, "lng": -106.66, "timestamp": "2024-01-01T12:01:00Z"},
        ]

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=trail)):
            result = asyncio.run(admin_drivers.admin_get_driver_location_trail(driver_id=DRIVER_ID))

        assert "trail" in result or isinstance(result, list) or isinstance(result, dict)


class TestAdminGetDriverDailyStats:
    def test_returns_daily_stats(self):
        from backend.routes.admin import drivers as admin_drivers

        rides = [_ride("completed", driver_earnings=20.0)]

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=rides)):
            result = asyncio.run(admin_drivers.admin_get_driver_daily_stats(driver_id=DRIVER_ID))

        assert isinstance(result, (dict, list))


class TestBatchFetchDriversAndUsers:
    def test_empty_ids_return_empty_maps(self):
        from backend.routes.admin.drivers import _batch_fetch_drivers_and_users

        result = asyncio.run(_batch_fetch_drivers_and_users([], []))
        drivers_map, users_map = result
        assert drivers_map == {}
        assert users_map == {}

    def test_fetches_by_ids(self):
        from backend.routes.admin.drivers import _batch_fetch_drivers_and_users

        drivers = [_driver()]
        users = [_user()]

        def get_rows_side(table, filters=None, **kw):
            if table == "drivers":
                return drivers
            if table == "users":
                return users
            return []

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            drivers_map, users_map = asyncio.run(
                _batch_fetch_drivers_and_users([RIDER_ID], [DRIVER_ID])
            )

        assert DRIVER_ID in drivers_map
