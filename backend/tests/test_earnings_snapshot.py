"""Unit tests for utils/earnings_snapshot.py — Decimal-safe T4A-feeding math.

The driver_earnings_snapshot total feeds the year-end T4A summary. These tests
pin the invariant that ``total`` is the exact Decimal sum of the components
(and of the rendered ``lines``), which the previous float-addition sites
(drivers.py complete_ride, rides.py tip paths, rides.py rider_complete_ride)
could not guarantee.
"""

import os
import random
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.earnings_snapshot import build_earnings_snapshot, fare_share  # noqa: E402

pytestmark = pytest.mark.unit


def _dec(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


class TestSnapshotTotalInvariant:
    def test_total_equals_sum_of_components_property(self):
        """Property: for random cent-quantized inputs, total == exact component sum."""
        rng = random.Random(42)
        for _ in range(500):
            fare = rng.randrange(0, 20_000) / 100  # $0.00 – $200.00
            tip = rng.randrange(0, 5_000) / 100
            incentive = rng.randrange(0, 2_000) / 100
            tax = rng.randrange(0, 3_000) / 100
            cancel_fee = rng.randrange(0, 1_000) / 100

            snap = build_earnings_snapshot(fare=fare, tip=tip, incentive=incentive, tax=tax, cancel_fee=cancel_fee)

            expected = _dec(fare) + _dec(tip) + _dec(incentive) + _dec(tax) + _dec(cancel_fee)
            assert _dec(snap["total"]) == expected, (
                f"total drifted: fare={fare} tip={tip} incentive={incentive} "
                f"tax={tax} cancel_fee={cancel_fee} → {snap['total']} != {expected}"
            )

    def test_total_equals_sum_of_lines_property(self):
        """Property: total always equals the sum of the rendered line amounts."""
        rng = random.Random(7)
        for _ in range(500):
            components = {k: rng.randrange(0, 10_000) / 100 for k in ("fare", "tip", "incentive", "tax", "cancel_fee")}
            snap = build_earnings_snapshot(**components)
            lines_sum = sum((_dec(ln["amount"]) for ln in snap["lines"]), Decimal("0"))
            assert _dec(snap["total"]) == lines_sum

    def test_classic_float_drift_case(self):
        """0.1 + 0.2 must produce 0.30, not 0.30000000000000004."""
        snap = build_earnings_snapshot(fare=0.1, tip=0.2)
        assert snap["total"] == 0.3
        assert _dec(snap["total"]) == Decimal("0.30")

    def test_legacy_subcent_values_round_half_up(self):
        """Legacy rows may hold sub-cent values from old float math.

        Decimal('10.985') quantizes HALF_UP to 10.99; the old float path
        (round(10.985, 2)) returned 10.98 because the nearest double to
        10.985 sits just below the half — a one-cent T4A underreport.
        """
        snap = build_earnings_snapshot(fare="10.985")
        assert snap["fare"] == 10.99
        assert snap["total"] == 10.99


class TestSnapshotShape:
    def test_zero_components_omitted_from_lines(self):
        snap = build_earnings_snapshot(fare=12.50)
        assert [ln["type"] for ln in snap["lines"]] == ["fare"]
        assert snap["tip"] == 0.0
        assert snap["tax"] == 0.0

    def test_none_components_treated_as_zero(self):
        snap = build_earnings_snapshot(fare=None, tip=None)
        assert snap["total"] == 0.0
        assert snap["lines"] == [{"label": "Ride Fare", "amount": 0.0, "type": "fare"}]

    def test_all_components_present_in_order(self):
        snap = build_earnings_snapshot(fare=10, tip=2, incentive=1, tax=0.5, cancel_fee=3)
        assert [ln["type"] for ln in snap["lines"]] == [
            "fare",
            "tip",
            "incentive",
            "tax",
            "cancel_fee",
        ]
        assert snap["total"] == 16.5

    def test_decimal_inputs_accepted(self):
        snap = build_earnings_snapshot(fare=Decimal("19.99"), tip=Decimal("3.01"))
        assert snap["total"] == 23.0


class TestFareShare:
    """fare_share is the driver's ride-fare portion for the snapshot: the
    driver keeps total_fare minus the platform's booking + airport fees (0%
    commission), including any minimum-fare uplift."""

    def test_fare_share_includes_minimum_fare_uplift(self):
        # total_fare 8.00 clamped up; components base+dist+time = 3.90.
        share = fare_share(total_fare=8.00, booking_fee=2.00, airport_fee=0, fallback_components=3.90)
        assert share == Decimal("6.00")  # 8.00 − 2.00, i.e. driver keeps the uplift

    def test_fare_share_excludes_booking_and_airport(self):
        share = fare_share(total_fare=30.00, booking_fee=2.00, airport_fee=5.00, fallback_components=23.00)
        assert share == Decimal("23.00")  # 30.00 − 2.00 − 5.00

    def test_fare_share_falls_back_to_components_for_legacy_rows(self):
        # No total_fare (legacy row) → itemised component sum.
        share = fare_share(total_fare=0, booking_fee=2.00, airport_fee=0, fallback_components=17.25)
        assert share == Decimal("17.25")

    def test_fare_share_feeds_snapshot_ride_fare_line(self):
        share = fare_share(total_fare=8.00, booking_fee=2.00, airport_fee=0, fallback_components=3.90)
        snap = build_earnings_snapshot(fare=share)
        assert snap["fare"] == 6.00
        assert snap["lines"][0] == {"label": "Ride Fare", "amount": 6.00, "type": "fare"}
