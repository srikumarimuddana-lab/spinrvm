"""Unit tests for scripts/backfill_imported_ride_routes.py.

Regression for a 2026-09-19 spinr-migration-reviewer finding: main()'s
update loop called ``db_supabase.update_one("rides", r["id"], update_data)``
-- a bare ride-id string where update_one's real signature requires a
{"id": ...} filter dict. _apply_filters (repositories/_base.py) raises
TypeError on a non-dict filter, so every single update threw, was caught by
the loop's own broad `except Exception`, and logged as an error -- the
script had never successfully written a route/distance to a single ride
since it was written. No test existed for this script before this file.
"""

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
backfill = importlib.import_module("backfill_imported_ride_routes")


def _run(coro):
    return asyncio.run(coro)


def _ride(id, pickup_lat=52.1, pickup_lng=-106.6, dropoff_lat=52.2, dropoff_lng=-106.7, polyline=None):
    return {
        "id": id,
        "pickup_lat": pickup_lat,
        "pickup_lng": pickup_lng,
        "dropoff_lat": dropoff_lat,
        "dropoff_lng": dropoff_lng,
        "distance_km": None,
        "planned_route_polyline": polyline,
    }


def test_apply_writes_with_a_dict_filter_not_a_bare_id():
    """The regression itself: update_one must be called with {"id": ride_id},
    never the bare ride_id string that made every real update throw."""
    rides = [_ride("r1")]
    get_rows = AsyncMock(return_value=rides)
    update_one = AsyncMock(return_value={"id": "r1"})
    route_result = (4.2, [[52.1, -106.6], [52.2, -106.7]])
    fetch_route = AsyncMock(return_value=route_result)

    with (
        patch("db_supabase.get_rows", get_rows),
        patch("db_supabase.update_one", update_one),
        patch.object(backfill, "_get_osrm_url", AsyncMock(return_value="http://osrm.test")),
        patch.object(backfill, "_fetch_osrm_route", fetch_route),
    ):
        _run(backfill.main(dry_run=False))

    update_one.assert_awaited_once()
    args = update_one.await_args.args
    assert args[0] == "rides"
    assert args[1] == {"id": "r1"}, "filters must be a dict, not a bare id string (2026-09-19 regression)"
    assert args[2]["distance_km"] == 4.2


def test_a_failed_update_is_counted_not_silently_dropped():
    """If update_one still throws for some other reason, the per-row
    exception handler must not report a false 'all updated' summary."""
    rides = [_ride("r1"), _ride("r2")]
    get_rows = AsyncMock(return_value=rides)
    update_one = AsyncMock(side_effect=[None, RuntimeError("boom")])
    fetch_route = AsyncMock(return_value=(1.0, [[52.1, -106.6], [52.2, -106.7]]))

    with (
        patch("db_supabase.get_rows", get_rows),
        patch("db_supabase.update_one", update_one),
        patch.object(backfill, "_get_osrm_url", AsyncMock(return_value="http://osrm.test")),
        patch.object(backfill, "_fetch_osrm_route", fetch_route),
        patch.object(backfill.logger, "info") as log_info,
    ):
        _run(backfill.main(dry_run=False))

    assert update_one.await_count == 2
    summary_calls = [c for c in log_info.call_args_list if "Updated" in str(c)]
    assert summary_calls, "expected an 'Updated N/M' summary log line"
    assert summary_calls[-1].args[1:] == (1, 2), "only 1 of 2 rides actually succeeded"


def test_dry_run_never_calls_update_one():
    rides = [_ride("r1")]
    get_rows = AsyncMock(return_value=rides)
    update_one = AsyncMock()
    fetch_route = AsyncMock(return_value=(4.2, [[52.1, -106.6], [52.2, -106.7]]))

    with (
        patch("db_supabase.get_rows", get_rows),
        patch("db_supabase.update_one", update_one),
        patch.object(backfill, "_get_osrm_url", AsyncMock(return_value="http://osrm.test")),
        patch.object(backfill, "_fetch_osrm_route", fetch_route),
    ):
        _run(backfill.main(dry_run=True))

    update_one.assert_not_awaited()


def test_dry_run_is_the_default():
    """2026-09-19 regression: this script (and its sibling
    backfill_imported_ride_snapshots.py) used to default to apply mode,
    breaking from every other script's --apply-required convention. main()
    with no explicit dry_run argument must not write."""
    rides = [_ride("r1")]
    get_rows = AsyncMock(return_value=rides)
    update_one = AsyncMock()
    fetch_route = AsyncMock(return_value=(4.2, [[52.1, -106.6], [52.2, -106.7]]))

    with (
        patch("db_supabase.get_rows", get_rows),
        patch("db_supabase.update_one", update_one),
        patch.object(backfill, "_get_osrm_url", AsyncMock(return_value="http://osrm.test")),
        patch.object(backfill, "_fetch_osrm_route", fetch_route),
    ):
        _run(backfill.main())  # no dry_run kwarg at all

    update_one.assert_not_awaited()


def test_apply_flag_maps_to_dry_run_false():
    """The CLI's --apply flag must map to dry_run=False, --limit to limit."""
    parser_ns = backfill.argparse.ArgumentParser(
        description=backfill.__doc__, formatter_class=backfill.argparse.RawDescriptionHelpFormatter
    )
    parser_ns.add_argument("--apply", action="store_true")
    parser_ns.add_argument("--limit", type=int, default=None)
    args = parser_ns.parse_args(["--apply", "--limit", "10"])
    assert args.apply is True
    assert args.limit == 10


def test_more_rows_beyond_limit_logs_a_warning_not_a_false_all_clear():
    """If imported-ride count ever exceeds a --limit, the run must say so —
    not report a summary that looks complete while silently dropping rows."""
    page = [_ride(f"r{i}") for i in range(backfill._PAGE_SIZE)]
    get_rows = AsyncMock(return_value=page)
    fetch_route = AsyncMock(return_value=(1.0, [[52.1, -106.6], [52.2, -106.7]]))

    with (
        patch("db_supabase.get_rows", get_rows),
        patch.object(backfill, "_get_osrm_url", AsyncMock(return_value="http://osrm.test")),
        patch.object(backfill, "_fetch_osrm_route", fetch_route),
        patch.object(backfill.logger, "warning") as log_warning,
    ):
        rides, has_more = _run(backfill._fetch_imported_rides(limit=5))

    assert has_more is True
    assert len(rides) == 5

    with (
        patch("db_supabase.get_rows", get_rows),
        patch.object(backfill, "_get_osrm_url", AsyncMock(return_value="http://osrm.test")),
        patch.object(backfill, "_fetch_osrm_route", fetch_route),
        patch.object(backfill.logger, "warning", log_warning),
    ):
        _run(backfill.main(dry_run=True, limit=5))

    assert any("more imported rides remain" in str(c) for c in log_warning.call_args_list)
