"""ACTION_ITEMS.md C26: reject a new `scheduled_time` that overlaps an
existing `scheduled`-status ride for the same rider, at booking time.

Before this check, `routes/rides/booking.py`'s active-ride guard only
covered `RideStatus.active_statuses()` (which excludes `scheduled`), so a
rider could book two overlapping/duplicate scheduled rides -- the conflict
surfaced only later, at dispatch time, when the second ride's
`scheduled -> searching` claim UPDATE collided with the partial unique
index in migrations/53_rides_one_active_per_rider.sql.

Harness mirrors tests/test_create_ride_scheduled_confirmation.py's
_run_scheduled_booking, but drives ``get_rows`` per-table/per-filter so the
"rider's other scheduled rides" lookup can be seeded independently of the
active-ride and unpaid-ride lookups sharing the same ``rides`` table.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

_RIDER_ID = "rider-sched-overlap-1"
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
    )
    defaults.update(kw)
    return CreateRideRequest(**defaults)


def _inserted_ride():
    return {
        "id": "ride-sched-overlap-new",
        "status": "scheduled",
        "is_scheduled": True,
        "total_fare": 10.0,
        "base_fare": 3.0,
        "distance_fare": 5.0,
        "time_fare": 2.0,
        "grand_total": 10.0,
        "planned_route_polyline": None,
    }


async def _run_booking(body_kwargs=None, existing_scheduled_rides=None):
    """Runs create_ride with `rides`-table get_rows calls disambiguated by
    filter shape:
      - {"status": {"$in": [...]}}   -> active-ride guard          -> []
      - {"status": "completed", ...} -> unpaid-ride guard          -> []
      - {"status": "scheduled"}      -> C26 overlap guard          -> seeded
    """
    from backend.routes.rides import create_ride

    inserted = _inserted_ride()
    existing_scheduled_rides = existing_scheduled_rides or []

    async def _get_rows(table, filters=None, **kwargs):
        if table == "rides":
            filters = filters or {}
            status_filter = filters.get("status")
            if isinstance(status_filter, dict) and "$in" in status_filter:
                return []  # active-ride guard: no active ride
            if status_filter == "scheduled":
                return existing_scheduled_rides
            return []  # unpaid-ride guard: none
        return []  # e.g. service_areas: none active -> geofence skipped

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
        patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})

        async def _find_one(table, *a, **kw):
            if table == "wallets":
                return {"id": "wallet-1", "balance": 1000.0}
            return None

        mock_supabase.find_one = AsyncMock(side_effect=_find_one)
        mock_supabase.get_rows = AsyncMock(side_effect=_get_rows)
        mock_supabase.get_service_area_for_point = AsyncMock(return_value=None)
        mock_supabase.insert_ride = AsyncMock(return_value=inserted)
        mock_supabase.get_ride = AsyncMock(return_value=inserted)
        mock_supabase.update_one = AsyncMock(return_value=None)
        mock_supabase.update_ride = AsyncMock(return_value=None)
        mock_manager.broadcast_to_admins = AsyncMock()
        mock_manager.send_personal_message = AsyncMock()

        return await create_ride(
            request=_starlette_request(),
            body=_body(**(body_kwargs or {})),
            current_user=_USER,
        )


class TestScheduledRideOverlapGuard:
    async def test_exact_duplicate_scheduled_time_rejected(self):
        target_time = datetime.now(timezone.utc) + timedelta(hours=3)
        existing = [{"id": "ride-existing-1", "scheduled_time": target_time.isoformat()}]

        with pytest.raises(Exception) as exc_info:
            await _run_booking(
                body_kwargs={"scheduled_time": target_time},
                existing_scheduled_rides=existing,
            )

        err = exc_info.value
        status_code = getattr(err, "status_code", None)
        assert status_code == 409
        details = getattr(err, "details", {}) or {}
        assert details.get("error_code") == "scheduled_ride_overlap"
        assert details.get("existing_ride_id") == "ride-existing-1"

    async def test_within_overlap_window_rejected(self):
        existing_time = datetime.now(timezone.utc) + timedelta(hours=3)
        # 30 minutes later -- inside the 60-minute SCHEDULE_OVERLAP_WINDOW_MINUTES.
        new_time = existing_time + timedelta(minutes=30)
        existing = [{"id": "ride-existing-2", "scheduled_time": existing_time.isoformat()}]

        with pytest.raises(Exception) as exc_info:
            await _run_booking(
                body_kwargs={"scheduled_time": new_time},
                existing_scheduled_rides=existing,
            )

        err = exc_info.value
        assert getattr(err, "status_code", None) == 409
        details = getattr(err, "details", {}) or {}
        assert details.get("error_code") == "scheduled_ride_overlap"

    async def test_well_outside_window_succeeds(self):
        existing_time = datetime.now(timezone.utc) + timedelta(hours=3)
        # 3+ hours later -- well outside the 60-minute window.
        new_time = existing_time + timedelta(hours=3, minutes=30)
        existing = [{"id": "ride-existing-3", "scheduled_time": existing_time.isoformat()}]

        result = await _run_booking(
            body_kwargs={"scheduled_time": new_time},
            existing_scheduled_rides=existing,
        )

        assert result is not None

    async def test_immediate_booking_unaffected_by_existing_scheduled_ride(self):
        """An immediate (non-scheduled) booking must not be blocked by the
        rider's existing scheduled ride(s) -- the C26 guard only runs when
        the incoming request itself carries a scheduled_time."""
        existing_time = datetime.now(timezone.utc) + timedelta(minutes=10)
        existing = [{"id": "ride-existing-4", "scheduled_time": existing_time.isoformat()}]

        result = await _run_booking(
            body_kwargs={"scheduled_time": None},
            existing_scheduled_rides=existing,
        )

        assert result is not None
