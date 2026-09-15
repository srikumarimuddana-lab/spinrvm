from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import scheduled_rides as m


def row(minutes=9, **extra):
    return dict(id='r1', rider_id='u1', service_area_id='a1', is_scheduled=True, status='scheduled',
                scheduled_time=(datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat(), **extra)


@pytest.mark.anyio
@pytest.mark.parametrize('enabled,minutes,expected', [(False,9,False),(True,9,True),(True,11,False)])
async def test_area_dispatch_window(enabled, minutes, expected):
    ride = row(minutes)
    async def rows(table, filters, **kwargs):
        if table == 'service_areas':
            return [{'id':'a1','scheduled_ride_config':{'enabled':enabled}}]
        return [ride] if filters['status']=='scheduled' else []
    with (patch.object(m, 'get_app_settings', AsyncMock(return_value={})),
          patch.object(m, 'redis_set_nx', AsyncMock(return_value=True)),
          patch.object(m, 'redis_delete', AsyncMock()),
          patch.object(m.db, 'get_rows', AsyncMock(side_effect=rows)),
          patch.object(m, '_send_reminder', AsyncMock()),
          patch.object(m, '_maybe_nudge_nearby_drivers', AsyncMock()),
          patch.object(m, '_dispatch_scheduled_ride', AsyncMock()) as dispatch):
        assert await m.check_scheduled_rides() is True
    assert dispatch.await_count == int(expected)


@pytest.mark.anyio
async def test_assigned_rider_reminder_uses_area_window_without_redispatch():
    ride = row(19)
    ride.update(status='driver_accepted', driver_id='d1', scheduled_dispatched=True)
    async def rows(table, filters, **kwargs):
        if table == 'service_areas':
            return [{'id':'a1','scheduled_ride_config':{'enabled':True,'rider_reminder_minutes':20}}]
        return [] if filters['status']=='scheduled' else [ride]
    with (patch.object(m, 'get_app_settings', AsyncMock(return_value={})),
          patch.object(m, 'redis_set_nx', AsyncMock(return_value=True)),
          patch.object(m, 'redis_delete', AsyncMock()),
          patch.object(m.db, 'get_rows', AsyncMock(side_effect=rows)),
          patch.object(m, '_send_reminder', AsyncMock()) as remind,
          patch.object(m, '_dispatch_scheduled_ride', AsyncMock()) as dispatch):
        await m.check_scheduled_rides()
    remind.assert_awaited_once_with(ride)
    dispatch.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize('delivered', [True, False])
async def test_driver_reminder_target_and_transient_failure(delivered):
    ride = row()
    ride.update(status='driver_accepted', driver_id='d1')
    with (patch.object(m.db, 'find_one', AsyncMock(return_value=ride)),
          patch.object(m.db, 'get_driver_by_id', AsyncMock(return_value={'user_id':'driver-user'})),
          patch.object(m.db, 'update_one', AsyncMock()) as update,
          patch.object(m, 'redis_set_nx', AsyncMock(return_value=True)),
          patch.object(m, 'redis_delete', AsyncMock()),
          patch.object(m, 'send_push_notification', AsyncMock(return_value=delivered)) as push):
        await m._send_driver_reminder(ride)
    assert push.await_args.args[0] == 'driver-user'
    assert push.await_args.kwargs['target_app'] == 'driver'
    assert update.await_count == int(delivered)


@pytest.mark.anyio
async def test_reassignment_never_notifies_previous_driver():
    ride = row()
    ride.update(driver_id='old',status='driver_accepted')
    with (patch.object(m.db, 'find_one', AsyncMock(return_value={**ride,'driver_id':'new'})),
          patch.object(m, 'send_push_notification', AsyncMock()) as push):
        await m._send_driver_reminder(ride)
    push.assert_not_awaited()
