"""utils/ride_offer_ring.py — ring_mode stamped on ride-offer payloads (migration 466)."""

import pytest

from backend.utils.ride_offer_ring import (
    RING_MODE_ALARM,
    RING_MODE_NOTIFICATION,
    ride_offer_ring_mode,
)


@pytest.mark.unit
def test_alarm_only_when_flag_is_true():
    assert ride_offer_ring_mode({"ride_offer_alarm_channel_enabled": True}) == RING_MODE_ALARM


@pytest.mark.unit
@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_anything_but_true_keeps_notification_channel(value):
    assert ride_offer_ring_mode({"ride_offer_alarm_channel_enabled": value}) == RING_MODE_NOTIFICATION


@pytest.mark.unit
def test_missing_flag_keeps_notification_channel():
    assert ride_offer_ring_mode({}) == RING_MODE_NOTIFICATION
