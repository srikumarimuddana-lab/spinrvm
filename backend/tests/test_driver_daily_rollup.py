"""Tests for utils/driver_daily_rollup.py — scheduled Regina-day rollup.

Pins:
  - Regina day bounds (UTC-6, no DST) drive every query window
  - driver discovery = insurance-period overlap ∪ rides that day (no
    breadcrumb scan), and the $or leaves it uses are expressible by the
    real OR builder (a dropped leaf would widen to the whole table)
  - rows are stamped day_tz='regina' with the v2 seconds columns
  - upsert is idempotent on the deterministic id
  - the loop never rolls up TODAY (a partial-day row would advance
    MAX(stat_date) and break the leaderboard freshness top-up)
  - the nightly 7-day sweep runs once per Regina date, after 02:00 local
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

STAT_DATE = date(2026, 8, 16)
# Regina is UTC-6 year-round: day boundaries at 06:00 UTC.
WIN_START = datetime(2026, 8, 16, 6, 0, 0, tzinfo=timezone.utc)
WIN_END = datetime(2026, 8, 17, 6, 0, 0, tzinfo=timezone.utc)


def test_or_leaves_are_expressible_by_the_real_or_builder():
    """The discovery filter's $or must compile — the shared filter layer
    raises on inexpressible leaves rather than dropping them (a dropped
    leaf would have matched the whole table)."""
    from repositories._base import _build_or_clause_term

    assert _build_or_clause_term("ended_at", {"$notnull": False}) == "ended_at.is.null"
    gte = _build_or_clause_term("ended_at", {"$gte": WIN_START.isoformat()})
    assert gte is not None and gte.startswith("ended_at.gte.")


def _rpc_result(**overrides):
    row = {
        "idle_km": 12.5,
        "navigating_km": 4.2,
        "trip_km": 30.1,
        "idle_seconds": 3600,
        "navigating_seconds": 900,
        "trip_seconds": 5400,
        "online_minutes": 480,
        "first_online_at": WIN_START.isoformat(),
        "last_online_at": (WIN_END - timedelta(hours=1)).isoformat(),
        "point_count": 900,
        "rejected_segments": 1,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_rollup_day_writes_regina_rows_with_seconds():
    calls = {"get_rows": [], "insert": [], "update": []}

    async def _get_rows(table, filters, **kw):
        calls["get_rows"].append((table, filters))
        if table == "rides":
            assert filters["created_at"]["$gte"] == WIN_START.isoformat()
            assert filters["created_at"]["$lt"] == WIN_END.isoformat()
            return [
                {
                    "driver_id": "drv-1",
                    "status": "completed",
                    "driver_earnings": "25.50",
                    "tip_amount": "3.00",
                }
            ]
        if table == "audit_logs":
            return [{"actor_id": "drv-1"}]
        if table == "driver_insurance_periods":
            assert filters["started_at"]["$lt"] == WIN_END.isoformat()
            return [{"driver_id": "drv-1"}, {"driver_id": "drv-2"}]
        if table == "driver_daily_stats":
            return []  # nothing exists yet → insert path
        raise AssertionError(f"unexpected table {table}")

    async def _insert(table, row):
        calls["insert"].append(row)
        return row

    with (
        patch("utils.driver_daily_rollup.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("utils.driver_daily_rollup.db_supabase.insert_one", AsyncMock(side_effect=_insert)),
        patch("utils.driver_daily_rollup.db_supabase.update_one", AsyncMock()),
        patch(
            "utils.driver_daily_rollup.db_supabase.get_driver_by_id",
            AsyncMock(return_value={"service_area_id": "sa-1"}),
        ),
        patch(
            "utils.driver_daily_rollup._phase_stats",
            AsyncMock(return_value=_rpc_result()),
        ),
    ):
        from utils.driver_daily_rollup import rollup_driver_day

        result = await rollup_driver_day(STAT_DATE)

    assert result["drivers_processed"] == 2
    assert result["created"] == 2
    assert result["failed"] == 0
    by_id = {r["driver_id"]: r for r in calls["insert"]}
    row = by_id["drv-1"]
    assert row["id"] == f"drv-1_{STAT_DATE.isoformat()}"
    assert row["day_tz"] == "regina"
    assert row["idle_seconds"] == 3600
    assert row["navigating_seconds"] == 900
    assert row["trip_seconds"] == 5400
    assert row["total_km"] == pytest.approx(46.8)
    assert row["total_earnings"] == 25.50
    assert row["rides_declined"] == 1
    # drv-2 was online (insurance periods) but had no rides — still recorded.
    assert by_id["drv-2"]["rides_completed"] == 0


@pytest.mark.asyncio
async def test_rollup_day_updates_existing_row_in_place():
    async def _get_rows(table, filters, **kw):
        if table == "driver_daily_stats":
            return [{"id": filters["id"]}]
        if table == "driver_insurance_periods":
            return [{"driver_id": "drv-1"}]
        return []

    update_mock = AsyncMock()
    with (
        patch("utils.driver_daily_rollup.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("utils.driver_daily_rollup.db_supabase.insert_one", AsyncMock()) as insert_mock,
        patch("utils.driver_daily_rollup.db_supabase.update_one", update_mock),
        patch("utils.driver_daily_rollup.db_supabase.get_driver_by_id", AsyncMock(return_value=None)),
        patch("utils.driver_daily_rollup._phase_stats", AsyncMock(return_value=_rpc_result())),
    ):
        from utils.driver_daily_rollup import rollup_driver_day

        result = await rollup_driver_day(STAT_DATE)

    assert result["updated"] == 1 and result["created"] == 0
    insert_mock.assert_not_awaited()
    _, payload = update_mock.await_args.args[1], update_mock.await_args.args[2]
    assert payload["day_tz"] == "regina"


@pytest.mark.asyncio
async def test_rpc_failure_counts_failed_and_skips_write():
    async def _get_rows(table, filters, **kw):
        if table == "driver_insurance_periods":
            return [{"driver_id": "drv-1"}]
        return []

    with (
        patch("utils.driver_daily_rollup.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("utils.driver_daily_rollup.db_supabase.insert_one", AsyncMock()) as insert_mock,
        patch("utils.driver_daily_rollup.db_supabase.update_one", AsyncMock()) as update_mock,
        patch("utils.driver_daily_rollup.db_supabase.get_driver_by_id", AsyncMock(return_value=None)),
        patch(
            "utils.driver_daily_rollup._phase_stats",
            AsyncMock(side_effect=RuntimeError("rpc down")),
        ),
    ):
        from utils.driver_daily_rollup import rollup_driver_day

        result = await rollup_driver_day(STAT_DATE)

    assert result["failed"] == 1
    insert_mock.assert_not_awaited()
    update_mock.assert_not_awaited()


def test_completed_regina_days_never_include_today():
    from utils.driver_daily_rollup import _completed_regina_days

    # 2026-08-17 03:00 UTC is still 2026-08-16 21:00 in Regina —
    # Regina "today" is the 16th, so the newest completed day is the 15th.
    now = datetime(2026, 8, 17, 3, 0, 0, tzinfo=timezone.utc)
    days = _completed_regina_days(now, 2)
    assert days == [date(2026, 8, 15), date(2026, 8, 14)]

    # After the Regina midnight (07:00 UTC on the 17th = 01:00 Regina),
    # the 16th becomes the newest completed day.
    now = datetime(2026, 8, 17, 7, 0, 0, tzinfo=timezone.utc)
    assert _completed_regina_days(now, 1) == [date(2026, 8, 16)]


@pytest.mark.asyncio
async def test_tick_sweeps_seven_days_once_per_regina_date():
    import utils.driver_daily_rollup as mod

    rolled: list = []

    async def _fake_rollup(d):
        rolled.append(d)
        return {"stat_date": d.isoformat()}

    mod._last_sweep_for = None
    with patch("utils.driver_daily_rollup.rollup_driver_day", AsyncMock(side_effect=_fake_rollup)):
        # 09:00 UTC = 03:00 Regina — past the sweep hour → 7-day sweep.
        await mod._tick(datetime(2026, 8, 17, 9, 0, 0, tzinfo=timezone.utc))
        assert len(rolled) == 7
        rolled.clear()
        # Same Regina date, later tick → back to the 2-day refresh.
        await mod._tick(datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc))
        assert len(rolled) == 2
        rolled.clear()
        # Before 02:00 Regina next date → still 2-day refresh (no sweep yet).
        await mod._tick(datetime(2026, 8, 18, 7, 0, 0, tzinfo=timezone.utc))
        assert len(rolled) == 2
    mod._last_sweep_for = None


@pytest.mark.asyncio
async def test_nightly_sweep_backfills_legacy_utc_days_within_retention():
    """Mixed utc/regina history mis-totals range sums at the seam, so the
    sweep converts legacy rows newest-first — but never days older than the
    GPS retention window (breadcrumbs purged → re-derive would zero km)."""
    import utils.driver_daily_rollup as mod

    now = datetime(2026, 8, 17, 9, 0, 0, tzinfo=timezone.utc)  # 03:00 Regina
    legacy_dates = ["2026-08-01", "2026-07-20", "2026-05-01"]  # last is > 90d old

    async def _get_rows(table, filters, **kw):
        assert table == "driver_daily_stats"
        assert filters["day_tz"] == "utc"
        cutoff = filters["stat_date"]["$gte"]
        return [{"stat_date": d} for d in legacy_dates if d >= cutoff]

    rolled: list = []

    async def _fake_rollup(d):
        rolled.append(d)
        return {"stat_date": d.isoformat()}

    mod._last_sweep_for = None
    with (
        patch("utils.driver_daily_rollup.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("utils.driver_daily_rollup.rollup_driver_day", AsyncMock(side_effect=_fake_rollup)),
    ):
        await mod._tick(now)
    mod._last_sweep_for = None

    # 7 recent completed Regina days + the two in-retention legacy dates;
    # the 2026-05-01 row (past the 90-day GPS window) is left as-is.
    assert len(rolled) == 9
    assert date(2026, 8, 1) in rolled
    assert date(2026, 7, 20) in rolled
    assert date(2026, 5, 1) not in rolled
