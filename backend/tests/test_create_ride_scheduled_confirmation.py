"""N15/R35,R37 (ACTION_ITEMS.md): a scheduled ride booking must push a
booking-confirmation notification to the rider at create time, not just the
existing ~10-minute-out reminder (utils/scheduled_rides.py::_send_reminder).

Harness mirrors tests/test_create_ride_post_insert_branches.py's
_run_happy_path, but captures spawn()ed coroutines instead of closing them
unrun -- the confirmation push is fired via _deps.spawn(), same as the rest
of create_ride's post-insert side effects, so it must actually be awaited to
observe the send_push_notification call.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

_RIDER_ID = "rider-sched-1"
_USER = {"id": _RIDER_ID}

_FARE_INFO = {
    "vehicle_type": {"id": "vt-1", "name": "Standard"},
    "per_km_rate": 1.5,
    "per_minute_rate": 0.25,
    "booking_fee": 2.0,
    "base_fare": 3.0,
    "minimum_fare": 5.0,
    "surge_multiplier": 1.0,
}


def _starlette_request(method="POST", path="/rides"):
    from starlette.requests import Request as SR

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "root_path": "",
        "client": ("127.0.0.1", 9999),
    }
    return SR(scope)


def _body(**kw):
    from backend.schemas import CreateRideRequest

    defaults = dict(
        pickup_address="100 Main St",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_address="200 Broadway Ave",
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        vehicle_type_id="vt-1",
        payment_method="wallet",
        scheduled_time=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    defaults.update(kw)
    return CreateRideRequest(**defaults)


def _inserted_ride():
    return {
        "id": "ride-sched-1",
        "status": "scheduled",
        "is_scheduled": True,
        "total_fare": 10.0,
        "base_fare": 3.0,
        "distance_fare": 5.0,
        "time_fare": 2.0,
        "grand_total": 10.0,
        "planned_route_polyline": None,
    }


async def _run_scheduled_booking(body_kwargs=None):
    """Runs create_ride through the deferred-scheduling path with a real
    (non-closing) spawn capture, then drains every spawned coroutine so
    the booking-confirmation push actually executes."""
    from backend.routes.rides import create_ride

    inserted = _inserted_ride()
    spawned: list = []

    def _capture_spawn(coro):
        spawned.append(coro)
        return None

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
        patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
        patch(
            "backend.routes.rides._deps.calculate_all_fees",
            AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
        ),
        patch("backend.routes.rides._deps.spawn", side_effect=_capture_spawn),
        patch("backend.routes.rides._deps.manager") as mock_manager,
        patch("backend.routes.rides._deps.send_push_notification", AsyncMock()) as push_mock,
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})

        async def _find_one(table, *a, **kw):
            if table == "wallets":
                return {"id": "wallet-1", "balance": 1000.0}
            return None

        mock_supabase.find_one = AsyncMock(side_effect=_find_one)
        mock_supabase.get_rows = AsyncMock(return_value=[])
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.insert_ride = AsyncMock(return_value=inserted)
        mock_supabase.get_ride = AsyncMock(return_value=inserted)
        mock_supabase.update_one = AsyncMock(return_value=None)
        mock_supabase.update_ride = AsyncMock(return_value=None)
        mock_manager.broadcast_to_admins = AsyncMock()
        mock_manager.send_personal_message = AsyncMock()

        result = await create_ride(
            request=_starlette_request(),
            body=_body(**(body_kwargs or {})),
            current_user=_USER,
        )

        # Drain every spawned background task (nav-prep/dispatch pipeline,
        # audit log, and the new booking-confirmation push).
        for coro in spawned:
            try:
                await coro
            except Exception:
                pass

    return result, push_mock


class TestScheduledRideBookingConfirmation:
    async def test_deferred_booking_sends_confirmation_push(self):
        result, push_mock = await _run_scheduled_booking()

        assert result is not None
        push_mock.assert_awaited_once()
        call = push_mock.await_args
        assert call.args[0] == _RIDER_ID
        assert call.args[1] == "Scheduled ride confirmed"
        assert "200 Broadway Ave" in call.args[2]
        assert call.kwargs.get("data", {}).get("type") == "scheduled_ride_confirmed"
        # ride.id is server-generated (uuid4), not the mocked insert_ride
        # return value -- just assert it's a non-empty string.
        assert call.kwargs.get("data", {}).get("ride_id")

    async def test_immediate_booking_does_not_send_scheduled_confirmation(self):
        """Sanity check: an immediate (non-scheduled) ride must not get the
        scheduled-ride confirmation copy -- it already gets normal dispatch
        pushes/WS events via the existing offer flow."""
        from backend.routes.rides import create_ride

        inserted = {
            "id": "ride-immediate-1",
            "status": "searching",
            "total_fare": 10.0,
            "base_fare": 3.0,
            "distance_fare": 5.0,
            "time_fare": 2.0,
            "grand_total": 10.0,
            "planned_route_polyline": None,
        }

        with (
            patch("backend.routes.rides._deps.validate_ride_location"),
            patch("backend.routes.rides._deps.db") as mock_db,
            patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
            patch("backend.routes.rides._deps._fares_for_location_impl", AsyncMock(return_value=[_FARE_INFO])),
            patch("backend.routes.rides._deps.calculate_airport_fee", AsyncMock(return_value={"airport_fee": 0.0})),
            patch(
                "backend.routes.rides._deps.calculate_all_fees",
                AsyncMock(return_value={"fees_total": 0, "tax_amount": 0, "fees": [], "tax_breakdown": {}}),
            ),
            patch("backend.routes.rides.matching.ride_search_timeout", AsyncMock()),
            patch("backend.routes.rides._deps.spawn", side_effect=lambda coro: coro.close()),
            patch("backend.routes.rides._deps.manager") as mock_manager,
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()) as push_mock,
        ):
            mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})

            async def _find_one(table, *a, **kw):
                if table == "wallets":
                    return {"id": "wallet-1", "balance": 1000.0}
                return None

            mock_supabase.find_one = AsyncMock(side_effect=_find_one)
            mock_supabase.get_rows = AsyncMock(return_value=[])
            mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
            mock_supabase.insert_ride = AsyncMock(return_value=inserted)
            mock_supabase.get_ride = AsyncMock(return_value={**inserted, "status": "searching"})
            mock_supabase.update_one = AsyncMock(return_value=None)
            mock_manager.broadcast_to_admins = AsyncMock()
            mock_manager.send_personal_message = AsyncMock()

            await asyncio.wait_for(
                create_ride(
                    request=_starlette_request(),
                    body=_body(scheduled_time=None),
                    current_user=_USER,
                ),
                timeout=5,
            )

        push_mock.assert_not_awaited()
