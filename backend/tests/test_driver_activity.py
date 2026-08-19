"""Unit tests for the driver daily-activity aggregator.

Empty = P1+P2 (no passenger), Riding = P3 (passenger aboard); per-phase km from
rides; per-ride empty-before gap; Regina day boundaries.
"""

from datetime import date, datetime, timezone

import pytest

from backend.utils.driver_activity import build_daily_activity, regina_day_bounds_utc

# Regina day 2026-06-01 → [06:00Z, next 06:00Z) since SK is UTC-6 (no DST).
WIN_START, WIN_END = regina_day_bounds_utc(date(2026, 6, 1))


def test_regina_day_bounds_are_utc_minus_6():
    assert WIN_START == datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
    assert WIN_END == datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)


def _period(p, s, e):
    return {"period": p, "started_at": s, "ended_at": e}


def test_empty_vs_riding_time_split():
    periods = [
        _period(1, "2026-06-01T14:00:00Z", "2026-06-01T14:10:00Z"),  # idle 600s (empty)
        _period(2, "2026-06-01T14:10:00Z", "2026-06-01T14:16:00Z"),  # en-route 360s (empty)
        _period(3, "2026-06-01T14:16:00Z", "2026-06-01T14:40:00Z"),  # trip 1440s (riding)
    ]
    out = build_daily_activity(periods, [], WIN_START, WIN_END)
    s = out["summary"]
    assert s["riding_seconds"] == 1440
    assert s["empty_seconds"] == 960  # 600 + 360
    assert s["online_seconds"] == 2400
    assert s["utilization"] == 0.6


def test_open_and_boundary_periods_clipped():
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    periods = [
        # started before the window → clipped to [06:00, 06:30] = 1800s
        _period(1, "2026-06-01T05:00:00Z", "2026-06-01T06:30:00Z"),
        # open period (no ended_at) → clipped to now (11:00→12:00 = 3600s)
        _period(3, "2026-06-01T11:00:00Z", None),
    ]
    out = build_daily_activity(periods, [], WIN_START, WIN_END, now=now)
    assert out["summary"]["empty_seconds"] == 1800
    assert out["summary"]["riding_seconds"] == 3600


def _ride(rid, accepted, arrived, started, completed, nav_km, trip_km, fare):
    return {
        "id": rid,
        "status": "completed",
        "driver_accepted_at": accepted,
        "driver_arrived_at": arrived,
        "ride_started_at": started,
        "ride_completed_at": completed,
        "phase_distances": {"navigating_to_pickup": nav_km, "trip_in_progress": trip_km},
        "actual_distance_km": trip_km,
        "total_fare": fare,
    }


def test_per_ride_phases_km_and_empty_before():
    rides = [
        _ride(
            "r1",
            "2026-06-01T14:10:00Z",
            "2026-06-01T14:15:00Z",
            "2026-06-01T14:16:00Z",
            "2026-06-01T14:40:00Z",
            2.0,
            8.0,
            14.5,
        ),
        _ride(
            "r2",
            "2026-06-01T15:00:00Z",
            "2026-06-01T15:05:00Z",
            "2026-06-01T15:06:00Z",
            "2026-06-01T15:30:00Z",
            1.0,
            5.0,
            9.0,
        ),
    ]
    out = build_daily_activity([], rides, WIN_START, WIN_END)
    s = out["summary"]
    assert s["phase_km"] == {"navigating_to_pickup": 3.0, "arrived_at_pickup": 0.0, "trip_in_progress": 13.0}
    assert s["total_km"] == 16.0
    assert s["ride_count"] == 2
    assert s["total_fare"] == 23.5

    r1, r2 = out["rides"]
    assert r1["empty_before_seconds"] is None  # first ride of the day
    nav, arrived, trip = r1["phases"]
    assert nav["phase"] == "navigating_to_pickup" and nav["km"] == 2.0 and nav["seconds"] == 300
    assert arrived["seconds"] == 60
    assert trip["km"] == 8.0 and trip["seconds"] == 1440
    # r2 accepted 15:00, prior ride completed 14:40 → 1200s empty between rides
    assert r2["empty_before_seconds"] == 1200


# ---------------------------------------------------------------------------
# Endpoint: closed days merge the rollup's GPS-derived summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closed_day_merges_rollup_gps_summary():
    """A closed Regina day with a day_tz='regina' stats row gains gps_km /
    gps_seconds (incl. Period-1 roaming, invisible to per-ride phase data)
    and is labeled day_source='rollup'. The live per-ride list is untouched."""
    from unittest.mock import AsyncMock, patch

    try:
        from backend.routes.admin import drivers as drivers_mod
    except ImportError:
        from routes.admin import drivers as drivers_mod  # type: ignore

    async def _get_rows(table, filters=None, **kw):
        if table == "driver_insurance_periods":
            return []
        if table == "rides":
            return []
        if table == "driver_daily_stats":
            assert filters["day_tz"] == "regina"
            return [
                {
                    "idle_km": 12.3,
                    "navigating_km": 4.0,
                    "trip_km": 30.0,
                    "total_km": 46.3,
                    "idle_seconds": 3600,
                    "navigating_seconds": 900,
                    "trip_seconds": 5400,
                }
            ]
        raise AssertionError(f"unexpected table {table}")

    with patch.object(drivers_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
        report = await drivers_mod.admin_driver_daily_activity(
            driver_id="drv-1", date="2026-06-01", admin_user={"email": "a@spinr.ca"}
        )

    assert report["day_source"] == "rollup"
    assert report["summary"]["gps_km"]["idle"] == 12.3
    assert report["summary"]["gps_seconds"]["trip"] == 5400


@pytest.mark.asyncio
async def test_closed_day_without_regina_row_stays_live():
    from unittest.mock import AsyncMock, patch

    try:
        from backend.routes.admin import drivers as drivers_mod
    except ImportError:
        from routes.admin import drivers as drivers_mod  # type: ignore

    with patch.object(drivers_mod.db_supabase, "get_rows", AsyncMock(return_value=[])):
        report = await drivers_mod.admin_driver_daily_activity(
            driver_id="drv-1", date="2026-06-01", admin_user={"email": "a@spinr.ca"}
        )

    assert report["day_source"] == "live"
    assert "gps_km" not in report["summary"]
