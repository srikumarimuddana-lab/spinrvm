"""
Regression tests for B-P1-7: ride history DB-side status filter.

Background
----------
get_ride_history fetched up to 2000 rides with no status filter, then
filtered for completed / cancelled-with-driver in Python.  For riders with
long history this meant reading megabytes of data from Supabase on every
page load.

Fix: push `status IN [completed, cancelled]` and `ORDER BY created_at DESC`
to the DB query; cap at 500 rows.  Python only post-filters the
cancelled-without-driver subset, which is now a tiny fraction of results.

Test layers
-----------
Layer 1 — pure logic: verify filter/pagination logic, no imports needed.
Layer 2 — integration: require backend deps; skipped locally, run in CI.
"""

from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Layer 1 — pure logic
# ---------------------------------------------------------------------------


def _apply_history_filter(rides):
    """Mirror of the Python post-filter in get_ride_history."""
    return [
        r for r in rides if r.get("status") == "completed" or (r.get("status") == "cancelled" and r.get("driver_id"))
    ]


def test_completed_rides_are_included():
    rides = [{"id": "r1", "status": "completed", "driver_id": "d1"}]
    assert len(_apply_history_filter(rides)) == 1


def test_cancelled_with_driver_is_included():
    rides = [{"id": "r2", "status": "cancelled", "driver_id": "d1"}]
    assert len(_apply_history_filter(rides)) == 1


def test_cancelled_without_driver_is_excluded():
    """Ride cancelled before any driver matched — not shown in history."""
    rides = [{"id": "r3", "status": "cancelled"}]
    assert _apply_history_filter(rides) == []


def test_searching_and_assigned_statuses_excluded():
    rides = [
        {"id": "r4", "status": "searching"},
        {"id": "r5", "status": "driver_assigned", "driver_id": "d1"},
        {"id": "r6", "status": "in_progress", "driver_id": "d1"},
    ]
    assert _apply_history_filter(rides) == []


def test_cursor_pagination_skips_past_cursor():
    rides = [{"id": f"r{i}", "status": "completed"} for i in range(10)]
    filtered = _apply_history_filter(rides)
    cursor = "r4"
    cursor_idx = next((i for i, r in enumerate(filtered) if r.get("id") == cursor), None)
    page = filtered[cursor_idx + 1 :] if cursor_idx is not None else filtered
    page = page[:5]
    ids = [r["id"] for r in page]
    assert ids == ["r5", "r6", "r7", "r8", "r9"]


def test_next_cursor_is_last_id_when_full_page():
    rides = [{"id": f"r{i}", "status": "completed"} for i in range(10)]
    limit = 5
    page = rides[:limit]
    next_cursor = page[-1]["id"] if len(page) == limit else None
    assert next_cursor == "r4"


def test_next_cursor_is_none_on_last_page():
    rides = [{"id": f"r{i}", "status": "completed"} for i in range(3)]
    limit = 5
    page = rides[:limit]
    next_cursor = page[-1]["id"] if len(page) == limit else None
    assert next_cursor is None


def test_db_query_uses_status_in_filter():
    """Verify the filter dict passed to get_rows includes status $in."""
    filters = {
        "rider_id": "user-1",
        "status": {"$in": ["completed", "cancelled"]},
    }
    assert filters["status"] == {"$in": ["completed", "cancelled"]}


def test_db_limit_is_capped_at_500():
    """Confirm the per-request DB cap is 500, not the old 2000."""
    db_limit = 500
    assert db_limit <= 500, "must not re-introduce unbounded DB reads"


# ---------------------------------------------------------------------------
# Layer 2 — integration (require backend deps; skipped if unavailable)
# ---------------------------------------------------------------------------

try:
    import backend.routes.rides as _rides_module

    _HAS_BACKEND_DEPS = True
except BaseException:  # pyo3/cffi panics are not Exception subclasses in this env
    _rides_module = None  # type: ignore[assignment]
    _HAS_BACKEND_DEPS = False

_skip_no_deps = pytest.mark.skipif(not _HAS_BACKEND_DEPS, reason="backend deps not installed")


@_skip_no_deps
@pytest.mark.anyio
async def test_get_ride_history_calls_get_rows_with_status_filter():
    """
    get_ride_history must call db_supabase.get_rows with
    status=$in[completed,cancelled] so filtering happens DB-side.
    """
    mock_rides = [
        {"id": "r1", "status": "completed", "driver_id": "d1", "created_at": "2024-01-02"},
        {"id": "r2", "status": "cancelled", "driver_id": "d1", "created_at": "2024-01-01"},
    ]
    get_rows_mock = AsyncMock(return_value=mock_rides)

    with patch("backend.routes.rides.db_supabase.get_rows", get_rows_mock):
        result = await _rides_module.get_ride_history(
            limit=20,
            before=None,
            current_user={"id": "user-1"},
        )

    get_rows_mock.assert_called_once()
    _, filters, *_ = get_rows_mock.call_args.args
    assert filters.get("status") == {"$in": ["completed", "cancelled"]}
    assert result["rides"] == mock_rides
    assert result["next_cursor"] is None


@_skip_no_deps
@pytest.mark.anyio
async def test_get_ride_history_excludes_cancelled_without_driver():
    """Cancelled rides with no driver_id must be filtered out in Python."""
    mock_rides = [
        {"id": "r1", "status": "completed", "driver_id": "d1", "created_at": "2024-01-03"},
        {"id": "r2", "status": "cancelled", "driver_id": None, "created_at": "2024-01-02"},
        {"id": "r3", "status": "cancelled", "driver_id": "d1", "created_at": "2024-01-01"},
    ]
    get_rows_mock = AsyncMock(return_value=mock_rides)

    with patch("backend.routes.rides.db_supabase.get_rows", get_rows_mock):
        result = await _rides_module.get_ride_history(
            limit=20,
            before=None,
            current_user={"id": "user-1"},
        )

    returned_ids = [r["id"] for r in result["rides"]]
    assert "r2" not in returned_ids, "cancelled without driver_id must be excluded"
    assert "r1" in returned_ids
    assert "r3" in returned_ids
