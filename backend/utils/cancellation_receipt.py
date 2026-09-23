"""What a cancelled ride actually charged, for every receipt surface.

A cancelled ride keeps its booking-time QUOTE in ``total_fare``,
``grand_total``, ``tax_amount``, ``tax_breakdown``, ``base_fare`` ... — those
columns are deliberately not overwritten on cancel (other readers still
expect the original quote there; see
docs/change-log/2026-09-23-cancellation-fee-tax-receipt-corporate.md). Before
this helper, the JSON receipt, the receipt PDF and the email receipt all
rendered that stale quote for a cancelled ride, so a rider charged a $4.50
cancellation fee saw a ~$19 trip fare with GST next to it — reading exactly
like a hidden or duplicate charge.

``cancellation_charge`` is the single, pure (no I/O) source the three
renderers branch on for ``status == "cancelled"``. It reads only the columns
that record what was actually billed:

* ``cancellation_fee_admin`` + ``cancellation_fee_driver`` — the pre-tax fee
  (rider cancel or driver no-show);
* ``cancellation_fee_tax_amount`` / ``cancellation_fee_tax_breakdown``
  (migration 455) — GST/PST on that fee, only when
  ``cancellation_fee_tax_enabled`` was on at cancel time;
* ``scheduled_notice_fee_amount`` — the pre-dispatch scheduled-ride notice
  fee, which records the amount actually collected.

Decimal-only (CLAUDE.md money rule).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List

_CENT = Decimal("0.01")


def _d(v: Any) -> Decimal:
    if v in (None, ""):
        return Decimal("0")
    return Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return v.quantize(_CENT, rounding=ROUND_HALF_UP)


def is_cancelled(ride: Dict[str, Any]) -> bool:
    return (ride or {}).get("status") == "cancelled"


def cancellation_charge(ride: Dict[str, Any]) -> Dict[str, Any]:
    """Return the actual charge of a cancelled ride.

    ``{"fee": Decimal, "notice_fee": Decimal, "tax_amount": Decimal,
    "tax_breakdown": dict, "grand_total": Decimal, "lines": [...]}`` where
    ``lines`` uses the same ``{"label", "amount", "type"}`` shape as
    ``routes/rides/_shared.py::_build_fare_breakdown`` and always sums to
    ``grand_total``. A free cancellation yields no lines and a $0 total.
    """
    fee = _q(_d(ride.get("cancellation_fee_admin")) + _d(ride.get("cancellation_fee_driver")))
    notice_fee = _q(_d(ride.get("scheduled_notice_fee_amount")))

    raw_breakdown = ride.get("cancellation_fee_tax_breakdown")
    tax_breakdown: Dict[str, Any] = raw_breakdown if isinstance(raw_breakdown, dict) else {}
    tax_lines: List[Dict[str, Any]] = []
    breakdown_sum = Decimal("0")
    for name, info in tax_breakdown.items():
        if not isinstance(info, dict):
            continue
        amount = _q(_d(info.get("amount")))
        if amount <= 0:
            continue
        breakdown_sum += amount
        rate = info.get("rate") or 0
        label = f"{name} ({rate}%)" if rate else str(name)
        tax_lines.append({"label": label, "amount": float(amount), "type": "tax"})
    persisted_tax = ride.get("cancellation_fee_tax_amount")
    tax_amount = _q(_d(persisted_tax)) if persisted_tax not in (None, "") else _q(breakdown_sum)
    if tax_amount > 0 and not tax_lines:
        # Amount persisted without a breakdown: still disclose it as a line so
        # the rows reconcile to the total.
        tax_lines.append({"label": "Tax", "amount": float(tax_amount), "type": "tax"})

    lines: List[Dict[str, Any]] = []
    if fee > 0:
        label = "No-show fee" if ride.get("cancellation_type") == "noshow" else "Cancellation fee"
        lines.append({"label": label, "amount": float(fee), "type": "fee"})
    if notice_fee > 0:
        lines.append({"label": "Late cancellation fee (scheduled ride)", "amount": float(notice_fee), "type": "fee"})
    lines.extend(tax_lines)

    return {
        "fee": fee,
        "notice_fee": notice_fee,
        "tax_amount": tax_amount,
        "tax_breakdown": tax_breakdown,
        "grand_total": _q(fee + notice_fee + tax_amount),
        "lines": lines,
    }
