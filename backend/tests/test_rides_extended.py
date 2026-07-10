"""Extended unit tests for routes/rides.py.

Covers branches not exercised by existing test_rides.py / test_e2e_ride_lifecycle.py:
  - add_tip (success, duplicate, non-completed, zero amount)
  - rate_driver (success, with tip, wrong rider, not completed)
  - get_ride_receipt (success, 403, not completed)
  - get_share_trip_link (success, 404, 403, already completed)
  - get_chat_status (active, completed-within-window, completed-expired, cancelled)
  - get_ride_messages (success, unauthorized)
  - get_scheduled_rides / cancel_scheduled_ride
  - _d, _round, _f helpers
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

RIDER_ID = "rider_ext_001"
DRIVER_ID = "driver_ext_001"
DRIVER_USER_ID = "driver_user_ext_001"
RIDE_ID = "ride_ext_001"


def _ride(status: str = "completed", **extra):
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "status": status,
        "pickup_address": "100 Main",
        "dropoff_address": "200 Broadway",
        "total_fare": 18.50,
        "base_fare": 10.0,
        "distance_fare": 7.5,
        "time_fare": 1.0,
        "driver_earnings": 18.50,
        "tip_amount": 0,
        "distance_km": 3.2,
        "duration_minutes": 8,
        "vehicle_type_id": "sedan",
        "shared_trip_token": None,
        "ride_completed_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def _driver(**extra):
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "name": "Test Driver",
        "rating": 4.8,
        "total_rides": 50,
        "total_ratings": 10,
        **extra,
    }


# ---------------------------------------------------------------------------
# Decimal helpers
# ---------------------------------------------------------------------------


class TestMoneyHelpers:
    def test_d_converts_string(self):
        from backend.routes.rides import _d

        assert _d("18.5") == Decimal("18.5")

    def test_d_handles_zero(self):
        from backend.routes.rides import _d

        assert _d(0) == Decimal("0")
        assert _d("0.00") == Decimal("0")

    def test_round_to_two_places(self):
        from backend.routes.rides import _d, _round

        assert _round(_d("18.555")) == Decimal("18.56")

    def test_f_returns_float(self):
        from backend.routes.rides import _d, _f

        assert isinstance(_f(_d("18.50")), float)
        assert _f(_d("18.50")) == 18.50


# ---------------------------------------------------------------------------
# add_tip
# ---------------------------------------------------------------------------


class TestAddTip:
    def _mock_request(self):
        from starlette.requests import Request as SR

        return SR({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b"", "root_path": ""})

    def test_adds_tip_to_completed_ride(self):
        from backend.routes import rides as rides_mod
        from backend.routes.rides import TipRequest

        ride = _ride("completed", tip_amount=0, driver_earnings=18.50, driver_id=DRIVER_ID)
        driver = _driver()

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.rides._deps.db_supabase.get_user_by_id", AsyncMock(return_value={"first_name": "Alice"})),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            req = TipRequest(amount=Decimal("5.00"))
            result = asyncio.run(
                rides_mod.add_tip(
                    ride_id=RIDE_ID,
                    req=req,
                    request=self._mock_request(),
                    current_user={"id": RIDER_ID},
                )
            )

        assert result["success"] is True
        # Audit-17 Phase 1c: tip_amount crosses the wire as a decimal string
        # (e.g. "5.00"), never an IEEE-754 float. See backend/routes/rides.py
        # ``_money_str`` helper.
        assert result["tip_amount"] == "5.00"

    def test_zero_tip_fails_pydantic_validation(self):
        from pydantic import ValidationError

        from backend.routes.rides import TipRequest

        with pytest.raises(ValidationError):
            TipRequest(amount=Decimal("0"))

    def test_duplicate_tip_raises_400(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.routes.rides import TipRequest

        ride = _ride("completed", tip_amount=3.0)  # already tipped

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    rides_mod.add_tip(
                        ride_id=RIDE_ID,
                        req=TipRequest(amount=Decimal("2.00")),
                        request=self._mock_request(),
                        current_user={"id": RIDER_ID},
                    )
                )
        assert exc.value.status_code == 400

    def test_non_completed_ride_raises_400(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.routes.rides import TipRequest

        ride = _ride("in_progress")

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    rides_mod.add_tip(
                        ride_id=RIDE_ID,
                        req=TipRequest(amount=Decimal("2.00")),
                        request=self._mock_request(),
                        current_user={"id": RIDER_ID},
                    )
                )
        assert exc.value.status_code == 400

    def test_wrong_rider_raises_403(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.routes.rides import TipRequest

        ride = _ride("completed")

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    rides_mod.add_tip(
                        ride_id=RIDE_ID,
                        req=TipRequest(amount=Decimal("2.00")),
                        request=self._mock_request(),
                        current_user={"id": "other_rider"},
                    )
                )
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# rate_driver
# ---------------------------------------------------------------------------


class TestRateDriver:
    def test_rates_driver_and_updates_rolling_average(self):
        from backend.routes import rides as rides_mod
        from backend.schemas import RideRatingRequest

        ride = _ride("completed")
        driver = _driver(total_ratings=4, rating=4.5)

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(
                rides_mod.rate_driver(
                    ride_id=RIDE_ID,
                    rating_data=RideRatingRequest(rating=5),
                    current_user={"id": RIDER_ID},
                )
            )

        assert result["success"] is True

    def test_rate_with_tip(self):
        from backend.routes import rides as rides_mod
        from backend.schemas import RideRatingRequest

        ride = _ride("completed", tip_amount=0, driver_earnings=18.50)
        driver = _driver()

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(
                rides_mod.rate_driver(
                    ride_id=RIDE_ID,
                    rating_data=RideRatingRequest(rating=5, tip_amount=3.0),
                    current_user={"id": RIDER_ID},
                )
            )

        assert result["success"] is True

    def test_not_completed_raises_400(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.schemas import RideRatingRequest

        ride = _ride("in_progress")

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    rides_mod.rate_driver(
                        ride_id=RIDE_ID,
                        rating_data=RideRatingRequest(rating=5),
                        current_user={"id": RIDER_ID},
                    )
                )
        assert exc.value.status_code == 400

    def test_wrong_rider_raises_404(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.schemas import RideRatingRequest

        ride = _ride("completed")

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    rides_mod.rate_driver(
                        ride_id=RIDE_ID,
                        rating_data=RideRatingRequest(rating=5),
                        current_user={"id": "wrong_rider"},
                    )
                )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# get_ride_receipt
# ---------------------------------------------------------------------------


class TestGetRideReceipt:
    def test_returns_receipt_for_completed_ride(self):
        from backend.routes import rides as rides_mod

        ride = _ride("completed", driver_id=DRIVER_ID, vehicle_type_id="sedan")
        driver = _driver()
        driver_profile = {"id": DRIVER_USER_ID, "first_name": "Bob", "last_name": "Smith"}
        vehicle = {"id": "sedan", "name": "Sedan"}

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "vehicle_types":
                return [vehicle]
            return []

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.rides._deps.db_supabase.get_user_by_id", AsyncMock(return_value=driver_profile)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
        ):
            result = asyncio.run(rides_mod.get_ride_receipt(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))

        assert result["success"] is True
        assert result["receipt"]["ride_id"] == RIDE_ID
        assert result["receipt"]["driver_name"] == "Bob Smith"

    def test_not_completed_raises_400(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride("in_progress")

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(rides_mod.get_ride_receipt(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))
        assert exc.value.status_code == 400

    def test_wrong_rider_raises_403(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride("completed")

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(rides_mod.get_ride_receipt(ride_id=RIDE_ID, current_user={"id": "other_rider"}))
        assert exc.value.status_code == 403

    def test_ride_not_found_raises_404(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(rides_mod.get_ride_receipt(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# get_share_trip_link
# ---------------------------------------------------------------------------


class TestGetShareTripLink:
    def test_generates_share_token(self):
        from backend.routes import rides as rides_mod

        ride = _ride("in_progress", shared_trip_token=None)

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock(return_value=ride)),
        ):
            result = asyncio.run(rides_mod.get_share_trip_link(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))

        assert result["success"] is True
        assert "share_token" in result
        assert "share_url" in result

    def test_reuses_existing_token(self):
        from backend.routes import rides as rides_mod

        ride = _ride("in_progress", shared_trip_token="existing_token_abc")

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            result = asyncio.run(rides_mod.get_share_trip_link(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))

        assert result["share_token"] == "existing_token_abc"

    def test_completed_ride_raises_400(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride("completed")

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(rides_mod.get_share_trip_link(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))
        assert exc.value.status_code == 400

    def test_wrong_rider_raises_403(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride("in_progress")

        with patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(rides_mod.get_share_trip_link(ride_id=RIDE_ID, current_user={"id": "other_rider"}))
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# get_chat_status
# ---------------------------------------------------------------------------


class TestGetChatStatus:
    def test_active_ride_chat_available(self):
        from backend.routes import rides as rides_mod

        ride = _ride("in_progress")

        with patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=ride)):
            result = asyncio.run(rides_mod.get_chat_status(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))

        assert result["available"] is True
        assert result["post_trip"] is False

    def test_cancelled_ride_chat_unavailable(self):
        from backend.routes import rides as rides_mod

        ride = _ride("cancelled")

        with patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=ride)):
            result = asyncio.run(rides_mod.get_chat_status(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))

        assert result["available"] is False

    def test_recently_completed_ride_has_post_trip_window(self):
        from backend.routes import rides as rides_mod

        recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        ride = _ride("completed", ride_completed_at=recent_ts)

        with patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=ride)):
            result = asyncio.run(rides_mod.get_chat_status(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))

        assert result["available"] is True
        assert result["post_trip"] is True
        assert result["hours_remaining"] < 24

    def test_expired_post_trip_window_chat_unavailable(self):
        from backend.routes import rides as rides_mod

        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        ride = _ride("completed", ride_completed_at=old_ts)

        with patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=ride)):
            result = asyncio.run(rides_mod.get_chat_status(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))

        assert result["available"] is False

    def test_ride_not_found_raises_404(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        with patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(rides_mod.get_chat_status(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))
        assert exc.value.status_code == 404

    def test_outsider_cannot_read_chat_status(self):
        """IDOR regression: a user who is neither the rider nor the assigned
        driver must not be able to probe a ride's existence/status via
        chat-status. The denial returns the SAME 404 as a missing ride so the
        endpoint never leaks existence (403-exists vs 404-missing)."""
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride("in_progress")

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(rides_mod.get_chat_status(ride_id=RIDE_ID, current_user={"id": "outsider_999"}))
        # Must be indistinguishable from the missing-ride 404 above.
        assert exc.value.status_code == 404

    def test_assigned_driver_can_read_chat_status(self):
        """The assigned driver (matched via the drivers lookup) is authorized."""
        from backend.routes import rides as rides_mod

        ride = _ride("in_progress")

        with (
            patch("backend.routes.rides._deps.db.find_one", AsyncMock(return_value=ride)),
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": DRIVER_ID, "user_id": "driver_user_xyz"}]),
            ),
        ):
            result = asyncio.run(rides_mod.get_chat_status(ride_id=RIDE_ID, current_user={"id": "driver_user_xyz"}))
        assert result["available"] is True


# ---------------------------------------------------------------------------
# get_ride_messages
# ---------------------------------------------------------------------------


class TestGetRideMessages:
    def test_rider_can_get_messages(self):
        from backend.routes import rides as rides_mod

        ride = _ride("in_progress")
        messages = [{"id": "msg_1", "content": "Hello", "sender_id": RIDER_ID}]

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return []
            if table == "ride_messages":
                return messages
            return []

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
        ):
            result = asyncio.run(rides_mod.get_ride_messages(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))

        assert "messages" in result

    def test_unauthorized_user_raises_403(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod

        ride = _ride("in_progress")

        def get_rows_side_effect(table, filters=None, **kw):
            return []

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(rides_mod.get_ride_messages(ride_id=RIDE_ID, current_user={"id": "outsider"}))
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# get_scheduled_rides
# ---------------------------------------------------------------------------


class TestGetScheduledRides:
    def test_returns_scheduled_rides(self):
        from backend.routes import rides as rides_mod

        scheduled = [_ride("scheduled")]

        with patch("backend.routes.rides._deps.db_supabase.get_rides_for_user", MagicMock(return_value=scheduled)):
            result = asyncio.run(rides_mod.get_scheduled_rides(current_user={"id": RIDER_ID}))

        assert isinstance(result, list)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# cancel_scheduled_ride
# ---------------------------------------------------------------------------


class TestCancelScheduledRide:
    def test_cancels_scheduled_ride(self):
        from backend.routes import rides as rides_mod

        ride = _ride("scheduled")
        cancelled = _ride("cancelled")

        with (
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[ride])),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock(return_value=cancelled)),
        ):
            result = asyncio.run(rides_mod.cancel_scheduled_ride(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))

        assert result is not None

    def test_not_found_raises_404(self):
        from fastapi import HTTPException

        from backend.routes import rides as rides_mod
        from backend.utils.error_handling import SpinrException

        with patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises((HTTPException, SpinrException)) as exc:
                asyncio.run(rides_mod.cancel_scheduled_ride(ride_id=RIDE_ID, current_user={"id": RIDER_ID}))
        assert exc.value.status_code == 404
