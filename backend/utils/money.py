"""Decimal-correct money conversions.

Centralizes the dollar↔cent conversions that show up in Stripe code paths
so float drift can't silently undercharge a rider. Two pitfalls these
helpers exist to prevent:

  - ``int(29.99 * 100)`` returns ``2998`` (float drift + truncation),
    not ``2999``. Riders charged $29.98 instead of $29.99 is a real bug
    we caught in payments.py.
  - ``amount_cents / 100`` returns a float that may not round-trip
    cleanly through Postgres NUMERIC for arbitrary cent values.

All conversions go through ``Decimal(str(...))`` so floats are coerced
via their printed form (the canonical 2-dp representation), avoiding the
binary-float artifacts that ``Decimal(float)`` would expose.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Union

Money = Union[int, float, str, Decimal]

_TWO_PLACES = Decimal("0.01")
_HUNDRED = Decimal("100")


def to_decimal(amount: Money) -> Decimal:
    """Coerce any numeric / string input to a 2-dp Decimal.

    ``Decimal(str(0.1))`` → ``Decimal('0.1')`` (clean), whereas
    ``Decimal(0.1)`` → ``Decimal('0.10000000000000000555…')`` (drift).
    """
    return Decimal(str(amount)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def dollars_to_cents(amount: Money) -> int:
    """Convert a dollar amount to integer cents (HALF_UP).

    Stripe and most payment processors charge in the smallest currency
    unit. This is the only correct conversion — ``int(amount * 100)``
    silently undercharges on values like 29.99.
    """
    quantized = Decimal(str(amount)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    return int((quantized * _HUNDRED).to_integral_value(rounding=ROUND_HALF_UP))


def cents_to_dollars(cents: int) -> Decimal:
    """Convert integer cents back to a 2-dp Decimal dollar amount.

    Returns Decimal so callers can keep arithmetic exact. If a float is
    needed at a serialization boundary, the caller converts explicitly.
    """
    return (Decimal(int(cents)) / _HUNDRED).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
