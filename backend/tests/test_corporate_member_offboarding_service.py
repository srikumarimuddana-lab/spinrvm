# backend/tests/test_corporate_member_offboarding_service.py
"""Covers member-scoped pre-pickup ride cancellation on removal (gap #3 —
employee/member offboarding). Member-level companion to
test_corporate_suspension_service.py's company-level coverage."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.tests._factories import driver_row, ride_row
from services import corporate_member_offboarding_service as svc


@pytest.mark.unit
@pytest.mark.anyio
async def test_cancels_pre_pickup_ride_and_releases_driver():
    ride = ride_row(status="driver_assigned", driver_id="d1", corporate_account_id="c1", corporate_member_id="m1")
    driver = driver_row(id="d1", user_id="driver-user-1")

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])) as mock_get_rows,
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})),
        patch.object(svc.db_supabase, "set_driver_available", AsyncMock()) as mock_set_avail,
        patch.object(svc.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch.object(svc, "record_period_transition", AsyncMock()) as mock_period,
        patch.object(svc.manager, "send_personal_message", AsyncMock()) as mock_ws,
        patch.object(svc, "send_push_notification", AsyncMock()) as mock_push,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_member("c1", "m1")

    assert cancelled == 1
    mock_set_avail.assert_awaited_once_with("d1", True)
    mock_period.assert_awaited_once_with("d1", 1)
    targets = {call.args[1] for call in mock_ws.await_args_list}
    assert targets == {f"driver_{driver['user_id']}", f"rider_{ride['rider_id']}"}
    mock_push.assert_awaited_once()

    filters = mock_get_rows.await_args.args[1]
    assert filters["corporate_member_id"] == "m1"
    assert filters["corporate_account_id"] == "c1"


@pytest.mark.unit
@pytest.mark.anyio
async def test_in_progress_ride_is_grandfathered():
    """Only rides for this member are touched; scoping by corporate_member_id
    means another member's or an in_progress ride for this member is left to
    the DB filter — the query itself only ever returns pre-pickup rides."""
    with patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[])):
        cancelled = await svc.cancel_pre_pickup_rides_for_member("c1", "m1")

    assert cancelled == 0


@pytest.mark.unit
@pytest.mark.anyio
async def test_race_ride_left_pre_trip_state_is_skipped():
    ride = ride_row(status="driver_assigned", driver_id=None, corporate_member_id="m1")

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value=None)),
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_member("c1", "m1")

    assert cancelled == 0


@pytest.mark.unit
@pytest.mark.anyio
async def test_one_ride_failure_does_not_stop_the_rest():
    ride_ok = ride_row(id="r1", status="searching", driver_id=None, corporate_member_id="m1")
    ride_bad = ride_row(id="r2", status="searching", driver_id=None, corporate_member_id="m1")

    async def _update_one(*args, **kwargs):
        filters = args[1] if len(args) > 1 else kwargs.get("filters")
        if filters and filters.get("id") == "r2":
            raise RuntimeError("boom")
        return {"id": "r1"}

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride_ok, ride_bad])),
        patch.object(svc.db_supabase, "update_one", side_effect=_update_one),
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_member("c1", "m1")

    assert cancelled == 1
