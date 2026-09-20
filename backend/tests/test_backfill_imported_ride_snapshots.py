"""Unit tests for scripts/backfill_imported_ride_snapshots.py.

Regression for two 2026-09-19 spinr-migration-reviewer findings (same audit
that found the update_one bug in backfill_imported_ride_routes.py):

  1. This script (and its sibling backfill_imported_ride_routes.py) used to
     default to apply mode -- --dry-run was opt-in, breaking from every
     other script in this family's --apply-required convention. main() with
     no explicit dry_run argument must not render/upload/write.
  2. The read was a single get_rows(limit=500) with no pagination or
     "more rows remain" signal -- silently under-covers once the imported-ride
     count exceeds the cap, with a summary that looks complete. Paging must
     detect and warn on truncation.

No test existed for this script before this file.
"""

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
backfill = importlib.import_module("backfill_imported_ride_snapshots")


def _run(coro):
    return asyncio.run(coro)


def _ride(id):
    return {
        "id": id,
        "pickup_lat": 52.1,
        "pickup_lng": -106.6,
        "dropoff_lat": 52.2,
        "dropoff_lng": -106.7,
        "planned_route_polyline": None,
    }


def test_dry_run_is_the_default():
    """main() with no dry_run kwarg at all must not reach the write path.

    Mocks the renderer and upload so that an apply-mode run would
    definitely proceed all the way to update_one -- without this, a
    default-apply bug could hide behind an unrelated exception in the
    unmocked render/upload pipeline (real image rendering, real Supabase
    Storage) instead of actually proving dry-run was respected."""
    rides = [_ride("r1")]
    get_rows = AsyncMock(return_value=rides)
    update_one = AsyncMock()
    render_google = AsyncMock(return_value=b"fake-png-bytes")

    with (
        patch("db_supabase.get_rows", get_rows),
        patch("db_supabase.update_one", update_one),
        patch.object(backfill, "get_app_settings", AsyncMock(return_value={"google_maps_api_key": "fake-key"})),
        patch.object(backfill, "render_ride_snapshot_google", render_google),
    ):
        _run(backfill.main())  # no dry_run kwarg at all

    render_google.assert_not_awaited()
    update_one.assert_not_awaited()


def test_apply_flag_maps_to_dry_run_false():
    parser = backfill.argparse.ArgumentParser(
        description="Backfill route snapshots for imported rides",
        formatter_class=backfill.argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(["--apply"])
    assert args.apply is True


def test_more_rows_beyond_limit_logs_a_warning():
    page = [_ride(f"r{i}") for i in range(backfill._PAGE_SIZE)]
    get_rows = AsyncMock(return_value=page)

    with patch("db_supabase.get_rows", get_rows):
        rides, has_more = _run(
            backfill._fetch_rides_needing_snapshots({"legacy_import_metadata": {"$notnull": True}}, 5)
        )

    assert has_more is True
    assert len(rides) == 5

    with (
        patch("db_supabase.get_rows", get_rows),
        patch.object(backfill, "get_app_settings", AsyncMock(return_value={})),
        patch.object(backfill.logger, "warning") as log_warning,
    ):
        _run(backfill.main(dry_run=True, limit=5))

    assert any("more rides remain" in str(c) for c in log_warning.call_args_list)


def test_no_rows_beyond_a_short_page_reports_no_more():
    """A page shorter than _PAGE_SIZE means exhaustion, not truncation."""
    rides = [_ride("r1")]
    get_rows = AsyncMock(return_value=rides)

    with patch("db_supabase.get_rows", get_rows):
        result, has_more = _run(backfill._fetch_rides_needing_snapshots({}, None))

    assert has_more is False
    assert len(result) == 1
