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
        km, basis = resolve_measured_distance_km(None, coverage=0.0, planned_km=1.8, straight_line_km=0.7)
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


class TestImplausibilityCeiling:
    """2026-09-12, SPR-EG7X86 (Android, all 564 points present, worst gap 35 s):
    the finalizer published 14.86 km for a 9.2 km trip — 11.216 km map-matched
    plus 3.893 km of routed connectors for 99 s of gaps. Booking said 9.21 km,
    the spike-filtered GPS sum said 9.17 km. There was a floor for impossibly
    SHORT results and nothing for impossibly LONG ones."""

    def test_reconstruction_far_above_both_references_is_capped_to_planned(self):
        km, basis = resolve_measured_distance_km(
            _recon(11.216, routed=3.893),
            coverage=0.997,
            planned_km=9.21,
            straight_line_km=6.4,
            gps_km=9.17,
        )
        assert km == 9.21
        assert basis == "planned_capped"

    def test_real_detour_passes_because_the_gps_sum_grows_with_it(self):
        # Driver took the long way round: the GPS sum agrees, so the matched
        # distance is trusted even though it is far above the booking.
        km, basis = resolve_measured_distance_km(
            _recon(13.0, routed=0.5), coverage=0.95, planned_km=9.21, straight_line_km=6.4, gps_km=13.2
        )
        assert km == 13.5
        assert basis == "observed"

    def test_ceiling_uses_the_larger_of_the_two_references(self):
        km, basis = resolve_measured_distance_km(
            _recon(11.0), coverage=0.95, planned_km=5.0, straight_line_km=3.0, gps_km=9.0
        )
        assert km == 11.0  # 11.0 <= 1.3 * 9.0
        assert basis == "observed"

    def test_ceiling_is_configurable(self):
        km, basis = resolve_measured_distance_km(
            _recon(11.0), coverage=0.95, planned_km=9.0, straight_line_km=6.0, gps_km=9.0, max_vs_reference=1.1
        )
        assert km == 9.0
        assert basis == "planned_capped"

    def test_no_booked_distance_skips_the_ceiling(self):
        # The GPS chord sum is a lower bound (holes shorten it), so on its own it
        # must never veto a road reconstruction: a sparse 4-point trail summing
        # to 0.38 km with a 1.55 km road reconstruction is the hole-bridging
        # case the resolver exists for, not an overshoot.
        km, basis = resolve_measured_distance_km(
            _recon(1.55), coverage=0.9, planned_km=0.0, straight_line_km=0.0, gps_km=0.38
        )
        assert km == 1.55
        assert basis == "observed"

    def test_locked_phone_ride_with_routed_gaps_still_resolves(self):
        # SPR-YNYA93 (iOS, two 4-min locked-phone holes bridged by road
        # connectors): 4.941 matched + 5.04 routed against a 10.08 km booking
        # and a 9.97 km GPS sum — plausible, kept, flagged reconstructed.
        km, basis = resolve_measured_distance_km(
            _recon(4.941, routed=5.04), coverage=0.514, planned_km=10.08, straight_line_km=7.9, gps_km=9.97
        )
        assert km == 9.981
        assert basis == "reconstructed"
