"""
Regression tests for two backend P0 fixes.

B-P0-1: rate_driver aggregation now reads rider_rating (the DB column written
        by this endpoint) instead of driver_rating (a synthetic field injected
        by augmenting helpers; never present in raw get_rows results).
        Before the fix, rated_rides was always empty — the drivers table
        rating was silently never updated after any ride.

B-P0-2: process_payment accepts RideStatus.COMPLETED ("completed") which
        matches what complete_ride writes.  Both sides now use the enum so a
        future rename can't reintroduce the mismatch.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Stub heavy packages that aren't installed in the unit-test environment.
# Must run before any backend module is imported.
_STUBS = [
    "supabase",
    "stripe",
    "gotrue",
    "postgrest",
    "realtime",
    "firebase_admin",
    "firebase_admin.auth",
    "firebase_admin.credentials",
    "firebase_admin.messaging",
    "twilio",
    "twilio.rest",
    "slowapi",
    "slowapi.errors",
    "slowapi.util",
    "redis",
    "redis.asyncio",
    "jwt",
]
for _m in _STUBS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()

# conftest.py puts backend/ and the project root on sys.path and sets the
# required env vars (JWT_SECRET, ADMIN_PASSWORD, etc.) before this runs.


# ── helpers ───────────────────────────────────────────────────────────────────


def _ride(**extra) -> dict:
    row = {
        "id": "ride_001",
        "rider_id": "user_rider_1",
        "driver_id": "driver_001",
        "status": "completed",
        "tip_amount": 0,
        "driver_earnings": 10.00,
        "payment_status": "pending",
        "total_fare": 15.00,
    }
    row.update(extra)
    return row


def _driver(**extra) -> dict:
    row = {
        "id": "driver_001",
        "user_id": "user_driver_1",
        "rating": None,
        "total_ratings": 0,
    }
    row.update(extra)
    return row


# ── B-P0-1 ────────────────────────────────────────────────────────────────────


class TestFirstRatingUpdatesDriverAverage:
    """
    Before fix: get_rows returns raw DB rows where driver_rating is always
    None (it is a synthetic field, not a DB column).  rated_rides was empty
    → drivers table never updated.

    After fix: rated_rides reads rider_rating → first rating writes the
    average to the drivers table.
    """

    async def test_first_rating_writes_average_to_drivers_table(self):
        """Brand-new driver (no prior ratings) gets first 5-star → rating=5.0."""
        from backend.routes import rides as rides_mod

        ride = _ride()
        driver = _driver()
        ride_with_rating = dict(ride, rider_rating=5)

        update_one_calls: list = []

        async def capture_update_one(table, filters, data):
            update_one_calls.append((table, filters, data))
            return data

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock(return_value=ride_with_rating)),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(return_value=[ride_with_rating]),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.update_one",
                AsyncMock(side_effect=capture_update_one),
            ),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            rating_req = MagicMock()
            rating_req.rating = 5
            rating_req.comment = ""
            rating_req.tip_amount = 0

            result = await rides_mod.rate_driver("ride_001", rating_req, current_user={"id": "user_rider_1"})

        assert result == {"success": True}

        driver_updates = [c for c in update_one_calls if c[0] == "drivers"]
        assert len(driver_updates) == 1, "drivers table must be updated on first rating"
        _, _, data = driver_updates[0]
        assert data["rating"] == 5.0
        assert data["total_ratings"] == 1

    async def test_second_rating_computes_correct_average(self):
        """Driver has one 4-star ride; new 5-star → average 4.5."""
        from backend.routes import rides as rides_mod

        ride = _ride()
        driver = _driver(rating=4.0, total_ratings=1)
        prior_ride = dict(_ride(id="ride_000"), rider_rating=4)
        current_ride_with_rating = dict(ride, rider_rating=5)

        update_one_calls: list = []

        async def capture_update_one(table, filters, data):
            update_one_calls.append((table, filters, data))
            return data

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.rides._deps.db_supabase.update_ride",
                AsyncMock(return_value=current_ride_with_rating),
            ),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(return_value=[prior_ride, current_ride_with_rating]),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.update_one",
                AsyncMock(side_effect=capture_update_one),
            ),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            rating_req = MagicMock()
            rating_req.rating = 5
            rating_req.comment = ""
            rating_req.tip_amount = 0

            await rides_mod.rate_driver("ride_001", rating_req, current_user={"id": "user_rider_1"})

        driver_updates = [c for c in update_one_calls if c[0] == "drivers"]
        assert len(driver_updates) == 1
        _, _, data = driver_updates[0]
        assert data["rating"] == 4.5
        assert data["total_ratings"] == 2


# ── B-P0-2 ────────────────────────────────────────────────────────────────────


class TestRiderPaymentAfterDriverCompletes:
    """
    complete_ride writes status='completed'.  process_payment must accept
    that value and not raise 409.  The RideStatus enum enforces both sides
    use the same constant.
    """

    async def test_non_completed_status_raises_409(self):
        """Ride in 'in_progress' must raise 409."""
        from backend.routes import rides as rides_mod

        ride = _ride(status="in_progress")

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        ):
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))

            with pytest.raises(HTTPException) as exc_info:
                await rides_mod.process_payment("ride_001", req, current_user={"id": "user_rider_1"})

        assert exc_info.value.status_code == 409
        assert "in_progress" in exc_info.value.detail

    async def test_completed_ride_passes_status_guard(self):
        """
        Ride in 'completed' must pass the status guard.

        We stop at the atomic race-guard (db.update_one) by returning
        modified_count=0 to simulate a concurrent payment already in flight —
        the function returns early with already_paid=True.  The important
        assertion is that no 409 is raised at the status check.
        """
        from backend.routes import rides as rides_mod

        ride = _ride(status="completed", payment_status="pending")

        guard_result = MagicMock()
        guard_result.modified_count = 0  # simulate race — already claimed

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db.update_one", AsyncMock(return_value=guard_result)),
        ):
            req = rides_mod.ProcessPaymentRequest(tip_amount=Decimal("0"))

            # Must not raise a 409 — any other exception is unrelated to our fix.
            try:
                result = await rides_mod.process_payment("ride_001", req, current_user={"id": "user_rider_1"})
                # If we get here the guard short-circuited cleanly.
                assert result.get("already_paid") is True
            except HTTPException as exc:
                assert exc.status_code != 409, f"process_payment raised 409 on a completed ride: {exc.detail}"
