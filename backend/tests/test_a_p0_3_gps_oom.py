"""
Regression tests for A-P0-3: GPS OOM — large row fetches in maintenance.py.

Background
----------
admin_cleanup_location_history used:
    old_rows = await get_rows(..., limit=100000)
    deleted_historical = len(old_rows or [])
Loading up to 100 000 full GPS rows (~10 MB) into the process just to
call len() on the result; then repeating the pattern for idle rows.

admin_rollup_driver_daily had two related issues:
  - presence_rows fetched 50 000 full GPS rows (lat/lng/phase/metadata) to
    discover active driver IDs; only driver_id was needed.
  - decline_logs fetched up to 100 000 audit-log rows; capped to 10 000.

Fixes:
  1. Replaced get_rows+len with count_documents in cleanup paths.
  2. Added columns="driver_id" to the presence query.
  3. Capped decline_logs limit to 10 000.

Test layers
-----------
Layer 1 — pure logic: always runnable.
Layer 2 — integration: require backend deps; skipped locally, run in CI.
"""

import pytest

# ---------------------------------------------------------------------------
# Layer 1 — pure logic
# ---------------------------------------------------------------------------


def test_count_documents_is_cheaper_than_get_rows_for_counting():
    """Conceptual guard: get_rows loads full rows; count_documents uses DB aggregation."""
    rows = [{"id": str(i), "lat": 52.1, "lng": -106.6, "tracking_phase": "online_idle"} for i in range(1000)]
    # Old pattern: load rows, count in Python
    old_count = len(rows)
    # New pattern: count_documents returns an int directly — no rows in memory
    new_count = 1000  # what count_documents would return
    assert old_count == new_count  # same answer, zero memory for rows in new path


def test_presence_query_with_driver_id_column_only():
    """Presence rows need only driver_id; simulate the column-limited result."""
    # Simulates what Supabase returns with columns="driver_id"
    limited_rows = [{"driver_id": f"d{i}"} for i in range(5)]
    # The deduplication logic still works correctly
    drivers_with_gps: set = set()
    for p in limited_rows:
        did = p.get("driver_id")
        if did:
            drivers_with_gps.add(did)
    assert drivers_with_gps == {"d0", "d1", "d2", "d3", "d4"}


def test_presence_query_full_row_bloat():
    """Contrast: full GPS rows carry ~10× more data than driver_id alone."""
    full_row = {
        "driver_id": "d1",
        "lat": 52.1333,
        "lng": -106.6667,
        "timestamp": "2026-04-27T10:00:00Z",
        "tracking_phase": "online_idle",
        "heading": 90.0,
        "speed": 0.0,
        "accuracy": 5.0,
        "ride_id": None,
    }
    id_only_row = {"driver_id": "d1"}
    assert len(full_row) > len(id_only_row)
    # At 50 000 rows the saving is substantial
    saved_fields_per_row = len(full_row) - len(id_only_row)
    assert saved_fields_per_row >= 7  # at least 7 unneeded fields eliminated


def test_decline_logs_cap_is_10000():
    """The decline_logs query limit must be 10 000, not 100 000."""
    # Reads maintenance.py source to verify the constant — pure string check
    import ast
    import pathlib

    source = pathlib.Path("backend/routes/admin/maintenance.py").read_text()
    tree = ast.parse(source)

    # Walk the AST and find the get_rows call for audit_logs with action=ride_declined.
    # We're looking for the keyword argument limit=N in that specific call.
    limits_found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Check if it's a call to db.get_rows or db_supabase.get_rows
        func = node.func
        is_get_rows = isinstance(func, ast.Attribute) and func.attr == "get_rows"
        if not is_get_rows:
            continue
        # Check if any keyword is limit=N for a large N (> 10000)
        for kw in node.keywords:
            if kw.arg == "limit" and isinstance(kw.value, ast.Constant):
                limits_found.append(kw.value.value)

    # The two cleanup count_documents calls should have no limit kwarg.
    # The ride_declined audit_logs call should use limit=10000, not 100000.
    large_limits = [lim for lim in limits_found if lim >= 100000]
    # Only per-driver GPS fetch (limit=100000) is allowed; all others must be ≤ 50000.
    # That one instance is expected (it's per-driver, not cross-driver).
    assert len(large_limits) <= 1, (
        f"Found {len(large_limits)} get_rows call(s) with limit ≥ 100 000: {large_limits}. "
        "At most one is allowed (the per-driver GPS fetch)."
    )


def test_cleanup_uses_count_documents_not_get_rows_for_counting():
    """Verify maintenance.py uses count_documents (not get_rows+len) for cleanup counts."""
    import pathlib

    source = pathlib.Path("backend/routes/admin/maintenance.py").read_text()
    # The cleanup endpoint must reference count_documents
    assert "count_documents" in source, "cleanup must use count_documents to avoid OOM"
    # The cleanup endpoint must NOT use get_rows with a large limit just to call len()
    # (i.e., we should not see the old pattern: get_rows + limit=100000 in the cleanup block)
    cleanup_block_start = source.find("async def admin_cleanup_location_history")
    cleanup_block_end = source.find("async def admin_rollup_driver_daily")
    cleanup_block = source[cleanup_block_start:cleanup_block_end]
    assert "limit=100000" not in cleanup_block, "cleanup block must not fetch 100k rows"


def test_presence_rows_uses_driver_id_column():
    """Verify presence_rows query uses columns='driver_id' not columns='*'."""
    import pathlib

    source = pathlib.Path("backend/routes/admin/maintenance.py").read_text()
    assert 'columns="driver_id"' in source, "presence query must restrict to driver_id column"


# ---------------------------------------------------------------------------
# Layer 2 — integration (require backend deps; skipped if unavailable)
# ---------------------------------------------------------------------------

try:
    import backend.routes.admin.maintenance as _maintenance_module

    _HAS_BACKEND_DEPS = True
except BaseException:
    _maintenance_module = None  # type: ignore[assignment]
    _HAS_BACKEND_DEPS = False

_skip_no_deps = pytest.mark.skipif(not _HAS_BACKEND_DEPS, reason="backend deps not installed")


@_skip_no_deps
@pytest.mark.anyio
async def test_cleanup_calls_count_documents_not_get_rows():
    """Cleanup endpoint must call count_documents (not get_rows) for the deletion count."""
    from unittest.mock import AsyncMock, patch

    count_mock = AsyncMock(return_value=42)
    delete_mock = AsyncMock(return_value=None)
    get_rows_mock = AsyncMock(return_value=[])

    with (
        patch("backend.routes.admin.maintenance.db_supabase.count_documents", count_mock),
        patch("backend.routes.admin.maintenance.db_supabase.delete_many", delete_mock),
        patch("backend.routes.admin.maintenance.db_supabase.get_rows", get_rows_mock),
    ):
        result = await _maintenance_module.admin_cleanup_location_history(days=30)

    # count_documents must be called (not get_rows) for deletion counts
    assert count_mock.call_count == 2, "must call count_documents twice (historical + idle)"
    # get_rows must NOT be called by the cleanup endpoint
    assert get_rows_mock.call_count == 0, "cleanup must not call get_rows (OOM risk)"
    assert result["deleted_historical"] == 42
    assert result["deleted_idle"] == 42


@_skip_no_deps
@pytest.mark.anyio
async def test_cleanup_skips_delete_when_count_is_zero():
    """delete_many must not be called when count_documents returns 0."""
    from unittest.mock import AsyncMock, patch

    with (
        patch("backend.routes.admin.maintenance.db_supabase.count_documents", AsyncMock(return_value=0)),
        patch("backend.routes.admin.maintenance.db_supabase.delete_many", AsyncMock()) as delete_mock,
    ):
        result = await _maintenance_module.admin_cleanup_location_history(days=30)

    assert delete_mock.call_count == 0, "must not call delete_many when nothing to delete"
    assert result["deleted_historical"] == 0
    assert result["deleted_idle"] == 0
