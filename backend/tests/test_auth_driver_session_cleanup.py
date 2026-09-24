"""Race regressions for login-time cleanup of a superseded driver session."""

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


async def _run_cleanup(get_rows, update_one):
    from backend.routes import auth

    with (
        patch.object(auth.db, "get_rows", get_rows),
        patch.object(auth.db, "update_one", update_one),
        patch.object(auth, "record_period_transition", AsyncMock()) as period,
        patch.object(auth, "clear_presence", AsyncMock()) as clear,
        patch.object(
            auth.driver_session_end_service,
            "stop_requests_for_session_end",
            AsyncMock(return_value="legacy"),
        ),
    ):
        await auth._offline_driver_for_logout_all("user-driver")
    return period, clear


async def test_losing_offer_claim_does_not_revert_ride():
    get_rows = AsyncMock(
        side_effect=[
            [{"id": "driver-1", "is_online": True}],
            [],
            [{"id": "offer-1", "ride_id": "ride-1"}],
            [],
        ]
    )
    update_one = AsyncMock(return_value=None)

    await _run_cleanup(get_rows, update_one)

    assert len(update_one.await_args_list) == 2
    assert update_one.await_args_list[0].args[0] == "ride_offers"
    assert update_one.await_args_list[0].args[1] == {"id": "offer-1", "status": "pending"}
    assert all(call.args[0] != "rides" for call in update_one.await_args_list)


async def test_acceptance_winning_ride_cas_preserves_driver_and_insurance():
    accepted_ride = [{"id": "ride-1", "status": "driver_accepted"}]
    get_rows = AsyncMock(
        side_effect=[
            [{"id": "driver-1", "is_online": True}],
            [],
            [{"id": "offer-1", "ride_id": "ride-1"}],
            accepted_ride,
        ]
    )
    update_one = AsyncMock(side_effect=[{"id": "offer-1"}, None])

    period, clear = await _run_cleanup(get_rows, update_one)

    assert update_one.await_args_list[0].args[0] == "ride_offers"
    assert update_one.await_args_list[1].args[0] == "rides"
    assert len(update_one.await_args_list) == 2
    period.assert_not_awaited()
    clear.assert_not_awaited()
