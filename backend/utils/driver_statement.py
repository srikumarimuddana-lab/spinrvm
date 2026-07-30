"""Driver earnings-statement builder — weekly/monthly period aggregates.

Uber-style periodic statements: every Monday each active driver gets last
week's (Mon–Sun) earnings + payout summary by email, and on the 1st of each
month the prior calendar month's. This module builds the DATA for one
(driver, period); rendering lives in ``driver_statement_pdf.py`` and the
scheduling/claiming/emailing in ``driver_statement_job.py``. The admin
download endpoint reuses the same builder so what the admin sees is exactly
what the driver was sent.

Money math mirrors ``routes/drivers/earnings.py::get_driver_earnings`` so the
statement always matches the app's Earnings tab for the same window:

    total = ride earnings (driver_earnings, tips included)
          + incentives (per-ride claims) + quest/referral bonuses
          + cancellation/no-show fees + GST/PST collected on fares

Payout rows in the window are listed as-is (standard, instant with its fee,
and — for pre-migration periods — 'stripe_sync' history rows). Decimal-only
arithmetic; strings only at the output boundary.

Period boundaries use America/Regina (Spinr's home market; no DST, fixed
UTC-6) so every driver's week ends at the same wall-clock midnight and a
batch is deterministic. Per-driver service-area timezones are deliberately
NOT used here — a statement is a fixed company document, not a live UI.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover
    import db_supabase  # type: ignore

STATEMENT_TZ = ZoneInfo("America/Regina")

WEEKLY = "weekly"
MONTHLY = "monthly"
# Arbitrary date range — admin "filter by dates → download / email" only;
# the scheduled job and driver self-serve use the anchored types above.
CUSTOM = "custom"
PERIOD_TYPES = {WEEKLY, MONTHLY}

# Cap for custom ranges: a statement is a period document, not a full-history
# export (that's the T4A / DSAR path).
MAX_CUSTOM_RANGE_DAYS = 366

_ZERO = Decimal("0")
_CENT = Decimal("0.01")


def _d(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def _money(value: Decimal) -> str:
    return str(value.quantize(_CENT))


# ── Period arithmetic ────────────────────────────────────────────────────────


def latest_completed_weekly(today_local: date) -> tuple[date, date]:
    """Most recent fully-completed Mon–Sun week before ``today_local``."""
    this_monday = today_local - timedelta(days=today_local.weekday())
    start = this_monday - timedelta(days=7)
    return start, start + timedelta(days=6)


def latest_completed_monthly(today_local: date) -> tuple[date, date]:
    """The calendar month before the one containing ``today_local``."""
    first_of_this = today_local.replace(day=1)
    end = first_of_this - timedelta(days=1)
    return end.replace(day=1), end


def period_bounds(period_type: str, period_start: date) -> tuple[date, date]:
    """(start, end) dates for a period identified by its start date."""
    if period_type == WEEKLY:
        if period_start.weekday() != 0:
            raise ValueError("weekly period_start must be a Monday")
        return period_start, period_start + timedelta(days=6)
    if period_type == MONTHLY:
        if period_start.day != 1:
            raise ValueError("monthly period_start must be the 1st")
        next_month = (period_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return period_start, next_month - timedelta(days=1)
    raise ValueError(f"unknown period_type {period_type!r}")


def period_window_utc(period_start: date, period_end: date) -> tuple[str, str]:
    """Half-open UTC ISO window [start 00:00, end+1d 00:00) in Regina time."""
    start_local = datetime.combine(period_start, time.min, tzinfo=STATEMENT_TZ)
    end_local = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=STATEMENT_TZ)
    return start_local.isoformat(), end_local.isoformat()


def period_label(period_type: str, period_start: date, period_end: date) -> str:
    if period_type == MONTHLY:
        return period_start.strftime("%B %Y")
    if period_start.month == period_end.month:
        return f"{period_start.strftime('%b %d')} - {period_end.strftime('%d, %Y')}"
    return f"{period_start.strftime('%b %d')} - {period_end.strftime('%b %d, %Y')}"


# ── Aggregation ──────────────────────────────────────────────────────────────


def _ride_income(r: dict) -> Decimal:
    """Canonical driver income for one completed ride — same rule as
    routes/drivers/_shared._ride_income (driver_earnings, with the
    fare-component fallback for legacy rows). Duplicated because utils must
    not import from routes; keep the two in sync."""
    if r.get("driver_earnings") is not None:
        return _d(r.get("driver_earnings"))
    return _d(r.get("base_fare")) + _d(r.get("distance_fare")) + _d(r.get("time_fare")) + _d(r.get("tip_amount"))


def _ride_tax(r: dict) -> Decimal:
    """GST/PST collected on a ride — tax_amount, falling back to the fare
    snapshot's tax lines (same fallback as the earnings endpoint)."""
    tax = _d(r.get("tax_amount"))
    if tax != _ZERO:
        return tax
    snap = r.get("fare_breakdown_snapshot") or {}
    for line in snap.get("lines") or []:
        if line.get("type") in ("tax", "gst", "pst"):
            tax += _d(line.get("amount"))
    return tax


_PAYOUT_TYPE_LABELS = {
    "standard": "Standard payout",
    "instant": "Instant payout",
    "legacy_import": "Settled in previous app",
    "stripe_sync": "Payout (synced from Stripe history)",
}


async def build_statement(
    driver: dict,
    period_type: str,
    period_start: date,
    *,
    driver_name: str | None = None,
) -> dict:
    """Aggregate one driver's earnings + payouts for one anchored period.

    ``driver`` needs at least ``id``. Returns a JSON-serializable dict with
    2-dp money strings; ``has_activity`` is False when the period contains no
    completed rides, bonuses, cancellation fees, or payouts (the job skips
    emailing those so inactive drivers aren't spammed with $0.00 statements).
    """
    start_d, end_d = period_bounds(period_type, period_start)
    return await _build(driver, period_type, start_d, end_d, driver_name=driver_name)


async def build_custom_statement(
    driver: dict,
    start_d: date,
    end_d: date,
    *,
    driver_name: str | None = None,
) -> dict:
    """Statement for an arbitrary inclusive date range (admin date filter)."""
    if end_d < start_d:
        raise ValueError("end date is before start date")
    if (end_d - start_d).days > MAX_CUSTOM_RANGE_DAYS:
        raise ValueError(f"range exceeds {MAX_CUSTOM_RANGE_DAYS} days")
    return await _build(driver, CUSTOM, start_d, end_d, driver_name=driver_name)


async def _build(
    driver: dict,
    period_type: str,
    start_d: date,
    end_d: date,
    *,
    driver_name: str | None = None,
) -> dict:
    win_gte, win_lt = period_window_utc(start_d, end_d)
    driver_id = driver["id"]
    window = {"$gte": win_gte, "$lt": win_lt}

    rides = (
        await db_supabase.get_rows(
            "rides",
            {"driver_id": driver_id, "status": "completed", "ride_completed_at": window},
            limit=10000,
        )
        or []
    )
    cancelled = (
        await db_supabase.get_rows(
            "rides",
            {"driver_id": driver_id, "status": "cancelled", "cancelled_at": window},
            limit=10000,
        )
        or []
    )
    bonuses = (
        await db_supabase.get_rows(
            "driver_bonuses",
            {"driver_id": driver_id, "created_at": window},
            limit=10000,
        )
        or []
    )
    payout_rows = (
        await db_supabase.get_rows(
            "payouts",
            {"driver_id": driver_id, "created_at": window},
            limit=10000,
        )
        or []
    )
    ride_ids = [r["id"] for r in rides if r.get("id")]
    incentive_claims: list[dict] = []
    if ride_ids:
        incentive_claims = (
            await db_supabase.get_rows(
                "ride_incentive_claims",
                {"ride_id": {"$in": ride_ids}},
                limit=10000,
            )
            or []
        )

    ride_earnings = sum((_ride_income(r) for r in rides), _ZERO)
    tips = sum((_d(r.get("tip_amount")) for r in rides), _ZERO)
    tax_collected = sum((_ride_tax(r) for r in rides), _ZERO)
    cancel_fees = sum((_d(r.get("cancellation_fee_driver")) for r in cancelled), _ZERO)
    bonus_total = sum((_d(b.get("amount")) for b in bonuses), _ZERO)
    incentive_total = sum((_d(c.get("bonus_amount")) for c in incentive_claims), _ZERO)
    total = ride_earnings + bonus_total + incentive_total + cancel_fees + tax_collected

    payouts_out: list[dict] = []
    payouts_total = _ZERO
    for p in sorted(payout_rows, key=lambda x: x.get("created_at") or ""):
        status = str(p.get("status") or "").lower()
        amount = _d(p.get("amount"))
        fee = _d(p.get("fee"))
        net = _d(p.get("net_amount")) if p.get("net_amount") is not None else amount - fee
        if status not in ("reversed", "failed"):
            payouts_total += amount
        payouts_out.append(
            {
                "date": (p.get("created_at") or "")[:10],
                "type": p.get("payout_type") or "standard",
                "label": _PAYOUT_TYPE_LABELS.get(p.get("payout_type") or "standard", "Payout"),
                "amount": _money(amount),
                "fee": _money(fee),
                "net": _money(net),
                "status": status or "unknown",
            }
        )

    distance_km = float(sum((_d(r.get("distance_km")) for r in rides), _ZERO))
    duration_minutes = float(sum((_d(r.get("duration_minutes")) for r in rides), _ZERO))

    return {
        "driver_id": driver_id,
        "driver_name": driver_name or driver.get("name") or "Driver",
        "period_type": period_type,
        "period_start": start_d.isoformat(),
        "period_end": end_d.isoformat(),
        "period_label": period_label(period_type, start_d, end_d),
        "trips": len(rides),
        "distance_km": round(distance_km, 1),
        "duration_minutes": int(duration_minutes),
        "earnings": {
            "ride_earnings": _money(ride_earnings),
            "tips_included": _money(tips),
            "incentives": _money(incentive_total),
            "bonuses": _money(bonus_total),
            "cancellation_fees": _money(cancel_fees),
            "tax_collected": _money(tax_collected),
            "total": _money(total),
        },
        "payouts": payouts_out,
        "payouts_total": _money(payouts_total),
        "has_activity": bool(rides or bonuses or incentive_claims or payouts_out or cancel_fees != _ZERO),
    }
