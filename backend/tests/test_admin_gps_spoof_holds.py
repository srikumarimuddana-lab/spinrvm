"""Admin visibility/action for GPS-spoof-held rides (item #4 of the
2026-09-02 GPS-to-billing audit): GET /rides/held-for-review,
POST /rides/{id}/held-for-review/release, POST .../waive.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

ADMIN_USER = {"id": "admin_1", "role": "admin", "email": "admin@spinr.ca"}
RIDE_ID = "ride_held_1"
RIDER_ID = "rider_held_1"


def _held_ride(**extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": None,
        "status": "completed",
        "payment_status": "held_for_review",
        "total_fare": 22.0,
        "tip_amount": 0,
        "pickup_address": "1 Main",
        "dropoff_address": "2 Broadway",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ride_completed_at": datetime.now(timezone.utc).isoformat(),
    }
    row.update(extra)
    return row


class TestAdminGetHeldForReviewRides:
    def test_lists_held_rides_with_their_gps_verdict(self):
        from backend.routes.admin import rides as admin_rides

        ride = _held_ride()
        route_row = {
            "ride_id": RIDE_ID,
            "route_quality": {"gps_route_validation": {"verdict": "likely_spoofed", "deviation_pct": 62.5}},
        }

        async def get_rows(table, filters, **kwargs):
            if table == "rides":
                return [ride]
            if table == "ride_routes":
                return [route_row]
            return []

        with (
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch(
                "backend.routes.admin.rides._batch_fetch_drivers_and_users",
                AsyncMock(return_value=({}, {})),
            ),
        ):
            result = asyncio.run(admin_rides.admin_get_held_for_review_rides())

        assert result["count"] == 1
        assert result["rides"][0]["id"] == RIDE_ID
        assert result["rides"][0]["gps_route_validation"]["deviation_pct"] == 62.5


class TestAdminReleaseHeldRide:
    def test_release_clears_verdict_and_reopens_payment(self):
        from backend.routes.admin import rides as admin_rides

        ride = _held_ride()
        route_row = {"route_quality": {"gps_route_validation": {"verdict": "likely_spoofed", "deviation_pct": 55.0}}}
        update_one = AsyncMock(return_value={"id": RIDE_ID})

        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=[route_row])),
            patch("backend.routes.admin.rides.db_supabase.update_one", update_one),
            patch("backend.routes.admin.rides.log_admin_action", AsyncMock()) as mock_audit,
        ):
            result = asyncio.run(admin_rides.admin_release_held_ride(ride_id=RIDE_ID, admin_user=ADMIN_USER))

        assert result["success"] is True
        assert result["payment_status"] == "pending"
        # First call clears the verdict on ride_routes; second reopens payment.
        route_call = update_one.await_args_list[0]
        assert route_call.args[0] == "ride_routes"
        assert route_call.args[2]["route_quality"]["gps_route_validation"]["verdict"] == "admin_cleared"
        rides_call = update_one.await_args_list[1]
        assert rides_call.args[0] == "rides"
        assert rides_call.args[2]["payment_status"] == "pending"
        mock_audit.assert_awaited_once()

    def test_rejects_ride_not_currently_held(self):
        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides

        ride = _held_ride(payment_status="paid")
        with patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(admin_rides.admin_release_held_ride(ride_id=RIDE_ID, admin_user=ADMIN_USER))
        assert exc.value.status_code == 400

    def test_missing_ride_returns_404(self):
        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides

        with patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(admin_rides.admin_release_held_ride(ride_id=RIDE_ID, admin_user=ADMIN_USER))
        assert exc.value.status_code == 404


class TestAdminWaiveHeldRide:
    def test_waive_sets_waived_admin_status(self):
        from backend.routes.admin import rides as admin_rides

        ride = _held_ride()
        update_one = AsyncMock(return_value={"id": RIDE_ID})

        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.admin.rides.db_supabase.update_one", update_one),
            patch("backend.routes.admin.rides.log_admin_action", AsyncMock()) as mock_audit,
        ):
            result = asyncio.run(admin_rides.admin_waive_held_ride(ride_id=RIDE_ID, admin_user=ADMIN_USER))

        assert result["success"] is True
        assert result["payment_status"] == "waived_admin"
        update_one.assert_awaited_once()
        assert update_one.await_args.args[0] == "rides"
        assert update_one.await_args.args[2]["payment_status"] == "waived_admin"
        mock_audit.assert_awaited_once()

    def test_rejects_ride_not_currently_held(self):
        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides

        ride = _held_ride(payment_status="pending")
        with patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(admin_rides.admin_waive_held_ride(ride_id=RIDE_ID, admin_user=ADMIN_USER))
        assert exc.value.status_code == 400
