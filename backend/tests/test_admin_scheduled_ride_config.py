from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from backend.routes.admin import service_areas as m


def test_sparse_area_update_keeps_config_unspecified():
    assert m.ServiceAreaUpdateRequest(name='Regina').scheduled_ride_config is None


def test_area_request_rejects_invalid_config():
    with pytest.raises(ValidationError):
        m.ServiceAreaUpdateRequest(scheduled_ride_config={'driver_reminder_minutes': 61})


@pytest.mark.anyio
async def test_area_update_persists_config():
    row = {'id': 'area-1', 'name': 'Regina'}
    with (patch.object(m.db_supabase, 'find_one', AsyncMock(return_value=row)),
          patch.object(m.db_supabase, 'update_one', AsyncMock(return_value=row)) as update,
          patch.object(m, 'invalidate_fare_cache', AsyncMock()),
          patch.object(m, 'log_admin_action', AsyncMock())):
        await m.admin_update_service_area('area-1', m.ServiceAreaUpdateRequest(scheduled_ride_config={'enabled':True, 'driver_reminder_minutes':15}), {'id':'admin-1','role':'super_admin'})
    payload = update.await_args.args[2]
    assert payload['scheduled_ride_config']['driver_reminder_minutes'] == 15
    assert payload['scheduled_ride_config']['enabled'] is True
