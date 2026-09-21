from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


def early_ride():
    now = datetime.now(timezone.utc)
    return dict(id='r1', rider_id='u1', driver_id='d1', status='driver_arrived', is_scheduled=True,
                driver_arrived_at=(now-timedelta(minutes=8)).isoformat(),
                driver_accepted_at=(now-timedelta(minutes=12)).isoformat(), scheduled_time=(now+timedelta(minutes=5)).isoformat())


def test_no_cancellation_fee_before_booked_pickup():
    from backend.services.cancellation_service import calculate_cancellation_fee
    assert calculate_cancellation_fee(early_ride(), {}) == (Decimal('0'), Decimal('0'))


@pytest.mark.anyio
async def test_no_show_cannot_charge_for_early_arrival():
    from backend.routes.drivers import ride_cancel as m
    with (patch.object(m.db_supabase, 'get_rows', AsyncMock(return_value=[{'id':'d1'}])),
          patch.object(m.db_supabase, 'get_ride', AsyncMock(return_value=early_ride()))):
        with pytest.raises(HTTPException) as exc:
            await m.mark_rider_noshow('r1', current_user={'id':'driver-user'})
    assert exc.value.status_code == 400
