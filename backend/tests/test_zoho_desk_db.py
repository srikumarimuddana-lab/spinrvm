"""Coverage tests for backend/services/zoho_desk_db.py — the Zoho Desk mirror
read layer (migration 123's ``zoho_desk_tickets`` table).

TEST-ONLY change: no application code touched. Follows the mocking convention
already established for Supabase-fluent-chain modules (see
tests/test_corporate_db_helpers.py): ``db_supabase.supabase`` is the
autouse-patched ``mock_supabase_client`` fixture; ``.range()``/``.or_()`` are
not pre-wired by conftest's base fixture, so each test wires them onto
``table`` explicitly before exercising the real (unmocked) module functions.

Every public function in the module has the same "None when supabase is
unconfigured" and "None (log + swallow) on unexpected exception" contract —
both branches are covered for each function per CLAUDE.md's
"do not silently swallow errors" logging rule (these ARE the documented
graceful-fallback paths, not silent swallows: callers fall back to the live
Zoho API when the mirror read returns None).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import services.zoho_desk_db as zdb

pytestmark = pytest.mark.anyio


def _resp(data=None, count=None):
    return MagicMock(data=data, count=count)


# ── mirror_ready ─────────────────────────────────────────────────────


async def test_mirror_ready_false_when_supabase_unconfigured(monkeypatch):
    monkeypatch.setattr(zdb.db_supabase, "supabase", None)
    assert await zdb.mirror_ready() is False


async def test_mirror_ready_true_when_backfilled(monkeypatch):
    async def _find_one(table, filters):
        assert table == "zoho_desk_config"
        assert filters == {"id": "default"}
        return {"id": "default", "mirror_backfilled": True}

    monkeypatch.setattr(zdb.db_supabase, "find_one", _find_one)
    assert await zdb.mirror_ready() is True


async def test_mirror_ready_false_when_not_backfilled_or_missing(monkeypatch):
    async def _find_one(table, filters):
        return None

    monkeypatch.setattr(zdb.db_supabase, "find_one", _find_one)
    assert await zdb.mirror_ready() is False


async def test_mirror_ready_false_on_exception(monkeypatch):
    async def _raise(table, filters):
        raise RuntimeError("db down")

    monkeypatch.setattr(zdb.db_supabase, "find_one", _raise)
    # Best-effort: an unexpected error yields "not ready" rather than raising —
    # callers fall back to the live Zoho API.
    assert await zdb.mirror_ready() is False


# ── open_closed_counts ───────────────────────────────────────────────


async def test_open_closed_counts_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(zdb.db_supabase, "supabase", None)
    assert await zdb.open_closed_counts(None) is None


async def test_open_closed_counts_returns_totals(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    calls = []

    def _execute():
        calls.append(list(table.eq.call_args_list))
        # First call (total): no status_type filter applied yet at execute time
        # for THIS call, second call has status_type="Closed" applied.
        closed = ("status_type", "Closed") in [c.args for c in table.eq.call_args_list]
        return _resp(count=4 if closed else 10)

    table.execute = MagicMock(side_effect=_execute)
    out = await zdb.open_closed_counts("dep1")
    assert out == (10, 4)
    eq_calls = [c.args for c in table.eq.call_args_list]
    assert ("department_id", "dep1") in eq_calls


async def test_open_closed_counts_none_on_exception(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.execute = MagicMock(side_effect=RuntimeError("boom"))
    assert await zdb.open_closed_counts(None) is None


# ── mirror_count ─────────────────────────────────────────────────────


async def test_mirror_count_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(zdb.db_supabase, "supabase", None)
    assert await zdb.mirror_count() is None


async def test_mirror_count_returns_int(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.execute = MagicMock(return_value=_resp(count=42))
    assert await zdb.mirror_count() == 42


async def test_mirror_count_none_on_exception(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.execute = MagicMock(side_effect=RuntimeError("boom"))
    assert await zdb.mirror_count() is None


# ── list_mirror ──────────────────────────────────────────────────────


async def test_list_mirror_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(zdb.db_supabase, "supabase", None)
    assert await zdb.list_mirror() is None


async def test_list_mirror_default_sort_uses_created_time_desc(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    rows = [{"raw": {"id": "t1"}}, {"raw": {"id": "t2"}}]
    table.execute = MagicMock(return_value=_resp(data=rows))

    out = await zdb.list_mirror()
    assert out == [{"id": "t1"}, {"id": "t2"}]
    table.order.assert_called_with("created_time", desc=True)


async def test_list_mirror_modified_time_ascending_sort(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    table.execute = MagicMock(return_value=_resp(data=[]))

    out = await zdb.list_mirror(sort_by="modifiedTime")
    assert out == []
    table.order.assert_called_with("modified_time", desc=False)


async def test_list_mirror_applies_all_filters(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    table.execute = MagicMock(return_value=_resp(data=[]))

    await zdb.list_mirror(
        status="Open",
        priority="High",
        channel="Email",
        assignee_id="agent1",
        department_id="dep1",
    )
    eq_calls = [c.args for c in table.eq.call_args_list]
    assert ("status", "Open") in eq_calls
    assert ("priority", "High") in eq_calls
    assert ("channel", "Email") in eq_calls
    assert ("assignee_id", "agent1") in eq_calls
    assert ("department_id", "dep1") in eq_calls


async def test_list_mirror_search_builds_or_clause_with_comma_replaced(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    table.or_.return_value = table
    table.execute = MagicMock(return_value=_resp(data=[]))

    await zdb.list_mirror(search=" Acme, Inc ")
    or_arg = table.or_.call_args.args[0]
    # module replaces "," with " " (not escaped) before building the ilike OR
    assert "Acme  Inc" in or_arg
    assert "ticket_number.ilike.%Acme  Inc%" in or_arg
    assert "subject.ilike.%Acme  Inc%" in or_arg
    assert "contact_email.ilike.%Acme  Inc%" in or_arg
    assert "contact_name.ilike.%Acme  Inc%" in or_arg


async def test_list_mirror_blank_search_skips_or_clause(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    table.execute = MagicMock(return_value=_resp(data=[]))

    await zdb.list_mirror(search="   ")
    table.or_.assert_not_called()


async def test_list_mirror_skips_rows_with_no_raw_payload(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    rows = [{"raw": {"id": "t1"}}, {"raw": None}, {}]
    table.execute = MagicMock(return_value=_resp(data=rows))

    out = await zdb.list_mirror()
    assert out == [{"id": "t1"}]


async def test_list_mirror_none_on_exception(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    table.execute = MagicMock(side_effect=RuntimeError("boom"))
    assert await zdb.list_mirror() is None


# ── count_by_status ──────────────────────────────────────────────────


async def test_count_by_status_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(zdb.db_supabase, "supabase", None)
    assert await zdb.count_by_status("dep1", ["Open", "Closed"]) is None


async def test_count_by_status_returns_total_and_breakdown(mock_supabase_client):
    table = mock_supabase_client.table.return_value

    def _execute():
        status_eqs = [c.args for c in table.eq.call_args_list if c.args[0] == "status"]
        if not status_eqs:
            return _resp(count=10)
        last_status = status_eqs[-1][1]
        return _resp(count={"Open": 6, "Closed": 4}[last_status])

    table.execute = MagicMock(side_effect=_execute)
    total, by_status = await zdb.count_by_status("dep1", ["Open", "Closed"])
    assert total == 10
    assert by_status == {"Open": 6, "Closed": 4}
    eq_calls = [c.args for c in table.eq.call_args_list]
    assert ("department_id", "dep1") in eq_calls


async def test_count_by_status_none_on_exception(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.execute = MagicMock(side_effect=RuntimeError("boom"))
    assert await zdb.count_by_status(None, ["Open"]) is None


# ── fetch_window ─────────────────────────────────────────────────────


async def test_fetch_window_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(zdb.db_supabase, "supabase", None)
    assert await zdb.fetch_window(since_iso="2026-01-01T00:00:00Z", department_id=None, assignee_id=None) is None


async def test_fetch_window_single_short_page_stops(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    rows = [{"raw": {"id": "t1"}}, {"raw": {"id": "t2"}}]
    table.execute = MagicMock(return_value=_resp(data=rows))

    out = await zdb.fetch_window(since_iso="2026-01-01T00:00:00Z", department_id="dep1", assignee_id="a1")
    assert out == [{"id": "t1"}, {"id": "t2"}]
    table.gte.assert_called_with("created_time", "2026-01-01T00:00:00Z")
    eq_calls = [c.args for c in table.eq.call_args_list]
    assert ("department_id", "dep1") in eq_calls
    assert ("assignee_id", "a1") in eq_calls


async def test_fetch_window_paginates_across_full_pages(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    full_page = [{"raw": {"id": f"t{i}"}} for i in range(zdb._PAGE)]
    short_page = [{"raw": {"id": "last"}}]
    table.execute = MagicMock(side_effect=[_resp(data=full_page), _resp(data=short_page)])

    out = await zdb.fetch_window(since_iso="2026-01-01T00:00:00Z", department_id=None, assignee_id=None)
    assert len(out) == zdb._PAGE + 1
    assert out[-1] == {"id": "last"}
    assert table.execute.call_count == 2


async def test_fetch_window_stops_at_max_rows_cap(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    full_page = [{"raw": {"id": f"t{i}"}} for i in range(zdb._PAGE)]
    table.execute = MagicMock(return_value=_resp(data=full_page))

    out = await zdb.fetch_window(
        since_iso="2026-01-01T00:00:00Z", department_id=None, assignee_id=None, max_rows=zdb._PAGE
    )
    assert len(out) == zdb._PAGE
    assert table.execute.call_count == 1


async def test_fetch_window_none_on_exception(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    table.execute = MagicMock(side_effect=RuntimeError("boom"))
    assert await zdb.fetch_window(since_iso="2026-01-01T00:00:00Z", department_id=None, assignee_id=None) is None
