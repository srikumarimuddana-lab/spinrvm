"""
Regression tests for B-P1-7: ride history DB-side status filter.

Background
----------
get_ride_history fetched up to 2000 rides with no status filter, then
filtered for completed / cancelled-with-driver in Python.  For riders with
long history this meant reading megabytes of data from Supabase on every
page load.

Fix: push completed / cancelled-with-driver filtering to the DB query and use
`ORDER BY created_at DESC, id DESC` so cursor pagination remains stable when
many rides share the same timestamp.

Test layers
-----------
Layer 1 — pure logic: verify filter/pagination logic, no imports needed.
Layer 2 — integration: require backend deps; skipped locally, run in CI.
"""

from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request as StarletteRequest

_MOCK_REQUEST = StarletteRequest(
    {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "root_path": ""}
)

# ---------------------------------------------------------------------------
# Layer 1 — pure logic
# ---------------------------------------------------------------------------


def _apply_history_filter(rides):
    """Mirror of the visible history predicate pushed into the DB query."""
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
def test_cursor_clause_uses_id_tiebreaker():
    clause = "created_at.lt.2026-05-29T10:00:00Z,and(created_at.eq.2026-05-29T10:00:00Z,id.lt.r20)"
    assert clause == _rides_module._ride_history_cursor_or("2026-05-29T10:00:00Z", "r20")


@_skip_no_deps
def test_visible_history_clause_excludes_unmatched_cancellations():
    clause = _rides_module._RIDE_HISTORY_VISIBLE_OR
    assert "status.eq.completed" in clause
    assert "status.eq.cancelled" in clause
    assert "driver_id.not.is.null" in clause


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.limit_value = None

    def select(self, columns):
        self.calls.append(("select", columns))
        return self

    def eq(self, column, value):
        self.calls.append(("eq", column, value))
        return self

    def or_(self, clause):
        self.calls.append(("or", clause))
        return self

    def order(self, column, desc=False):
        self.calls.append(("order", column, desc))
        return self

    def limit(self, value):
        self.calls.append(("limit", value))
        self.limit_value = value
        return self

    def execute(self):
        return _FakeResult(self.rows[: self.limit_value])


class _FakeSupabase:
    def __init__(self, rows):
        self.query = _FakeQuery(rows)

    def table(self, table_name):
        self.query.calls.append(("table", table_name))
        return self.query


async def _run_sync_now(fn):
    return fn()


@_skip_no_deps
@pytest.mark.anyio
async def test_fetch_ride_history_page_uses_stable_cursor_query():
    rows = [{"id": f"r{i}", "status": "completed", "created_at": "2026-05-29T10:00:00Z"} for i in range(21)]
    fake_supabase = _FakeSupabase(rows)

    with (
        patch("backend.routes.rides._deps.db_supabase.supabase", fake_supabase),
        patch("backend.routes.rides._deps.db_supabase.run_sync", _run_sync_now),
    ):
        result = await _rides_module._fetch_ride_history_page(
            rider_id="user-1",
            limit=20,
            cursor_ts="2026-05-29T10:00:00Z",
            before="r20",
        )

    assert len(result) == 21
    assert ("eq", "rider_id", "user-1") in fake_supabase.query.calls
    assert ("or", _rides_module._RIDE_HISTORY_VISIBLE_OR) in fake_supabase.query.calls
    assert (
        "or",
        "created_at.lt.2026-05-29T10:00:00Z,and(created_at.eq.2026-05-29T10:00:00Z,id.lt.r20)",
    ) in fake_supabase.query.calls
    assert ("order", "created_at", True) in fake_supabase.query.calls
    assert ("order", "id", True) in fake_supabase.query.calls
    assert ("limit", 21) in fake_supabase.query.calls


@_skip_no_deps
@pytest.mark.anyio
async def test_get_ride_history_returns_next_cursor_when_extra_row_exists():
    mock_rides = [
        {"id": f"r{i:02d}", "status": "completed", "driver_id": "d1", "created_at": "2026-05-29T10:00:00Z"}
        for i in range(21)
    ]

    with (
        patch("backend.routes.rides.queries._fetch_ride_history_page", AsyncMock(return_value=mock_rides)),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
    ):
        result = await _rides_module.get_ride_history(
            request=_MOCK_REQUEST,
            limit=20,
            before=None,
            current_user={"id": "user-1"},
        )

    assert len(result["rides"]) == 20
    assert result["rides"][-1]["id"] == "r19"
    assert result["next_cursor"] == "r19"
