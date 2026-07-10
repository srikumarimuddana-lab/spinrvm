import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from dependencies import get_admin_user  # noqa: F401
    from utils.audit_logger import log_admin_action  # noqa: F401

from .drivers import _batch_fetch_drivers_and_users, _user_display_name

logger = logging.getLogger(__name__)

router = APIRouter()

# ============================================================
# Spinr Pass — Driver Subscription Plans
# ============================================================


class SubscriptionPlanCreate(BaseModel):
    name: str  # "Basic", "Pro", "Unlimited"
    price: float  # 19.99, 49.99
    duration_days: int = 30  # 1=daily, 7=weekly, 30=monthly
    rides_per_day: int = -1  # -1 = unlimited, or 4, 8, etc.
    description: Optional[str] = None
    features: Optional[List[str]] = None  # ["Priority support", "Surge protection"]
    vehicle_types: Optional[List[str]] = None  # restrict to vehicle type IDs, null=all
    service_areas: Optional[List[str]] = None  # restrict to area IDs, null=all
    is_active: bool = True
    # Recurring Stripe Price (price_...) created in the Stripe dashboard.
    # When set, subscribe_to_plan uses mode="subscription" auto-renew Checkout;
    # when null the plan stays on the one-off Checkout-per-period flow.
    stripe_price_id: Optional[str] = None


class SubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    duration_days: Optional[int] = None
    rides_per_day: Optional[int] = None
    description: Optional[str] = None
    features: Optional[List[str]] = None
    vehicle_types: Optional[List[str]] = None
    service_areas: Optional[List[str]] = None
    is_active: Optional[bool] = None
    stripe_price_id: Optional[str] = None


@router.get("/subscription-plans")
async def list_subscription_plans():
    """List all Spinr Pass subscription plans."""
    plans = await db_supabase.get_rows("subscription_plans", limit=50)
    return plans


@router.post("/subscription-plans")
async def create_subscription_plan(req: SubscriptionPlanCreate, admin: dict = Depends(get_admin_user)):
    """Create a new driver subscription plan."""
    plan = {
        "id": str(uuid.uuid4()),
        "name": req.name,
        "price": req.price,
        "duration_days": req.duration_days,
        "rides_per_day": req.rides_per_day,
        "description": req.description or "",
        "features": req.features or [],
        "vehicle_types": req.vehicle_types,
        "service_areas": req.service_areas,
        "is_active": req.is_active,
        "stripe_price_id": req.stripe_price_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("subscription_plans", plan)
    await log_admin_action(
        admin,
        "subscription_plan_created",
        "subscription_plans",
        plan["id"],
        {"name": req.name, "price": req.price, "duration_days": req.duration_days},
    )
    return plan


@router.put("/subscription-plans/{plan_id}")
async def update_subscription_plan(plan_id: str, req: SubscriptionPlanUpdate, admin: dict = Depends(get_admin_user)):
    """Update a subscription plan."""
    updates = {k: v for k, v in req.dict().items() if v is not None}
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("subscription_plans", {"id": plan_id}, updates)
        await log_admin_action(
            admin,
            "subscription_plan_updated",
            "subscription_plans",
            plan_id,
            {"updated_fields": list(updates.keys())},
        )
    return {"success": True}


@router.delete("/subscription-plans/{plan_id}")
async def delete_subscription_plan(plan_id: str, admin: dict = Depends(get_admin_user)):
    """Delete a subscription plan."""
    await db_supabase.delete_many("subscription_plans", {"id": plan_id})
    await log_admin_action(admin, "subscription_plan_deleted", "subscription_plans", plan_id, {})
    return {"success": True}


# ─── Driver Subscription Management ───


@router.get("/driver-subscriptions")
async def list_driver_subscriptions(status: Optional[str] = Query(None)):
    """List all driver subscriptions, optionally filtered by status."""
    subs = await db_supabase.get_rows("driver_subscriptions", {}, limit=200)
    if status:
        subs = [s for s in subs if s.get("status") == status]
    return subs


@router.get("/subscription-stats")
async def admin_get_subscription_stats(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    service_area_ids: Optional[str] = None,
):
    """Get Spinr Pass subscription revenue stats, transaction list, and chart data.

    service_area_ids: comma-separated list of area IDs to filter by driver's area.
    """
    from collections import defaultdict

    area_filter = set(service_area_ids.split(",")) if service_area_ids else None

    now = datetime.now(timezone.utc)
    if start_date:
        range_start = datetime.fromisoformat(start_date.replace("Z", "").replace("+00:00", ""))
    else:
        range_start = now - timedelta(days=30)
    range_start = range_start.replace(hour=0, minute=0, second=0, microsecond=0)

    if end_date:
        range_end = datetime.fromisoformat(end_date.replace("Z", "").replace("+00:00", ""))
        range_end = range_end.replace(hour=23, minute=59, second=59)
    else:
        range_end = now

    # parse_dt() (and the explicit start/end branches) strip tz and yield NAIVE
    # datetimes, but the default branches derive from a tz-aware `now`. Force
    # both bounds naive so the `range_start <= dt <= range_end` comparisons
    # below never mix offset-aware and offset-naive datetimes (TypeError).
    if range_start.tzinfo is not None:
        range_start = range_start.replace(tzinfo=None)
    if range_end.tzinfo is not None:
        range_end = range_end.replace(tzinfo=None)

    # Fetch all subscriptions
    all_subs = await db_supabase.get_rows("driver_subscriptions", {}, limit=10000)

    # Fetch all plans for lookup
    all_plans = await db_supabase.get_rows("subscription_plans", limit=100)
    plan_map = {p["id"]: p for p in all_plans}

    # Fetch drivers for name + area lookup (batch)
    driver_ids = list({s.get("driver_id") for s in all_subs if s.get("driver_id")})
    raw_drivers_map, raw_users_map = await _batch_fetch_drivers_and_users([], driver_ids)
    drivers_map: Dict[str, str] = {}
    driver_area_map: Dict[str, str] = {}
    for did, d in raw_drivers_map.items():
        u = raw_users_map.get(d.get("user_id")) if d.get("user_id") else None
        name = _user_display_name(u) if u else ""
        drivers_map[did] = name or d.get("name") or did[:8]
        if d.get("service_area_id"):
            driver_area_map[did] = d["service_area_id"]

    # Filter by service area if requested
    if area_filter:
        all_subs = [s for s in all_subs if driver_area_map.get(s.get("driver_id", "")) in area_filter]

    # ── Subscriber state (counts) — from driver_subscriptions ────────────
    # A pending/superseded checkout row carries no realized subscriber; every
    # other state is a real subscriber (incl. legacy pre-checkout rows whose
    # payment_status defaults to "pending" after migration 148).
    _UNPAID_STATUSES = {"pending", "superseded"}
    real_subs = [s for s in all_subs if s.get("payment_status") == "paid" or s.get("status") not in _UNPAID_STATUSES]
    active = [s for s in all_subs if s.get("status") == "active"]
    expired = [s for s in all_subs if s.get("status") == "expired"]
    cancelled = [s for s in all_subs if s.get("status") == "cancelled"]

    # Current MRR-ish: sum of active subscriptions' plan price.
    # Money stays in Decimal through every aggregation; we convert to a clean
    # 2-dp float only at the JSON boundary (the admin API contract is numeric).
    def _money(v) -> float:
        return float(Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    # Current MRR-ish: sum of active subscriptions' plan price.
    active_revenue = sum((Decimal(str(s.get("price") or 0)) for s in active), Decimal("0"))

    def parse_dt(s):
        try:
            return datetime.fromisoformat(str(s).replace("Z", "").replace("+00:00", ""))
        except Exception:
            return None

    # ── Realized revenue & transactions — from the subscription_payments
    #    ledger, which records the first charge AND every recurring renewal
    #    (driver_subscriptions only holds current state, so reading it would
    #    miss renewals). Legacy rows were backfilled by migration 151. ───────
    payments = await db_supabase.get_rows("subscription_payments", {}, limit=20000) or []
    if area_filter:
        payments = [p for p in payments if driver_area_map.get(p.get("driver_id", "")) in area_filter]

    total_revenue = sum((Decimal(str(p.get("amount") or 0)) for p in payments), Decimal("0"))

    payments_in_range = []
    for p in payments:
        dt = parse_dt(p.get("created_at"))
        if dt and range_start <= dt <= range_end:
            payments_in_range.append(p)
    range_revenue = sum((Decimal(str(p.get("amount") or 0)) for p in payments_in_range), Decimal("0"))

    # New-subscriber chart counts new subscriptions (state), not payments.
    new_subs_in_range = []
    for s in real_subs:
        dt = parse_dt(s.get("created_at") or s.get("started_at"))
        if dt and range_start <= dt <= range_end:
            new_subs_in_range.append(s)

    # Per-plan breakdown: revenue from the ledger, subscriber counts from subs.
    plan_stats = defaultdict(lambda: {"name": "", "count": 0, "revenue": Decimal("0"), "active": 0})
    for s in real_subs:
        pid = s.get("plan_id") or "unknown"
        plan_stats[pid]["name"] = s.get("plan_name") or plan_map.get(pid, {}).get("name", "Unknown")
        plan_stats[pid]["count"] += 1
        if s.get("status") == "active":
            plan_stats[pid]["active"] += 1
    for p in payments:
        pid = p.get("plan_id") or "unknown"
        if not plan_stats[pid]["name"]:
            plan_stats[pid]["name"] = p.get("plan_name") or plan_map.get(pid, {}).get("name", "Unknown")
        plan_stats[pid]["revenue"] += Decimal(str(p.get("amount") or 0))

    # Daily charts (within date range)
    num_days = min((range_end - range_start).days + 1, 365)
    daily_revenue: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    daily_new_subs = defaultdict(int)
    for p in payments_in_range:
        dt = parse_dt(p.get("created_at"))
        if dt:
            daily_revenue[dt.strftime("%Y-%m-%d")] += Decimal(str(p.get("amount") or 0))
    for s in new_subs_in_range:
        dt = parse_dt(s.get("created_at") or s.get("started_at"))
        if dt:
            daily_new_subs[dt.strftime("%Y-%m-%d")] += 1

    revenue_chart = []
    subscribers_chart = []
    for i in range(num_days):
        day = range_start + timedelta(days=i)
        day_key = day.strftime("%Y-%m-%d")
        day_label = day.strftime("%b %d")
        revenue_chart.append({"date": day_label, "date_raw": day_key, "amount": _money(daily_revenue.get(day_key, 0))})
        subscribers_chart.append({"date": day_label, "date_raw": day_key, "count": daily_new_subs.get(day_key, 0)})

    # Transaction list = ledger payments in range (each a realized charge,
    # including recurring renewals).
    transactions = []
    for p in sorted(payments_in_range, key=lambda x: x.get("created_at", ""), reverse=True):
        _did = p.get("driver_id") or ""
        transactions.append(
            {
                "id": p.get("id"),
                "driver_id": _did,
                "driver_name": drivers_map.get(_did, _did[:8]),
                "plan_name": p.get("plan_name") or plan_map.get(p.get("plan_id", ""), {}).get("name", "Unknown"),
                "price": _money(p.get("amount")),
                "billing_reason": p.get("billing_reason"),
                "created_at": p.get("created_at"),
            }
        )

    return {
        "stats": {
            "total_subscribers": len(real_subs),
            "active": len(active),
            "expired": len(expired),
            "cancelled": len(cancelled),
            "total_revenue": _money(total_revenue),
            "active_mrr": _money(active_revenue),
            "range_revenue": _money(range_revenue),
            "range_transactions": len(payments_in_range),
        },
        "plan_breakdown": [
            {
                "plan_id": k,
                "name": v["name"],
                "count": v["count"],
                "active": v["active"],
                "revenue": _money(v["revenue"]),
            }
            for k, v in plan_stats.items()
        ],
        "charts": {
            "daily_revenue": revenue_chart,
            "daily_subscribers": subscribers_chart,
        },
        "transactions": transactions,
        "service_areas": [
            {"id": a["id"], "name": a.get("name", "Unknown")}
            for a in await db_supabase.get_rows("service_areas", order="name", limit=200)
            if not a.get("parent_service_area_id")
        ],
    }


@router.get("/subscription/payments")
async def admin_list_subscription_payments(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    driver_id: Optional[str] = Query(default=None),
    plan_id: Optional[str] = Query(default=None),
    billing_reason: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    admin: dict = Depends(get_admin_user),
):
    """Paginated subscription payment history for admin panel.

    Supports filtering by driver, plan, billing reason, and date range.
    Returns tax breakdown (subtotal/GST/PST/HST) per row using stored columns
    (migration 186) with a legacy back-compute fallback.
    """
    filters: Dict = {}
    if driver_id:
        filters["driver_id"] = driver_id
    if plan_id:
        filters["plan_id"] = plan_id
    if billing_reason:
        filters["billing_reason"] = billing_reason

    def _parse_dt(s: str | None):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "").replace("+00:00", ""))
        except Exception:
            return None

    if start_date or end_date:
        # When a date range is requested, fetch all matching non-date rows so the
        # count and pagination are computed over the fully filtered set rather than
        # a pre-paginated slice whose total would be wrong.
        _all = (
            await db_supabase.get_rows(
                "subscription_payments",
                filters,
                order="created_at",
                desc=True,
                limit=10_000,
                offset=0,
            )
            or []
        )
        _start = _parse_dt(start_date)
        _end = _parse_dt(end_date)
        filtered: list = []
        for p in _all:
            _pd = _parse_dt(p.get("created_at") or "")
            if _pd is None:
                continue
            if _start and _pd < _start:
                continue
            if _end and _pd > _end:
                continue
            filtered.append(p)
        total = len(filtered)
        payments = filtered[offset : offset + limit]
    else:
        payments = (
            await db_supabase.get_rows(
                "subscription_payments",
                filters,
                order="created_at",
                desc=True,
                limit=limit,
                offset=offset,
            )
            or []
        )
        total = await db_supabase.count_documents("subscription_payments", filters)

    # Batch-fetch driver names.
    d_ids = list({p["driver_id"] for p in payments if p.get("driver_id")})
    _raw_d, _raw_u = await _batch_fetch_drivers_and_users([], d_ids)
    _name_map: Dict[str, str] = {}
    for did, d in _raw_d.items():
        u = _raw_u.get(d.get("user_id")) if d.get("user_id") else None
        _name_map[did] = _user_display_name(u) if u else did[:8]

    def _q2(v) -> Decimal:
        return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _row(p: dict) -> dict:
        total_d = _q2(p.get("amount"))
        if p.get("subtotal") is not None:
            subtotal_d = _q2(p["subtotal"])
            gst_d = _q2(p.get("gst_amount"))
            pst_d = _q2(p.get("pst_amount"))
            hst_d = _q2(p.get("hst_amount"))
        else:
            # Legacy rows written before migration 186 have no stored tax breakdown.
            # Return zeroes rather than fabricating tax that was never collected.
            subtotal_d = total_d
            gst_d = pst_d = hst_d = Decimal("0")
        _did = p.get("driver_id") or ""
        return {
            "id": p["id"],
            "driver_id": _did,
            "driver_name": _name_map.get(_did, _did[:8]),
            "plan_id": p.get("plan_id"),
            "plan_name": p.get("plan_name"),
            "amount": float(total_d),
            "subtotal": float(subtotal_d),
            "gst_amount": float(gst_d),
            "pst_amount": float(pst_d),
            "hst_amount": float(hst_d),
            "province": p.get("province") or "SK",
            "currency": (p.get("currency") or "cad").upper(),
            "billing_reason": p.get("billing_reason"),
            "stripe_invoice_id": p.get("stripe_invoice_id"),
            "created_at": p.get("created_at"),
        }

    return {
        "payments": [_row(p) for p in payments],
        "total": total,
        "has_more": (offset + len(payments)) < total,
        "limit": limit,
        "offset": offset,
    }


class SubscriptionTaxConfig(BaseModel):
    enabled: bool = True
    province: str = Field(default="SK", min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")
    gst_rate: float = Field(default=5.0, ge=0.0, le=50.0)
    pst_rate: float = Field(default=6.0, ge=0.0, le=50.0)
    hst_rate: float = Field(default=0.0, ge=0.0, le=50.0)


@router.put("/service-areas/{area_id}/subscription-tax")
async def update_subscription_tax_config(
    area_id: str,
    req: SubscriptionTaxConfig,
    admin: dict = Depends(get_admin_user),
):
    """Update the Spinr Pass subscription tax configuration for a service area.

    Tax rates are stored in the subscription_tax_config JSONB column added by
    migration 185.  Changes apply to the next checkout in the area — existing
    payments are not retroactively updated.
    """
    area = await db_supabase.find_one("service_areas", {"id": area_id})
    if not area:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Service area not found")

    tax_config = {
        "enabled": req.enabled,
        "province": req.province.upper(),
        "gst_rate": float(Decimal(str(req.gst_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "pst_rate": float(Decimal(str(req.pst_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "hst_rate": float(Decimal(str(req.hst_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
    }

    await db_supabase.update_one(
        "service_areas",
        {"id": area_id},
        {"$set": {"subscription_tax_config": tax_config}},
    )
    await log_admin_action(
        admin,
        "update_subscription_tax_config",
        "service_area",
        area_id,
        tax_config,
    )
    logger.info(
        "[ADMIN] subscription_tax_config updated for area=%s config=%s admin=%s",
        area_id,
        tax_config,
        admin.get("id"),
        extra={"domain": "payments"},
    )
    return {"success": True, "area_id": area_id, "subscription_tax_config": tax_config}


# ============================================================
# Per-Service-Area Offer Analytics
# Mounted separately under require_module("dashboard") in __init__.py
# so analytics-access admins can reach it without earnings permission.
# ============================================================

offer_analytics_router = APIRouter()


@offer_analytics_router.get("/offer-analytics")
async def get_offer_analytics(
    start_date: Optional[str] = Query(None, description="ISO date, e.g. 2025-01-01"),
    end_date: Optional[str] = Query(None, description="ISO date, e.g. 2025-12-31"),
    service_area_id: Optional[str] = Query(None, description="Filter to a single area"),
    _admin=Depends(get_admin_user),
):
    """Offer acceptance rates, avg response time, and offer counts grouped by service area.

    Reads ride_offers joined to rides (in Python via batch lookup) for the given
    date window. Defaults to the last 30 days when no dates are supplied.
    """
    now = datetime.now(timezone.utc)
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        except ValueError:
            start_dt = now - timedelta(days=30)
    else:
        start_dt = now - timedelta(days=30)

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
            # Date-only inputs (no "T") land at midnight of that day, which
            # excludes all offers later on the same day. Extend to end-of-day.
            if "T" not in end_date:
                end_dt = end_dt + timedelta(days=1) - timedelta(seconds=1)
        except ValueError:
            end_dt = now
    else:
        end_dt = now

    _truncated = False  # set True when the 200k hard cap is hit

    # Single paginated offer fetch for both the global and per-area paths.
    # Paging through all offers in the date window ensures no row is silently
    # dropped. When service_area_id is provided the ride lookup below is
    # additionally filtered by area, so unrelated offers are bucketed as
    # unknown and dropped by the aggregation loop — no separate area-first
    # ride query needed, and no 20k lifetime-rides cap.
    _PAGE = 5_000
    _HARD_CAP = 200_000
    offers_in_window: List[Dict] = []
    _offset = 0
    _date_filter = {
        "$and": [
            {"offered_at": {"$gte": start_dt.isoformat()}},
            {"offered_at": {"$lte": end_dt.isoformat()}},
        ]
    }
    while True:
        _page = (
            await db_supabase.get_rows(
                "ride_offers",
                _date_filter,
                order="offered_at",
                desc=True,
                limit=_PAGE,
                offset=_offset,
            )
            or []
        )
        offers_in_window.extend(_page)
        if len(_page) < _PAGE:
            break
        _offset += _PAGE
        if _offset >= _HARD_CAP:
            _truncated = True
            break

    if not offers_in_window:
        return {
            "window": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
            "areas": [],
            "totals": _offer_totals([]),
        }

    ride_ids = list({o["ride_id"] for o in offers_in_window if o.get("ride_id")})
    rides: List[Dict] = []
    _ride_filter_base: Dict = {"id": {"$in": []}}
    if service_area_id:
        _ride_filter_base = {"id": {"$in": []}, "service_area_id": service_area_id}
    for batch_start in range(0, len(ride_ids), 100):
        batch = ride_ids[batch_start : batch_start + 100]
        _batch_filter = {**_ride_filter_base, "id": {"$in": batch}}
        chunk = await db_supabase.get_rows("rides", _batch_filter, limit=len(batch)) or []
        rides.extend(chunk)

    ride_area = {r["id"]: r.get("service_area_id") for r in rides}

    # Fetch service area names once
    area_rows = await db_supabase.get_rows("service_areas", {}, limit=500) or []
    area_name = {a["id"]: a.get("name", "Unknown") for a in area_rows}

    # Aggregate per service area
    buckets: Dict[str, List[Dict]] = {}
    for offer in offers_in_window:
        area_id = ride_area.get(offer.get("ride_id")) or "__unknown__"
        if service_area_id and area_id != service_area_id:
            continue
        buckets.setdefault(area_id, []).append(offer)

    areas = []
    for area_id, bucket in sorted(buckets.items(), key=lambda kv: -(len(kv[1]))):
        areas.append(
            {
                "service_area_id": area_id if area_id != "__unknown__" else None,
                "service_area_name": area_name.get(area_id, "Unknown") if area_id != "__unknown__" else "Unknown area",
                **_offer_totals(bucket),
            }
        )

    # When filtering to a single area, totals must reflect only those offers
    # so the summary matches the breakdown — not the platform-wide count.
    filtered_offers = [o for b in buckets.values() for o in b] if service_area_id else offers_in_window

    result: Dict = {
        "window": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
        "areas": areas,
        "totals": _offer_totals(filtered_offers),
    }
    if _truncated:
        result["truncated"] = True
        result["warning"] = "Result set capped at 200,000 offers. Narrow the date range for full accuracy."
    return result


def _parse_ts(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _offer_totals(offers: List[Dict]) -> Dict:
    total = len(offers)
    accepted = sum(1 for o in offers if o.get("status") == "accepted")
    declined = sum(1 for o in offers if o.get("status") == "declined")
    expired = sum(1 for o in offers if o.get("status") == "expired")

    response_times = []
    for o in offers:
        if o.get("responded_at") and o.get("offered_at"):
            delta = _parse_ts(o["responded_at"]) - _parse_ts(o["offered_at"])
            secs = delta.total_seconds()
            if 0 <= secs < 3600:  # sanity cap at 1 hour
                response_times.append(secs)

    avg_response_s = (sum(response_times) / len(response_times)) if response_times else None

    return {
        "total_offers": total,
        "accepted": accepted,
        "declined": declined,
        "expired": expired,
        "acceptance_rate": round(accepted / total, 4) if total else 0.0,
        "avg_response_seconds": round(avg_response_s, 1) if avg_response_s is not None else None,
    }


# ============================================================
# Spinr Pass — per-payment invoice actions (download + resend)
# ============================================================


@router.get("/subscription/payments/{payment_id}/invoice.pdf")
async def admin_download_subscription_invoice(
    payment_id: str,
    admin: dict = Depends(get_admin_user),
):
    """Download the Spinr Pass invoice PDF for a single subscription payment.

    Identical document to the one emailed to the driver — assembled by the
    shared builder so admin and driver see the same figures.
    """
    from fastapi import HTTPException
    from fastapi.responses import Response as _Response

    try:
        from ...utils.subscription_invoice import build_subscription_invoice_pdf
    except ImportError:
        from utils.subscription_invoice import build_subscription_invoice_pdf  # type: ignore

    payment = await db_supabase.find_one("subscription_payments", {"id": payment_id})
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    built = await build_subscription_invoice_pdf(payment)
    if built is None:
        raise HTTPException(status_code=404, detail="Invoice could not be generated")
    pdf_bytes, filename = built

    await log_admin_action(
        admin,
        "download_subscription_invoice",
        "subscription_payment",
        payment_id,
        {"driver_id": payment.get("driver_id"), "amount": str(payment.get("amount"))},
    )
    return _Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/subscription/payments/{payment_id}/resend-invoice")
async def admin_resend_subscription_invoice(
    payment_id: str,
    admin: dict = Depends(get_admin_user),
):
    """Email the Spinr Pass invoice to the driver's address on file.

    Mirrors the driver-app self-resend, but admin-triggered and audit-logged.
    """
    from fastapi import HTTPException

    try:
        from ...utils.subscription_invoice import build_invoice_email_kwargs
    except ImportError:
        from utils.subscription_invoice import build_invoice_email_kwargs  # type: ignore
    try:
        from ..drivers import subscriptions as _drv_subs
    except ImportError:
        from routes.drivers import subscriptions as _drv_subs  # type: ignore

    payment = await db_supabase.find_one("subscription_payments", {"id": payment_id})
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    kwargs = await build_invoice_email_kwargs(payment)
    if kwargs is None:
        raise HTTPException(status_code=404, detail="Invoice could not be generated")

    # Per-payment cooldown: this endpoint emails a third party (the driver), so a
    # stuck/looping admin action must not be able to email-bomb them. 60s window,
    # cleared on delivery failure so a genuine retry is never blocked. Replay-safe
    # across replicas via the shared Redis SET NX (in-memory fallback in dev).
    try:
        from ...utils.redis_client import redis_delete, redis_set_nx
    except ImportError:
        from utils.redis_client import redis_delete, redis_set_nx  # type: ignore
    _cooldown_key = f"spinr:sub_invoice_resend:{payment_id}"
    if not await redis_set_nx(_cooldown_key, "1", 60):
        raise HTTPException(
            status_code=429,
            detail="Invoice was just resent — please wait a minute before retrying.",
        )

    sent = await _drv_subs._send_subscription_invoice_email(**kwargs)
    if not sent:
        await redis_delete(_cooldown_key)  # delivery failed → don't hold the cooldown against a retry
    await log_admin_action(
        admin,
        "resend_subscription_invoice",
        "subscription_payment",
        payment_id,
        {"driver_id": payment.get("driver_id"), "sent": bool(sent)},
    )
    if not sent:
        raise HTTPException(status_code=502, detail="Invoice email could not be delivered")
    return {"success": True}
