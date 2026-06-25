"""Admin analytics — acceptance rate, cancellation breakdown, driver performance.

Provides aggregated operational intelligence for the admin dashboard.
"""

import logging
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

    # Reason / party / hour classification is done in Postgres
    # (admin_cancellation_breakdown) instead of fetching up to 5,000 cancelled
    # rides and bucketing in Python.
    try:
        bd = await db.rpc(
            "admin_cancellation_breakdown",
            {"p_start": start_date.isoformat(), "p_service_area_id": service_area_id},
        )
    except Exception as e:
        logger.error(f"Failed to aggregate cancelled rides: {e}", exc_info=True)
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(status_code=503, detail="Analytics data unavailable — database error") from e

    bd = bd[0] if isinstance(bd, list) and bd else bd
    if not isinstance(bd, dict):
        bd = {}

    total = int(bd.get("total") or 0)
    reasons = [
        {
            "reason": row.get("reason"),
            "count": int(row.get("count") or 0),
            "pct": round(int(row.get("count") or 0) / total * 100, 1) if total > 0 else 0,
        }
        for row in (bd.get("reasons") or [])
    ]
    by_party = [
        {
            "party": row.get("party"),
            "count": int(row.get("count") or 0),
            "pct": round(int(row.get("count") or 0) / total * 100, 1) if total > 0 else 0,
        }
        for row in (bd.get("by_party") or [])
    ]
    hourly_map = bd.get("hourly") or {}
    hourly = [{"hour": h, "count": int(hourly_map.get(str(h), 0) or 0)} for h in range(24)]

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

    # Per-driver ride counts are aggregated in Postgres (admin_driver_acceptance_rates)
    # over the window instead of fetching up to 10,000 rides and rolling up in
    # Python. Drivers with no rides in the window simply aren't returned; we
    # default them to zero from the `drivers` list below.
    acc_by_driver: dict = {}
    if driver_ids:
        try:
            acc_rows = await db.rpc(
                "admin_driver_acceptance_rates",
                {"p_start": start_date.isoformat(), "p_service_area_id": service_area_id},
            )
        except Exception as e:
            logger.error(
                f"Failed to aggregate rides for acceptance stats: {e}",
                exc_info=True,
                extra={"domain": "admin"},
            )
            raise HTTPException(status_code=503, detail="analytics_unavailable") from e
        for r in acc_rows or []:
            did = r.get("driver_id")
            if did:
                acc_by_driver[did] = r

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

    users_map = {u["id"]: u for u in users_list if u.get("id")}

    result = []
    for driver in drivers:
        driver_id = driver["id"]
        agg = acc_by_driver.get(driver_id) or {}
        total_assigned = int(agg.get("total_rides") or 0)
        completed = int(agg.get("completed") or 0)
        cancelled_by_driver = int(agg.get("cancelled_by_driver") or 0)

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

    # Status counts, completed revenue/tips, and the daily/hourly buckets are
    # aggregated in Postgres (admin_analytics_overview) instead of fetching up to
    # 10,000 rides and summing in Python.
    try:
        ov = await db.rpc("admin_analytics_overview", {"p_start": start_date.isoformat()})
    except Exception as e:
        logger.error(f"Failed to aggregate rides for overview: {e}", exc_info=True)
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(status_code=503, detail="Analytics data unavailable — database error") from e

    ov = ov[0] if isinstance(ov, list) and ov else ov
    if not isinstance(ov, dict):
        ov = {}

    total = int(ov.get("total") or 0)
    completed = int(ov.get("completed") or 0)
    cancelled = int(ov.get("cancelled") or 0)
    in_progress = int(ov.get("in_progress") or 0)
    searching = int(ov.get("searching") or 0)
    scheduled = int(ov.get("scheduled") or 0)

    completion_rate = round(completed / total * 100, 1) if total > 0 else 0
    cancellation_rate = round(cancelled / total * 100, 1) if total > 0 else 0

    total_revenue = float(Decimal(str(ov.get("total_revenue") or 0)))
    total_tips = float(Decimal(str(ov.get("total_tips") or 0)))
    avg_fare = round(total_revenue / completed, 2) if completed > 0 else 0

    # Daily ride counts for chart (date -> {completed, cancelled, total}).
    daily_map = ov.get("daily") or {}
    daily_chart = [
        {
            "date": date,
            "completed": int((counts or {}).get("completed") or 0),
            "cancelled": int((counts or {}).get("cancelled") or 0),
            "total": int((counts or {}).get("total") or 0),
        }
        for date, counts in sorted(daily_map.items())
    ]

    # Peak hours — top 5 by ride count.
    hourly_map = ov.get("hourly") or {}
    peak_hours = sorted(((int(h), int(c or 0)) for h, c in hourly_map.items()), key=lambda x: x[1], reverse=True)[:5]

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


# ── Main dashboard overview (service-area + time pills) ─────────────


def _dashboard_window(range_key: str) -> tuple:
    """Return (start, end) datetimes for a dashboard time pill."""
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_key == "today":
        return midnight, now
    if range_key == "yesterday":
        return midnight - timedelta(days=1), midnight
    if range_key == "7d":
        return now - timedelta(days=7), now
    return now - timedelta(hours=24), now  # "24h" default


_DASH_ACTIVE_STATUSES = ["searching", "driver_assigned", "driver_accepted", "driver_arrived", "in_progress"]
_DASH_BREAKDOWN_STATUSES = ["searching", "in_progress", "completed", "cancelled", "scheduled"]


@api_router.get("/dashboard")
async def get_dashboard_overview(
    range: str = Query("24h", pattern="^(today|yesterday|24h|7d)$"),
    service_area_id: Optional[str] = Query(default=None),
    admin: dict = Depends(get_admin_user),
):
    """Main-dashboard stat cards, filtered by service area + time window.

    Everything is aggregated by the query at request time — no stored function /
    extra schema, and no Python summing. Counts use PostgREST ``count="exact"``;
    money uses PostgREST aggregate ``.sum()``. If aggregate functions are not
    enabled on the project, the counts still render and money returns null.

    Spinr takes 0% of ride fares, so `driver_earnings` is the fare drivers keep
    and `platform_revenue` is Spinr Pass (+ corporate later) — never a ride cut.
    """
    import asyncio as _asyncio

    try:
        from ... import db_supabase as _dbs
    except ImportError:
        import db_supabase as _dbs  # type: ignore

    start, end = _dashboard_window(range)
    start_iso, end_iso = start.isoformat(), end.isoformat()
    area = {"service_area_id": service_area_id} if service_area_id else {}

    def _in_range(extra: Optional[dict] = None) -> dict:
        f: dict = {"$and": [{"created_at": {"$gte": start_iso}}, {"created_at": {"$lte": end_iso}}]}
        return {**extra, **f} if extra else f

    _count = _dbs.count_documents

    # ── Counts — aggregated in-query via PostgREST count="exact" ──────────
    (
        drivers_total,
        drivers_online,
        drivers_active,
        drivers_new,
        riders_total,
        riders_new,
        rides_total,
        rides_active,
        *bd_counts,
    ) = await _asyncio.gather(
        _count("drivers", area),
        _count("drivers", {**area, "is_online": True}),
        _count("drivers", {**area, "is_available": True}),
        _count("drivers", _in_range(area)),
        _count("users", {"role": "rider"}),
        _count("users", _in_range({"role": "rider"})),
        _count("rides", _in_range(area)),
        _count("rides", {**area, "status": {"$in": _DASH_ACTIVE_STATUSES}}),
        *[_count("rides", _in_range({**area, "status": s})) for s in _DASH_BREAKDOWN_STATUSES],
    )
    breakdown = {s: int(bd_counts[i] or 0) for i, s in enumerate(_DASH_BREAKDOWN_STATUSES)}

    # ── Money — aggregated in-query via PostgREST .sum() (no function/Python) ─
    money: dict = {
        "ride_volume": None,
        "driver_earnings": None,
        "spinr_pass_earnings": None,
        "platform_revenue": None,
        "aggregates_enabled": True,
    }

    def _ride_money_query():
        q = (
            _dbs.supabase.table("rides")
            .select("ride_volume:grand_total.sum(),driver_earnings:driver_earnings.sum()")
            .eq("status", "completed")
            .gte("created_at", start_iso)
            .lt("created_at", end_iso)
        )
        if service_area_id:
            q = q.eq("service_area_id", service_area_id)
        return q.execute()

    try:
        _rm = await _dbs.run_sync(_ride_money_query)
        _row = (getattr(_rm, "data", None) or [{}])[0]
        money["ride_volume"] = round(float(_row.get("ride_volume") or 0), 2)
        # 0% commission: when driver_earnings wasn't snapshotted, the fare IS the
        # driver's earning, so fall back to ride_volume.
        money["driver_earnings"] = round(float(_row.get("driver_earnings") or _row.get("ride_volume") or 0), 2)

        # Spinr Pass — scope to the area's drivers when an area is selected.
        area_driver_ids: Optional[list] = None
        if service_area_id:
            _ad = await _dbs.get_rows("drivers", area, columns="id", limit=10000)
            area_driver_ids = [d["id"] for d in (_ad or []) if d.get("id")]

        def _sub_money_query():
            q = (
                _dbs.supabase.table("subscription_payments")
                .select("spinr_pass:amount.sum()")
                .gte("created_at", start_iso)
                .lt("created_at", end_iso)
            )
            if area_driver_ids is not None:
                q = q.in_("driver_id", area_driver_ids or ["__none__"])
            return q.execute()

        _sm = await _dbs.run_sync(_sub_money_query)
        _srow = (getattr(_sm, "data", None) or [{}])[0]
        _pass = round(float(_srow.get("spinr_pass") or 0), 2)
        money["spinr_pass_earnings"] = _pass
        # Spinr takes 0% of rides — platform revenue is Spinr Pass (+ corporate later).
        money["platform_revenue"] = _pass
    except Exception as e:
        # Most likely cause: PostgREST aggregate functions disabled on the project.
        # Counts still render; surface the money cards as unavailable rather than 500.
        logger.warning(f"dashboard money aggregation failed (aggregates enabled?): {e}")
        money["aggregates_enabled"] = False

    return {
        "range": range,
        "service_area_id": service_area_id,
        "window": {"start": start_iso, "end": end_iso},
        "drivers": {
            "total": int(drivers_total or 0),
            "online": int(drivers_online or 0),
            "active": int(drivers_active or 0),
            "new": int(drivers_new or 0),
        },
        "riders": {"total": int(riders_total or 0), "new": int(riders_new or 0)},
        "rides": {"total": int(rides_total or 0), "active": int(rides_active or 0), "breakdown": breakdown},
        "money": money,
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

    # Per-driver rollup is done in Postgres (admin_driver_offer_stats) over the
    # append-only ride_offers ledger, instead of streaming up to 100k offer rows
    # into Python. Area scoping is applied inside the function.
    try:
        rows = await db.rpc(
            "admin_driver_offer_stats",
            {"p_start": start_date.isoformat(), "p_service_area_id": service_area_id},
        )
    except Exception as e:
        logger.error(
            f"Failed to aggregate ride_offers for offer stats: {e}",
            exc_info=True,
            extra={"domain": "admin"},
        )
        raise HTTPException(status_code=503, detail="analytics_unavailable") from e

    by_driver: dict = {}
    for r in rows or []:
        did = r.get("driver_id")
        if not did:
            continue
        by_driver[did] = {
            "offered": int(r.get("offered") or 0),
            "accepted": int(r.get("accepted") or 0),
            "declined": int(r.get("declined") or 0),
            "ignored": int(r.get("ignored") or 0),
            "preempted": int(r.get("preempted") or 0),
            "pending": int(r.get("pending") or 0),
            "avg_response_secs": (
                round(float(r["avg_response_secs"]), 1) if r.get("avg_response_secs") is not None else None
            ),
        }

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
                "avg_response_seconds": agg["avg_response_secs"],
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

    # Per-day bucketing done in Postgres (admin_driver_offer_trends) instead of
    # scanning up to 100k offer rows. A driver_id filter takes precedence; else
    # an optional service-area scope is applied inside the function.
    try:
        rows = await db.rpc(
            "admin_driver_offer_trends",
            {
                "p_start": start_date.isoformat(),
                "p_driver_id": driver_id,
                "p_service_area_id": service_area_id,
            },
        )
    except Exception as e:
        logger.error(
            f"Failed to aggregate ride_offers for offer trends: {e}",
            exc_info=True,
            extra={"domain": "admin"},
        )
        raise HTTPException(status_code=503, detail="analytics_unavailable") from e

    daily_chart = [
        {
            "date": str(r.get("day")),
            "offered": int(r.get("offered") or 0),
            "accepted": int(r.get("accepted") or 0),
            "declined": int(r.get("declined") or 0),
            "ignored": int(r.get("ignored") or 0),
            "preempted": int(r.get("preempted") or 0),
        }
        for r in (rows or [])
    ]
    return {
        "date_range": date_range,
        "driver_id": driver_id,
        "service_area_id": service_area_id,
        "daily_chart": daily_chart,
    }
