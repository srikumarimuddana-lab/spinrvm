"""Minimum-tip rule shared by every tip entry point.

A rider may give no tip ($0) or at least settings.min_tip_amount (migration
438; ships at 0 = off, to be set to $1.00 once the rider app that pre-checks
it is live). Anything in between is rejected with a 400 that the updated rider
app shows as-is, so the rider stays on the tip screen and fixes the amount —
it is never silently dropped. Background: on a card ride a tip over the
authorization buffer is a separate Stripe charge, and Stripe rejects charges
under $0.50 CAD, so a $0.05 tip used to vanish (never charged, never paid to
the driver). min_tip_amount = 0 switches the rule off without a deploy.

Entry points: POST /rides/{id}/tip, POST /rides/{id}/process-payment and
POST /rides/{id}/rate (routes/rides/payments.py, routes/rides/rating.py).
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import HTTPException

try:
    from ..settings_loader import get_app_settings
except ImportError:
    from settings_loader import get_app_settings


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


async def enforce_min_tip(tip) -> None:
    """Raise 400 when 0 < tip < the configured minimum; otherwise return."""
    # Cent-rounded like the amount actually charged, so every entry point
    # judges $0.995 the same way (POST /tip already rounds before calling).
    tip_d = _to_decimal(tip).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if tip_d <= 0:
        return
    settings = await get_app_settings()
    minimum = _to_decimal(settings.get("min_tip_amount"))
    if minimum > 0 and tip_d < minimum:
        raise HTTPException(status_code=400, detail=f"Minimum tip is ${minimum:.2f}")
