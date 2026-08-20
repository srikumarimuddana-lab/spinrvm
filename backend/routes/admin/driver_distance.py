"""Admin Distance Travelled / Distance Logs — per-driver, per-Regina-day.

The insurance/ops view of how far a driver drove in each tracking phase:

  * ``GET /drivers/{id}/distance-travelled`` — one row per Regina day over a
    range (default 30, max 92 days): km + duration for driving-around (P1),
    on-pickup-way (P2 incl. waiting at pickup), on-ride (P3), plus totals.
    Closed days come from driver_daily_stats (the scheduled rollup);
    the current Regina day is computed live from GPS via the same v2 SQL
    function, labeled day_source='live'. Formats: json | csv | pdf | xlsx.

  * ``GET /drivers/{id}/distance-logs`` — the drill-down for one day: each
    insurance-period span clipped to the day with its phase label, ride
    code, span duration, and the audited distance from
    driver_period_distances_current (the revision-aware view — late GPS
    corrections surface here automatically).

Distances here are audit/insurance figures (GPS-derived), never billing.
Mounted under require_module("drivers") in routes/admin/__init__.py.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.datetime_utils import parse_iso_utc
    from ...utils.driver_activity import REGINA_TZ, regina_day_bounds_utc
    from ...utils.driver_daily_rollup import _phase_stats
    from .compliance import _render_tabular_report
except ImportError:  # pragma: no cover - dual import path
    import db_supabase  # type: ignore
    from dependencies import get_admin_user  # type: ignore
    from routes.admin.compliance import _render_tabular_report  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.driver_activity import REGINA_TZ, regina_day_bounds_utc  # type: ignore
    from utils.driver_daily_rollup import _phase_stats  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_RANGE_DAYS = 92  # one quarter — matches the GPS retention story (90d) + slack

_PHASE_LABELS = {1: "Driving around", 2: "On pickup way", 3: "On ride"}


def _parse_date(value: str, param: str) -> date_cls:
    try:
        return date_cls.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{param} must be YYYY-MM-DD") from None


def _fmt_seconds(total: int) -> str:
    total = max(0, int(total))
    h, rem = divmod(total, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m"


@router.get("/drivers/{driver_id}/distance-travelled")
async def admin_driver_distance_travelled(
    driver_id: str,
    start: Optional[str] = Query(None, description="YYYY-MM-DD (Regina); default end-29d"),
    end: Optional[str] = Query(None, description="YYYY-MM-DD (Regina); default today"),
    format: str = Query("json", pattern="^(json|csv|pdf|xlsx)$"),
    admin_user: dict = Depends(get_admin_user),
):
    """Per-day phase distances + durations for one driver over a date range."""
    today_regina = datetime.now(REGINA_TZ).date()
    end_d = _parse_date(end, "end") if end else today_regina
    start_d = _parse_date(start, "start") if start else end_d - timedelta(days=29)
    if start_d > end_d:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    if (end_d - start_d).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail=f"range is capped at {MAX_RANGE_DAYS} days")

    stats_rows = (
        await db_supabase.get_rows(
            "driver_daily_stats",
            {
                "driver_id": driver_id,
                "stat_date": {"$gte": start_d.isoformat(), "$lte": end_d.isoformat()},
            },
            order="stat_date",
            desc=True,
            limit=MAX_RANGE_DAYS + 1,
        )
        or []
    )
    by_date: Dict[str, dict] = {str(r.get("stat_date"))[:10]: r for r in stats_rows}

    days: List[Dict[str, Any]] = []
    d = end_d
    while d >= start_d:
        iso = d.isoformat()
        row = by_date.get(iso)
        if row is None and d == today_regina:
            # The in-progress Regina day is never rolled up (partial-day
            # guard) — compute it live from GPS via the same v2 function.
            ws, we = regina_day_bounds_utc(d)
            try:
                gps = await _phase_stats(driver_id, ws.isoformat(), we.isoformat())
            except Exception:
                logger.error("distance-travelled: live compute failed (driver day omitted)", exc_info=True)
                gps = None
            if gps:
                row = {
                    "idle_km": gps.get("idle_km"),
                    "navigating_km": gps.get("navigating_km"),
                    "trip_km": gps.get("trip_km"),
                    "total_km": (gps.get("idle_km") or 0) + (gps.get("navigating_km") or 0) + (gps.get("trip_km") or 0),
                    "idle_seconds": gps.get("idle_seconds"),
                    "navigating_seconds": gps.get("navigating_seconds"),
                    "trip_seconds": gps.get("trip_seconds"),
                    "online_minutes": gps.get("online_minutes"),
                    "rides_completed": None,  # not derivable from GPS alone mid-day
                    "day_tz": "live",
                }
        if row is not None:
            days.append(
                {
                    "date": iso,
                    "driving_around_km": round(float(row.get("idle_km") or 0), 2),
                    "driving_around_seconds": int(row.get("idle_seconds") or 0),
                    "on_pickup_way_km": round(float(row.get("navigating_km") or 0), 2),
                    "on_pickup_way_seconds": int(row.get("navigating_seconds") or 0),
                    "on_ride_km": round(float(row.get("trip_km") or 0), 2),
                    "on_ride_seconds": int(row.get("trip_seconds") or 0),
                    "total_km": round(float(row.get("total_km") or 0), 2),
                    "online_minutes": int(row.get("online_minutes") or 0),
                    "rides_completed": row.get("rides_completed"),
                    "day_source": "live" if row.get("day_tz") == "live" else str(row.get("day_tz") or "utc"),
                }
            )
        d -= timedelta(days=1)

    totals = {
        "driving_around_km": round(sum(x["driving_around_km"] for x in days), 2),
        "on_pickup_way_km": round(sum(x["on_pickup_way_km"] for x in days), 2),
        "on_ride_km": round(sum(x["on_ride_km"] for x in days), 2),
        "total_km": round(sum(x["total_km"] for x in days), 2),
        "driving_around_seconds": sum(x["driving_around_seconds"] for x in days),
        "on_pickup_way_seconds": sum(x["on_pickup_way_seconds"] for x in days),
        "on_ride_seconds": sum(x["on_ride_seconds"] for x in days),
    }

    if format == "json":
        return {
            "driver_id": driver_id,
            "start": start_d.isoformat(),
            "end": end_d.isoformat(),
            "tz": "America/Regina",
            "days": days,
            "totals": totals,
        }

    fieldnames = [
        "date",
        "driving_around_km",
        "driving_around_time",
        "on_pickup_way_km",
        "on_pickup_way_time",
        "on_ride_km",
        "on_ride_time",
        "total_km",
    ]
    export_rows = [
        {
            "date": x["date"],
            "driving_around_km": f"{x['driving_around_km']:.2f}",
            "driving_around_time": _fmt_seconds(x["driving_around_seconds"]),
            "on_pickup_way_km": f"{x['on_pickup_way_km']:.2f}",
            "on_pickup_way_time": _fmt_seconds(x["on_pickup_way_seconds"]),
            "on_ride_km": f"{x['on_ride_km']:.2f}",
            "on_ride_time": _fmt_seconds(x["on_ride_seconds"]),
            "total_km": f"{x['total_km']:.2f}",
        }
        for x in days
    ]
    export_rows.append(
        {
            "date": "TOTAL",
            "driving_around_km": f"{totals['driving_around_km']:.2f}",
            "driving_around_time": _fmt_seconds(totals["driving_around_seconds"]),
            "on_pickup_way_km": f"{totals['on_pickup_way_km']:.2f}",
            "on_pickup_way_time": _fmt_seconds(totals["on_pickup_way_seconds"]),
            "on_ride_km": f"{totals['on_ride_km']:.2f}",
            "on_ride_time": _fmt_seconds(totals["on_ride_seconds"]),
            "total_km": f"{totals['total_km']:.2f}",
        }
    )
    return _render_tabular_report(
        title="Driver Distance Travelled",
        filename_base=f"distance-travelled_{driver_id}_{start_d.isoformat()}_{end_d.isoformat()}",
        fieldnames=fieldnames,
        rows=export_rows,
        subtitle=[f"Driver {driver_id}", f"{start_d.isoformat()} to {end_d.isoformat()} (America/Regina days)"],
        format=format,
        pdf_landscape=True,
    )


@router.get("/drivers/{driver_id}/distance-logs")
async def admin_driver_distance_logs(
    driver_id: str,
    date: str = Query(..., description="YYYY-MM-DD (Regina day)"),
    admin_user: dict = Depends(get_admin_user),
):
    """Drill-down for one Regina day: per insurance-period span with phase,
    ride code, duration, and the audited (revision-current) distance."""
    d = _parse_date(date, "date")
    win_start, win_end = regina_day_bounds_utc(d)
    ws_iso, we_iso = win_start.isoformat(), win_end.isoformat()

    # Spans overlapping the day (lookback catches one that began pre-midnight).
    lookback_iso = (win_start - timedelta(days=2)).isoformat()
    spans = (
        await db_supabase.get_rows(
            "driver_insurance_periods",
            {"driver_id": driver_id, "started_at": {"$gte": lookback_iso, "$lt": we_iso}},
            order="started_at",
            limit=2000,
        )
        or []
    )

    # Audited distances for the day: ride-scoped (P2/P3) keyed by
    # (ride_id, period); P1 rows (ride_id NULL) matched to spans by overlap.
    dist_rows = (
        await db_supabase.get_rows(
            "driver_period_distances_current",
            {"driver_id": driver_id, "ended_at": {"$gte": lookback_iso, "$lt": we_iso}},
            order="ended_at",
            limit=2000,
        )
        or []
    )
    ride_dist: Dict[tuple, dict] = {}
    p1_rows: List[dict] = []
    for r in dist_rows:
        if r.get("ride_id"):
            ride_dist[(r["ride_id"], int(r.get("period") or 0))] = r
        elif int(r.get("period") or 0) == 1:
            p1_rows.append(r)

    def _p1_distance_for(span_start, span_end) -> Optional[dict]:
        """Best overlap match: the finalizer writes one row per completed
        Period-1 stretch; timestamps line up with the span within seconds."""
        best, best_overlap = None, 0.0
        for r in p1_rows:
            rs, re = parse_iso_utc(r.get("started_at")), parse_iso_utc(r.get("ended_at"))
            if rs is None or re is None or span_start is None:
                continue
            lo = max(rs, span_start)
            hi = min(re, span_end or re)
            overlap = (hi - lo).total_seconds()
            if overlap > best_overlap:
                best, best_overlap = r, overlap
        return best if best_overlap > 0 else None

    ride_ids = sorted({s["ride_id"] for s in spans if s.get("ride_id")})
    ride_codes: Dict[str, str] = {}
    if ride_ids:
        for r in (
            await db_supabase.get_rows("rides", {"id": {"$in": ride_ids}}, columns="id,ride_code", limit=len(ride_ids))
            or []
        ):
            ride_codes[r["id"]] = r.get("ride_code") or ""

    logs: List[Dict[str, Any]] = []
    for s in spans:
        period = int(s.get("period") or 0)
        if period not in _PHASE_LABELS:
            continue  # period 0 (offline) carries no distance
        s_start = parse_iso_utc(s.get("started_at"))
        s_end = parse_iso_utc(s.get("ended_at"))
        # Clip to the day; skip spans that never actually reach the window.
        c_start = max(s_start, win_start) if s_start else win_start
        c_end = min(s_end or win_end, win_end)
        if c_end <= c_start:
            continue

        dist = None
        if s.get("ride_id"):
            dist = ride_dist.get((s["ride_id"], period))
        elif period == 1:
            dist = _p1_distance_for(s_start, s_end)

        logs.append(
            {
                "from": c_start.isoformat(),
                "to": c_end.isoformat() if s_end or c_end < win_end else None,
                "seconds": int((c_end - c_start).total_seconds()),
                "phase": _PHASE_LABELS[period],
                "period": period,
                "ride_id": s.get("ride_id"),
                "ride_code": ride_codes.get(s.get("ride_id") or "", "") or None,
                "distance_km": float(dist["distance_km"]) if dist and dist.get("distance_km") is not None else None,
                "distance_source": dist.get("source") if dist else None,
                "open": s.get("ended_at") is None,
                # Migration 332: True when this span's boundaries were backfilled
                # from timestamps during legacy migration rather than logged live.
                # Regulator-facing scripts/compliance_export.py already surfaces
                # this; this is the admin-dashboard read-only counterpart
                # (legacy-migration-playbook.md checklist item #5(b)).
                "is_reconstructed": bool(s.get("is_reconstructed", False)),
            }
        )

    return {
        "driver_id": driver_id,
        "date": d.isoformat(),
        "tz": "America/Regina",
        "logs": logs,
        "total_km": round(sum(x["distance_km"] or 0 for x in logs), 2),
    }
