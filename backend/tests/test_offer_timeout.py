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

        Production code:
            db.find_one("rides", {...})
            db_supabase.set_driver_available(driver_id, available=True)  -- NOT
                a raw db.update_one("drivers", ...); this enforces the
                is_available => is_online invariant and is skipped entirely
                on the miss-streak auto-offline branch, which instead uses
                db.update_one("drivers", ...) directly.
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
            patch(
                "backend.routes.rides._deps.db_supabase.set_driver_available",
                AsyncMock(return_value={"id": "driver_1", "is_available": True}),
            ) as mock_set_available,
            patch("backend.routes.rides._deps.record_period_transition", AsyncMock()),
            patch("utils.driver_presence.increment_miss_streak", AsyncMock(return_value=1)),
            patch("utils.driver_presence.reset_miss_streak", AsyncMock()),
            patch("utils.driver_presence.clear_presence", AsyncMock()),
        ):
            # Flat API: db.find_one("rides", {...})
            mock_db.find_one = AsyncMock(return_value=ride_still_assigned)
            mock_db.update_one = AsyncMock(side_effect=_capture_update)
            mock_manager.send_personal_message = AsyncMock()

            from backend.routes.rides import _offer_timeout_handler

            await _offer_timeout_handler("ride_1", "driver_1", rider_id="user_rider_1", timeout_seconds=30)

            # Driver released via set_driver_available (below the miss-streak
            # auto-offline threshold), not a raw db.update_one("drivers", ...).
            mock_set_available.assert_awaited_once_with("driver_1", available=True)

            # Ride reset to searching via db.update_one("rides", ...)
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
                # claim_driver_atomic only guards id + is_available; the
                # freshly-read row is then revalidated against the full
                # eligibility set (is_online + is_verified + status=='active')
                # before the driver is actually claimed for an offer.
                AsyncMock(return_value={**self._DRIVER, "is_online": True, "is_verified": True, "status": "active"}),
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
            # Batch-offer dispatch inserts ride_offers rows (and does
            # best-effort quest-progress/incentive lookups) via
            # db_supabase.run_sync(lambda: supabase.table(...)...execute()) --
            # unmocked this hits a real, unconfigured client and the
            # ride_offers insert path re-raises on failure (unlike the
            # other lookups, which are wrapped and degrade quietly).
            patch(
                "backend.routes.rides._deps.db_supabase.run_sync",
                AsyncMock(return_value=MagicMock(data=[])),
            ),
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


class TestBatchOfferTimeoutInsuranceGuard:
    """Regression for the batch offer-timeout insurance-period write.

    The single-offer handler only opens insurance Period 1 (online / no ride /
    TNC contingent liability) when the driver's *committed* state actually
    became available (matching.py:962-970) — otherwise a driver who went
    offline between offer dispatch and timeout would get a Period-1 row that
    falsely reopens a commercial-insurance window. The batch handler must apply
    the same guard; recording Period 1 unconditionally is an insurance
    misclassification and a regulatory (SGI) liability.
    """

    @pytest.mark.asyncio
    async def test_batch_timeout_skips_period1_when_driver_clamped_offline(self):
        # One pending offer for a driver who is now offline; set_driver_available
        # clamps is_available→False to preserve the is_available⇒is_online
        # invariant. Period 1 must NOT be recorded for that driver.
        pending_result = MagicMock(data=[{"driver_id": "d_offline"}])

        with (
            patch(
                "backend.routes.rides._deps.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_b", "status": "searching"}),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.run_sync",
                AsyncMock(return_value=pending_result),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.set_driver_available",
                AsyncMock(return_value={"is_available": False}),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_driver_by_id",
                AsyncMock(return_value=None),
            ),
            patch(
                "backend.routes.rides._deps.get_app_settings",
                AsyncMock(return_value={"auto_offline_miss_threshold": 3}),
            ),
            patch(
                "backend.routes.rides._deps.record_period_transition",
                new_callable=AsyncMock,
            ) as mock_period,
            patch("backend.routes.rides._deps.manager") as mock_manager,
            patch("backend.repositories.driver_repo.update_acceptance_rate", new_callable=AsyncMock),
            patch(
                "backend.utils.driver_presence.increment_miss_streak",
                AsyncMock(return_value=1),  # below threshold → normal-release else branch
            ),
            patch("backend.utils.driver_presence.clear_presence", new_callable=AsyncMock),
            patch("backend.utils.driver_presence.reset_miss_streak", new_callable=AsyncMock),
            patch("backend.utils.redis_client.redis_set", new_callable=AsyncMock),
        ):
            mock_manager.send_personal_message = AsyncMock()

            from backend.routes.rides import _batch_offer_timeout_handler

            await _batch_offer_timeout_handler("ride_b", rider_id=None, timeout_seconds=0)

            period1_calls = [c for c in mock_period.call_args_list if c.args == ("d_offline", 1)]
            assert not period1_calls, (
                "Batch timeout recorded insurance Period 1 for a driver whose committed "
                "state is offline (is_available=False) — must mirror the single-offer "
                "guard at matching.py:962-970"
            )


@pytest.mark.asyncio
async def test_process_expired_offer_is_idempotent():
    """The pending->expired claim gates the side-effects: a second call for an
    offer that is already expired runs NO side-effects. This is what lets the
    in-process timeout handler and the durable reaper both call
    process_expired_offer without ever double-counting a miss / double-writing
    an insurance-period row for the same offer."""
    from backend.routes.rides import matching as m

    # First claim wins (returns a row); second finds nothing pending.
    claim_results = [MagicMock(data=[{"id": "off1"}]), MagicMock(data=[])]

    with (
        patch(
            "backend.routes.rides._deps.db_supabase.run_sync",
            AsyncMock(side_effect=claim_results),
        ),
        patch(
            "backend.routes.rides._deps.db_supabase.set_driver_available",
            AsyncMock(return_value={"is_available": True}),
        ),
        patch(
            "backend.routes.rides._deps.db_supabase.get_driver_by_id",
            AsyncMock(return_value=None),
        ),
        patch("backend.routes.rides._deps.record_period_transition", new_callable=AsyncMock) as mock_period,
        patch("backend.routes.rides._deps.manager") as mock_mgr,
        patch("backend.repositories.driver_repo.update_acceptance_rate", new_callable=AsyncMock) as mock_ar,
        patch("backend.utils.driver_presence.increment_miss_streak", AsyncMock(return_value=1)) as mock_miss,
        patch("backend.utils.driver_presence.clear_presence", new_callable=AsyncMock),
        patch("backend.utils.driver_presence.reset_miss_streak", new_callable=AsyncMock),
        patch("backend.utils.redis_client.redis_set", new_callable=AsyncMock),
    ):
        mock_mgr.send_personal_message = AsyncMock()

        won_first = await m.process_expired_offer("ride_b", "d1", 3)
        won_second = await m.process_expired_offer("ride_b", "d1", 3)

    assert won_first is True
    assert won_second is False
    # Side-effects ran for the winning claim only.
    assert mock_miss.await_count == 1
    assert mock_ar.await_count == 1
    mock_period.assert_awaited_once_with("d1", 1)


@pytest.mark.asyncio
async def test_process_expired_offer_claim_lost_returns_false():
    """A conditional UPDATE that matches zero rows (already claimed by a peer
    reaper or accepted by the driver) must run NO side-effects."""
    from backend.routes.rides import matching as m

    with (
        patch(
            "backend.routes.rides._deps.db_supabase.run_sync",
            AsyncMock(return_value=MagicMock(data=[])),
        ),
        patch("backend.repositories.driver_repo.update_acceptance_rate", new_callable=AsyncMock) as mock_ar,
        patch("backend.utils.driver_presence.increment_miss_streak", new_callable=AsyncMock) as mock_miss,
    ):
        won = await m.process_expired_offer("ride_c", "d1", 3)
    assert won is False
    mock_miss.assert_not_awaited()
    mock_ar.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_expired_offer_auto_offline_at_threshold():
    """Hitting the miss-streak threshold takes the driver offline, records
    insurance Period 0, and sends an auto_offline WS notice."""
    from backend.routes.rides import matching as m

    with (
        patch(
            "backend.routes.rides._deps.db_supabase.run_sync",
            AsyncMock(return_value=MagicMock(data=[{"id": "off1"}])),
        ),
        patch("backend.routes.rides._deps.db_supabase.set_driver_available", new_callable=AsyncMock) as mock_avail,
        patch(
            "backend.routes.rides._deps.db_supabase.get_driver_by_id",
            AsyncMock(return_value={"id": "d1", "user_id": "user-1"}),
        ),
        patch("backend.routes.rides._deps.record_period_transition", new_callable=AsyncMock) as mock_period,
        patch("backend.routes.rides._deps.manager") as mock_mgr,
        patch("backend.repositories.driver_repo.update_acceptance_rate", new_callable=AsyncMock),
        patch("backend.utils.driver_presence.increment_miss_streak", AsyncMock(return_value=3)),
        patch("backend.utils.driver_presence.clear_presence", new_callable=AsyncMock) as mock_clear,
        patch("backend.utils.driver_presence.reset_miss_streak", new_callable=AsyncMock) as mock_reset,
        patch("backend.utils.redis_client.redis_set", new_callable=AsyncMock),
    ):
        mock_mgr.send_personal_message = AsyncMock()
        won = await m.process_expired_offer("ride_d", "d1", 3)

    assert won is True
    mock_avail.assert_awaited_once_with("d1", False)
    mock_clear.assert_awaited_once_with("d1")
    mock_reset.assert_awaited_once_with("d1")
    mock_period.assert_awaited_once_with("d1", 0)
    ws_call = mock_mgr.send_personal_message.await_args
    assert ws_call.args[0]["type"] == "auto_offline"
    assert ws_call.args[1] == "driver_user-1"


@pytest.mark.asyncio
async def test_process_expired_offer_redis_skip_key_failure_is_swallowed():
    from backend.routes.rides import matching as m

    with (
        patch(
            "backend.routes.rides._deps.db_supabase.run_sync",
            AsyncMock(return_value=MagicMock(data=[{"id": "off1"}])),
        ),
        patch(
            "backend.routes.rides._deps.db_supabase.set_driver_available",
            AsyncMock(return_value={"is_available": True}),
        ),
        patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=None)),
        patch("backend.routes.rides._deps.record_period_transition", new_callable=AsyncMock),
        patch("backend.repositories.driver_repo.update_acceptance_rate", new_callable=AsyncMock),
        patch("backend.utils.driver_presence.increment_miss_streak", AsyncMock(return_value=1)),
        patch(
            "backend.utils.redis_client.redis_set",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ),
    ):
        won = await m.process_expired_offer("ride_e", "d1", 3)
    assert won is True


@pytest.mark.asyncio
async def test_process_expired_offer_ws_notify_failure_is_swallowed():
    from backend.routes.rides import matching as m

    with (
        patch(
            "backend.routes.rides._deps.db_supabase.run_sync",
            AsyncMock(return_value=MagicMock(data=[{"id": "off1"}])),
        ),
        patch(
            "backend.routes.rides._deps.db_supabase.set_driver_available",
            AsyncMock(return_value={"is_available": True}),
        ),
        patch(
            "backend.routes.rides._deps.db_supabase.get_driver_by_id",
            AsyncMock(return_value={"id": "d1", "user_id": "user-1"}),
        ),
        patch("backend.routes.rides._deps.record_period_transition", new_callable=AsyncMock),
        patch("backend.routes.rides._deps.manager") as mock_mgr,
        patch("backend.repositories.driver_repo.update_acceptance_rate", new_callable=AsyncMock),
        patch("backend.utils.driver_presence.increment_miss_streak", AsyncMock(return_value=1)),
        patch("backend.utils.redis_client.redis_set", new_callable=AsyncMock),
    ):
        mock_mgr.send_personal_message = AsyncMock(side_effect=RuntimeError("ws down"))
        won = await m.process_expired_offer("ride_f", "d1", 3)
    assert won is True


@pytest.mark.asyncio
async def test_batch_offer_timeout_noop_when_ride_left_searching():
    from backend.routes.rides import matching as m

    with (
        patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "backend.routes.rides._deps.db_supabase.get_ride",
            AsyncMock(return_value={"id": "ride_g", "status": "driver_accepted"}),
        ),
        patch("backend.routes.rides._deps.db_supabase.run_sync", new_callable=AsyncMock) as mock_run_sync,
    ):
        await m._batch_offer_timeout_handler("ride_g", rider_id=None, timeout_seconds=0)
    mock_run_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_offer_timeout_noop_when_no_pending_offers():
    from backend.routes.rides import matching as m

    with (
        patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "backend.routes.rides._deps.db_supabase.get_ride",
            AsyncMock(return_value={"id": "ride_h", "status": "searching"}),
        ),
        patch(
            "backend.routes.rides._deps.db_supabase.run_sync",
            AsyncMock(return_value=MagicMock(data=[])),
        ),
        patch("backend.routes.rides._deps.manager") as mock_mgr,
    ):
        mock_mgr.send_personal_message = AsyncMock()
        await m._batch_offer_timeout_handler("ride_h", rider_id="rider-1", timeout_seconds=0)
    mock_mgr.send_personal_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_offer_timeout_settings_fetch_failure_falls_back_to_default_threshold():
    from backend.routes.rides import matching as m

    pending_result = MagicMock(data=[{"driver_id": "d1"}])
    with (
        patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "backend.routes.rides._deps.db_supabase.get_ride",
            AsyncMock(return_value={"id": "ride_i", "status": "searching"}),
        ),
        patch("backend.routes.rides._deps.db_supabase.run_sync", AsyncMock(return_value=pending_result)),
        patch(
            "backend.routes.rides._deps.get_app_settings",
            AsyncMock(side_effect=RuntimeError("settings db down")),
        ),
        patch(
            "backend.routes.rides.matching.process_expired_offer",
            new_callable=AsyncMock,
        ) as mock_process,
        patch("backend.routes.rides._deps.manager") as mock_mgr,
    ):
        mock_mgr.send_personal_message = AsyncMock()
        await m._batch_offer_timeout_handler("ride_i", rider_id=None, timeout_seconds=0)
    # Falls back to the hardcoded default (3) rather than raising.
    mock_process.assert_awaited_once_with("ride_i", "d1", 3)


@pytest.mark.asyncio
async def test_batch_offer_timeout_handler_error_is_swallowed():
    """Any unexpected error in the batch handler must not propagate -- it's a
    fire-and-forget background task with no caller to catch it."""
    from backend.routes.rides import matching as m

    with (
        patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "backend.routes.rides._deps.db_supabase.get_ride",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
    ):
        # Must not raise.
        await m._batch_offer_timeout_handler("ride_j", rider_id=None, timeout_seconds=0)


@pytest.mark.asyncio
async def test_create_demo_drivers_is_a_noop():
    """create_demo_drivers is a deliberate deprecated no-op -- confirm it
    still resolves for any stale caller and does nothing observable."""
    from backend.routes.rides import matching as m

    result = await m.create_demo_drivers("economy", 52.1, -106.6)
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_retry_stops_after_max_attempts():
    from backend.routes.rides import matching as m

    with (
        patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
        patch("backend.routes.rides._deps.db_supabase.get_ride", new_callable=AsyncMock) as mock_get_ride,
    ):
        await m._dispatch_retry("ride_k", delay=0, attempt=m._MAX_DISPATCH_ATTEMPTS + 1)
    mock_get_ride.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_retry_noop_when_ride_left_searching():
    from backend.routes.rides import matching as m

    with (
        patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "backend.routes.rides._deps.db_supabase.get_ride",
            AsyncMock(return_value={"id": "ride_l", "status": "driver_accepted"}),
        ),
        patch("backend.routes.rides.matching.match_driver_to_ride", new_callable=AsyncMock) as mock_match,
    ):
        await m._dispatch_retry("ride_l", delay=0, attempt=1)
    mock_match.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_retry_reschedules_with_backoff_on_error():
    """A transient failure keeps the retry chain alive with an escalating
    backoff instead of stranding the ride in `searching`."""
    from backend.routes.rides import matching as m

    with (
        patch("backend.routes.rides._deps.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "backend.routes.rides._deps.db_supabase.get_ride",
            AsyncMock(side_effect=RuntimeError("db blip")),
        ),
        patch("backend.routes.rides._deps.spawn") as mock_spawn,
    ):
        await m._dispatch_retry("ride_m", delay=0, attempt=1)
    mock_spawn.assert_called_once()
    # spawn() was handed a fresh _dispatch_retry coroutine for the reschedule
    # -- close it to avoid an "never awaited" warning since we don't run it.
    scheduled_coro = mock_spawn.call_args.args[0]
    scheduled_coro.close()


def test_build_offer_rows_persists_expires_at():
    """Every dispatched ride_offers row must carry expires_at so the durable
    reaper can expire it even if the in-process asyncio timer is lost on a
    backend restart (migration 224 persists this deadline)."""
    from backend.routes.rides.matching import _build_offer_rows

    rows = _build_offer_rows(
        [({"id": "d1"}, 120), ({"id": "d2"}, 90)],
        ride_id="ride_1",
        offered_at_iso="2026-01-01T00:00:00+00:00",
        expires_at_iso="2026-01-01T00:00:30+00:00",
    )

    assert [r["driver_id"] for r in rows] == ["d1", "d2"]
    assert rows[0]["eta_seconds"] == 120
    for r in rows:
        assert r["status"] == "pending"
        assert r["ride_id"] == "ride_1"
        assert r["offered_at"] == "2026-01-01T00:00:00+00:00"
        assert r["expires_at"] == "2026-01-01T00:00:30+00:00"
