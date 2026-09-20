import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
async def test_reminder_retries_keep_one_inbox_identity_per_recipient():
    from backend import features as f
    data = {'type': 'scheduled_driver_reminder', 'ride_id': 'ride-1'}
    with patch.object(f.db, 'insert_many_ignore_conflicts', AsyncMock()) as insert:
        f._record_inbox_notification('user-1', 'Pickup', 'Soon', data)
        f._record_inbox_notification('user-1', 'Pickup', 'Soon', data)
        f._record_inbox_notification('user-2', 'Pickup', 'Soon', data)
        await asyncio.sleep(0)
    ids = [call.args[1][0]['id'] for call in insert.await_args_list]
    assert len(ids) == 3 and ids[0] == ids[1] and ids[0] != ids[2]
    assert all(call.kwargs['on_conflict'] == 'id' for call in insert.await_args_list)


@pytest.mark.anyio
async def test_reminder_suppression_is_terminal_but_delivery_failure_retries():
    from backend import features as f
    with (patch.object(f, '_record_inbox_notification'),
          patch.object(f.db, 'get_rows', AsyncMock(return_value=[{'push_enabled': False}]))):
        assert await f.send_push_notification('u', 't', 'b', report_suppression=True) is None
        assert await f.send_push_notification('u', 't', 'b') is False
    with (patch.object(f, '_record_inbox_notification'),
          patch.object(f.db, 'get_rows', AsyncMock(return_value=[])),
          patch.object(f.db, 'find_one', AsyncMock(return_value={'fcm_token': 'token'})),
          patch.object(f, '_deliver_push_now', AsyncMock(return_value=False))):
        assert await f.send_push_notification('u', 't', 'b', report_suppression=True) is False
