"""Tests for routes/admin/driver_distance.py — Distance Travelled / Logs.

Pins:
  - closed days come from driver_daily_stats; the in-progress Regina day is
    computed live via the v2 SQL function and labeled day_source='live'
  - range validation (order, 92-day cap, date format)
  - the drill-down joins insurance-period spans with the revision-aware
    driver_period_distances_current view (P2/P3 by (ride_id, period),
    P1 by span overlap), clips to the Regina day, and skips Period 0
  - CSV export renders through the shared branded report pipeline
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

try:
    from backend.routes.admin import driver_distance as mod
except ImportError:
    from routes.admin import driver_distance as mod  # type: ignore

ADMIN = {"email": "admin@spinr.ca"}
# Regina day 2026-08-10 spans 06:00 UTC boundaries.
DAY = "2026-08-10"


def _stats_row(stat_date, **over):
    row = {
        "stat_date": stat_date,
        "idle_km": 10.0,
        "navigating_km": 5.0,
        "trip_km": 20.0,
        "total_km": 35.0,
        "idle_seconds": 1800,
        "navigating_seconds": 600,
        "trip_seconds": 3600,
        "online_minutes": 240,
        "rides_completed": 4,
        "day_tz": "regina",
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_distance_travelled_serves_closed_days_from_stats():
    async def _get_rows(table, filters=None, **kw):
        assert table == "driver_daily_stats"
        return [_stats_row("2026-08-10"), _stats_row("2026-08-09", day_tz="utc")]

    with patch.object(mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
        out = await mod.admin_driver_distance_travelled(
            driver_id="drv-1", start="2026-08-09", end="2026-08-10", format="json", admin_user=ADMIN
        )

    assert out["tz"] == "America/Regina"
    assert [x["date"] for x in out["days"]] == ["2026-08-10", "2026-08-09"]
    top = out["days"][0]
    assert top["driving_around_km"] == 10.0
    assert top["on_pickup_way_seconds"] == 600
    assert top["day_source"] == "regina"
    assert out["days"][1]["day_source"] == "utc"
    assert out["totals"]["total_km"] == 70.0


@pytest.mark.asyncio
async def test_distance_travelled_computes_today_live():
    today = datetime.now(mod.REGINA_TZ).date()
    gps = {
        "idle_km": 1.5,
        "navigating_km": 0.5,
        "trip_km": 3.0,
        "idle_seconds": 600,
        "navigating_seconds": 120,
        "trip_seconds": 900,
        "online_minutes": 45,
    }
    with (
        patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[])),
        patch.object(mod, "_phase_stats", AsyncMock(return_value=gps)) as rpc_mock,
    ):
        out = await mod.admin_driver_distance_travelled(
            driver_id="drv-1",
            start=today.isoformat(),
            end=today.isoformat(),
            format="json",
            admin_user=ADMIN,
        )

    rpc_mock.assert_awaited_once()
    assert len(out["days"]) == 1
    day = out["days"][0]
    assert day["day_source"] == "live"
    assert day["total_km"] == 5.0
    assert day["rides_completed"] is None


@pytest.mark.asyncio
async def test_distance_travelled_range_validation():
    with pytest.raises(HTTPException) as exc:
        await mod.admin_driver_distance_travelled(
            driver_id="d", start="2026-08-10", end="2026-08-01", format="json", admin_user=ADMIN
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await mod.admin_driver_distance_travelled(
            driver_id="d", start="2026-01-01", end="2026-08-01", format="json", admin_user=ADMIN
        )
    assert exc.value.status_code == 400
    assert "92" in exc.value.detail

    with pytest.raises(HTTPException) as exc:
        await mod.admin_driver_distance_travelled(
            driver_id="d", start="not-a-date", end=None, format="json", admin_user=ADMIN
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_distance_travelled_csv_goes_through_branded_renderer():
    rendered = {}

    def _render(**kwargs):
        rendered.update(kwargs)
        return "RESPONSE"

    with (
        patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[_stats_row("2026-08-10")])),
        patch.object(mod, "_render_tabular_report", _render),
    ):
        out = await mod.admin_driver_distance_travelled(
            driver_id="drv-1", start="2026-08-09", end="2026-08-10", format="csv", admin_user=ADMIN
        )

    assert out == "RESPONSE"
    assert rendered["format"] == "csv"
    # Last row is the totals line.
    assert rendered["rows"][-1]["date"] == "TOTAL"
    assert rendered["rows"][0]["driving_around_time"] == "0h 30m"


@pytest.mark.asyncio
async def test_distance_logs_joins_spans_with_current_distances():
    # Regina day boundaries for DAY.
    ws = datetime(2026, 8, 10, 6, 0, 0, tzinfo=timezone.utc)

    spans = [
        {  # Period 1 roaming, closed
            "period": 1,
            "started_at": (ws + timedelta(hours=1)).isoformat(),
            "ended_at": (ws + timedelta(hours=2)).isoformat(),
            "ride_id": None,
        },
        {  # Period 2 to pickup
            "period": 2,
            "started_at": (ws + timedelta(hours=2)).isoformat(),
            "ended_at": (ws + timedelta(hours=2, minutes=10)).isoformat(),
            "ride_id": "ride-1",
        },
        {  # Period 3 passenger aboard
            "period": 3,
            "started_at": (ws + timedelta(hours=2, minutes=10)).isoformat(),
            "ended_at": (ws + timedelta(hours=2, minutes=40)).isoformat(),
            "ride_id": "ride-1",
        },
        {  # Period 0 offline — never rendered
            "period": 0,
            "started_at": (ws + timedelta(hours=3)).isoformat(),
            "ended_at": None,
            "ride_id": None,
        },
    ]
    dists = [
        {
            "driver_id": "drv-1",
            "ride_id": None,
            "period": 1,
            "distance_km": "7.250",
            "started_at": (ws + timedelta(hours=1)).isoformat(),
            "ended_at": (ws + timedelta(hours=2)).isoformat(),
            "source": "gps_scalar_p1",
        },
        {
            "driver_id": "drv-1",
            "ride_id": "ride-1",
            "period": 2,
            "distance_km": "3.100",
            "source": "gps_measured",
        },
        {
            "driver_id": "drv-1",
            "ride_id": "ride-1",
            "period": 3,
            "distance_km": "12.400",
            "source": "late_tail_rederivation",
        },
    ]

    async def _get_rows(table, filters=None, **kw):
        if table == "driver_insurance_periods":
            return spans
        if table == "driver_period_distances_current":
            return dists
        if table == "rides":
            return [{"id": "ride-1", "ride_code": "SPR-PE7TTB"}]
        raise AssertionError(f"unexpected table {table}")

    with patch.object(mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
        out = await mod.admin_driver_distance_logs(driver_id="drv-1", date=DAY, admin_user=ADMIN)

    assert [x["phase"] for x in out["logs"]] == ["Driving around", "On pickup way", "On ride"]
    p1, p2, p3 = out["logs"]
    assert p1["distance_km"] == 7.25
    assert p1["ride_code"] is None
    assert p2["ride_code"] == "SPR-PE7TTB"
    assert p2["seconds"] == 600
    assert p3["distance_km"] == 12.4
    assert p3["distance_source"] == "late_tail_rederivation"
    assert out["total_km"] == pytest.approx(22.75)
    # Spans with no is_reconstructed column (or an explicit False) default to
    # live-logged, not reconstructed.
    assert p1["is_reconstructed"] is False
    assert p2["is_reconstructed"] is False
    assert p3["is_reconstructed"] is False


@pytest.mark.asyncio
async def test_distance_logs_open_span_never_fabricates_an_end_or_distance():
    """An ended_at-IS-NULL span (trip still running or abandoned) must render
    to=None / open=True / distance_km=None — never a concrete end time or a
    stale 'final' distance for an ongoing trip."""
    ws = datetime(2026, 8, 10, 6, 0, 0, tzinfo=timezone.utc)
    spans = [
        {
            "period": 3,
            "started_at": (ws + timedelta(hours=2)).isoformat(),
            "ended_at": None,
            "ride_id": "ride-open",
        }
    ]

    async def _get_rows(table, filters=None, **kw):
        if table == "driver_insurance_periods":
            return spans
        if table == "driver_period_distances_current":
            return []
        if table == "rides":
            return [{"id": "ride-open", "ride_code": "SPR-OPEN1"}]
        return []

    with patch.object(mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
        out = await mod.admin_driver_distance_logs(driver_id="drv-1", date=DAY, admin_user=ADMIN)

    assert len(out["logs"]) == 1
    row = out["logs"][0]
    assert row["open"] is True
    assert row["to"] is None
    assert row["distance_km"] is None
    assert row["ride_code"] == "SPR-OPEN1"
    # Duration is the clipped in-window time so far, never beyond the day end.
    assert row["seconds"] == 22 * 3600


@pytest.mark.asyncio
async def test_distance_logs_clips_spans_to_the_regina_day():
    ws = datetime(2026, 8, 10, 6, 0, 0, tzinfo=timezone.utc)
    spans = [
        {  # began the previous evening, ended mid-morning → clipped at 06:00
            "period": 1,
            "started_at": (ws - timedelta(hours=3)).isoformat(),
            "ended_at": (ws + timedelta(hours=1)).isoformat(),
            "ride_id": None,
        },
        {  # entirely before the window → dropped
            "period": 1,
            "started_at": (ws - timedelta(hours=6)).isoformat(),
            "ended_at": (ws - timedelta(hours=5)).isoformat(),
            "ride_id": None,
        },
    ]

    async def _get_rows(table, filters=None, **kw):
        if table == "driver_insurance_periods":
            return spans
        if table == "driver_period_distances_current":
            return []
        return []

    with patch.object(mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
        out = await mod.admin_driver_distance_logs(driver_id="drv-1", date=DAY, admin_user=ADMIN)

    assert len(out["logs"]) == 1
    assert out["logs"][0]["from"] == ws.isoformat()
    assert out["logs"][0]["seconds"] == 3600


@pytest.mark.asyncio
async def test_distance_logs_surfaces_is_reconstructed_per_span():
    """Migration 332's `is_reconstructed` marker (legacy-migration-playbook.md
    checklist item #5(b)) must pass through per span, not be dropped or
    collapsed to a single day-level flag — a driver can have a mix of
    live-logged and backfilled spans in the same day."""
    ws = datetime(2026, 8, 10, 6, 0, 0, tzinfo=timezone.utc)
    spans = [
        {  # live-logged
            "period": 1,
            "started_at": (ws + timedelta(hours=1)).isoformat(),
            "ended_at": (ws + timedelta(hours=2)).isoformat(),
            "ride_id": None,
            "is_reconstructed": False,
        },
        {  # backfilled from driverlocationlogs.csv timestamps
            "period": 2,
            "started_at": (ws + timedelta(hours=2)).isoformat(),
            "ended_at": (ws + timedelta(hours=2, minutes=10)).isoformat(),
            "ride_id": "ride-1",
            "is_reconstructed": True,
        },
    ]

    async def _get_rows(table, filters=None, **kw):
        if table == "driver_insurance_periods":
            return spans
        if table == "driver_period_distances_current":
            return []
        if table == "rides":
            return [{"id": "ride-1", "ride_code": "SPR-PE7TTB"}]
        return []

    with patch.object(mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
        out = await mod.admin_driver_distance_logs(driver_id="drv-1", date=DAY, admin_user=ADMIN)

    assert len(out["logs"]) == 2
    live, backfilled = out["logs"]
    assert live["is_reconstructed"] is False
    assert backfilled["is_reconstructed"] is True


@pytest.mark.asyncio
async def test_distance_logs_prefers_correction_over_original_span():
    """ACTION_ITEMS.md B34: when a driver_insurance_period_corrections row
    exists for a span, the drill-down must render the CORRECTED
    started_at/ended_at (and flag is_corrected), not the original — not
    just that the corrections table can be queried."""
    ws = datetime(2026, 8, 10, 6, 0, 0, tzinfo=timezone.utc)
    spans = [
        {
            "id": "span-1",
            "period": 2,
            "started_at": (ws + timedelta(hours=2)).isoformat(),  # wrong original start
            "ended_at": (ws + timedelta(hours=2, minutes=10)).isoformat(),
            "ride_id": "ride-1",
        }
    ]
    corrected_start = (ws + timedelta(hours=1, minutes=45)).isoformat()  # real GPS start, earlier

    async def _get_rows(table, filters=None, **kw):
        if table == "driver_insurance_periods":
            return spans
        if table == "driver_insurance_period_corrections":
            assert filters == {"original_period_id": {"$in": ["span-1"]}}
            return [
                {
                    "original_period_id": "span-1",
                    "corrected_started_at": corrected_start,
                    "corrected_ended_at": spans[0]["ended_at"],
                }
            ]
        if table == "driver_period_distances_current":
            return []
        if table == "rides":
            return [{"id": "ride-1", "ride_code": "SPR-PE7TTB"}]
        raise AssertionError(f"unexpected table {table}")

    with patch.object(mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
        out = await mod.admin_driver_distance_logs(driver_id="drv-1", date=DAY, admin_user=ADMIN)

    assert len(out["logs"]) == 1
    row = out["logs"][0]
    assert row["from"] == corrected_start
    assert row["is_corrected"] is True
    # Corrected span is 25 minutes (1h45m -> 2h10m offset), not the
    # original 10 minutes.
    assert row["seconds"] == 25 * 60


@pytest.mark.asyncio
async def test_distance_logs_keeps_original_when_no_correction_on_file():
    ws = datetime(2026, 8, 10, 6, 0, 0, tzinfo=timezone.utc)
    original_start = (ws + timedelta(hours=2)).isoformat()
    spans = [
        {
            "id": "span-1",
            "period": 2,
            "started_at": original_start,
            "ended_at": (ws + timedelta(hours=2, minutes=10)).isoformat(),
            "ride_id": "ride-1",
        }
    ]

    async def _get_rows(table, filters=None, **kw):
        if table == "driver_insurance_periods":
            return spans
        if table == "driver_insurance_period_corrections":
            assert filters == {"original_period_id": {"$in": ["span-1"]}}
            return []
        if table == "driver_period_distances_current":
            return []
        if table == "rides":
            return [{"id": "ride-1", "ride_code": "SPR-PE7TTB"}]
        raise AssertionError(f"unexpected table {table}")

    with patch.object(mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
        out = await mod.admin_driver_distance_logs(driver_id="drv-1", date=DAY, admin_user=ADMIN)

    assert len(out["logs"]) == 1
    row = out["logs"][0]
    assert row["from"] == original_start
    assert row["is_corrected"] is False


@pytest.mark.asyncio
async def test_distance_logs_no_correction_lookup_when_spans_carry_no_id():
    """Existing fixtures/spans that never carried an `id` field (id-less
    fixture rows, or any future response missing it) must short-circuit —
    never issue an all-empty $in against the corrections table."""
    ws = datetime(2026, 8, 10, 6, 0, 0, tzinfo=timezone.utc)
    spans = [
        {
            "period": 1,
            "started_at": (ws + timedelta(hours=1)).isoformat(),
            "ended_at": (ws + timedelta(hours=2)).isoformat(),
            "ride_id": None,
        }
    ]
    calls = []

    async def _get_rows(table, filters=None, **kw):
        calls.append(table)
        if table == "driver_insurance_periods":
            return spans
        return []

    with patch.object(mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
        out = await mod.admin_driver_distance_logs(driver_id="drv-1", date=DAY, admin_user=ADMIN)

    assert "driver_insurance_period_corrections" not in calls
    assert out["logs"][0]["is_corrected"] is False
