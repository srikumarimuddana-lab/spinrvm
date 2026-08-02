# backend/tests/test_corporate_suspension_service.py
"""Covers the pre-pickup ride cancellation triggered by company suspend/close.

See docs/change-log for the corresponding Change Impact Log entry — this
closes the gap where suspending/closing a corporate account had zero effect
on rides already created for that company.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.tests._factories import driver_row, ride_row
from services import corporate_suspension_service as svc


@pytest.mark.unit
@pytest.mark.anyio
async def test_cancels_pre_pickup_ride_and_releases_driver():
    ride = ride_row(status="driver_assigned", driver_id="d1", corporate_account_id="c1")
    driver = driver_row(id="d1", user_id="driver-user-1")

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})),
        patch.object(svc.db_supabase, "set_driver_available", AsyncMock()) as mock_set_avail,
        patch.object(svc.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch.object(svc, "record_period_transition", AsyncMock()) as mock_period,
        patch.object(svc.manager, "send_personal_message", AsyncMock()) as mock_ws,
        patch.object(svc, "send_push_notification", AsyncMock()) as mock_push,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    assert cancelled == 1
    mock_set_avail.assert_awaited_once_with("d1", True)
    mock_period.assert_awaited_once_with("d1", 1)
    # Rider and driver both get a ride_cancelled WS message.
    targets = {call.args[1] for call in mock_ws.await_args_list}
    assert targets == {f"driver_{driver['user_id']}", f"rider_{ride['rider_id']}"}
    mock_push.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.anyio
async def test_no_driver_assigned_skips_driver_release():
    ride = ride_row(status="searching", driver_id=None, corporate_account_id="c1")

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})),
        patch.object(svc.db_supabase, "set_driver_available", AsyncMock()) as mock_set_avail,
        patch.object(svc, "record_period_transition", AsyncMock()) as mock_period,
        patch.object(svc.manager, "send_personal_message", AsyncMock()),
        patch.object(svc, "send_push_notification", AsyncMock()),
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    assert cancelled == 1
    mock_set_avail.assert_not_awaited()
    mock_period.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_race_ride_left_pre_trip_state_is_skipped():
    """update_one returning None means the ride already flipped to in_progress
    between our read and the write — must not overwrite it, and must not
    count it as cancelled."""
    ride = ride_row(status="driver_arrived", driver_id="d1", corporate_account_id="c1")

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value=None)),
        patch.object(svc.db_supabase, "set_driver_available", AsyncMock()) as mock_set_avail,
        patch.object(svc.manager, "send_personal_message", AsyncMock()) as mock_ws,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    assert cancelled == 0
    mock_set_avail.assert_not_awaited()
    mock_ws.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_in_progress_ride_not_queried_by_pre_pickup_filter():
    """cancel_pre_pickup_rides_for_company must only ask the DB for pre-pickup
    statuses — in_progress rides are grandfathered and must never appear in
    the filter, let alone be cancelled."""
    with patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock_get_rows:
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    assert cancelled == 0
    _, filters = mock_get_rows.await_args.args
    assert "in_progress" not in filters["status"]["$in"]
    assert "completed" not in filters["status"]["$in"]
    assert "cancelled" not in filters["status"]["$in"]


@pytest.mark.unit
@pytest.mark.anyio
async def test_scheduled_status_is_included_in_the_pre_pickup_filter():
    """Finding #16: a not-yet-dispatched scheduled ride must be included in
    the candidate query -- this was the actual gap (previously excluded,
    so a suspended company's scheduled bookings kept dispatching and
    billing right up to their pickup time)."""
    with patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock_get_rows:
        await svc.cancel_pre_pickup_rides_for_company("c1")

    _, filters = mock_get_rows.await_args.args
    assert "scheduled" in filters["status"]["$in"]


@pytest.mark.unit
@pytest.mark.anyio
async def test_cancels_scheduled_ride_with_no_driver_release_needed():
    """A scheduled ride has driver_id=None pre-dispatch -- the cancel must
    still succeed and correctly skip the driver-release branch, and the
    rider must still be notified (no fee, no hold to release either)."""
    ride = ride_row(status="scheduled", driver_id=None, corporate_account_id="c1", is_scheduled=True)

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})),
        patch.object(svc.db_supabase, "set_driver_available", AsyncMock()) as mock_set_avail,
        patch.object(svc, "record_period_transition", AsyncMock()) as mock_period,
        patch.object(svc.manager, "send_personal_message", AsyncMock()) as mock_ws,
        patch.object(svc, "send_push_notification", AsyncMock()) as mock_push,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    assert cancelled == 1
    mock_set_avail.assert_not_awaited()
    mock_period.assert_not_awaited()
    mock_ws.assert_awaited_once()
    assert mock_ws.await_args.args[1] == f"rider_{ride['rider_id']}"
    mock_push.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.anyio
async def test_one_ride_failure_does_not_block_the_rest():
    ride_ok = ride_row(status="searching", driver_id=None, corporate_account_id="c1")
    ride_bad = ride_row(status="searching", driver_id=None, corporate_account_id="c1")

    async def _update_one(table, filters, update):
        if filters["id"] == ride_bad["id"]:
            raise RuntimeError("boom")
        return {"id": filters["id"]}

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride_bad, ride_ok])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(side_effect=_update_one)),
        patch.object(svc.manager, "send_personal_message", AsyncMock()),
        patch.object(svc, "send_push_notification", AsyncMock()),
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    assert cancelled == 1
