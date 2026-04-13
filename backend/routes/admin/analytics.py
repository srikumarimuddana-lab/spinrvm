"""Admin analytics — acceptance rate, cancellation breakdown, driver performance.

Provides aggregated operational intelligence for the admin dashboard.
"""

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query

try:
    from ...db import db
    from ...dependencies import get_admin_user
except ImportError:
    from db import db
    from dependencies import get_admin_user

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/admin/analytics", tags=["Admin Analytics"])


def _parse_date_range(date_range: str) -> datetime:
    """Convert a shorthand range like '7d', '30d', '90d' to a start datetime."""
    now = datetime.utcnow()
    mapping = {
        "today": timedelta(days=1),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "90d": timedelta(days=90),
        "1y": timedelta(days=365),
    }
    delta = mapping.get(date_range, timedelta(days=30))
    return now - delta


# ── Cancellation Reason Breakdown ────────────────────────────────────


@api_router.get("/cancellation-reasons")
async def get_cancellation_breakdown(
    date_range: str = Query("30d", pattern="^(today|7d|30d|90d|1y)$"),
    service_area_id: Optional[str] = None,
    admin: dict = Depends(get_admin_user),
):
    """Aggregated cancellation reason breakdown by date range and optionally service area."""
    start_date = _parse_date_range(date_range)

    try:
        filters = {"status": "cancelled"}
        rides = await db.get_rows("rides", filters, limit=10000, order_by="created_at", order_desc=True)
    except Exception as e:
        logger.error(f"Failed to fetch cancelled rides: {e}")
        rides = []

    # Filter by date and optionally by service area
    filtered = []
    for r in rides:
        created = r.get("created_at", "")
        if isinstance(created, str) and created < start_date.isoformat():
            continue
        if service_area_id and r.get("service_area_id") != service_area_id:
            continue
        filtered.append(r)

    # Categorize cancellation reasons
    reason_counter = Counter()
    cancelled_by_counter = Counter()
    hourly_cancellations = defaultdict(int)

    for r in filtered:
        raw_reason = r.get("cancellation_reason", "")
        cancelled_by = "unknown"

        if not raw_reason:
            reason = "unspecified"
        elif "no nearby drivers" in raw_reason.lower() or "no driver" in raw_reason.lower():
            reason = "no_drivers_available"
        elif "rider" in raw_reason.lower() or "cancelled by rider" in raw_reason.lower():
            reason = "rider_cancelled"
            cancelled_by = "rider"
        elif "driver" in raw_reason.lower():
            reason = "driver_cancelled"
            cancelled_by = "driver"
        elif "timeout" in raw_reason.lower() or "expired" in raw_reason.lower():
            reason = "search_timeout"
        elif "scheduled" in raw_reason.lower():
            reason = "scheduled_cancelled"
            cancelled_by = "rider"
        else:
            reason = "other"

        reason_counter[reason] += 1
        cancelled_by_counter[cancelled_by] += 1

        # Hourly distribution
        created = r.get("cancelled_at") or r.get("updated_at") or r.get("created_at", "")
        if isinstance(created, str) and len(created) >= 13:
            try:
                hour = int(created[11:13])
                hourly_cancellations[hour] += 1
            except (ValueError, IndexError):
                pass

    total = len(filtered)
    reasons = [
        {"reason": reason, "count": count, "pct": round(count / total * 100, 1) if total > 0 else 0}
        for reason, count in reason_counter.most_common()
    ]

    by_party = [
        {"party": party, "count": count, "pct": round(count / total * 100, 1) if total > 0 else 0}
        for party, count in cancelled_by_counter.most_common()
    ]

    hourly = [
        {"hour": h, "count": hourly_cancellations.get(h, 0)}
        for h in range(24)
    ]

    return {
        "total_cancellations": total,
        "date_range": date_range,
        "reasons": reasons,
        "by_party": by_party,
        "hourly_distribution": hourly,
    }


# ── Driver Acceptance Rates ──────────────────────────────────────────


@api_router.get("/driver-acceptance")
async def get_driver_acceptance_rates(
    date_range: str = Query("30d", pattern="^(today|7d|30d|90d|1y)$"),
    service_area_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_admin_user),
):
    """Driver acceptance rate rankings and performance metrics."""
    start_date = _parse_date_range(date_range)

    try:
        drivers = await db.get_rows("drivers", {}, limit=500)
    except Exception as e:
        logger.error(f"Failed to fetch drivers: {e}")
        drivers = []

    if service_area_id:
        drivers = [d for d in drivers if d.get("service_area_id") == service_area_id]

    result = []
    for driver in drivers:
        driver_id = driver["id"]
        try:
            all_rides = await db.get_rows(
                "rides",
                {"driver_id": driver_id},
                limit=500,
                order_by="created_at",
                order_desc=True,
            )
        except Exception:
            all_rides = []

        # Filter by date range
        period_rides = [
            r for r in all_rides
            if isinstance(r.get("created_at", ""), str) and r.get("created_at", "") >= start_date.isoformat()
        ]

        total_assigned = len(period_rides)
        completed = sum(1 for r in period_rides if r.get("status") == "completed")
        cancelled_by_driver = sum(
            1 for r in period_rides
            if r.get("status") == "cancelled" and "driver" in (r.get("cancellation_reason") or "").lower()
        )

        acceptance_rate = round((completed / total_assigned * 100), 1) if total_assigned > 0 else 0
        cancellation_rate = round((cancelled_by_driver / total_assigned * 100), 1) if total_assigned > 0 else 0

        # Get driver name
        user = await db.users.find_one({"id": driver.get("user_id")})
        name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Unknown"

        result.append({
            "driver_id": driver_id,
            "name": name,
            "total_rides": total_assigned,
            "completed": completed,
            "cancelled_by_driver": cancelled_by_driver,
            "acceptance_rate": acceptance_rate,
            "cancellation_rate": cancellation_rate,
            "rating": driver.get("rating", 0),
            "lat": driver.get("lat"),
            "lng": driver.get("lng"),
            "is_online": driver.get("is_online", False),
        })

    # Sort by acceptance rate descending
    result.sort(key=lambda x: x["acceptance_rate"], reverse=True)

    # Summary stats
    avg_acceptance = round(sum(r["acceptance_rate"] for r in result) / len(result), 1) if result else 0
    low_performers = [r for r in result if r["acceptance_rate"] < 70 and r["total_rides"] >= 5]

    return {
        "date_range": date_range,
        "total_drivers": len(result),
        "avg_acceptance_rate": avg_acceptance,
        "low_performer_count": len(low_performers),
        "drivers": result[:limit],
    }


# ── Operational Overview ─────────────────────────────────────────────


@api_router.get("/overview")
async def get_analytics_overview(
    date_range: str = Query("30d", pattern="^(today|7d|30d|90d|1y)$"),
    admin: dict = Depends(get_admin_user),
):
    """High-level operational metrics for the analytics dashboard."""
    start_date = _parse_date_range(date_range)

    try:
        all_rides = await db.get_rows("rides", {}, limit=10000, order_by="created_at", order_desc=True)
    except Exception as e:
        logger.error(f"Failed to fetch rides: {e}")
        all_rides = []

    period_rides = [
        r for r in all_rides
        if isinstance(r.get("created_at", ""), str) and r.get("created_at", "") >= start_date.isoformat()
    ]

    total = len(period_rides)
    completed = sum(1 for r in period_rides if r.get("status") == "completed")
    cancelled = sum(1 for r in period_rides if r.get("status") == "cancelled")
    in_progress = sum(1 for r in period_rides if r.get("status") in ("in_progress", "driver_arrived", "driver_accepted"))
    searching = sum(1 for r in period_rides if r.get("status") == "searching")
    scheduled = sum(1 for r in period_rides if r.get("is_scheduled"))

    completion_rate = round(completed / total * 100, 1) if total > 0 else 0
    cancellation_rate = round(cancelled / total * 100, 1) if total > 0 else 0

    total_revenue = sum(float(r.get("total_fare") or 0) for r in period_rides if r.get("status") == "completed")
    total_tips = sum(float(r.get("tip_amount") or 0) for r in period_rides if r.get("status") == "completed")
    avg_fare = round(total_revenue / completed, 2) if completed > 0 else 0

    # Daily ride counts for chart
    daily = defaultdict(lambda: {"completed": 0, "cancelled": 0, "total": 0})
    for r in period_rides:
        date_str = (r.get("created_at") or "")[:10]
        if date_str:
            daily[date_str]["total"] += 1
            if r.get("status") == "completed":
                daily[date_str]["completed"] += 1
            elif r.get("status") == "cancelled":
                daily[date_str]["cancelled"] += 1

    daily_chart = [
        {"date": date, **counts}
        for date, counts in sorted(daily.items())
    ]

    # Peak hours
    hourly = defaultdict(int)
    for r in period_rides:
        created = r.get("created_at", "")
        if isinstance(created, str) and len(created) >= 13:
            try:
                hour = int(created[11:13])
                hourly[hour] += 1
            except (ValueError, IndexError):
                pass

    peak_hours = sorted(hourly.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "date_range": date_range,
        "total_rides": total,
        "completed": completed,
        "cancelled": cancelled,
        "in_progress": in_progress,
        "searching": searching,
        "scheduled": scheduled,
        "completion_rate": completion_rate,
        "cancellation_rate": cancellation_rate,
        "total_revenue": round(total_revenue, 2),
        "total_tips": round(total_tips, 2),
        "avg_fare": avg_fare,
        "daily_chart": daily_chart,
        "peak_hours": [{"hour": h, "rides": c} for h, c in peak_hours],
    }


# ── Demand Forecasting ──────────────────────────────────────────────


@api_router.get("/demand-forecast")
async def get_demand_forecast(
    area_id: Optional[str] = None,
    hours_ahead: int = Query(24, ge=1, le=72),
    admin: dict = Depends(get_admin_user),
):
    """Get hourly demand forecast for the next N hours."""
    try:
        from utils.demand_forecast import forecast_demand
    except ImportError:
        from ...utils.demand_forecast import forecast_demand

    forecast = await forecast_demand(area_id, hours_ahead)
    return {"hours_ahead": hours_ahead, "area_id": area_id, "forecast": forecast}


@api_router.get("/demand-forecast/summary")
async def get_demand_forecast_summary(
    area_id: Optional[str] = None,
    admin: dict = Depends(get_admin_user),
):
    """Get high-level demand forecast summary for the dashboard."""
    try:
        from utils.demand_forecast import get_forecast_summary
    except ImportError:
        from ...utils.demand_forecast import get_forecast_summary

    return await get_forecast_summary(area_id)


# ── Surge History ────────────────────────────────────────────────────


@api_router.get("/surge-history")
async def get_surge_history(
    area_id: str = Query(...),
    hours: int = Query(24, ge=1, le=168),
    admin: dict = Depends(get_admin_user),
):
    """Get surge pricing history for a specific service area (last N hours)."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    try:
        records = await db.get_rows(
            "surge_pricing",
            {"service_area_id": area_id},
            limit=500,
            order_by="created_at",
            order_desc=True,
        )
        # Filter by time
        filtered = [
            {
                "multiplier": r.get("multiplier", 1.0),
                "demand_count": r.get("demand_count", 0),
                "supply_count": r.get("supply_count", 0),
                "ratio": r.get("ratio", 0),
                "source": r.get("source", "auto"),
                "created_at": r.get("created_at"),
            }
            for r in records
            if isinstance(r.get("created_at", ""), str) and r.get("created_at", "") >= cutoff
        ]
        # Reverse to chronological order
        filtered.reverse()
        return {"area_id": area_id, "hours": hours, "history": filtered}
    except Exception as e:
        logger.error(f"Failed to fetch surge history: {e}")
        return {"area_id": area_id, "hours": hours, "history": []}
