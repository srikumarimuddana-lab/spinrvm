"""Regression: multi-stop rides must be priced on the through-route distance.

The fare quote (/rides/estimate) and the booked fare (POST /rides) both derive
distance_km from geo_utils.multi_leg_distance. Before this, distance_km was a
straight pickup→dropoff haversine that ignored stops, so adding a stop never
changed the km or the price. These tests pin the helper's contract.

Run:
    pytest backend/tests/test_multi_leg_distance.py -v
"""

from __future__ import annotations

import pytest

from backend.geo_utils import calculate_distance, multi_leg_distance

# Regina-area coordinates roughly matching the reported flow.
PICKUP = (50.4452, -104.6189)   # downtown Regina
STOP_EAST = (50.4700, -104.5400)  # Walmart east
DROPOFF_NORTH = (50.5000, -104.6000)  # Costco north


@pytest.mark.unit
def test_no_stops_equals_direct_distance():
    direct = calculate_distance(*PICKUP, *DROPOFF_NORTH)
    assert multi_leg_distance(*PICKUP, *DROPOFF_NORTH, None) == pytest.approx(direct)
    assert multi_leg_distance(*PICKUP, *DROPOFF_NORTH, []) == pytest.approx(direct)


@pytest.mark.unit
def test_stop_increases_distance():
    direct = calculate_distance(*PICKUP, *DROPOFF_NORTH)
    with_stop = multi_leg_distance(
        *PICKUP, *DROPOFF_NORTH,
        [{"address": "Walmart East", "lat": STOP_EAST[0], "lng": STOP_EAST[1]}],
    )
    # A detour through an off-axis stop is always at least as long as the
    # straight line (triangle inequality), and here strictly longer.
    assert with_stop > direct


@pytest.mark.unit
def test_distance_is_sum_of_legs():
    leg1 = calculate_distance(*PICKUP, *STOP_EAST)
    leg2 = calculate_distance(*STOP_EAST, *DROPOFF_NORTH)
    with_stop = multi_leg_distance(
        *PICKUP, *DROPOFF_NORTH,
        [{"lat": STOP_EAST[0], "lng": STOP_EAST[1]}],
    )
    assert with_stop == pytest.approx(leg1 + leg2)


@pytest.mark.unit
def test_placeholder_and_missing_stops_are_skipped():
    direct = calculate_distance(*PICKUP, *DROPOFF_NORTH)
    # Unfilled stop fields arrive from the rider app as (0, 0) placeholders, and
    # some carry no coords at all — neither may inflate the fare.
    polluted = [
        {"address": "", "lat": 0, "lng": 0},
        {"address": "no coords"},
        {"lat": None, "lng": None},
    ]
    assert multi_leg_distance(*PICKUP, *DROPOFF_NORTH, polluted) == pytest.approx(direct)


@pytest.mark.unit
def test_multiple_stops_are_ordered():
    stops = [
        {"lat": STOP_EAST[0], "lng": STOP_EAST[1]},
        {"lat": DROPOFF_NORTH[0] - 0.01, "lng": DROPOFF_NORTH[1] - 0.01},
    ]
    expected = (
        calculate_distance(*PICKUP, *STOP_EAST)
        + calculate_distance(STOP_EAST[0], STOP_EAST[1], stops[1]["lat"], stops[1]["lng"])
        + calculate_distance(stops[1]["lat"], stops[1]["lng"], *DROPOFF_NORTH)
    )
    assert multi_leg_distance(*PICKUP, *DROPOFF_NORTH, stops) == pytest.approx(expected)
