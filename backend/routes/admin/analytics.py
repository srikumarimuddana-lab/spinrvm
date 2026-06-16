"""Admin analytics — acceptance rate, cancellation breakdown, driver performance.

Provides aggregated operational intelligence for the admin dashboard.
"""

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

try:
    from ...db import db
    from ...dependencies import get_admin_user
    from ...utils.redis_client import redis_get, redis_set
except ImportError:
    from db import db
    from dependencies import get_admin_user
    from utils.redis_client import redis_get, redis_set  # noqa: F401

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/analytics", tags=["Admin Analytics"])

_OVERVIEW_CACHE_TTL = 300  # 5 minutes


def _parse_date_range(date_range: str) -> datetime:
    """Convert a shorthand range like '7d', '30d', '90d' to a start datetime."""
    now = datetime.now(timezone.utc)
    mapping = {
        "today": timedelta(days=1),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "90d": timedelta(days=90),
        "1y": timedelta(days=365),
    }
    delta = mapping.get(date_range, timedelta(days=30))
    return now - delta


def _parse_iso(value) -> Optional[datetime]:
    """Best-effort parse of an ISO-8601 timestamp string to an aware datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _fetch_rows_in_chunks(table: str, ids: list, chunk_size: int = 200) -> list:
    """Fetch rows by id in bounded `IN (...)` batches.

    A single huge `IN` over thousands of distinct ids can blow past URL/query
    limits and dominate latency. Chunking keeps each query bounded.
    """
    out: list = []
    for i in range(0, len(ids), chunk_size):
        batch = ids[i : i + chunk_size]
        if not batch:
            continue
        out.extend(await db.get_rows(table, {"id": {"$in": batch}}, limit=len(batch)))
    return out


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
        filters: dict = {
            "status": "cancelled",
            "created_at": {"$gte": start_date.isoformat()},
        }
        if service_area_id:
            filters["service_area_id"] = service_area_id
        filtered = await db.get_rows("rides", filters, limit=5000, order="created_at")
    except Exception as e:
        logger.error(f"Failed to fetch cancelled rides: {e}", exc_info=True)
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(status_code=503, detail="Analytics data unavailable — database error") from e

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
        {
            "reason": reason,
            "count": count,
            "pct": round(count / total * 100, 1) if total > 0 else 0,
        }
        for reason, count in reason_counter.most_common()
    ]

    by_party = [
        {
            "party": party,
            "count": count,
            "pct": round(count / total * 100, 1) if total > 0 else 0,
        }
        for party, count in cancelled_by_counter.most_common()
    ]

    hourly = [{"hour": h, "count": hourly_cancellations.get(h, 0)} for h in range(24)]

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
        logger.error(f"Failed to fetch drivers: {e}", exc_info=True, extra={"domain": "admin"})
        raise HTTPException(status_code=503, detail="analytics_unavailable") from e

    if service_area_id:
        drivers = [d for d in drivers if d.get("service_area_id") == service_area_id]

    driver_ids = [d["id"] for d in drivers]
    user_ids = [d["user_id"] for d in drivers if d.get("user_id")]

    # Batch-fetch all rides and users in 2 queries instead of 2N (F-48).
    all_rides: list = []
    if driver_ids:
        try:
            all_rides = await db.get_rows(
                "rides",
                {
                    "driver_id": {"$in": driver_ids},
                    "created_at": {"$gte": start_date.isoformat()},
                },
                limit=10000,
            )
        except Exception as e:
            logger.error(
                f"Failed to fetch rides for acceptance stats: {e}",
                exc_info=True,
                extra={"domain": "admin"},
            )
            raise HTTPException(status_code=503, detail="analytics_unavailable") from e

    users_list: list = []
    if user_ids:
        try:
            # Only the driver's display name is read from these rows — project
            # the name columns so we don't pull base64 profile_image blobs.
            users_list = await db.get_rows(
                "users", {"id": {"$in": user_ids}}, columns="id,first_name,last_name", limit=len(user_ids)
            )
        except Exception as e:
            logger.error(
                f"Failed to fetch users for acceptance stats: {e}",
                exc_info=True,
                extra={"domain": "admin"},
            )
            raise HTTPException(status_code=503, detail="analytics_unavailable") from e

    rides_by_driver: dict = {}
    for r in all_rides:
        did = r.get("driver_id")
        if did:
            rides_by_driver.setdefault(did, []).append(r)

    users_map = {u["id"]: u for u in users_list if u.get("id")}

    result = []
    for driver in drivers:
        driver_id = driver["id"]
        period_rides = rides_by_driver.get(driver_id, [])
        total_assigned = len(period_rides)
        completed = sum(1 for r in period_rides if r.get("status") == "completed")
        cancelled_by_driver = sum(
            1
            for r in period_rides
            if r.get("status") == "cancelled" and "driver" in (r.get("cancellation_reason") or "").lower()
        )

        acceptance_rate = round((completed / total_assigned * 100), 1) if total_assigned > 0 else 0
        cancellation_rate = round((cancelled_by_driver / total_assigned * 100), 1) if total_assigned > 0 else 0

        user = users_map.get(driver.get("user_id"))
        name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Unknown"

        result.append(
            {
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
            }
        )

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
    """High-level operational metrics for the analytics dashboard. Cached 5 min (F-50)."""
    import json as _json

    cache_key = f"analytics:overview:{date_range}"
    cached = await redis_get(cache_key)
    if cached:
        try:
            return _json.loads(cached)
        except Exception:  # noqa: S110
            pass  # corrupt cache entry — fall through to fresh fetch

    start_date = _parse_date_range(date_range)

    try:
        period_rides = await db.get_rows(
            "rides",
            {"created_at": {"$gte": start_date.isoformat()}},
            limit=10000,
            order="created_at",
        )
    except Exception as e:
        logger.error(f"Failed to fetch rides: {e}", exc_info=True)
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(status_code=503, detail="Analytics data unavailable — database error") from e

    total = len(period_rides)
    completed = sum(1 for r in period_rides if r.get("status") == "completed")
    cancelled = sum(1 for r in period_rides if r.get("status") == "cancelled")
    in_progress = sum(
        1 for r in period_rides if r.get("status") in ("in_progress", "driver_arrived", "driver_accepted")
    )
    searching = sum(1 for r in period_rides if r.get("status") == "searching")
    scheduled = sum(1 for r in period_rides if r.get("is_scheduled"))

    completion_rate = round(completed / total * 100, 1) if total > 0 else 0
    cancellation_rate = round(cancelled / total * 100, 1) if total > 0 else 0

    total_revenue = float(
        sum(Decimal(str(r.get("total_fare") or 0)) for r in period_rides if r.get("status") == "completed")
    )
    total_tips = float(
        sum(Decimal(str(r.get("tip_amount") or 0)) for r in period_rides if r.get("status") == "completed")
    )
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

    daily_chart = [{"date": date, **counts} for date, counts in sorted(daily.items())]

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

    result = {
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
    try:
        await redis_set(cache_key, _json.dumps(result), ttl=_OVERVIEW_CACHE_TTL)
    except Exception:  # noqa: S110
        pass  # Redis unavailable — return fresh result uncached
    return result


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
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        # Time filter and ordering happen DB-side: ascending order with the
        # filter applied in Python fetched the OLDEST 500 rows for the area,
        # so the requested window was empty once history outgrew the limit.
        # Newest-first + $gte cutoff always returns the requested window and
        # is served by idx_surge_pricing_area_created (migration 142).
        records = await db.get_rows(
            "surge_pricing",
            {"service_area_id": area_id, "created_at": {"$gte": cutoff}},
            limit=500,
            order="created_at",
            desc=True,
            columns="multiplier,demand_count,supply_count,ratio,source,created_at",
        )
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
        ]
        # Reverse to chronological order
        filtered.reverse()
        return {"area_id": area_id, "hours": hours, "history": filtered}
    except Exception as e:
        logger.error(
            f"Failed to fetch surge history: {e}",
            exc_info=True,
            extra={"domain": "admin"},
        )
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(status_code=503, detail="Surge history unavailable — database error") from e


# ── Driver Offer Stats (dispatch funnel) ─────────────────────────────


@api_router.get("/driver-offer-stats")
async def get_driver_offer_stats(
    date_range: str = Query("30d", pattern="^(today|7d|30d|90d|1y)$"),
    service_area_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    admin: dict = Depends(get_admin_user),
):
    """Per-driver aggregation of ride offers — who accepted / declined / ignored.

    Reads the append-only ``ride_offers`` ledger (offered_at, responded_at,
    status) over the window and rolls it up per driver so ops can see who
    accepts most, who declines most, and who ignores (lets offers time out)
    most. ``ignored`` == offers that expired without a response; ``pending``
    (still in-flight) is excluded from the decided-rate denominators.
    """
    start_date = _parse_date_range(date_range)

    try:
        offers = await db.get_rows(
            "ride_offers",
            {"offered_at": {"$gte": start_date.isoformat()}},
            limit=100000,
        )
    except Exception as e:
        logger.error(
            f"Failed to fetch ride_offers for offer stats: {e}",
            exc_info=True,
            extra={"domain": "admin"},
        )
        raise HTTPException(status_code=503, detail="analytics_unavailable") from e

    # Roll up per driver.
    by_driver: dict = defaultdict(
        lambda: {
            "offered": 0,
            "accepted": 0,
            "declined": 0,
            "ignored": 0,
            "preempted": 0,
            "pending": 0,
            "_response_secs": [],
        }
    )
    for o in offers:
        did = o.get("driver_id")
        if not did:
            continue
        agg = by_driver[did]
        agg["offered"] += 1
        status = o.get("status")
        if status == "accepted":
            agg["accepted"] += 1
        elif status == "declined":
            agg["declined"] += 1
        elif status == "expired":
            # Genuine timeout — the driver received the offer and never responded.
            agg["ignored"] += 1
        elif status == "preempted":
            # Another driver accepted first — NOT an ignore; tracked separately
            # and excluded from the decided-rate denominators below.
            agg["preempted"] += 1
        elif status == "pending":
            agg["pending"] += 1
        # Response latency for the offers the driver actually answered.
        if status in ("accepted", "declined"):
            offered_at = _parse_iso(o.get("offered_at"))
            responded_at = _parse_iso(o.get("responded_at"))
            if offered_at and responded_at and responded_at >= offered_at:
                agg["_response_secs"].append((responded_at - offered_at).total_seconds())

    driver_ids = list(by_driver.keys())
    drivers_list: list = []
    if driver_ids:
        try:
            drivers_list = await _fetch_rows_in_chunks("drivers", driver_ids)
        except Exception as e:
            logger.error(
                f"Failed to fetch drivers for offer stats: {e}",
                exc_info=True,
                extra={"domain": "admin"},
            )
            raise HTTPException(status_code=503, detail="analytics_unavailable") from e

    if service_area_id:
        drivers_list = [d for d in drivers_list if d.get("service_area_id") == service_area_id]

    drivers_map = {d["id"]: d for d in drivers_list if d.get("id")}

    user_ids = list({d.get("user_id") for d in drivers_list if d.get("user_id")})
    users_list: list = []
    if user_ids:
        try:
            users_list = await _fetch_rows_in_chunks("users", user_ids)
        except Exception as e:
            logger.error(
                f"Failed to fetch users for offer stats: {e}",
                exc_info=True,
                extra={"domain": "admin"},
            )
            raise HTTPException(status_code=503, detail="analytics_unavailable") from e
    users_map = {u["id"]: u for u in users_list if u.get("id")}

    result = []
    for did, agg in by_driver.items():
        driver = drivers_map.get(did)
        # When a service-area filter is set, drop drivers outside it.
        if service_area_id and driver is None:
            continue
        decided = agg["accepted"] + agg["declined"] + agg["ignored"]
        user = users_map.get(driver.get("user_id")) if driver else None
        name = (
            f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
            if user
            else (driver.get("name") if driver else None)
        ) or did[:12]
        response_secs = agg.pop("_response_secs")
        result.append(
            {
                "driver_id": did,
                "name": name,
                "offered": agg["offered"],
                "accepted": agg["accepted"],
                "declined": agg["declined"],
                "ignored": agg["ignored"],
                "preempted": agg["preempted"],
                "pending": agg["pending"],
                "accept_rate": round(agg["accepted"] / decided * 100, 1) if decided else 0.0,
                "decline_rate": round(agg["declined"] / decided * 100, 1) if decided else 0.0,
                "ignore_rate": round(agg["ignored"] / decided * 100, 1) if decided else 0.0,
                "avg_response_seconds": (round(sum(response_secs) / len(response_secs), 1) if response_secs else None),
                "rating": driver.get("rating") if driver else None,
                "is_online": driver.get("is_online", False) if driver else False,
            }
        )

    # Default ordering: most offers handled first (most active drivers on top).
    result.sort(key=lambda x: x["offered"], reverse=True)

    totals = {
        "offered": sum(r["offered"] for r in result),
        "accepted": sum(r["accepted"] for r in result),
        "declined": sum(r["declined"] for r in result),
        "ignored": sum(r["ignored"] for r in result),
        "preempted": sum(r["preempted"] for r in result),
        "pending": sum(r["pending"] for r in result),
    }

    return {
        "date_range": date_range,
        "total_drivers": len(result),
        "totals": totals,
        "drivers": result[:limit],
    }


@api_router.get("/driver-offer-trends")
async def get_driver_offer_trends(
    date_range: str = Query("30d", pattern="^(today|7d|30d|90d|1y)$"),
    driver_id: Optional[str] = None,
    service_area_id: Optional[str] = None,
    admin: dict = Depends(get_admin_user),
):
    """Daily offer-outcome trend for the Driver Offers page chart.

    Buckets ride_offers by day (offered_at) into accepted/declined/ignored/
    preempted counts. Optional driver_id drills into one driver's trend;
    optional service_area_id scopes to drivers in that area.
    """
    start_date = _parse_date_range(date_range)

    filters: dict = {"offered_at": {"$gte": start_date.isoformat()}}
    if driver_id:
        filters["driver_id"] = driver_id
    try:
        offers = await db.get_rows("ride_offers", filters, limit=100000)
    except Exception as e:
        logger.error(
            f"Failed to fetch ride_offers for offer trends: {e}",
            exc_info=True,
            extra={"domain": "admin"},
        )
        raise HTTPException(status_code=503, detail="analytics_unavailable") from e

    # Scope to a service area by intersecting with that area's drivers.
    if service_area_id and not driver_id:
        try:
            area_drivers = await db.get_rows("drivers", {"service_area_id": service_area_id}, limit=10000)
        except Exception as e:
            logger.error(
                f"Failed to fetch area drivers for offer trends: {e}",
                exc_info=True,
                extra={"domain": "admin"},
            )
            raise HTTPException(status_code=503, detail="analytics_unavailable") from e
        area_ids = {d["id"] for d in area_drivers if d.get("id")}
        offers = [o for o in offers if o.get("driver_id") in area_ids]

    daily: dict = defaultdict(lambda: {"offered": 0, "accepted": 0, "declined": 0, "ignored": 0, "preempted": 0})
    for o in offers:
        day = (o.get("offered_at") or "")[:10]
        if not day:
            continue
        b = daily[day]
        b["offered"] += 1
        status = o.get("status")
        if status == "accepted":
            b["accepted"] += 1
        elif status == "declined":
            b["declined"] += 1
        elif status == "expired":
            b["ignored"] += 1
        elif status == "preempted":
            b["preempted"] += 1

    daily_chart = [{"date": d, **counts} for d, counts in sorted(daily.items())]
    return {
        "date_range": date_range,
        "driver_id": driver_id,
        "service_area_id": service_area_id,
        "daily_chart": daily_chart,
    }
