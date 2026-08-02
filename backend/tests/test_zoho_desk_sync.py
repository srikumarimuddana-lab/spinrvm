"""Coverage tests for backend/utils/zoho_desk_sync.py — the Zoho Desk ->
Postgres mirror sync (one of the ~17 startup background loops mounted in
core/lifespan.py).

TEST-ONLY change: no application code touched.

Replay-safety contract exercised here (per spinr-background-loop skill):
  - the loop is gated behind a Redis leader lock (redis_set_nx) — both the
    "lock acquired" (this replica syncs) and "lock not acquired" (another
    replica is already leader, this replica no-ops) paths are covered
  - run_sync() itself is upsert-keyed on zoho_id (replay-safe on its own),
    covered via _upsert_batch call-count/row assertions
  - a ZohoDeskError (integration not configured) is caught and logged as a
    warning, not allowed to crash the loop
  - any other exception is caught and logged as an error, not allowed to
    crash the loop (the tick-survives-failure contract, same as
    reconciliation_loop's test convention)
"""

from __future__ import annotations

import asyncio
from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import utils.zoho_desk_sync as zds
from services.zoho_desk_service import ZohoDeskError

pytestmark = pytest.mark.anyio


def _ticket(id_, modified_time, status="Open", status_type="Open", **overrides):
    t = {
        "id": id_,
        "ticketNumber": f"TK-{id_}",
        "subject": "help",
        "status": status,
        "statusType": status_type,
        "priority": "Medium",
        "channel": "Email",
        "category": "General",
        "classification": None,
        "departmentId": "dep1",
        "assignee": {"id": "agent1", "firstName": "A", "lastName": "Gent"},
        "contact": {"email": "rider@x.ca", "firstName": "R", "lastName": "Ider"},
        "tags": [{"name": "vip"}, None],
        "createdTime": modified_time,
        "modifiedTime": modified_time,
        "closedTime": None,
        "dueDate": None,
        "webUrl": "https://desk.example/t/" + id_,
    }
    t.update(overrides)
    return t


def _page(rows):
    return {"data": rows}


# ── _parse ───────────────────────────────────────────────────────────


def test_parse_none_and_empty_return_none():
    assert zds._parse(None) is None
    assert zds._parse("") is None


def test_parse_z_suffix_gets_utc_tzinfo():
    dt = zds._parse("2026-08-01T12:00:00.000Z")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_naive_iso_gets_utc_attached():
    dt = zds._parse("2026-08-01T12:00:00")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_parse_invalid_string_returns_none():
    assert zds._parse("not-a-date") is None


# ── _name ────────────────────────────────────────────────────────────


def test_name_joins_first_and_last():
    assert zds._name({"firstName": "Jane", "lastName": "Doe"}) == "Jane Doe"


def test_name_empty_when_both_missing():
    assert zds._name({}) == ""


# ── _map_ticket ──────────────────────────────────────────────────────


def test_map_ticket_full_shape():
    t = _ticket("1", "2026-08-01T12:00:00.000Z")
    row = zds._map_ticket(t)
    assert row["zoho_id"] == "1"
    assert row["ticket_number"] == "TK-1"
    assert row["department_id"] == "dep1"
    assert row["assignee_id"] == "agent1"
    assert row["assignee_name"] == "A Gent"
    assert row["contact_email"] == "rider@x.ca"
    assert row["contact_name"] == "R Ider"
    assert row["tags"] == ["vip"]  # falsy tag entries dropped
    assert row["raw"] is t
    assert "synced_at" in row


def test_map_ticket_falls_back_to_top_level_assignee_and_email():
    t = _ticket("2", "2026-08-01T12:00:00.000Z", assignee=None, contact=None)
    t["assigneeId"] = "top-level-agent"
    t["email"] = "top@x.ca"
    row = zds._map_ticket(t)
    assert row["assignee_id"] == "top-level-agent"
    assert row["assignee_name"] is None
    assert row["contact_email"] == "top@x.ca"
    assert row["contact_name"] is None


# ── _upsert_batch ────────────────────────────────────────────────────


async def test_upsert_batch_noop_on_empty_rows(mock_supabase_client):
    with patch.object(zds.db_supabase, "supabase", mock_supabase_client):
        await zds._upsert_batch([])
    mock_supabase_client.table.assert_not_called()


async def test_upsert_batch_noop_when_supabase_unconfigured():
    with patch.object(zds.db_supabase, "supabase", None):
        # Must not raise even though rows is non-empty.
        await zds._upsert_batch([{"zoho_id": "1"}])


async def test_upsert_batch_calls_upsert_with_serialized_rows(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.upsert.return_value = table
    table.execute = MagicMock(return_value=MagicMock(data=[]))
    with patch.object(zds.db_supabase, "supabase", mock_supabase_client):
        await zds._upsert_batch([{"zoho_id": "1", "subject": "hi"}])
    mock_supabase_client.table.assert_called_with(zds._TABLE)
    table.upsert.assert_called_once()
    upserted = table.upsert.call_args.args[0]
    assert upserted[0]["zoho_id"] == "1"


# ── run_sync ─────────────────────────────────────────────────────────


def _patch_run_sync(monkeypatch, *, cfg, list_tickets, close_linked=None, update_one=None):
    find_one = AsyncMock(return_value=cfg)
    update_one = update_one or AsyncMock(return_value=None)
    monkeypatch.setattr(zds.db_supabase, "find_one", find_one)
    monkeypatch.setattr(zds.db_supabase, "update_one", update_one)
    monkeypatch.setattr(zds.zoho, "list_tickets", list_tickets)
    monkeypatch.setattr(zds, "close_linked_records", close_linked or AsyncMock(return_value=None))
    monkeypatch.setattr(zds, "_upsert_batch", AsyncMock(return_value=None))
    return find_one, update_one


async def test_run_sync_skipped_when_config_missing(monkeypatch):
    _patch_run_sync(monkeypatch, cfg=None, list_tickets=AsyncMock())
    out = await zds.run_sync()
    assert out == {"skipped": "disabled"}
    zds.zoho.list_tickets.assert_not_awaited()


async def test_run_sync_skipped_when_disabled(monkeypatch):
    _patch_run_sync(monkeypatch, cfg={"id": "default", "enabled": False}, list_tickets=AsyncMock())
    out = await zds.run_sync()
    assert out == {"skipped": "disabled"}


async def test_run_sync_seed_backfill_pages_to_end_and_marks_backfilled(monkeypatch):
    page0 = [_ticket(str(i), f"2026-08-01T00:{i:02d}:00.000Z") for i in range(100)]
    page1 = [_ticket("last", "2026-08-01T00:03:00.000Z")]  # short page -> reached_end
    list_tickets = AsyncMock(side_effect=[_page(page0), _page(page1)])
    _, update_one = _patch_run_sync(
        monkeypatch,
        cfg={"id": "default", "enabled": True, "mirror_backfilled": False, "sync_cursor": None},
        list_tickets=list_tickets,
    )

    out = await zds.run_sync()

    assert out["upserted"] == 101
    assert out["backfilled"] is True
    assert zds._upsert_batch.await_count == 2
    update_one.assert_awaited_once()
    _, filters, updates = update_one.call_args.args
    assert filters == {"id": "default"}
    assert updates["last_sync_count"] == 101
    assert updates["mirror_backfilled"] is True
    assert "sync_cursor" in updates
    # Seeding never triggers reverse-sync, even though it never looks for
    # closed tickets during a first-run backfill in this fixture.
    zds.close_linked_records.assert_not_awaited()


async def test_run_sync_incremental_stops_at_stored_cursor(monkeypatch):
    cursor_iso = "2026-08-01T00:05:00+00:00"
    # Newest-first page: two tickets newer than the cursor, one at/older than it
    # (which must stop the batch and NOT be included).
    rows = [
        _ticket("new2", "2026-08-01T00:07:00.000Z"),
        _ticket("new1", "2026-08-01T00:06:00.000Z"),
        _ticket("old", "2026-08-01T00:05:00.000Z"),
    ]
    list_tickets = AsyncMock(return_value=_page(rows))
    _, update_one = _patch_run_sync(
        monkeypatch,
        cfg={"id": "default", "enabled": True, "mirror_backfilled": True, "sync_cursor": cursor_iso},
        list_tickets=list_tickets,
    )

    out = await zds.run_sync()

    assert out["upserted"] == 2  # "old" excluded — at cursor, stop triggered
    assert out["backfilled"] is True
    batch = zds._upsert_batch.await_args.args[0]
    assert {r["zoho_id"] for r in batch} == {"new1", "new2"}
    updates = update_one.call_args.args[2]
    assert updates["sync_cursor"] == "2026-08-01T00:07:00+00:00"


async def test_run_sync_empty_first_page_reaches_end_with_zero_upserted(monkeypatch):
    list_tickets = AsyncMock(return_value=_page([]))
    _, update_one = _patch_run_sync(
        monkeypatch,
        cfg={"id": "default", "enabled": True, "mirror_backfilled": True, "sync_cursor": None},
        list_tickets=list_tickets,
    )
    out = await zds.run_sync()
    assert out == {"upserted": 0, "backfilled": True}
    updates = update_one.call_args.args[2]
    assert updates["last_sync_count"] == 0
    assert "sync_cursor" not in updates  # newest stays falsy -> never set


async def test_run_sync_detects_closed_by_status_type_and_by_status_text(monkeypatch):
    rows = [
        _ticket("c1", "2026-08-01T00:01:00.000Z", status="Resolved", status_type="Closed"),
        _ticket("c2", "2026-08-01T00:02:00.000Z", status="Marked Closed by agent", status_type="Open"),
        _ticket("open1", "2026-08-01T00:03:00.000Z", status="Open", status_type="Open"),
    ]
    close_linked = AsyncMock(return_value=None)
    list_tickets = AsyncMock(side_effect=[_page(rows), _page([])])
    _patch_run_sync(
        monkeypatch,
        cfg={"id": "default", "enabled": True, "mirror_backfilled": True, "sync_cursor": None},
        list_tickets=list_tickets,
        close_linked=close_linked,
    )
    await zds.run_sync()
    close_linked.assert_awaited_once()
    closed_ids = close_linked.call_args.args[0]
    assert set(closed_ids) == {"c1", "c2"}


async def test_run_sync_seeding_never_calls_reverse_sync_even_with_closed_tickets(monkeypatch):
    rows = [_ticket("c1", "2026-08-01T00:01:00.000Z", status="Closed", status_type="Closed")]
    close_linked = AsyncMock(return_value=None)
    list_tickets = AsyncMock(return_value=_page(rows))
    _patch_run_sync(
        monkeypatch,
        cfg={"id": "default", "enabled": True, "mirror_backfilled": False, "sync_cursor": None},
        list_tickets=list_tickets,
        close_linked=close_linked,
    )
    await zds.run_sync()
    close_linked.assert_not_awaited()


async def test_run_sync_respects_max_pages_safety_cap(monkeypatch):
    """Neither a short page nor the stored cursor is ever hit -- the safety
    cap (SEED_MAX_PAGES / INCREMENTAL_MAX_PAGES) must still stop the loop."""
    monkeypatch.setattr(zds, "INCREMENTAL_MAX_PAGES", 2)
    full_page = [_ticket(f"p{i}", "2026-08-01T00:00:00.000Z") for i in range(100)]
    list_tickets = AsyncMock(return_value=_page(full_page))
    # mirror_backfilled stays True in cfg (so this exercises the incremental
    # path, where INCREMENTAL_MAX_PAGES applies) -- but that means the
    # returned "backfilled" flag is trivially True from cfg regardless of
    # whether reached_end fired this cycle. The real proof the cap (not a
    # short page / stop signal) is what stopped the loop is that
    # update_one's payload has no "mirror_backfilled" key at all -- that key
    # is only ever set `if reached_end`.
    _, update_one = _patch_run_sync(
        monkeypatch,
        cfg={"id": "default", "enabled": True, "mirror_backfilled": True, "sync_cursor": None},
        list_tickets=list_tickets,
    )
    out = await zds.run_sync()
    assert list_tickets.await_count == 2
    assert out["upserted"] == 200
    assert out["backfilled"] is True  # from cfg, not from reaching the end
    updates = update_one.call_args.args[2]
    assert "mirror_backfilled" not in updates


# ── zoho_desk_sync_loop ──────────────────────────────────────────────


async def test_loop_skips_when_lock_not_acquired(monkeypatch):
    """Redis leader-lock contract: another replica already holds the lock ->
    this replica must not call run_sync this tick."""
    find_one = AsyncMock(return_value={"id": "default", "enabled": True, "auto_sync_enabled": True})
    run_sync_mock = AsyncMock(return_value={"upserted": 0})
    monkeypatch.setattr(zds.db_supabase, "find_one", find_one)
    monkeypatch.setattr(zds, "run_sync", run_sync_mock)
    monkeypatch.setattr(zds, "redis_set_nx", AsyncMock(return_value=False))

    sleep_calls = []

    async def fake_sleep(secs):
        sleep_calls.append(secs)
        raise asyncio.CancelledError()

    with patch.object(zds.asyncio, "sleep", fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await zds.zoho_desk_sync_loop()

    run_sync_mock.assert_not_awaited()
    assert sleep_calls == [zds.SYNC_INTERVAL_SECONDS]


async def test_loop_runs_sync_when_lock_acquired(monkeypatch):
    find_one = AsyncMock(return_value={"id": "default", "enabled": True, "auto_sync_enabled": True})
    run_sync_mock = AsyncMock(return_value={"upserted": 3})
    monkeypatch.setattr(zds.db_supabase, "find_one", find_one)
    monkeypatch.setattr(zds, "run_sync", run_sync_mock)
    monkeypatch.setattr(zds, "redis_set_nx", AsyncMock(return_value=True))

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with patch.object(zds.asyncio, "sleep", fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await zds.zoho_desk_sync_loop()

    run_sync_mock.assert_awaited_once()


async def test_loop_skips_when_auto_sync_disabled(monkeypatch):
    find_one = AsyncMock(return_value={"id": "default", "enabled": True, "auto_sync_enabled": False})
    run_sync_mock = AsyncMock(return_value={"upserted": 0})
    monkeypatch.setattr(zds.db_supabase, "find_one", find_one)
    monkeypatch.setattr(zds, "run_sync", run_sync_mock)
    lock_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(zds, "redis_set_nx", lock_mock)

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with patch.object(zds.asyncio, "sleep", fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await zds.zoho_desk_sync_loop()

    lock_mock.assert_not_awaited()
    run_sync_mock.assert_not_awaited()


async def test_loop_catches_zoho_desk_error_and_continues(monkeypatch):
    find_one = AsyncMock(side_effect=ZohoDeskError("not configured", status=503))
    monkeypatch.setattr(zds.db_supabase, "find_one", find_one)

    sleep_calls = []

    async def fake_sleep(secs):
        sleep_calls.append(secs)
        raise asyncio.CancelledError()

    with patch.object(zds.asyncio, "sleep", fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await zds.zoho_desk_sync_loop()

    assert sleep_calls == [zds.SYNC_INTERVAL_SECONDS]


async def test_loop_catches_generic_exception_and_continues(monkeypatch):
    find_one = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(zds.db_supabase, "find_one", find_one)

    sleep_calls = []

    async def fake_sleep(secs):
        sleep_calls.append(secs)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    with patch.object(zds.asyncio, "sleep", fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await zds.zoho_desk_sync_loop()

    # Loop survived a raised exception and looped again -- proves the
    # exception was caught, not propagated (matches reconciliation_loop's
    # "tick failure doesn't crash the loop" convention).
    assert sleep_calls == [zds.SYNC_INTERVAL_SECONDS, zds.SYNC_INTERVAL_SECONDS]
