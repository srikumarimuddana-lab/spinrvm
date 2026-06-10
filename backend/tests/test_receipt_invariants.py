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
