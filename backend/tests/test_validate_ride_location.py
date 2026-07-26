"""validate_ride_location proximity floor.

The pickup≠dropoff rule used to be exact float equality, so a ride whose
endpoints sat 80 m apart in the same parking lot was accepted and charged a
minimum fare (rider standing at Walmart quoted 0.08 km to the same Walmart).
The floor is now 25 m — GPS noise, never a trip — while the 25 m–100 m band
stays bookable so the client/assistant confirmable same-place flow works.
"""

import pytest
from fastapi import HTTPException

from backend.validators import validate_ride_location

PICKUP = (50.40790, -104.65010)


def _dropoff_north(meters: float) -> tuple:
    # 1 degree of latitude ≈ 111 km.
    return (PICKUP[0] + meters / 111_000, PICKUP[1])


class TestProximityFloor:
    def test_identical_coordinates_rejected(self):
        with pytest.raises(HTTPException) as exc:
            validate_ride_location(*PICKUP, *PICKUP)
        assert exc.value.status_code == 400
        assert "cannot be the same" in exc.value.detail

    def test_10m_apart_rejected(self):
        with pytest.raises(HTTPException):
            validate_ride_location(*PICKUP, *_dropoff_north(10))

    def test_10m_apart_returns_false_without_exception(self):
        ok, coords = validate_ride_location(*PICKUP, *_dropoff_north(10), raise_exception=False)
        assert ok is False and coords is None

    def test_80m_apart_allowed_for_the_confirmable_band(self):
        ok, coords = validate_ride_location(*PICKUP, *_dropoff_north(80))
        assert ok is True
        assert coords == (*PICKUP, *_dropoff_north(80))

    def test_normal_trip_allowed(self):
        ok, _ = validate_ride_location(*PICKUP, 50.4497, -104.5345)
        assert ok is True
