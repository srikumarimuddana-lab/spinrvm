"""Unit tests for the driver_period_distances backfill script
(scripts/backfill_period_distances.py) — backfills GPS-measured Period 2/3
distance for rides that completed before the live writer (ride_complete.py)
existed, from the already-stored rides.ride_metrics.phases JSON.

Contract:
  - only backfills a phase with a real actual_distance_km — never falls back
    to estimated_distance_km (a pre-trip guess, not GPS-measured);
  - dry run (default) never writes;
  - --apply writes via record_ride_period_distances, which is itself
    replay-safe, so a re-run or overlap with the live writer is safe;
  - a per-ride failure is counted, never silently dropped.
"""

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
backfill = importlib.import_module("backfill_period_distances")


def _run(coro):
    return asyncio.run(coro)


def _ride(id, driver_id="d1", phases=None):
    return {
        "id": id,
        "driver_id": driver_id,
        "ride_metrics": {"phases": phases or {}},
        "assigned_at": "t_assigned",
        "driver_accepted_at": "t_accepted",
        "ride_started_at": "t_started",
        "ride_completed_at": "t_completed",
    }


def test_phase_distance_km_requires_actual_not_estimated():
    assert backfill._phase_distance_km({"phases": {"p": {"estimated_distance_km": 5.0}}}, "p") is None
    assert backfill._phase_distance_km({"phases": {"p": {"actual_distance_km": 5.0}}}, "p") == 5.0
    assert backfill._phase_distance_km({}, "p") is None
    assert backfill._phase_distance_km({"phases": {"p": {"actual_distance_km": -1}}}, "p") is None


def test_dry_run_counts_but_never_writes():
    rides = [
        _ride(
            "r1",
            phases={
                "navigating_to_pickup": {"actual_distance_km": 1.0},
                "trip_in_progress": {"actual_distance_km": 4.0},
            },
        ),
        _ride("r2", phases={"navigating_to_pickup": {"estimated_distance_km": 2.0}}),  # estimate-only, skipped
    ]
    get_rows = AsyncMock(return_value=rides)
    record = AsyncMock(return_value=2)
    with (
        patch("db_supabase.get_rows", get_rows),
        patch("utils.period_distance_audit.record_ride_period_distances", record),
    ):
        code = _run(backfill._main(apply_changes=False, before=None))

    assert code == 0
    record.assert_not_awaited()


def test_apply_writes_only_gps_measured_phases():
    rides = [
        _ride(
            "r1",
            phases={
                "navigating_to_pickup": {"actual_distance_km": 1.0},
                "trip_in_progress": {"actual_distance_km": 4.0},
            },
        ),
        _ride("r2", phases={"navigating_to_pickup": {"estimated_distance_km": 2.0}}),  # skipped: no actual
        _ride("r3", driver_id=None, phases={"trip_in_progress": {"actual_distance_km": 3.0}}),
    ]
    get_rows = AsyncMock(return_value=[r for r in rides if r["driver_id"]])  # $notnull filter excludes r3 server-side
    record = AsyncMock(return_value=2)
    with (
        patch("db_supabase.get_rows", get_rows),
        patch("utils.period_distance_audit.record_ride_period_distances", record),
    ):
        code = _run(backfill._main(apply_changes=True, before="2026-07-28"))

    assert code == 0
    record.assert_awaited_once()
    kwargs = record.await_args.kwargs
    assert kwargs["ride_id"] == "r1"
    periods = {p["period"]: p for p in kwargs["phases"]}
    assert periods[2]["distance_km"] == 1.0
    assert periods[2]["started_at"] == "t_assigned"
    assert periods[3]["distance_km"] == 4.0
    assert all(p["source"] == "gps_measured_backfill" for p in periods.values())


def test_per_ride_failure_is_counted_not_raised():
    rides = [_ride("r1", phases={"trip_in_progress": {"actual_distance_km": 4.0}})]
    get_rows = AsyncMock(return_value=rides)
    record = AsyncMock(side_effect=RuntimeError("db down"))
    with (
        patch("db_supabase.get_rows", get_rows),
        patch("utils.period_distance_audit.record_ride_period_distances", record),
    ):
        code = _run(backfill._main(apply_changes=True, before=None))

    assert code == 1  # nonzero exit signals the failure, but the run completes


def test_hitting_the_row_ceiling_logs_a_warning_not_a_silent_truncation():
    """2026-09-19 finding: a single limit=_ROW_LIMIT read would silently
    under-cover once completed rides exceed the ceiling, with a
    'scanned=10000' summary indistinguishable from a full scan. Paging must
    detect the truncation and warn."""
    import db_supabase as db_supabase_mod

    full_page = [
        _ride(f"r{i}", phases={"trip_in_progress": {"actual_distance_km": 1.0}}) for i in range(backfill._PAGE_SIZE)
    ]
    n_pages = backfill._ROW_LIMIT // backfill._PAGE_SIZE

    call_count = 0

    async def _paged_get_rows(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Always return a full page so the ceiling, not exhaustion, is hit.
        return full_page

    with (
        patch("db_supabase.get_rows", AsyncMock(side_effect=_paged_get_rows)),
    ):
        rides, has_more = _run(backfill._fetch_completed_rides(db_supabase_mod, {"status": "completed"}))

    assert has_more is True
    assert len(rides) == backfill._ROW_LIMIT
    assert call_count == n_pages

    with (
        patch("db_supabase.get_rows", AsyncMock(side_effect=_paged_get_rows)),
        patch.object(backfill.logger, "warning") as log_warning,
        patch("utils.period_distance_audit.record_ride_period_distances", AsyncMock(return_value=1)),
    ):
        _run(backfill._main(apply_changes=False, before=None))

    assert any("safety ceiling" in str(c) for c in log_warning.call_args_list)
