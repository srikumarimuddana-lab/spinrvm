import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

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
        "subscriber_count": 0,
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
