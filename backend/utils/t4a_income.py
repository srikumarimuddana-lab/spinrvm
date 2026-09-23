"""Non-ride-fare income that belongs on a driver's T4A (CRA Box 048).

The T4A slip (routes/drivers/tax_exports.get_t4a_summary), the tax-year list
(tax_exports.get_t4a_years) and the $500 CRA-threshold eligibility check
(utils/t4a_annual_job._driver_annual_earnings) historically summed only
completed-ride ``driver_earnings`` plus settled legacy Stripe payouts. Three
streams the driver is actually PAID for were missing, although the driver's
own earnings statement (utils/driver_statement._build) and the payable
balance that drives Stripe payouts (utils/auto_payout._balance_from_rows)
already count all three:

- cancellation / no-show fees: ``rides.cancellation_fee_driver`` on the
  driver's ``status='cancelled'`` rides, attributed by ``cancelled_at``. This
  is the same value ``services/cancellation_service.pay_driver_cancellation_fee``
  credits, and the column the payout balance reads — the wallet
  ``cancellation_fee`` ledger entry is deliberately NOT summed as well, it
  records the same money and adding both would double-count.
- quest / driver-referral bonuses: ``driver_bonuses.amount`` (append-only
  ledger, reversals are negative rows), attributed by ``created_at``.
- per-ride incentives: ``ride_incentive_claims.bonus_amount`` for the
  completed rides already on the slip. ``rides.driver_earnings`` is fare-only
  (routes/drivers/ride_complete.py), so these are not already included.

GST/PST collected on fares is intentionally NOT added here: the statement
shows it as a remittance reminder, but it is tax the driver collected, not a
fee for services, and the T4A has never included it.

The ``db`` module is passed in rather than imported so each caller's own
``db_supabase`` binding (and its established test patch point) is used.
Decimal-only; a DB error propagates — a slip must never silently understate.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Iterable

_ZERO = Decimal("0")


def _d(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def cancellation_fee(ride: dict) -> Decimal:
    return _d(ride.get("cancellation_fee_driver"))


def bonus_amount(row: dict) -> Decimal:
    return _d(row.get("amount"))


def incentive_amount(claim: dict) -> Decimal:
    return _d(claim.get("bonus_amount"))


async def fetch_supplementary_income(
    db: Any,
    driver_id: str,
    window_gte: str,
    window_lt: str,
    completed_ride_ids: Iterable[str],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Return ``(cancelled_rides, bonus_rows, incentive_claims)`` for one
    driver over the half-open UTC window ``[window_gte, window_lt)``.

    ``completed_ride_ids`` are the completed rides the caller already counts;
    incentive claims are attributed to the same period as their ride.
    """
    window = {"$gte": window_gte, "$lt": window_lt}
    # Only fee-bearing cancellations are income; filtering server-side keeps
    # fee-less cancellations from consuming the row budget.
    cancelled, bonuses = await asyncio.gather(
        db.get_rows(
            "rides",
            {
                "driver_id": driver_id,
                "status": "cancelled",
                "cancelled_at": window,
                "cancellation_fee_driver": {"$gt": 0},
            },
            limit=10000,
        ),
        db.get_rows(
            "driver_bonuses",
            {"driver_id": driver_id, "created_at": window},
            limit=10000,
        ),
    )
    cancelled = cancelled or []
    bonuses = bonuses or []
    ride_ids = [rid for rid in completed_ride_ids if rid]
    claims: list[dict] = []
    if ride_ids:
        # Batched: a full tax year of ride ids is far too long for one
        # PostgREST `in.(...)` URL.
        claims = await db.get_rows_batched_in("ride_incentive_claims", "ride_id", ride_ids) or []
    return cancelled, bonuses, claims


def sum_supplementary_income(
    cancelled: list[dict], bonuses: list[dict], claims: list[dict]
) -> tuple[Decimal, Decimal, Decimal]:
    """``(cancellation_fees, bonuses, incentives)`` totals, Decimal."""
    return (
        sum((cancellation_fee(r) for r in cancelled), _ZERO),
        sum((bonus_amount(b) for b in bonuses), _ZERO),
        sum((incentive_amount(c) for c in claims), _ZERO),
    )
