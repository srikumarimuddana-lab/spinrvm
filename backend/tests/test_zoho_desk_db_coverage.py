"""Coverage for services/zoho_desk_db.py (A1c, Sub-tier B).

Read helpers for the local Zoho Desk mirror (`zoho_desk_tickets`, migration
123) — every function is documented to return None (not raise) when the
mirror is unavailable/empty, so routes/support routes can transparently
fall back to the live Zoho API. Had no dedicated test file; only 11.76%
coverage as an incidental side effect of admin support-ticket route tests
that exercise the fallback path but rarely the mirror-hit path directly.

Query-builder mocking: `db_supabase.supabase.table(...)` returns a
self-chaining mock (every method — .select/.eq/.gte/.order/.range/.or_/
.limit — returns the SAME mock object) so optional filter calls don't break
the chain, matching how this repo's other db-layer tests mock Supabase's
fluent query builder. Only `.execute()` differs per test.

Test-only change — no application code modified.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


def _chaining_query_mock(execute_return=None, execute_side_effect=None):
    """A MagicMock whose every chainable method returns itself, so
    `.table(...).select(...).eq(...).order(...).range(...).execute()`
    works regardless of which optional filters get applied."""
    q = MagicMock()
    for method in ("table", "select", "eq", "gte", "order", "range", "or_", "limit"):
        getattr(q, method).return_value = q
    if execute_side_effect is not None:
        q.execute.side_effect = execute_side_effect
    else:
        q.execute.return_value = execute_return
    return q


def _res(data=None, count=None):
    r = MagicMock()
    r.data = data
    r.count = count
    return r


@pytest.fixture
def mock_client(monkeypatch):
    """A configured chaining query mock, wired in as db_supabase.supabase."""
    q = _chaining_query_mock()
    monkeypatch.setattr("backend.db_supabase.supabase", q)
    return q


@pytest.fixture
def unconfigured(monkeypatch):
    """db_supabase.supabase is falsy — every function must return None."""
    monkeypatch.setattr("backend.db_supabase.supabase", None)


class TestMirrorReady:
    @pytest.mark.anyio
    async def test_returns_false_when_unconfigured(self, unconfigured):
        from backend.services.zoho_desk_db import mirror_ready

        assert await mirror_ready() is False

    @pytest.mark.anyio
    async def test_returns_true_when_backfilled(self, monkeypatch, mock_client):
        from backend.services import zoho_desk_db

        monkeypatch.setattr(
            zoho_desk_db.db_supabase, "find_one", MagicMock(return_value=_await({"mirror_backfilled": True}))
        )
        assert await zoho_desk_db.mirror_ready() is True

    @pytest.mark.anyio
    async def test_returns_false_when_not_yet_backfilled(self, monkeypatch, mock_client):
        from backend.services import zoho_desk_db

        monkeypatch.setattr(
            zoho_desk_db.db_supabase, "find_one", MagicMock(return_value=_await({"mirror_backfilled": False}))
        )
        assert await zoho_desk_db.mirror_ready() is False

    @pytest.mark.anyio
    async def test_returns_false_when_config_row_missing(self, monkeypatch, mock_client):
        from backend.services import zoho_desk_db

        monkeypatch.setattr(zoho_desk_db.db_supabase, "find_one", MagicMock(return_value=_await(None)))
        assert await zoho_desk_db.mirror_ready() is False

    @pytest.mark.anyio
    async def test_swallows_db_error_and_returns_false(self, monkeypatch, mock_client):
        from backend.services import zoho_desk_db

        async def _boom(*a, **kw):
            raise ConnectionError("db down")

        monkeypatch.setattr(zoho_desk_db.db_supabase, "find_one", _boom)
        assert await zoho_desk_db.mirror_ready() is False


def _await(value):
    """Helper: wrap a plain value as an already-resolved coroutine for
    monkeypatching an `await`ed function with MagicMock(return_value=...)."""

    async def _coro(*_a, **_kw):
        return value

    return _coro()


class TestOpenClosedCounts:
    @pytest.mark.anyio
    async def test_returns_none_when_unconfigured(self, unconfigured):
        from backend.services.zoho_desk_db import open_closed_counts

        assert await open_closed_counts(None) is None

    @pytest.mark.anyio
    async def test_returns_total_and_closed_counts(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = _chaining_query_mock(execute_side_effect=[_res(count=42), _res(count=7)])
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        result = await zoho_desk_db.open_closed_counts(None)
        assert result == (42, 7)

    @pytest.mark.anyio
    async def test_applies_department_filter(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = _chaining_query_mock(execute_side_effect=[_res(count=10), _res(count=3)])
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        result = await zoho_desk_db.open_closed_counts("dept-1")
        assert result == (10, 3)
        q.eq.assert_any_call("department_id", "dept-1")

    @pytest.mark.anyio
    async def test_swallows_db_error_and_returns_none(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = MagicMock()
        q.table.side_effect = ConnectionError("db down")
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        assert await zoho_desk_db.open_closed_counts(None) is None


class TestMirrorCount:
    @pytest.mark.anyio
    async def test_returns_none_when_unconfigured(self, unconfigured):
        from backend.services.zoho_desk_db import mirror_count

        assert await mirror_count() is None

    @pytest.mark.anyio
    async def test_returns_count(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = _chaining_query_mock(execute_return=_res(count=123))
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        assert await zoho_desk_db.mirror_count() == 123

    @pytest.mark.anyio
    async def test_swallows_db_error_and_returns_none(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = MagicMock()
        q.table.side_effect = RuntimeError("not migrated")
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        assert await zoho_desk_db.mirror_count() is None


class TestApplyFilters:
    def test_no_filters_returns_query_unchanged(self):
        from backend.services.zoho_desk_db import _apply_filters

        q = MagicMock()
        result = _apply_filters(q, status=None, priority=None, channel=None, assignee_id=None, department_id=None)
        assert result is q
        q.eq.assert_not_called()

    def test_all_filters_applied(self):
        from backend.services.zoho_desk_db import _apply_filters

        q = _chaining_query_mock()
        _apply_filters(
            q, status="Open", priority="High", channel="Email", assignee_id="agent-1", department_id="dept-1"
        )
        q.eq.assert_any_call("status", "Open")
        q.eq.assert_any_call("priority", "High")
        q.eq.assert_any_call("channel", "Email")
        q.eq.assert_any_call("assignee_id", "agent-1")
        q.eq.assert_any_call("department_id", "dept-1")


class TestListMirror:
    @pytest.mark.anyio
    async def test_returns_none_when_unconfigured(self, unconfigured):
        from backend.services.zoho_desk_db import list_mirror

        assert await list_mirror() is None

    @pytest.mark.anyio
    async def test_returns_raw_payloads_for_rows_with_raw(self, monkeypatch):
        from backend.services import zoho_desk_db

        rows = [{"raw": {"id": "1"}}, {"raw": None}, {"raw": {"id": "3"}}]
        q = _chaining_query_mock(execute_return=_res(data=rows))
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        result = await zoho_desk_db.list_mirror()
        assert result == [{"id": "1"}, {"id": "3"}]

    @pytest.mark.anyio
    async def test_modified_time_sort_column_selected(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = _chaining_query_mock(execute_return=_res(data=[]))
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        await zoho_desk_db.list_mirror(sort_by="-modifiedTime")
        q.order.assert_any_call("modified_time", desc=True)

    @pytest.mark.anyio
    async def test_created_time_ascending_sort(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = _chaining_query_mock(execute_return=_res(data=[]))
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        await zoho_desk_db.list_mirror(sort_by="createdTime")
        q.order.assert_any_call("created_time", desc=False)

    @pytest.mark.anyio
    async def test_search_term_escapes_commas_and_builds_or_clause(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = _chaining_query_mock(execute_return=_res(data=[]))
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        await zoho_desk_db.list_mirror(search="smith, jones")
        called_arg = q.or_.call_args.args[0]
        assert "smith  jones" in called_arg  # comma replaced with a space
        assert "ticket_number.ilike" in called_arg

    @pytest.mark.anyio
    async def test_blank_search_term_skips_or_clause(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = _chaining_query_mock(execute_return=_res(data=[]))
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        await zoho_desk_db.list_mirror(search="   ")
        q.or_.assert_not_called()

    @pytest.mark.anyio
    async def test_swallows_db_error_and_returns_none(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = MagicMock()
        q.table.side_effect = ConnectionError("db down")
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        assert await zoho_desk_db.list_mirror() is None


class TestCountByStatus:
    @pytest.mark.anyio
    async def test_returns_none_when_unconfigured(self, unconfigured):
        from backend.services.zoho_desk_db import count_by_status

        assert await count_by_status(None, ["Open", "Closed"]) is None

    @pytest.mark.anyio
    async def test_returns_total_and_per_status_counts(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = _chaining_query_mock(execute_side_effect=[_res(count=100), _res(count=60), _res(count=40)])
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        total, by_status = await zoho_desk_db.count_by_status(None, ["Open", "Closed"])
        assert total == 100
        assert by_status == {"Open": 60, "Closed": 40}

    @pytest.mark.anyio
    async def test_empty_statuses_list_returns_total_only(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = _chaining_query_mock(execute_return=_res(count=100))
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        total, by_status = await zoho_desk_db.count_by_status(None, [])
        assert total == 100
        assert by_status == {}

    @pytest.mark.anyio
    async def test_swallows_db_error_and_returns_none(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = MagicMock()
        q.table.side_effect = RuntimeError("db down")
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        assert await zoho_desk_db.count_by_status(None, ["Open"]) is None

    @pytest.mark.anyio
    async def test_department_id_filters_both_total_and_per_status_queries(self, monkeypatch):
        """A truthy department_id must be applied to every _count() call —
        both the unfiltered-by-status total and each per-status count."""
        from backend.services import zoho_desk_db

        q = _chaining_query_mock(execute_side_effect=[_res(count=25), _res(count=25)])
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        total, by_status = await zoho_desk_db.count_by_status("dept-1", ["Open"])
        assert total == 25
        assert by_status == {"Open": 25}
        q.eq.assert_any_call("department_id", "dept-1")


class TestFetchWindow:
    @pytest.mark.anyio
    async def test_returns_none_when_unconfigured(self, unconfigured):
        from backend.services.zoho_desk_db import fetch_window

        assert await fetch_window(since_iso="2026-01-01T00:00:00Z", department_id=None, assignee_id=None) is None

    @pytest.mark.anyio
    async def test_single_page_under_page_size_stops_immediately(self, monkeypatch):
        from backend.services import zoho_desk_db

        rows = [{"raw": {"id": str(i)}} for i in range(5)]
        q = _chaining_query_mock(execute_return=_res(data=rows))
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        result = await zoho_desk_db.fetch_window(since_iso="2026-01-01T00:00:00Z", department_id=None, assignee_id=None)
        assert len(result) == 5
        assert q.execute.call_count == 1

    @pytest.mark.anyio
    async def test_paginates_when_first_page_is_full(self, monkeypatch):
        """A first page returning exactly _PAGE (1000) rows must trigger a
        second fetch; a page short of _PAGE stops the loop."""
        from backend.services import zoho_desk_db

        page1 = [{"raw": {"id": str(i)}} for i in range(zoho_desk_db._PAGE)]
        page2 = [{"raw": {"id": "last"}}]
        q = _chaining_query_mock(execute_side_effect=[_res(data=page1), _res(data=page2)])
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        result = await zoho_desk_db.fetch_window(
            since_iso="2026-01-01T00:00:00Z", department_id=None, assignee_id=None, max_rows=zoho_desk_db._PAGE + 500
        )
        assert len(result) == zoho_desk_db._PAGE + 1
        assert q.execute.call_count == 2

    @pytest.mark.anyio
    async def test_department_and_assignee_filters_applied(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = _chaining_query_mock(execute_return=_res(data=[]))
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        await zoho_desk_db.fetch_window(since_iso="2026-01-01T00:00:00Z", department_id="dept-1", assignee_id="agent-1")
        q.eq.assert_any_call("department_id", "dept-1")
        q.eq.assert_any_call("assignee_id", "agent-1")

    @pytest.mark.anyio
    async def test_swallows_db_error_and_returns_none(self, monkeypatch):
        from backend.services import zoho_desk_db

        q = MagicMock()
        q.table.side_effect = ConnectionError("db down")
        monkeypatch.setattr(zoho_desk_db.db_supabase, "supabase", q)

        result = await zoho_desk_db.fetch_window(since_iso="2026-01-01T00:00:00Z", department_id=None, assignee_id=None)
        assert result is None
