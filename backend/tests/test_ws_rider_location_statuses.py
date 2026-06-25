"""Regression: the rider must receive the driver's live location during the
"driver arriving" phase so the car marker moves on their map.

The WS receive loop previously skipped `driver_accepted` when forwarding
driver_location_update to riders ("rider hasn't been told yet"), which froze the
car for the entire arriving phase — and it still forwarded the pre-acceptance
`driver_assigned` state (leaking an un-accepted offer's driver location). The
forward set must START at driver_accepted and EXCLUDE pre-acceptance states.
"""

from __future__ import annotations

from routes.websocket import _RIDER_LOCATION_STATUSES


def test_car_moves_for_confirmed_states():
    # Accepted → arriving → trip: the rider's car marker must update.
    assert "driver_accepted" in _RIDER_LOCATION_STATUSES
    assert "driver_arrived" in _RIDER_LOCATION_STATUSES
    assert "in_progress" in _RIDER_LOCATION_STATUSES


def test_no_leak_before_acceptance():
    # Pre-acceptance: never forward an offered-but-unaccepted driver's location.
    assert "driver_assigned" not in _RIDER_LOCATION_STATUSES
    assert "searching" not in _RIDER_LOCATION_STATUSES
