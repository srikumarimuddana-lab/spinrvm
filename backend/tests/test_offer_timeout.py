"""
Tests for _offer_timeout_handler — the backend-enforced offer TTL.

Also covers the related dispatch hardening:
  • null-coord abort in match_driver_to_ride
  • countdown_seconds in the new_ride_assignment WS payload
  • ride_offer_expired event sent to the driver on expiry
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestOfferTimeoutHandler:
    """Tests for routes/rides._offer_timeout_handler."""

    @pytest.fixture
    def ride_still_assigned(self):
        return {
            "id": "ride_1",
            "rider_id": "user_rider_1",
            "driver_id": "driver_1",
            "status": "driver_assigned",
        }

    @pytest.mark.asyncio
    async def test_expires_and_resets(self, ride_still_assigned):
        """Ride still `driver_assigned` after timeout → release driver, reset to searching, re-dispatch.

        Production code uses the flat Supabase API:
            db.find_one("rides", {...})
            db.update_one("drivers", {...}, {...})
            db.update_one("rides", {...}, {...})
        """
        update_calls = []

        async def _capture_update(table, filt, patch_doc):
            update_calls.append((table, filt, patch_doc))

        with (
            patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
            patch("backend.routes.rides._deps.db") as mock_db,
            patch("backend.routes.rides._deps.manager") as mock_manager,  # noqa: F841
            patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock) as mock_redispatch,
        ):
            # Flat API: db.find_one("rides", {...})
            mock_db.find_one = AsyncMock(return_value=ride_still_assigned)
            mock_db.update_one = AsyncMock(side_effect=_capture_update)
            mock_manager.send_personal_message = AsyncMock()

            from backend.routes.rides import _offer_timeout_handler

            await _offer_timeout_handler("ride_1", "driver_1", rider_id="user_rider_1", timeout_seconds=30)

            # Driver released — first update_one call is to "drivers" table
            driver_calls = [c for c in update_calls if c[0] == "drivers"]
            assert driver_calls, "Expected db.update_one('drivers', ...) to be called"
            assert driver_calls[0][1] == {"id": "driver_1"}

            # Ride reset to searching — second update_one call is to "rides" table
            ride_calls = [c for c in update_calls if c[0] == "rides"]
            assert ride_calls, "Expected db.update_one('rides', ...) to be called"

            # Rider notified
            mock_manager.send_personal_message.assert_called_once()
            ws_msg = mock_manager.send_personal_message.call_args[0][0]
            assert ws_msg["type"] == "driver_timeout"

            # Re-dispatch triggered
            mock_redispatch.assert_called_once_with("ride_1")

    @pytest.mark.asyncio
    async def test_noop_if_ride_progressed(self):
        """Ride already accepted → handler does nothing."""
        progressed_ride = {
            "id": "ride_1",
            "rider_id": "user_rider_1",
            "driver_id": "driver_1",
            "status": "driver_accepted",  # past assignment
        }
        with (
            patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
            patch("backend.routes.rides._deps.db") as mock_db,
            patch("backend.routes.rides._deps.manager") as mock_manager,  # noqa: F841
            patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock) as mock_redispatch,
        ):
            # Flat API
            mock_db.find_one = AsyncMock(return_value=progressed_ride)
            mock_db.update_one = AsyncMock()

            from backend.routes.rides import _offer_timeout_handler

            await _offer_timeout_handler("ride_1", "driver_1", rider_id="user_rider_1")

            mock_db.update_one.assert_not_called()
            mock_redispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_if_different_driver(self):
        """Ride reassigned to a different driver → handler does nothing."""
        different_driver_ride = {
            "id": "ride_1",
            "rider_id": "user_rider_1",
            "driver_id": "driver_2",  # different from the one we're timing out
            "status": "driver_assigned",
        }
        with (
            patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
            patch("backend.routes.rides._deps.db") as mock_db,
            patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock) as mock_redispatch,
        ):
            # Flat API
            mock_db.find_one = AsyncMock(return_value=different_driver_ride)
            mock_db.update_one = AsyncMock()

            from backend.routes.rides import _offer_timeout_handler

            await _offer_timeout_handler("ride_1", "driver_1", rider_id="user_rider_1")

            mock_db.update_one.assert_not_called()
            mock_redispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_if_ride_gone(self):
        """Ride deleted/not found → handler does nothing."""
        with (
            patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
            patch("backend.routes.rides._deps.db") as mock_db,
            patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock) as mock_redispatch,
        ):
            # Flat API
            mock_db.find_one = AsyncMock(return_value=None)
            mock_db.update_one = AsyncMock()

            from backend.routes.rides import _offer_timeout_handler

            await _offer_timeout_handler("ride_1", "driver_1", rider_id="user_rider_1")

            mock_db.update_one.assert_not_called()
            mock_redispatch.assert_not_called()


class TestDispatchHardening:
    """Tests for related changes in match_driver_to_ride.

    Patterns mirror test_e2e_wav_dispatch.py — patch every external dep of
    match_driver_to_ride and assert on the captured WS payload / early return.
    """

    _RIDE_BASE = {
        "id": "ride_disp_1",
        "rider_id": "rider_disp_1",
        "vehicle_type_id": "economy",
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.14,
        "dropoff_lng": -106.65,
        "status": "searching",
        "requires_wav": False,
    }
    _DRIVER = {
        "id": "driver_disp_1",
        "user_id": "user_driver_disp_1",
        "lat": 52.131,
        "lng": -106.671,
        "rating": 4.9,
        "is_wav": False,
        "vehicle_type_id": "economy",
    }

    @pytest.mark.asyncio
    async def test_dispatch_aborts_on_null_coords(self):
        """Ride row with any null lat/lng must abort before driver lookup."""
        from backend.routes import rides as rides_mod

        bad_ride = {**self._RIDE_BASE, "pickup_lng": None}

        get_rows_mock = AsyncMock(return_value=[self._DRIVER])
        update_ride_mock = AsyncMock()
        create_task_mock = MagicMock()
        send_personal_mock = AsyncMock()

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=bad_ride)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", get_rows_mock),
            patch("backend.routes.rides._deps.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.rides._deps.manager.send_personal_message", send_personal_mock),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
            patch("backend.routes.rides._deps.asyncio.create_task", create_task_mock),
        ):
            await rides_mod.match_driver_to_ride(ride_id=self._RIDE_BASE["id"])

        get_rows_mock.assert_not_called()  # never queried drivers
        update_ride_mock.assert_not_called()  # never assigned a driver
        send_personal_mock.assert_not_called()  # never broadcast
        create_task_mock.assert_not_called()  # no offer-timeout scheduled

    @pytest.mark.asyncio
    async def test_dispatch_candidate_query_is_geo_bounded(self):
        """The drivers candidate fetch must carry a lat/lng bounding box.

        Regression: an un-geo-filtered LIMIT 500 fetch of all online drivers
        meant that above 500 candidates province-wide the nearest driver
        could sit in row 501 → false "no drivers" → ride auto-cancelled.
        """
        from backend.routes import rides as rides_mod

        get_rows_mock = AsyncMock(return_value=[])

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=self._RIDE_BASE)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", get_rows_mock),
            patch(
                "backend.routes.rides._shared.dispatch.resolve_matching_config",
                AsyncMock(return_value=("nearest", 4.0, 10.0, 3, False)),
            ),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
            patch("backend.routes.rides._deps.spawn", MagicMock()),
            patch("backend.routes.rides._deps.asyncio.create_task", MagicMock()),
        ):
            await rides_mod.match_driver_to_ride(ride_id=self._RIDE_BASE["id"])

        driver_fetches = [c for c in get_rows_mock.call_args_list if c.args and c.args[0] == "drivers"]
        assert driver_fetches, "Expected a drivers candidate fetch"
        filt = driver_fetches[0].args[1]
        box = filt.get("$and")
        assert box, f"drivers fetch must be geo-bounded, got filter: {filt}"
        bounded_cols = {col for clause in box for col in clause}
        assert bounded_cols == {"lat", "lng"}
        # Box must bracket the pickup on both axes.
        flat = {(col, op): val for clause in box for col, pred in clause.items() for op, val in pred.items()}
        assert flat[("lat", "$gte")] < self._RIDE_BASE["pickup_lat"] < flat[("lat", "$lte")]
        assert flat[("lng", "$gte")] < self._RIDE_BASE["pickup_lng"] < flat[("lng", "$lte")]

    @pytest.mark.asyncio
    async def test_dispatch_payload_includes_countdown_seconds(self):
        """new_ride_assignment WS payload carries the per-offer countdown."""
        from backend.routes import rides as rides_mod

        send_personal_mock = AsyncMock()

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=self._RIDE_BASE)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[self._DRIVER])),
            patch(
                "backend.routes.rides._shared.dispatch.resolve_matching_config",
                AsyncMock(return_value=("nearest", 4.0, 10.0, 3, True)),
            ),
            patch("backend.routes.rides._deps.db_supabase.match_and_claim_driver", AsyncMock(return_value=None)),
            patch("backend.routes.rides._deps.db_supabase.claim_driver_atomic", AsyncMock(return_value=True)),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch(
                "backend.routes.rides._deps.db_supabase.get_driver_by_id",
                AsyncMock(return_value={**self._DRIVER, "is_online": True}),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Test", "last_name": "Rider"}),
            ),
            patch("backend.routes.rides._deps.manager.send_personal_message", send_personal_mock),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
            patch(
                "backend.routes.rides._deps.get_app_settings",
                AsyncMock(return_value={"ride_offer_timeout_seconds": 22}),
            ),
            patch("backend.routes.rides._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.rides._deps.asyncio.create_task", MagicMock()),
        ):
            await rides_mod.match_driver_to_ride(ride_id=self._RIDE_BASE["id"])

        ws_payloads = [c.args[0] for c in send_personal_mock.call_args_list]
        dispatch_msgs = [p for p in ws_payloads if p.get("type") == "new_ride_assignment"]
        assert dispatch_msgs, "Expected a new_ride_assignment WS event"
        assert dispatch_msgs[0]["countdown_seconds"] == 22, (
            f"Expected countdown_seconds=22 from app_settings, got {dispatch_msgs[0].get('countdown_seconds')}"
        )

    @pytest.mark.asyncio
    async def test_offer_expiry_notifies_driver(self):
        """_offer_timeout_handler must also send ride_offer_expired to the driver."""
        from backend.routes import rides as rides_mod

        ride = {
            "id": "ride_disp_2",
            "rider_id": "rider_disp_2",
            "driver_id": "driver_disp_2",
            "status": "driver_assigned",
        }
        driver_row = {"id": "driver_disp_2", "user_id": "user_driver_disp_2"}

        send_personal_mock = AsyncMock()

        with (
            patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
            patch("backend.routes.rides._deps.db") as mock_db,
            patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock),
            patch("backend.routes.rides._deps.manager.send_personal_message", send_personal_mock),
            patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver_row)),
            patch("backend.routes.rides._deps.record_period_transition", AsyncMock()),
        ):
            mock_db.find_one = AsyncMock(return_value=ride)
            mock_db.update_one = AsyncMock()

            await rides_mod._offer_timeout_handler(
                "ride_disp_2", "driver_disp_2", rider_id="rider_disp_2", timeout_seconds=30
            )

        events = [(c.args[0]["type"], c.args[1]) for c in send_personal_mock.call_args_list]
        assert ("driver_timeout", "rider_rider_disp_2") in events
        assert ("ride_offer_expired", "driver_user_driver_disp_2") in events
