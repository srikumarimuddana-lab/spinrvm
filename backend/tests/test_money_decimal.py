"""Pin Decimal correctness for dollar↔cent conversions.

These tests document the float-drift bugs that motivated
``backend/utils/money.py``. They double as regression tests for the
naive arithmetic patterns we removed from payments.py / webhooks.py /
stripe_charge.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.utils.money import cents_to_dollars, dollars_to_cents, to_decimal

# ── Documenting the bugs ────────────────────────────────────────────────


class TestNaiveArithmeticBugs:
    """These assertions show *why* we cannot use raw float math for money.

    Each one is an undercharge waiting to happen. Keeping them as tests
    means a future contributor who reverts to ``int(amount * 100)`` will
    see them flip to passing in their changeset.
    """

    @pytest.mark.parametrize(
        "amount,naive_cents,correct_cents",
        [
            # Each of these is a binary-float that prints as the
            # nominal value but is actually stored just below it, so
            # ``* 100`` lands at e.g. 28.999… and ``int`` truncates.
            ("0.29", 28, 29),
            ("0.57", 56, 57),
            ("1.13", 112, 113),
            ("2.01", 200, 201),
            ("17.81", 1780, 1781),
            ("77.49", 7748, 7749),
        ],
    )
    def test_int_truncation_undercharges(self, amount: str, naive_cents: int, correct_cents: int):
        """``int(float * 100)`` truncates a binary-float that lands just below an integer cent."""
        f = float(amount)
        assert int(f * 100) == naive_cents  # the bug
        assert dollars_to_cents(amount) == correct_cents  # the fix

    def test_cents_to_dollars_returns_exact_decimal(self):
        """``cents / 100`` in float is lossy for most cent values; Decimal is exact."""
        assert cents_to_dollars(12345) == Decimal("123.45")
        assert cents_to_dollars(10010) == Decimal("100.10")
        assert cents_to_dollars(1) == Decimal("0.01")


# ── Helper contracts ────────────────────────────────────────────────────


class TestToDecimal:
    @pytest.mark.parametrize(
        "amount,expected",
        [
            (29.99, Decimal("29.99")),
            ("29.99", Decimal("29.99")),
            (Decimal("29.99"), Decimal("29.99")),
            (0, Decimal("0.00")),
            ("0.475", Decimal("0.48")),  # HALF_UP
            ("0.485", Decimal("0.49")),  # HALF_UP (not banker's)
            ("0.444", Decimal("0.44")),
        ],
    )
    def test_quantize_half_up(self, amount, expected: Decimal):
        assert to_decimal(amount) == expected

    def test_negative_amount_round_trips(self):
        assert to_decimal("-12.5") == Decimal("-12.50")


class TestDollarsToCents:
    @pytest.mark.parametrize(
        "amount,expected",
        [
            ("0", 0),
            ("0.01", 1),
            ("1.00", 100),
            ("29.99", 2999),
            ("100.10", 10010),
            (29.99, 2999),
            (Decimal("29.99"), 2999),
            ("0.475", 48),  # HALF_UP first, then * 100
            ("999.99", 99999),
        ],
    )
    def test_known_amounts(self, amount, expected: int):
        assert dollars_to_cents(amount) == expected

    def test_returns_int(self):
        assert isinstance(dollars_to_cents("29.99"), int)


class TestCentsToDollars:
    @pytest.mark.parametrize(
        "cents,expected",
        [
            (0, Decimal("0.00")),
            (1, Decimal("0.01")),
            (100, Decimal("1.00")),
            (2999, Decimal("29.99")),
            (10010, Decimal("100.10")),
            (99999, Decimal("999.99")),
        ],
    )
    def test_known_cents(self, cents: int, expected: Decimal):
        assert cents_to_dollars(cents) == expected

    def test_round_trip_dollars_cents(self):
        for s in ["0.01", "1.00", "29.99", "100.10", "999.99", "5000.00"]:
            assert cents_to_dollars(dollars_to_cents(s)) == Decimal(s)


# ── Stripe-amount realism ──────────────────────────────────────────────


class TestStripeAmounts:
    """Sanity-check the conversion for amounts Spinr actually sees in prod.

    The naive ``int(round(float(amount) * 100))`` works for these because
    of the round, but ``int(amount * 100)`` (no round) does not — see
    the bug class above.
    """

    @pytest.mark.parametrize(
        "fare_dollars,expected_cents",
        [
            ("8.50", 850),  # near minimum_fare
            ("12.75", 1275),
            ("29.99", 2999),
            ("47.83", 4783),
            ("100.00", 10000),
            ("250.00", 25000),  # corporate top-up tier
            ("500.00", 50000),
            ("1000.00", 100000),
        ],
    )
    def test_realistic_fares(self, fare_dollars: str, expected_cents: int):
        assert dollars_to_cents(fare_dollars) == expected_cents
