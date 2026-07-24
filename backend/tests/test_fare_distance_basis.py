"""Tests for road-vs-haversine fare distance selection.

`select_fare_distance` (backend/routes/rides/_shared.py) is the arbiter that
stops fares being priced on straight-line haversine (0.7 km) when the real
road route is longer (1.8 km). It prefers the Directions road distance behind
a sanity band, and the estimate token carries the chosen distance/basis to
booking so the rider is charged exactly what they were shown.
"""

from __future__ import annotations

from routes.rides._shared import (
    _road_distance_plausible,
    resolve_booking_distance,
    select_fare_distance,
)
from utils.estimate_token import sign_estimate_token, verify_estimate_token

# Reported incident: crow-flies 0.7 km, real road route 1.8 km.
HAVERSINE = 0.7
ROAD = 1.8


class TestRoadDistancePlausible:
    def test_road_within_band_is_plausible(self):
        assert _road_distance_plausible(HAVERSINE, ROAD) is True

    def test_road_none_is_implausible(self):
        assert _road_distance_plausible(HAVERSINE, None) is False

    def test_road_shorter_than_crowflies_rejected(self):
        # A road route materially shorter than the straight line is impossible.
        assert _road_distance_plausible(1.0, 0.5) is False

    def test_road_more_than_3x_rejected(self):
        # Directions routed somewhere wrong (>3x) — distrust it.
        assert _road_distance_plausible(1.0, 3.5) is False

    def test_degenerate_zero_haversine_trusts_small_road(self):
        assert _road_distance_plausible(0.0, 0.4) is True
        assert _road_distance_plausible(0.0, 5.0) is False


class TestSelectFareDistance:
    def test_road_mode_prices_on_road(self):
        km, basis = select_fare_distance(HAVERSINE, ROAD, mode="road")
        assert km == 1.8
        assert basis == "road_route"

    def test_road_mode_falls_back_when_road_missing(self):
        km, basis = select_fare_distance(HAVERSINE, None, mode="road")
        assert km == 0.7
        assert basis == "haversine_fallback"

    def test_road_mode_falls_back_when_road_implausible(self):
        km, basis = select_fare_distance(1.0, 9.0, mode="road")
        assert km == 1.0
        assert basis == "haversine_fallback"

    def test_shadow_mode_always_bills_haversine(self):
        # Even with a valid road distance, shadow bills haversine (no money
        # change) — the road value is only logged/metered by the caller.
        km, basis = select_fare_distance(HAVERSINE, ROAD, mode="shadow")
        assert km == 0.7
        assert basis == "haversine"

    def test_haversine_mode_is_kill_switch(self):
        km, basis = select_fare_distance(HAVERSINE, ROAD, mode="haversine")
        assert km == 0.7
        assert basis == "haversine"

    def test_unknown_mode_degrades_to_haversine(self):
        km, basis = select_fare_distance(HAVERSINE, ROAD, mode="bogus")
        assert basis == "haversine"


def _token_kwargs():
    return dict(
        rider_id="rider_fd",
        vehicle_type_id="economy",
        pickup_lat=52.13,
        pickup_lng=-106.67,
        dropoff_lat=52.12,
        dropoff_lng=-106.65,
    )


class TestEstimateTokenCarriesDistance:
    def test_distance_roundtrips_through_token(self):
        tok = sign_estimate_token(
            surge_multiplier=1.0,
            total_fare=6.10,
            distance_km=1.8,
            distance_basis="road_route",
            **_token_kwargs(),
        )
        payload = verify_estimate_token(tok, **_token_kwargs())
        assert payload["dk"] == 1.8
        assert payload["db"] == "road_route"

    def test_token_without_distance_omits_fields(self):
        # Back-compat: callers that don't pass distance produce the old shape,
        # so booking safely falls back to recomputing haversine.
        tok = sign_estimate_token(surge_multiplier=1.0, total_fare=6.10, **_token_kwargs())
        payload = verify_estimate_token(tok, **_token_kwargs())
        assert "dk" not in payload
        assert "db" not in payload


class TestResolveBookingDistance:
    def test_charges_quoted_road_distance_from_token(self):
        payload = {"dk": 1.8, "db": "road_route"}
        km, mins, basis = resolve_booking_distance(HAVERSINE, payload)
        assert km == 1.8
        assert basis == "road_route"
        # Duration derives from the chosen distance (same model /estimate uses).
        assert mins == int(1.8 / 30 * 60) + 5

    def test_falls_back_to_haversine_without_token(self):
        km, mins, basis = resolve_booking_distance(HAVERSINE, None)
        assert km == 0.7
        assert basis == "haversine"
        assert mins == int(0.7 / 30 * 60) + 5

    def test_falls_back_when_token_has_no_distance(self):
        # Old token (surge only) → booking recomputes haversine.
        km, _mins, basis = resolve_booking_distance(HAVERSINE, {"sm": 1.0})
        assert km == 0.7
        assert basis == "haversine"

    def test_zero_or_negative_token_distance_ignored(self):
        km, _mins, basis = resolve_booking_distance(HAVERSINE, {"dk": 0, "db": "road_route"})
        assert km == 0.7
        assert basis == "haversine"

    def test_haversine_fallback_basis_preserved_from_token(self):
        # When /estimate itself fell back to haversine, dk carries that value
        # and booking honours the same basis label.
        payload = {"dk": 0.7, "db": "haversine_fallback"}
        km, _mins, basis = resolve_booking_distance(HAVERSINE, payload)
        assert km == 0.7
        assert basis == "haversine_fallback"
