"""Coverage for utils/zoho_desk_sync.py (A1c, Sub-tier B).

Zoho Desk → Postgres mirror sync (migration 123). One of the 17 background
loops (see `core/lifespan.py`), replay-safe via upsert-on-zoho_id, gated
behind a Redis leader-election lock so N replicas don't each spend Zoho API
credits every interval. Had no dedicated test file; only 22.33% coverage.

Background-loop testing pattern: patch `asyncio.sleep` with a fake that
raises `asyncio.CancelledError` after N iterations, matching the existing
convention in test_reconciliation.py's `reconciliation_loop` tests.

Test-only change — no application code modified.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ── _parse ───────────────────────────────────────────────────────────────


class TestParse:
    def test_none_returns_none(self):
        from backend.utils.zoho_desk_sync import _parse

        assert _parse(None) is None

    def test_empty_string_returns_none(self):
        from backend.utils.zoho_desk_sync import _parse

        assert _parse("") is None

    def test_invalid_string_returns_none(self):
        from backend.utils.zoho_desk_sync import _parse

        assert _parse("not-a-date") is None

    def test_z_suffix_parsed_as_utc(self):
        from backend.utils.zoho_desk_sync import _parse

        result = _parse("2026-07-01T08:00:00Z")
        assert result is not None
        assert result.tzinfo is not None

    def test_naive_datetime_gets_utc_attached(self):
        from backend.utils.zoho_desk_sync import _parse

        result = _parse("2026-07-01T08:00:00")
        assert result.tzinfo is not None


# ── _name ────────────────────────────────────────────────────────────────


class TestName:
    def test_both_names_present(self):
        from backend.utils.zoho_desk_sync import _name

        assert _name({"firstName": "Jordan", "lastName": "Smith"}) == "Jordan Smith"

    def test_only_first_name(self):
        from backend.utils.zoho_desk_sync import _name

        assert _name({"firstName": "Jordan"}) == "Jordan"

    def test_neither_name_returns_empty_string(self):
        from backend.utils.zoho_desk_sync import _name

        assert _name({}) == ""


# ── _map_ticket ──────────────────────────────────────────────────────────


class TestMapTicket:
    def test_full_ticket_maps_all_fields(self):
        from backend.utils.zoho_desk_sync import _map_ticket

        ticket = {
            "id": "1001",
            "ticketNumber": "T-1001",
            "subject": "App crashing",
            "status": "Open",
            "statusType": "Open",
            "priority": "High",
            "channel": "Email",
            "category": "Bug",
            "classification": "App",
            "departmentId": "dept-1",
            "assignee": {"id": "agent-1", "firstName": "Ada", "lastName": "Lovelace"},
            "contact": {"email": "rider@example.com", "firstName": "Rider", "lastName": "One"},
            "tags": [{"name": "urgent"}, "vip", None],
            "createdTime": "2026-07-01T08:00:00Z",
            "modifiedTime": "2026-07-02T08:00:00Z",
            "closedTime": None,
            "dueDate": "2026-07-05T08:00:00Z",
            "webUrl": "https://desk.zoho.com/ticket/1001",
        }
        result = _map_ticket(ticket)
        assert result["zoho_id"] == "1001"
        assert result["assignee_id"] == "agent-1"
        assert result["assignee_name"] == "Ada Lovelace"
        assert result["contact_email"] == "rider@example.com"
        assert result["contact_name"] == "Rider One"
        assert result["tags"] == ["urgent", "vip"]
        assert "raw" in result and result["raw"] == ticket
        assert "synced_at" in result

    def test_missing_contact_and_assignee_default_gracefully(self):
        from backend.utils.zoho_desk_sync import _map_ticket

        result = _map_ticket({"id": "2", "assigneeId": "fallback-agent", "email": "fallback@example.com"})
        assert result["assignee_id"] == "fallback-agent"
        assert result["assignee_name"] is None
        assert result["contact_email"] == "fallback@example.com"
        assert result["contact_name"] is None

    def test_missing_id_and_ticket_number_default_to_empty_string(self):
        from backend.utils.zoho_desk_sync import _map_ticket

        result = _map_ticket({})
        assert result["zoho_id"] == ""
        assert result["ticket_number"] == ""
        assert result["tags"] == []


# ── _upsert_batch ────────────────────────────────────────────────────────


class TestUpsertBatch:
    @pytest.mark.anyio
    async def test_empty_rows_is_noop(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        run_sync = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "run_sync", run_sync)
        await zoho_desk_sync._upsert_batch([])
        run_sync.assert_not_awaited()

    @pytest.mark.anyio
    async def test_unconfigured_supabase_is_noop(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        run_sync = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "supabase", None)
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "run_sync", run_sync)
        await zoho_desk_sync._upsert_batch([{"zoho_id": "1"}])
        run_sync.assert_not_awaited()

    @pytest.mark.anyio
    async def test_upserts_serialized_rows(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        q = MagicMock()
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "supabase", q)
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "_serialize_for_api", lambda r: r)
        run_sync = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "run_sync", run_sync)

        await zoho_desk_sync._upsert_batch([{"zoho_id": "1"}])
        run_sync.assert_awaited_once()


# ── run_sync ─────────────────────────────────────────────────────────────


def _page(rows, total=None):
    return {"data": rows}


class TestRunSync:
    @pytest.mark.anyio
    async def test_disabled_config_skips(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(zoho_desk_sync.db_supabase, "find_one", AsyncMock(return_value=None))
        result = await zoho_desk_sync.run_sync()
        assert result == {"skipped": "disabled"}

    @pytest.mark.anyio
    async def test_explicitly_disabled_config_skips(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(zoho_desk_sync.db_supabase, "find_one", AsyncMock(return_value={"enabled": False}))
        result = await zoho_desk_sync.run_sync()
        assert result == {"skipped": "disabled"}

    @pytest.mark.anyio
    async def test_seeding_pages_until_short_page_and_marks_backfilled(self, monkeypatch):
        """Not yet backfilled: pages full (100 rows) then a short page (< 100)
        ends the run and flips mirror_backfilled True."""
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(
            zoho_desk_sync.db_supabase,
            "find_one",
            AsyncMock(return_value={"enabled": True, "mirror_backfilled": False}),
        )
        monkeypatch.setattr(zoho_desk_sync, "_upsert_batch", AsyncMock())
        update_one = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "update_one", update_one)

        full_page = [{"id": str(i), "modifiedTime": "2026-07-01T08:00:00Z"} for i in range(100)]
        short_page = [{"id": "last", "modifiedTime": "2026-07-02T08:00:00Z"}]
        monkeypatch.setattr(
            zoho_desk_sync.zoho, "list_tickets", AsyncMock(side_effect=[_page(full_page), _page(short_page)])
        )

        result = await zoho_desk_sync.run_sync()
        assert result["upserted"] == 101
        assert result["backfilled"] is True
        update_one.assert_awaited_once()
        updates = update_one.await_args.args[2]
        assert updates["mirror_backfilled"] is True

    @pytest.mark.anyio
    async def test_empty_first_page_reaches_end_immediately(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(
            zoho_desk_sync.db_supabase,
            "find_one",
            AsyncMock(return_value={"enabled": True, "mirror_backfilled": False}),
        )
        monkeypatch.setattr(zoho_desk_sync, "_upsert_batch", AsyncMock())
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "update_one", AsyncMock())
        monkeypatch.setattr(zoho_desk_sync.zoho, "list_tickets", AsyncMock(return_value=_page([])))

        result = await zoho_desk_sync.run_sync()
        assert result == {"upserted": 0, "backfilled": True}

    @pytest.mark.anyio
    async def test_incremental_run_stops_at_cursor(self, monkeypatch):
        """Backfilled mirror: a ticket whose modifiedTime <= the stored
        cursor stops the batch (and the page loop) without including it."""
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(
            zoho_desk_sync.db_supabase,
            "find_one",
            AsyncMock(
                return_value={
                    "enabled": True,
                    "mirror_backfilled": True,
                    "sync_cursor": "2026-07-01T08:00:00+00:00",
                }
            ),
        )
        upsert_batch = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync, "_upsert_batch", upsert_batch)
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "update_one", AsyncMock())
        monkeypatch.setattr(zoho_desk_sync, "close_linked_records", AsyncMock())

        rows = [
            {"id": "new-1", "modifiedTime": "2026-07-02T08:00:00Z", "status": "Open"},
            {"id": "old-1", "modifiedTime": "2026-07-01T08:00:00Z", "status": "Open"},  # == cursor, stops here
        ]
        monkeypatch.setattr(zoho_desk_sync.zoho, "list_tickets", AsyncMock(return_value=_page(rows)))

        result = await zoho_desk_sync.run_sync()
        assert result["upserted"] == 1
        upsert_batch.assert_awaited_once()
        (batch_arg,) = upsert_batch.await_args.args
        assert len(batch_arg) == 1
        assert batch_arg[0]["zoho_id"] == "new-1"

    @pytest.mark.anyio
    async def test_closed_tickets_trigger_reverse_sync_when_not_seeding(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(
            zoho_desk_sync.db_supabase,
            "find_one",
            AsyncMock(return_value={"enabled": True, "mirror_backfilled": True}),
        )
        monkeypatch.setattr(zoho_desk_sync, "_upsert_batch", AsyncMock())
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "update_one", AsyncMock())
        close_linked = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync, "close_linked_records", close_linked)

        rows = [{"id": "closed-1", "modifiedTime": "2026-07-02T08:00:00Z", "statusType": "Closed", "status": "Closed"}]
        monkeypatch.setattr(zoho_desk_sync.zoho, "list_tickets", AsyncMock(return_value=_page(rows)))

        await zoho_desk_sync.run_sync()
        close_linked.assert_awaited_once_with(["closed-1"])

    @pytest.mark.anyio
    async def test_closed_status_text_match_without_status_type(self, monkeypatch):
        """A ticket lacking statusType but whose status string contains
        'closed' (case-insensitive) still counts as closed."""
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(
            zoho_desk_sync.db_supabase,
            "find_one",
            AsyncMock(return_value={"enabled": True, "mirror_backfilled": True}),
        )
        monkeypatch.setattr(zoho_desk_sync, "_upsert_batch", AsyncMock())
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "update_one", AsyncMock())
        close_linked = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync, "close_linked_records", close_linked)

        rows = [{"id": "closed-2", "modifiedTime": "2026-07-02T08:00:00Z", "status": "Auto-Closed"}]
        monkeypatch.setattr(zoho_desk_sync.zoho, "list_tickets", AsyncMock(return_value=_page(rows)))

        await zoho_desk_sync.run_sync()
        close_linked.assert_awaited_once_with(["closed-2"])

    @pytest.mark.anyio
    async def test_seeding_skips_reverse_sync_even_with_closed_tickets(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(
            zoho_desk_sync.db_supabase,
            "find_one",
            AsyncMock(return_value={"enabled": True, "mirror_backfilled": False}),
        )
        monkeypatch.setattr(zoho_desk_sync, "_upsert_batch", AsyncMock())
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "update_one", AsyncMock())
        close_linked = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync, "close_linked_records", close_linked)

        rows = [{"id": "closed-3", "modifiedTime": "2026-07-02T08:00:00Z", "statusType": "Closed"}]
        monkeypatch.setattr(zoho_desk_sync.zoho, "list_tickets", AsyncMock(return_value=_page(rows)))

        await zoho_desk_sync.run_sync()
        close_linked.assert_not_awaited()

    @pytest.mark.anyio
    async def test_max_pages_safety_cap_stops_pagination(self, monkeypatch):
        """A feed that never runs dry (always returns a full 100-row page)
        must stop at max_pages, not loop forever."""
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(
            zoho_desk_sync.db_supabase,
            "find_one",
            AsyncMock(return_value={"enabled": True, "mirror_backfilled": True}),
        )
        monkeypatch.setattr(zoho_desk_sync, "_upsert_batch", AsyncMock())
        monkeypatch.setattr(zoho_desk_sync.db_supabase, "update_one", AsyncMock())
        monkeypatch.setattr(zoho_desk_sync, "INCREMENTAL_MAX_PAGES", 2)

        full_page = [{"id": str(i), "modifiedTime": "2026-07-01T08:00:00Z"} for i in range(100)]
        list_tickets = AsyncMock(return_value=_page(full_page))
        monkeypatch.setattr(zoho_desk_sync.zoho, "list_tickets", list_tickets)

        await zoho_desk_sync.run_sync()
        assert list_tickets.await_count == 2


# ── zoho_desk_sync_loop ──────────────────────────────────────────────────


class TestZohoDeskSyncLoop:
    @pytest.mark.anyio
    async def test_disabled_config_skips_run_sync_but_keeps_looping(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(zoho_desk_sync.db_supabase, "find_one", AsyncMock(return_value=None))
        run_sync = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync, "run_sync", run_sync)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        with patch.object(zoho_desk_sync.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await zoho_desk_sync.zoho_desk_sync_loop()

        run_sync.assert_not_awaited()
        assert sleep_calls == [zoho_desk_sync.SYNC_INTERVAL_SECONDS]

    @pytest.mark.anyio
    async def test_auto_sync_disabled_skips_run_sync(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(
            zoho_desk_sync.db_supabase,
            "find_one",
            AsyncMock(return_value={"enabled": True, "auto_sync_enabled": False}),
        )
        run_sync = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync, "run_sync", run_sync)

        async def fake_sleep(secs):
            raise asyncio.CancelledError()

        with patch.object(zoho_desk_sync.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await zoho_desk_sync.zoho_desk_sync_loop()

        run_sync.assert_not_awaited()

    @pytest.mark.anyio
    async def test_lock_acquired_runs_sync(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(
            zoho_desk_sync.db_supabase,
            "find_one",
            AsyncMock(return_value={"enabled": True, "auto_sync_enabled": True}),
        )
        monkeypatch.setattr(zoho_desk_sync, "redis_set_nx", AsyncMock(return_value=True))
        run_sync = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync, "run_sync", run_sync)

        async def fake_sleep(secs):
            raise asyncio.CancelledError()

        with patch.object(zoho_desk_sync.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await zoho_desk_sync.zoho_desk_sync_loop()

        run_sync.assert_awaited_once()

    @pytest.mark.anyio
    async def test_lock_not_acquired_skips_sync(self, monkeypatch):
        """Another replica holds the leader lock — this replica must not
        also call Zoho, so API-credit usage doesn't scale with replica count."""
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(
            zoho_desk_sync.db_supabase,
            "find_one",
            AsyncMock(return_value={"enabled": True, "auto_sync_enabled": True}),
        )
        monkeypatch.setattr(zoho_desk_sync, "redis_set_nx", AsyncMock(return_value=False))
        run_sync = AsyncMock()
        monkeypatch.setattr(zoho_desk_sync, "run_sync", run_sync)

        async def fake_sleep(secs):
            raise asyncio.CancelledError()

        with patch.object(zoho_desk_sync.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await zoho_desk_sync.zoho_desk_sync_loop()

        run_sync.assert_not_awaited()

    @pytest.mark.anyio
    async def test_zoho_desk_error_is_logged_and_loop_continues(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(
            zoho_desk_sync.db_supabase,
            "find_one",
            AsyncMock(side_effect=zoho_desk_sync.ZohoDeskError("not configured", status=503)),
        )

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 1:
                raise asyncio.CancelledError()

        with patch.object(zoho_desk_sync.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await zoho_desk_sync.zoho_desk_sync_loop()

        assert sleep_calls  # loop survived the ZohoDeskError and reached sleep

    @pytest.mark.anyio
    async def test_generic_exception_is_logged_and_loop_continues(self, monkeypatch):
        from backend.utils import zoho_desk_sync

        monkeypatch.setattr(zoho_desk_sync.db_supabase, "find_one", AsyncMock(side_effect=RuntimeError("unexpected")))

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        with patch.object(zoho_desk_sync.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await zoho_desk_sync.zoho_desk_sync_loop()

        assert sleep_calls  # loop survived the generic exception and reached sleep
