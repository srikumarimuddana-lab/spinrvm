"""Decimal-safe driver earnings snapshot assembly.

``rides.driver_earnings_snapshot`` (migration 104) freezes the driver's
earnings breakdown at completion and is amended when a tip lands. It feeds
the driver earnings screens and the year-end T4A summary, so the component
sum must be exact — float addition here drifts cents into a tax document.

All four write sites (driver complete_ride, rider_complete_ride, and the two
tip paths in routes/rides.py) build the snapshot through this module so the
invariant "``total`` equals the sum of ``lines`` amounts" holds everywhere.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict

try:
    from .money import Money, to_decimal
except ImportError:
    from utils.money import Money, to_decimal  # type: ignore


def fare_share(
    total_fare: "Money | None",
    booking_fee: "Money | None",
    airport_fee: "Money | None",
    *,
    fallback_components: "Money | None" = 0,
) -> Decimal:
    """The driver's ride-fare share for the earnings snapshot.

    The driver keeps everything the rider is charged for the ride except the
    platform's booking + airport fees (0% commission) — including any
    minimum-fare uplift. Derive it from ``total_fare`` so a minimum-fare ride
    books the full clamped amount, matching the receipt's ride line and the
    ``driver_earnings`` column. Legacy rows without a ``total_fare`` fall back
    to the itemised component sum (base + distance + time).
    """
    total_d = to_decimal(total_fare or 0)
    if total_d > 0:
        return total_d - to_decimal(booking_fee or 0) - to_decimal(airport_fee or 0)
    return to_decimal(fallback_components or 0)


def build_earnings_snapshot(
    fare: "Money | None",
    tip: "Money | None" = 0,
    incentive: "Money | None" = 0,
    tax: "Money | None" = 0,
    cancel_fee: "Money | None" = 0,
) -> Dict[str, Any]:
    """Build the ``driver_earnings_snapshot`` dict with an exact Decimal total.

    Each component is quantized to cents (HALF_UP) individually and ``total``
    is their exact Decimal sum, so ``snapshot["total"]`` always equals the sum
    of the ``lines`` amounts. Floats appear only at the JSONB serialization
    boundary.
    """
    fare_d = to_decimal(fare or 0)
    tip_d = to_decimal(tip or 0)
    incentive_d = to_decimal(incentive or 0)
    tax_d = to_decimal(tax or 0)
    cancel_fee_d = to_decimal(cancel_fee or 0)
    total_d = fare_d + tip_d + incentive_d + tax_d + cancel_fee_d

    lines = [{"label": "Ride Fare", "amount": float(fare_d), "type": "fare"}]
    if tip_d > 0:
        lines.append({"label": "Tip", "amount": float(tip_d), "type": "tip"})
    if incentive_d > 0:
        lines.append({"label": "Area Boost", "amount": float(incentive_d), "type": "incentive"})
    if tax_d > 0:
        lines.append({"label": "Tax", "amount": float(tax_d), "type": "tax"})
    if cancel_fee_d > 0:
        lines.append({"label": "Cancel Fee", "amount": float(cancel_fee_d), "type": "cancel_fee"})

    return {
        "fare": float(fare_d),
        "tip": float(tip_d),
        "incentive": float(incentive_d),
        "tax": float(tax_d),
        "cancel_fee": float(cancel_fee_d),
        "total": float(total_d),
        "lines": lines,
    }
