from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.utils.scheduled_ride_config import ScheduledRideConfig, pickup_wait_start, scheduled_search_deadline


def test_defaults_ship_disabled():
    c = ScheduledRideConfig()
    assert c.model_dump() == dict(enabled=False, dispatch_lead_minutes=10, driver_reminder_minutes=10, rider_reminder_minutes=10)


@pytest.mark.parametrize('config', [dict(enabled='true'), dict(dispatch_lead_minutes=-1), dict(dispatch_lead_minutes=31), dict(driver_reminder_minutes=0), dict(rider_reminder_minutes=61), dict(driver_reminder_minutes=1.5), dict(driver_reminder_minutes=True), dict(other=5)])
def test_rejects_invalid_settings(config):
    with pytest.raises(ValidationError):
        ScheduledRideConfig(**config)


def test_early_arrival_and_search_deadline_use_booked_pickup():
    now = datetime.now(timezone.utc)
    pickup = now + timedelta(minutes=10)
    ride = dict(is_scheduled=True, scheduled_time=pickup.isoformat(), ride_requested_at=now.isoformat())
    assert pickup_wait_start(ride, now) == pickup
    assert scheduled_search_deadline(ride) == pickup + timedelta(minutes=5)
    assert pickup_wait_start({}, now) == now
    assert scheduled_search_deadline({}) is None
