import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

try:
    from ...db import db
except ImportError:
    from db import db

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


@router.get("/subscription-plans")
async def list_subscription_plans():
    """List all Spinr Pass subscription plans."""
    plans = await db.get_rows("subscription_plans", limit=50)
    return plans


@router.post("/subscription-plans")
async def create_subscription_plan(req: SubscriptionPlanCreate):
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
        "subscriber_count": 0,
        "created_at": datetime.utcnow().isoformat(),
    }
    await db.subscription_plans.insert_one(plan)
    return plan


@router.put("/subscription-plans/{plan_id}")
async def update_subscription_plan(plan_id: str, req: SubscriptionPlanUpdate):
    """Update a subscription plan."""
    updates = {k: v for k, v in req.dict().items() if v is not None}
    if updates:
        updates["updated_at"] = datetime.utcnow().isoformat()
        await db.subscription_plans.update_one({"id": plan_id}, {"$set": updates})
    return {"success": True}


@router.delete("/subscription-plans/{plan_id}")
async def delete_subscription_plan(plan_id: str):
    """Delete a subscription plan."""
    await db.subscription_plans.delete_many({"id": plan_id})
    return {"success": True}


# ─── Driver Subscription Management ───


@router.get("/driver-subscriptions")
async def list_driver_subscriptions(status: Optional[str] = Query(None)):
    """List all driver subscriptions, optionally filtered by status."""
    subs = await db.driver_subscriptions.find({}).to_list(200)
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

    now = datetime.utcnow()
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
    all_subs = await db.driver_subscriptions.find({}).to_list(10000)

    # Fetch all plans for lookup
    all_plans = await db.get_rows("subscription_plans", limit=100)
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

    # Overall stats
    active = [s for s in all_subs if s.get("status") == "active"]
    expired = [s for s in all_subs if s.get("status") == "expired"]
    cancelled = [s for s in all_subs if s.get("status") == "cancelled"]
    total_revenue = sum(float(s.get("price") or 0) for s in all_subs)
    active_revenue = sum(float(s.get("price") or 0) for s in active)

    # Filter to date range for transactions and charts
    def parse_dt(s):
        try:
            return datetime.fromisoformat(str(s).replace("Z", "").replace("+00:00", ""))
        except Exception:
            return None

    in_range = []
    for s in all_subs:
        dt = parse_dt(s.get("created_at") or s.get("started_at"))
        if dt and range_start <= dt <= range_end:
            in_range.append(s)

    range_revenue = sum(float(s.get("price") or 0) for s in in_range)

    # Per-plan breakdown
    plan_stats = defaultdict(lambda: {"name": "", "count": 0, "revenue": 0.0, "active": 0})
    for s in all_subs:
        pid = s.get("plan_id") or "unknown"
        plan_stats[pid]["name"] = s.get("plan_name") or plan_map.get(pid, {}).get("name", "Unknown")
        plan_stats[pid]["count"] += 1
        plan_stats[pid]["revenue"] += float(s.get("price") or 0)
        if s.get("status") == "active":
            plan_stats[pid]["active"] += 1

    # Daily charts (within date range)
    num_days = min((range_end - range_start).days + 1, 365)
    daily_revenue = defaultdict(float)
    daily_new_subs = defaultdict(int)
    for s in in_range:
        dt = parse_dt(s.get("created_at") or s.get("started_at"))
        if dt:
            day_key = dt.strftime("%Y-%m-%d")
            daily_revenue[day_key] += float(s.get("price") or 0)
            daily_new_subs[day_key] += 1

    revenue_chart = []
    subscribers_chart = []
    for i in range(num_days):
        day = range_start + timedelta(days=i)
        day_key = day.strftime("%Y-%m-%d")
        day_label = day.strftime("%b %d")
        revenue_chart.append(
            {"date": day_label, "date_raw": day_key, "amount": round(daily_revenue.get(day_key, 0), 2)}
        )
        subscribers_chart.append({"date": day_label, "date_raw": day_key, "count": daily_new_subs.get(day_key, 0)})

    # Transaction list (in range, enriched)
    transactions = []
    for s in sorted(in_range, key=lambda x: x.get("created_at", ""), reverse=True):
        transactions.append(
            {
                "id": s.get("id"),
                "driver_id": s.get("driver_id"),
                "driver_name": drivers_map.get(s.get("driver_id"), s.get("driver_id", "")[:8]),
                "plan_name": s.get("plan_name") or plan_map.get(s.get("plan_id", ""), {}).get("name", "Unknown"),
                "price": float(s.get("price") or 0),
                "status": s.get("status", "unknown"),
                "started_at": s.get("started_at"),
                "expires_at": s.get("expires_at"),
                "created_at": s.get("created_at"),
            }
        )

    return {
        "stats": {
            "total_subscribers": len(all_subs),
            "active": len(active),
            "expired": len(expired),
            "cancelled": len(cancelled),
            "total_revenue": round(total_revenue, 2),
            "active_mrr": round(active_revenue, 2),
            "range_revenue": round(range_revenue, 2),
            "range_transactions": len(in_range),
        },
        "plan_breakdown": [{"plan_id": k, **v} for k, v in plan_stats.items()],
        "charts": {
            "daily_revenue": revenue_chart,
            "daily_subscribers": subscribers_chart,
        },
        "transactions": transactions,
        "service_areas": [
            {"id": a["id"], "name": a.get("name", "Unknown")}
            for a in await db.get_rows("service_areas", order="name", limit=200)
            if not a.get("parent_service_area_id")
        ],
    }
