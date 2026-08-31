"""Regression test for #4604 finding 2: float-before-round on the surge
multiplier feeding the signed estimate token and the rider-facing estimate
response.

backend/routes/rides/estimates.py used `round(float(surge), 2)`, converting
Decimal -> float BEFORE rounding -- the opposite of the codebase's
documented-safe order (`float(_round(...))`, see `_f()`'s docstring). Clean
auto-tier surge values (1.0, 1.25, 1.5, 1.75, 2.0, 2.5) round-trip through
either order identically, but a non-2dp value (reachable via the admin
manual-override range 1.0-10.0) is a genuine precision-loss vector: some
Decimals don't have an exact binary float representation, so converting to
float first can shift which way the subsequent round() lands.
"""

from __future__ import annotations

from decimal import Decimal

from backend.routes.rides._shared import _f, _round


def test_round_then_convert_differs_from_convert_then_round_for_a_known_value():
    """Decimal('2.675') is the textbook case: its nearest double is
    2.67499999999999982..., so round(float(...), 2) rounds DOWN to 2.67 --
    the wrong answer for a value that should round to 2.68 under
    ROUND_HALF_UP on the exact decimal. This proves the two orders are not
    interchangeable, i.e. that the fix is not a no-op reshuffle."""
    surge = Decimal("2.675")

    buggy_order = round(float(surge), 2)
    fixed_order = _f(_round(surge))

    assert buggy_order == 2.67
    assert fixed_order == 2.68
    assert buggy_order != fixed_order


def test_clean_two_dp_auto_tier_values_are_unaffected():
    """Every real auto-tier value (CLAUDE.md's surge table) round-trips
    identically under both orders -- this fix changes behavior only for the
    non-2dp admin-override precision-loss vector, not for normal traffic."""
    for tier in ("1.0", "1.25", "1.5", "1.75", "2.0", "2.5"):
        surge = Decimal(tier)
        assert round(float(surge), 2) == _f(_round(surge))
