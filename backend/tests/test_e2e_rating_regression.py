"""
E2E — Driver-rating regression suite (B-P0-1).

The first-rating crash was caused by rides.py:rate_driver computing a rolling
average while `driver.get("rated_rides")` returned None, dividing by zero or
crashing on None arithmetic. The fix (PR #106) guards with `total_ratings or 0`
and uses a rolling-average formula that handles the first-rating case cleanly.

This file pins that fix and the surrounding rating contract so a future
refactor can't silently reintroduce it.

Scenarios:
  1. First-ever rating (total_ratings=0 / missing)  → no crash, avg = rating
  2. Subsequent rating                               → rolling average accumulates
  3. Rating with tip                                 → tip added to driver_earnings
  4. Ride with no driver_id                          → returns success, no driver update
  5. Unauthorized rider                              → 404
  6. Completed ride missing                          → 404
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

RIDER_ID = "rider_rating_e2e"
DRIVER_ID = "driver_rating_e2e"
DRIVER_USER_ID = "driver_user_rating_e2e"
RIDE_ID = "ride_rating_e2e_001"


def _completed_ride(driver_id: str | None = DRIVER_ID, **extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": driver_id,
        "status": "completed",
        "tip_amount": 0,
        "driver_earnings": 16.0,
    }
    row.update(extra)
    return row


def _driver(total_ratings: int = 10, rating: float = 4.8, **extra) -> dict:
    row = {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "total_ratings": total_ratings,
        "rating": rating,
    }
    row.update(extra)
    return row


def _rating_req(rating: float = 5.0, tip: float = 0.0, comment: str = "") -> MagicMock:
    req = MagicMock()
    req.rating = rating
    req.tip_amount = tip
    req.comment = comment
    return req


@pytest.mark.e2e
@pytest.mark.asyncio
class TestFirstRatingNoCrash:
    """B-P0-1 regression — rating a driver who has never been rated must not crash."""

    async def test_first_rating_total_ratings_zero(self):
        """Driver with total_ratings=0: rolling average = the single new rating."""
        from backend.routes import rides as rides_mod

        driver_new = _driver(total_ratings=0, rating=0.0)
        update_mock = AsyncMock()

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_completed_ride())),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver_new)),
            patch("backend.routes.rides._deps.db_supabase.update_one", update_mock),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            result = await rides_mod.rate_driver(
                ride_id=RIDE_ID,
                rating_data=_rating_req(rating=5.0),
                current_user={"id": RIDER_ID},
            )

        assert result == {"success": True}
        update_mock.assert_awaited_once()
        _, _, update_payload = update_mock.call_args[0]
        assert update_payload["total_ratings"] == 1
        assert update_payload["rating"] == 5.0

    async def test_first_rating_total_ratings_missing(self):
        """Driver row missing total_ratings key entirely — treated as 0, no KeyError."""
        from backend.routes import rides as rides_mod

        # Simulate a driver row that pre-dates the total_ratings column
        driver_legacy = {"id": DRIVER_ID, "user_id": DRIVER_USER_ID, "rating": 4.5}
        update_mock = AsyncMock()

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_completed_ride())),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver_legacy)),
            patch("backend.routes.rides._deps.db_supabase.update_one", update_mock),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            result = await rides_mod.rate_driver(
                ride_id=RIDE_ID,
                rating_data=_rating_req(rating=4.0),
                current_user={"id": RIDER_ID},
            )

        assert result == {"success": True}
        _, _, payload = update_mock.call_args[0]
        assert payload["total_ratings"] == 1


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRollingAverageAccumulation:
    """Rolling average must compute correctly as more ratings accumulate."""

    async def test_rolling_average_with_existing_ratings(self):
        """(old_avg * old_count + new_rating) / new_count."""
        from backend.routes import rides as rides_mod

        # Driver has 10 ratings averaging 4.0 → add a 5.0 → new avg = 4.09
        driver_existing = _driver(total_ratings=10, rating=4.0)
        update_mock = AsyncMock()

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_completed_ride())),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver_existing)),
            patch("backend.routes.rides._deps.db_supabase.update_one", update_mock),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            await rides_mod.rate_driver(
                ride_id=RIDE_ID,
                rating_data=_rating_req(rating=5.0),
                current_user={"id": RIDER_ID},
            )

        _, _, payload = update_mock.call_args[0]
        expected_avg = round((4.0 * 10 + 5.0) / 11, 2)
        assert payload["rating"] == expected_avg
        assert payload["total_ratings"] == 11


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRatingWithTip:
    """Tip is added to driver_earnings and tip_amount on the ride row."""

    async def test_tip_added_to_driver_earnings(self):
        from backend.routes import rides as rides_mod

        ride = _completed_ride(tip_amount=0, driver_earnings=16.0)
        driver = _driver(total_ratings=5, rating=4.5)
        update_ride_mock = AsyncMock()

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            result = await rides_mod.rate_driver(
                ride_id=RIDE_ID,
                rating_data=_rating_req(rating=5.0, tip=3.00),
                current_user={"id": RIDER_ID},
            )

        assert result == {"success": True}
        # Second update_ride call is the tip update
        tip_call_args = [c for c in update_ride_mock.call_args_list if "tip_amount" in c[0][1]]
        assert len(tip_call_args) == 1
        tip_payload = tip_call_args[0][0][1]
        assert tip_payload["tip_amount"] == 3.00
        assert tip_payload["driver_earnings"] == 19.0  # 16.0 + 3.0


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRatingEdgeCases:
    """Edge cases that should not crash or charge incorrectly."""

    async def test_rating_ride_without_driver_returns_success(self):
        """Ride where driver_id is None (pre-accept cancel then mistaken rate call)."""
        from backend.routes import rides as rides_mod

        ride_no_driver = _completed_ride(driver_id=None)

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride_no_driver)),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
        ):
            result = await rides_mod.rate_driver(
                ride_id=RIDE_ID,
                rating_data=_rating_req(rating=4.0),
                current_user={"id": RIDER_ID},
            )

        assert result == {"success": True}

    async def test_rating_wrong_rider_returns_404(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _completed_ride()  # rider_id = RIDER_ID

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.rate_driver(
                    ride_id=RIDE_ID,
                    rating_data=_rating_req(rating=3.0),
                    current_user={"id": "wrong_rider"},
                )

        assert exc.value.status_code == 404

    async def test_rating_missing_ride_returns_404(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await rides_mod.rate_driver(
                    ride_id="nonexistent",
                    rating_data=_rating_req(rating=3.0),
                    current_user={"id": RIDER_ID},
                )

        assert exc.value.status_code == 404

    async def test_zero_tip_does_not_update_earnings(self):
        """tip_amount=0 must skip the driver_earnings update entirely."""
        from backend.routes import rides as rides_mod

        ride = _completed_ride(tip_amount=0, driver_earnings=16.0)
        driver = _driver()
        update_ride_mock = AsyncMock()

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            await rides_mod.rate_driver(
                ride_id=RIDE_ID,
                rating_data=_rating_req(rating=5.0, tip=0.0),
                current_user={"id": RIDER_ID},
            )

        tip_calls = [c for c in update_ride_mock.call_args_list if "tip_amount" in c[0][1]]
        assert len(tip_calls) == 0, "Zero tip must not trigger an earnings update"
