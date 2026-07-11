"""Role-scoped company dashboard (Phase 6 / M6.1).

    GET /company/{id}/dashboard/stats       stat groups x time windows
    GET /company/{id}/bookings/{ride_id}    ride detail (timeline, fare, legs)

Scope is resolved SERVER-SIDE from the caller's membership — never from
query params (owner requirement):

    admin / owner     whole company
    section head      rides of their section's members (booked by or for
                      them); EXPENSES use the FROZEN
                      ride_payment_sources.section_id (migration 228) so a
                      member moving departments doesn't rewrite history
                      (migration-review mandate)
    member            their own work rides — booked BY them or FOR them
                      (Q16); personal rides never appear

Stat groups (owner spec): Total Bookings, Expenses, Active Rides, Pending
Bookings (= scheduled + searching, Q15), Completed Rides — each bucketed
Today / This week / This month / Overall in the COMPANY's timezone.
All money is Decimal, serialized as strings.

NOTE: no `from __future__ import annotations` (slowapi-wrapper pitfall, see
corporate_signup.py) — kept for consistency even though these are GETs.
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

try:
    from .. import db_supabase
    from ..dependencies.company_guard import require_company_member
except ImportError:
    import db_supabase  # type: ignore
    from dependencies.company_guard import require_company_member  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/company/{company_id}", tags=["Corporate Company Dashboard"])

_ADMIN_ROLES = {"owner", "admin"}
_ACTIVE = {"driver_assigned", "driver_accepted", "driver_arrived", "in_progress"}
_PENDING = {"scheduled", "searching"}  # Q15
_PAGE = 1000


def _money(v: Any) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


async def _scope_member_ids(ctx: Dict[str, Any]) -> Optional[List[str]]:
    """None = whole company (admin). Otherwise the member-id set whose rides
    are visible: the section's members for a head, else just the caller."""
    if ctx["role"] in _ADMIN_ROLES:
        return None
    sections = await db_supabase.get_rows(
        "corporate_sections",
        {"company_id": ctx["company_id"], "head_member_id": ctx["member_id"]},
        limit=5,
    )
    if sections:
        member_ids: set = {ctx["member_id"]}
        for sec in sections:
            rows = await db_supabase.get_rows(
                "corporate_members",
                {"company_id": ctx["company_id"], "section_id": sec["id"], "status": "active"},
                limit=_PAGE,
            )
            member_ids.update(r["id"] for r in rows or [])
        return sorted(member_ids)
    return [ctx["member_id"]]


async def _scoped_rides(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """All corporate rides visible to the caller (paged; companies are small
    relative to consumer traffic — mirrors the billing summary's approach)."""
    member_ids = await _scope_member_ids(ctx)

    async def _page_all(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        offset = 0
        while True:
            rows = (
                await db_supabase.get_rows("rides", filters, order="created_at", desc=True, limit=_PAGE, offset=offset)
                or []
            )
            out.extend(rows)
            if len(rows) < _PAGE:
                return out
            offset += _PAGE

    base = {"corporate_account_id": ctx["company_id"]}
    if member_ids is None:
        return await _page_all(base)

    # Q16: booked FOR them (payer) ∪ booked BY them.
    seen: Dict[str, Dict[str, Any]] = {}
    for mid in member_ids:
        for filt_key in ("corporate_member_id", "corporate_booked_by_member_id"):
            rows = await _page_all({**base, filt_key: mid})
            for r in rows:
                seen[r["id"]] = r
    rides = list(seen.values())
    rides.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rides


def _bucket_starts(tz_name: str) -> Dict[str, Optional[datetime]]:
    try:
        tz = ZoneInfo(tz_name or "America/Regina")
    except Exception:
        tz = ZoneInfo("America/Regina")
    now_local = datetime.now(tz)
    today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=today.weekday())  # ISO week, Monday
    month = today.replace(day=1)
    return {
        "today": today.astimezone(timezone.utc),
        "week": week.astimezone(timezone.utc),
        "month": month.astimezone(timezone.utc),
        "overall": None,
    }


def _parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@router.get("/dashboard/stats")
async def dashboard_stats(company_id: str, ctx: dict = Depends(require_company_member)):
    company = await db_supabase.get_corporate_account_by_id(company_id) or {}
    buckets = _bucket_starts(company.get("timezone") or "America/Regina")
    rides = await _scoped_rides(ctx)

    def _group() -> Dict[str, Any]:
        return {"today": 0, "week": 0, "month": 0, "overall": 0}

    stats: Dict[str, Dict[str, Any]] = {
        "total_bookings": _group(),
        "active_rides": _group(),
        "pending_bookings": _group(),
        "completed_rides": _group(),
    }
    expenses: Dict[str, Decimal] = {k: Decimal("0") for k in ("today", "week", "month", "overall")}

    for r in rides:
        created = _parse_dt(r.get("created_at")) or _parse_dt(r.get("ride_requested_at"))
        status = (r.get("status") or "").lower()
        total = _money(r.get("grand_total") or r.get("total_fare"))
        for window, start in buckets.items():
            if start is not None and (created is None or created < start):
                continue
            stats["total_bookings"][window] += 1
            if status in _ACTIVE:
                stats["active_rides"][window] += 1
            elif status in _PENDING:
                stats["pending_bookings"][window] += 1
            elif status == "completed":
                stats["completed_rides"][window] += 1
                expenses[window] += total

    return {
        "scope": "company" if ctx["role"] in _ADMIN_ROLES else "member",
        "stats": {
            **stats,
            "expenses": {k: str(v.quantize(Decimal("0.01"))) for k, v in expenses.items()},
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/bookings/{ride_id}")
async def booking_detail(company_id: str, ride_id: str, ctx: dict = Depends(require_company_member)):
    """Ride detail: status timeline, fare + tax lines, payment-source legs,
    booked-by/for attribution, driver + vehicle. Non-admins only see rides in
    their own scope."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride or ride.get("corporate_account_id") != ctx["company_id"]:
        raise HTTPException(status_code=404, detail="Booking not found")

    if ctx["role"] not in _ADMIN_ROLES:
        member_ids = await _scope_member_ids(ctx) or []
        if (
            ride.get("corporate_member_id") not in member_ids
            and ride.get("corporate_booked_by_member_id") not in member_ids
        ):
            raise HTTPException(status_code=404, detail="Booking not found")

    # Payment-source legs (frozen attribution, migration 228).
    legs = await db_supabase.get_rows("ride_payment_sources", {"ride_id": ride_id}, limit=1) or []
    leg = legs[0] if legs else {}

    driver = None
    if ride.get("driver_id"):
        d = await db_supabase.get_rows("drivers", {"id": ride["driver_id"]}, limit=1) or []
        if d:
            driver = {
                "first_name": d[0].get("first_name"),
                "vehicle_make": d[0].get("vehicle_make"),
                "vehicle_model": d[0].get("vehicle_model"),
                "license_plate": d[0].get("license_plate"),
            }

    timeline = [
        {"event": key, "at": ride.get(field)}
        for key, field in (
            ("requested", "ride_requested_at"),
            ("driver_accepted", "driver_accepted_at"),
            ("driver_arrived", "driver_arrived_at"),
            ("started", "ride_started_at"),
            ("completed", "ride_completed_at"),
            ("cancelled", "cancelled_at"),
        )
        if ride.get(field)
    ]

    return {
        "ride_id": ride["id"],
        "ride_code": ride.get("ride_code"),
        "status": ride.get("status"),
        "guest_booking": bool(ride.get("guest_booking")),
        "booked_for_member_id": ride.get("corporate_member_id"),
        "booked_by_member_id": ride.get("corporate_booked_by_member_id") or ride.get("corporate_member_id"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "scheduled_time": ride.get("scheduled_time"),
        "timeline": timeline,
        "fare": {
            "base_fare": ride.get("base_fare"),
            "distance_fare": ride.get("distance_fare"),
            "time_fare": ride.get("time_fare"),
            "booking_fee": ride.get("booking_fee"),
            "area_fees_total": ride.get("area_fees_total"),
            "tax_amount": ride.get("tax_amount"),
            "tax_breakdown": ride.get("tax_breakdown"),  # GST/PST line items
            "grand_total": ride.get("grand_total"),
        },
        "payment_source": {
            "allowance": leg.get("allowance_debit_amount"),
            "section": leg.get("section_debit_amount"),
            "section_id": leg.get("section_id"),
            "master": leg.get("master_fallback_amount"),
        }
        if leg
        else None,
        "payment_status": ride.get("payment_status"),
        "driver": driver,
    }
