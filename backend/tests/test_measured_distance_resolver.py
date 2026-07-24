"""Tests for resolve_measured_distance_km (route_finalizer).

This resolver replaced the finalizer's "write observed_distance_km" logic that,
for a trip whose GPS died mid-route, published only the jitter near the ends —
the number that mis-reported the incident ride. It weighs observed + routed
(road-following) connector distance against coverage and the physical crow-flies
floor, and falls back to the booked distance instead of a wrong GPS figure.
"""

from __future__ import annotations

from utils.route_finalizer import resolve_measured_distance_km


def _recon(observed, routed=0.0, straight=0.0):
    return {
        "observed_distance_km": observed,
        "routed_connector_distance_km": routed,
        "straight_connector_distance_km": straight,
    }


class TestResolveMeasuredDistance:
    def test_good_coverage_uses_observed_plus_routed(self):
        km, basis = resolve_measured_distance_km(
            _recon(1.5, routed=0.3), coverage=0.9, planned_km=1.8, straight_line_km=1.0
        )
        assert km == 1.8
        assert basis == "observed"

    def test_mid_trip_dropout_bridged_by_routed_connector(self):
        # The incident shape after jitter filtering: little observed GPS (0.5 km
        # near pickup), but a road-following connector bridged the missing middle
        # (1.3 km) → the real ~1.8 km road distance, flagged reconstructed.
        km, basis = resolve_measured_distance_km(
            _recon(0.5, routed=1.3), coverage=0.3, planned_km=1.8, straight_line_km=0.7
        )
        assert km == 1.8
        assert basis == "reconstructed"

    def test_impossible_low_distance_falls_back_to_planned(self):
        # Gaps couldn't be routed; the candidate is below the crow-flies floor —
        # physically impossible, so publish the booked distance, not garbage.
        km, basis = resolve_measured_distance_km(
            _recon(0.3, routed=0.0, straight=0.1), coverage=0.2, planned_km=1.8, straight_line_km=0.7
        )
        assert km == 1.8
        assert basis == "planned_estimated"

    def test_straight_connector_dominated_falls_back_to_planned(self):
        # Above the floor, but most of the "distance" is blind straight-line
        # gap fill (>25% share) → not a trustworthy road distance.
        km, basis = resolve_measured_distance_km(
            _recon(1.0, routed=0.0, straight=1.0), coverage=0.3, planned_km=1.8, straight_line_km=0.7
        )
        assert km == 1.8
        assert basis == "planned_estimated"

    def test_none_reconstruction_is_planned(self):
        km, basis = resolve_measured_distance_km(
            None, coverage=0.0, planned_km=1.8, straight_line_km=0.7
        )
        assert km == 1.8
        assert basis == "planned_estimated"

    def test_no_endpoints_skips_floor(self):
        # straight_line_km == 0 (missing endpoints) → floor is not applied; a
        # small but fully-observed distance is still trusted.
        km, basis = resolve_measured_distance_km(
            _recon(0.4, routed=0.0), coverage=0.9, planned_km=1.8, straight_line_km=0.0
        )
        assert km == 0.4
        assert basis == "observed"

    def test_straight_connector_distance_never_counted_in_value(self):
        # A 1.5 km observed+routed candidate with 0.2 km of straight fill (13%,
        # under the share cap) is trusted, but the straight fill is excluded.
        km, basis = resolve_measured_distance_km(
            _recon(1.2, routed=0.3, straight=0.2), coverage=0.9, planned_km=1.4, straight_line_km=1.0
        )
        assert km == 1.5  # 1.2 + 0.3, NOT +0.2
        assert basis == "observed"
