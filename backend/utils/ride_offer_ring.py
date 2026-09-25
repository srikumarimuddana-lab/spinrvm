"""Which Android channel a ride-offer notification rings on (migration 471).

Stamped on every ``new_ride_assignment`` payload as ``ring_mode`` so the
driver app's headless push handler knows without a settings fetch. The app
uses the alarm channel only when it also finds the native
``ride-offers-alarm-v1`` channel on the device; older builds ignore the field.
"""

from typing import Any, Mapping

RING_MODE_ALARM = "alarm"
RING_MODE_NOTIFICATION = "notification"


def ride_offer_ring_mode(settings: Mapping[str, Any]) -> str:
    """``"alarm"`` only when ``ride_offer_alarm_channel_enabled`` is exactly True."""
    if settings.get("ride_offer_alarm_channel_enabled") is True:
        return RING_MODE_ALARM
    return RING_MODE_NOTIFICATION
