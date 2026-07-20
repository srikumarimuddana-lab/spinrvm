"""Receipt invariant tests — line items always sum to grand_total.

``grand_total`` on every rider-facing surface is produced by
``_sum_fare_breakdown`` over the ``fare_breakdown`` lines (get_ride,
ride history, invoices, tip updates). These property tests pin the
invariant that the returned total is the exact Decimal sum of the
rendered line items — the float accumulation it replaced could drift a
cent below the items the rider sees.
"""

import random
from decimal import Decimal

import pytest

pytestmark = pytest.mark.unit


def _dec(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


def _expected_total(lines) -> float:
    """Reference implementation: exact Decimal sum of rendered amounts, clamped at 0."""
    total = sum(
        (_dec(ln["amount"]) for ln in lines if ln.get("amount") is not None),
        Decimal("0"),
    )
    return float(max(Decimal("0"), total))


class TestSumFareBreakdown:
    def test_line_items_always_sum_to_grand_total_property(self):
        """Property: for random line lists, grand_total == exact sum of items."""
        from backend.routes.rides import _sum_fare_breakdown

        rng = random.Random(1234)
        for _ in range(500):
            lines = []
            for _i in range(rng.randrange(1, 9)):
                kind = rng.choice(["ride", "fee", "tax", "tip", "discount", "modifier"])
                if kind == "modifier":
                    lines.append({"label": "Surge (1.5×)", "amount": None, "type": "modifier"})
                    continue
                amount = rng.randrange(1, 10_000) / 100
                if kind == "discount":
                    amount = -amount
                lines.append({"label": kind, "amount": amount, "type": kind})

            assert _sum_fare_breakdown(lines) == _expected_total(lines), lines

    def test_build_breakdown_round_trip_sums_to_grand_total_property(self):
        """Property: lines built from a ride row always sum to the grand_total shown."""
        from backend.routes.rides import _build_fare_breakdown, _sum_fare_breakdown

        rng = random.Random(99)
        for _ in range(300):
            ride = {
                "base_fare": rng.randrange(200, 600) / 100,
                "distance_fare": rng.randrange(0, 3_000) / 100,
                "time_fare": rng.randrange(0, 1_500) / 100,
                "distance_km": rng.randrange(1, 400) / 10,
                "airport_fee": rng.choice([0, rng.randrange(0, 800) / 100]),
                "booking_fee": rng.choice([0, rng.randrange(0, 500) / 100]),
                "surge_multiplier": rng.choice([1, 1.25, 1.5, 2.5]),
                "area_fees_breakdown": [{"name": "Downtown fee", "calculated_value": rng.randrange(0, 300) / 100}],
                "tax_breakdown": {
                    "GST": {"amount": rng.randrange(0, 500) / 100, "rate": 5},
                    "PST": {"amount": rng.randrange(0, 600) / 100, "rate": 6},
                },
                "discount_amount": rng.choice([0, rng.randrange(0, 2_000) / 100]),
                "promo_code": "SAVE10",
                "tip_amount": rng.choice([0, rng.randrange(0, 1_000) / 100]),
            }
            lines = _build_fare_breakdown(ride)
            assert _sum_fare_breakdown(lines) == _expected_total(lines), ride

    def test_legacy_subcent_amount_rounds_half_up(self):
        """A legacy sub-cent line (2.675) must round HALF_UP to 2.68.

        The old float path returned 2.67: round(2.675, 2) rounds the
        nearest-double 2.67499… down — one cent below the rendered item.
        """
        from backend.routes.rides import _sum_fare_breakdown

        assert _sum_fare_breakdown([{"label": "Ride fare", "amount": "2.675", "type": "ride"}]) == 2.68

    def test_classic_float_drift_case(self):
        from backend.routes.rides import _sum_fare_breakdown

        lines = [
            {"label": "Ride fare", "amount": 0.1, "type": "ride"},
            {"label": "Booking fee", "amount": 0.2, "type": "fee"},
        ]
        assert _sum_fare_breakdown(lines) == 0.3

    def test_modifier_and_garbage_lines_skipped(self):
        from backend.routes.rides import _sum_fare_breakdown

        lines = [
            {"label": "Ride fare", "amount": 10.00, "type": "ride"},
            {"label": "Surge (1.5×)", "amount": None, "type": "modifier"},
            {"label": "Bad", "amount": "not-a-number", "type": "fee"},
            "not-a-dict",
        ]
        assert _sum_fare_breakdown(lines) == 10.00

    def test_negative_total_clamped_to_zero(self):
        from backend.routes.rides import _sum_fare_breakdown

        lines = [
            {"label": "Ride fare", "amount": 5.00, "type": "ride"},
            {"label": "Promo", "amount": -8.00, "type": "discount"},
        ]
        assert _sum_fare_breakdown(lines) == 0.0


class TestMinimumFareBreakdown:
    """On a minimum-fare ride the line items must sum to the charged total and
    the ride-fare line must equal the driver's share (total − booking − airport).

    The estimate builder already folds the uplift into 'Ride fare'; this pins
    the same truth for the get_ride / history / receipt / payments surfaces that
    go through _build_fare_breakdown, which previously itemised only the
    un-clamped components and under-summed the amount actually charged.
    """

    def _min_ride(self, **overrides):
        # Tiny ride: base+dist+time+booking = 5.90, floored up to 8.00.
        ride = {
            "base_fare": 3.50,
            "distance_fare": 0.15,
            "time_fare": 0.25,
            "booking_fee": 2.00,
            "airport_fee": 0,
            "distance_km": 0.1,
            "surge_multiplier": 1,
            "total_fare": 8.00,
        }
        ride.update(overrides)
        return ride

    def test_min_fare_lines_sum_to_charged_total(self):
        from backend.routes.rides import _build_fare_breakdown, _sum_fare_breakdown

        lines = _build_fare_breakdown(self._min_ride())
        # No fees/tax/tip/discount → the charged total is total_fare exactly.
        assert _sum_fare_breakdown(lines) == 8.00

    def test_ride_line_equals_driver_share_on_min_fare_ride(self):
        from backend.routes.rides import _build_fare_breakdown

        lines = _build_fare_breakdown(self._min_ride())
        ride_line = next(ln for ln in lines if ln["type"] == "ride")
        # Driver keeps total − booking − airport = 8.00 − 2.00 − 0.
        assert ride_line["amount"] == 6.00

    def test_min_fare_with_surge_sums_to_charged_total(self):
        from backend.routes.rides import _build_fare_breakdown, _sum_fare_breakdown

        # Surged (2×) but still floored to 20.00; surge contributed $0.
        ride = self._min_ride(distance_fare=3.00, time_fare=1.00, distance_km=1, surge_multiplier=2, total_fare=20.00)
        lines = _build_fare_breakdown(ride)
        assert _sum_fare_breakdown(lines) == 20.00
        ride_line = next(ln for ln in lines if ln["type"] == "ride")
        assert ride_line["amount"] == 18.00  # 20.00 − booking 2.00

    def test_legacy_row_without_total_fare_unchanged(self):
        from backend.routes.rides import _build_fare_breakdown

        ride = {
            "base_fare": 3.50,
            "distance_fare": 5.00,
            "time_fare": 2.00,
            "booking_fee": 2.00,
            "distance_km": 4,
            "surge_multiplier": 1,
            # no total_fare — legacy row, uplift must be 0
        }
        lines = _build_fare_breakdown(ride)
        ride_line = next(ln for ln in lines if ln["type"] == "ride")
        assert ride_line["amount"] == 10.50  # base + dist + time, unchanged

    def test_min_fare_breakdown_sums_to_charged_total_property(self):
        """Property: min-clamped rows still sum to the charged total (total_fare)."""
        from backend.routes.rides import _build_fare_breakdown, _sum_fare_breakdown

        rng = random.Random(4242)
        for _ in range(300):
            base = rng.randrange(200, 600) / 100
            dist = rng.randrange(0, 3_000) / 100
            time = rng.randrange(0, 1_500) / 100
            booking = rng.choice([0, rng.randrange(0, 500) / 100])
            airport = rng.choice([0, rng.randrange(0, 800) / 100])
            surge = rng.choice([1, 1.25, 1.5, 2.5])
            components = _dec(base) + _dec(dist) + _dec(time) + _dec(booking) + _dec(airport)
            uplift = rng.choice([Decimal("0"), _dec(rng.randrange(0, 1_500) / 100)])
            total_fare = float(components + uplift)
            ride = {
                "base_fare": base,
                "distance_fare": dist,
                "time_fare": time,
                "booking_fee": booking,
                "airport_fee": airport,
                "distance_km": rng.randrange(1, 400) / 10,
                "surge_multiplier": surge,
                "total_fare": total_fare,
                # no fees/tax/tip/discount → charged total == total_fare
            }
            lines = _build_fare_breakdown(ride)
            assert _sum_fare_breakdown(lines) == round(total_fare, 2), ride
