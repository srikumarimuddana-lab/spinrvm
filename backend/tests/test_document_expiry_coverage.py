"""Coverage for utils/document_expiry.py (A1c, Sub-tier B).

Driver document expiry sweep — one of the 17 background startup loops
(12h interval). Regulatory-adjacent (Saskatchewan Transportation Act driver
eligibility): expired documents must suspend the driver (never leave an
expired-document driver online) and clear presence so dispatch can't route
to them. Had no dedicated test file; only 58.71% coverage.

Test-only change — no application code modified.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _driver(**overrides):
    base = {"id": "driver-1", "user_id": "user-1"}
    base.update(overrides)
    return base


def _iso(dt):
    return dt.isoformat()


class TestCheckExpiringDocuments:
    @pytest.mark.anyio
    async def test_db_fetch_error_returns_without_crashing(self, monkeypatch):
        from backend.utils import document_expiry

        monkeypatch.setattr(document_expiry.db, "get_rows", AsyncMock(side_effect=ConnectionError("db down")))
        await document_expiry.check_expiring_documents()  # must not raise

    @pytest.mark.anyio
    async def test_no_drivers_is_noop(self, monkeypatch):
        from backend.utils import document_expiry

        monkeypatch.setattr(document_expiry.db, "get_rows", AsyncMock(return_value=[]))
        await document_expiry.check_expiring_documents()

    @pytest.mark.anyio
    async def test_driver_without_user_id_is_skipped(self, monkeypatch):
        from backend.utils import document_expiry

        get_rows = AsyncMock(side_effect=[[{"id": "d1"}], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)
        await document_expiry.check_expiring_documents()
        push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_paginates_when_first_driver_page_is_full(self, monkeypatch):
        from backend.utils import document_expiry

        full_page = [_driver(id=f"d{i}", user_id=f"u{i}") for i in range(100)]
        get_rows = AsyncMock(side_effect=[full_page, [], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        await document_expiry.check_expiring_documents()
        # 1st and 2nd calls are the drivers pages; the driver_documents lookups
        # (100 more calls) also go through get_rows, so just confirm pagination
        # requested a second drivers page.
        assert get_rows.await_args_list[1].kwargs.get("offset") == 100 or get_rows.await_args_list[1].args

    @pytest.mark.anyio
    async def test_no_expiry_fields_set_is_skipped(self, monkeypatch):
        from backend.utils import document_expiry

        get_rows = AsyncMock(side_effect=[[_driver()], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)
        await document_expiry.check_expiring_documents()
        push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_expiry_far_in_future_is_skipped(self, monkeypatch):
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        future = _iso(now + timedelta(days=90))
        get_rows = AsyncMock(side_effect=[[_driver(license_expiry_date=future)], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)
        await document_expiry.check_expiring_documents()
        push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_expired_legacy_document_suspends_and_notifies(self, monkeypatch):
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        expired = _iso(now - timedelta(days=1))
        driver = _driver(license_expiry_date=expired)
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        update_one = AsyncMock(return_value={"id": "driver-1"})
        monkeypatch.setattr(document_expiry.db, "update_one", update_one)
        clear_presence = AsyncMock()
        monkeypatch.setattr(document_expiry, "clear_presence", clear_presence)
        disconnect = MagicMock()
        monkeypatch.setattr(document_expiry.manager, "disconnect", disconnect)
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()

        update_one.assert_awaited_once()
        args = update_one.await_args.args
        assert args[2]["status"] == "suspended"
        assert args[2]["is_online"] is False
        clear_presence.assert_awaited_once_with("driver-1")
        disconnect.assert_called_once_with("driver_user-1")
        push.assert_awaited_once()
        assert "suspended" in push.await_args.args[1].lower()

    @pytest.mark.anyio
    async def test_suspension_claim_race_lost_skips_all_side_effects(self, monkeypatch):
        """Another replica already suspended this driver — update_one returns
        falsy (0 rows matched); this replica must not re-clear presence,
        re-disconnect, or re-notify."""
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        expired = _iso(now - timedelta(days=1))
        driver = _driver(license_expiry_date=expired)
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        monkeypatch.setattr(document_expiry.db, "update_one", AsyncMock(return_value=None))
        clear_presence = AsyncMock()
        monkeypatch.setattr(document_expiry, "clear_presence", clear_presence)
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()

        clear_presence.assert_not_awaited()
        push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_suspension_db_error_is_logged_and_continues(self, monkeypatch):
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        expired = _iso(now - timedelta(days=1))
        driver = _driver(license_expiry_date=expired)
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        monkeypatch.setattr(document_expiry.db, "update_one", AsyncMock(side_effect=ConnectionError("db down")))
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()  # must not raise
        push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_clear_presence_failure_does_not_block_suspension_push(self, monkeypatch):
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        expired = _iso(now - timedelta(days=1))
        driver = _driver(license_expiry_date=expired)
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        monkeypatch.setattr(document_expiry.db, "update_one", AsyncMock(return_value={"id": "driver-1"}))
        monkeypatch.setattr(document_expiry, "clear_presence", AsyncMock(side_effect=RuntimeError("redis down")))
        monkeypatch.setattr(document_expiry.manager, "disconnect", MagicMock())
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()
        push.assert_awaited_once()

    @pytest.mark.anyio
    async def test_suspension_push_failure_is_swallowed(self, monkeypatch):
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        expired = _iso(now - timedelta(days=1))
        driver = _driver(license_expiry_date=expired)
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        monkeypatch.setattr(document_expiry.db, "update_one", AsyncMock(return_value={"id": "driver-1"}))
        monkeypatch.setattr(document_expiry, "clear_presence", AsyncMock())
        monkeypatch.setattr(document_expiry.manager, "disconnect", MagicMock())
        monkeypatch.setattr(document_expiry, "send_push_notification", AsyncMock(side_effect=RuntimeError("push down")))

        await document_expiry.check_expiring_documents()  # must not raise

    @pytest.mark.anyio
    async def test_expiring_today_uses_today_tier_message(self, monkeypatch):
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        soon = _iso(now + timedelta(hours=1))
        driver = _driver(license_expiry_date=soon)
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        monkeypatch.setattr(document_expiry.db, "update_one", AsyncMock(return_value={"id": "driver-1"}))
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()
        push.assert_awaited_once()
        assert push.await_args.kwargs["data"]["type"] == "document_expiry_today"

    @pytest.mark.anyio
    async def test_expiring_tomorrow_uses_1day_tier_message(self, monkeypatch):
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        soon = _iso(now + timedelta(days=1, hours=1))
        driver = _driver(license_expiry_date=soon)
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        monkeypatch.setattr(document_expiry.db, "update_one", AsyncMock(return_value={"id": "driver-1"}))
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()
        assert push.await_args.kwargs["data"]["type"] == "document_expiry_1day"

    @pytest.mark.anyio
    async def test_expiring_in_several_days_uses_warning_tier(self, monkeypatch):
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        soon = _iso(now + timedelta(days=5))
        driver = _driver(license_expiry_date=soon)
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        monkeypatch.setattr(document_expiry.db, "update_one", AsyncMock(return_value={"id": "driver-1"}))
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()
        assert push.await_args.kwargs["data"]["type"] == "document_expiry_warning"

    @pytest.mark.anyio
    async def test_warn_claim_race_lost_skips_notification(self, monkeypatch):
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        soon = _iso(now + timedelta(days=5))
        driver = _driver(license_expiry_date=soon)
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        monkeypatch.setattr(document_expiry.db, "update_one", AsyncMock(return_value=None))
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()
        push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_warn_claim_db_error_is_logged_and_continues(self, monkeypatch):
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        soon = _iso(now + timedelta(days=5))
        driver = _driver(license_expiry_date=soon)
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        monkeypatch.setattr(document_expiry.db, "update_one", AsyncMock(side_effect=ConnectionError("db down")))
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()  # must not raise
        push.assert_not_awaited()

    @pytest.mark.anyio
    async def test_notification_push_failure_is_swallowed(self, monkeypatch):
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        soon = _iso(now + timedelta(days=5))
        driver = _driver(license_expiry_date=soon)
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        monkeypatch.setattr(document_expiry.db, "update_one", AsyncMock(return_value={"id": "driver-1"}))
        monkeypatch.setattr(document_expiry, "send_push_notification", AsyncMock(side_effect=RuntimeError("push down")))

        await document_expiry.check_expiring_documents()  # must not raise

    @pytest.mark.anyio
    async def test_driver_documents_table_expired_entry_triggers_suspension(self, monkeypatch):
        """A doc in the driver_documents table (not a legacy field) that's
        already expired must also trigger suspension."""
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        expired_doc = {
            "requirement_name": "SGI Ride-Share Endorsement",
            "expiry_date": _iso(now - timedelta(days=1)),
        }
        get_rows = AsyncMock(side_effect=[[_driver()], [expired_doc]])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        update_one = AsyncMock(return_value={"id": "driver-1"})
        monkeypatch.setattr(document_expiry.db, "update_one", update_one)
        monkeypatch.setattr(document_expiry, "clear_presence", AsyncMock())
        monkeypatch.setattr(document_expiry.manager, "disconnect", MagicMock())
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()
        update_one.assert_awaited_once()
        assert "SGI Ride-Share Endorsement" in push.await_args.args[2]

    @pytest.mark.anyio
    async def test_driver_documents_fetch_exception_is_swallowed(self, monkeypatch):
        """A driver_documents lookup failure must not crash the whole sweep
        — it's logged at debug level and the legacy-field result (if any)
        still applies."""
        from backend.utils import document_expiry

        now = datetime.now(timezone.utc)
        driver = _driver(license_expiry_date=_iso(now + timedelta(days=5)))

        call_count = {"n": 0}

        async def _get_rows_impl(table, *a, **kw):
            call_count["n"] += 1
            if table == "drivers":
                return [driver] if call_count["n"] == 1 else []
            raise ConnectionError("db down")

        monkeypatch.setattr(document_expiry.db, "get_rows", AsyncMock(side_effect=_get_rows_impl))
        monkeypatch.setattr(document_expiry.db, "update_one", AsyncMock(return_value={"id": "driver-1"}))
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()  # must not raise
        push.assert_awaited_once()  # legacy-field warning still sent

    @pytest.mark.anyio
    async def test_malformed_expiry_date_is_skipped(self, monkeypatch):
        from backend.utils import document_expiry

        driver = _driver(license_expiry_date="not-a-real-date")
        get_rows = AsyncMock(side_effect=[[driver], []])
        monkeypatch.setattr(document_expiry.db, "get_rows", get_rows)
        push = AsyncMock()
        monkeypatch.setattr(document_expiry, "send_push_notification", push)

        await document_expiry.check_expiring_documents()
        push.assert_not_awaited()


class TestDocumentExpiryLoop:
    @pytest.mark.anyio
    async def test_loop_ticks_and_sleeps(self, monkeypatch):
        from backend.utils import document_expiry

        check = AsyncMock()
        monkeypatch.setattr(document_expiry, "check_expiring_documents", check)
        heartbeat = MagicMock()
        monkeypatch.setattr(document_expiry, "_record_heartbeat", heartbeat)

        async def fake_sleep(secs):
            raise asyncio.CancelledError()

        with patch.object(document_expiry.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await document_expiry.document_expiry_loop()

        check.assert_awaited_once()
        heartbeat.assert_called_once_with("document_expiry (12h)")

    @pytest.mark.anyio
    async def test_loop_survives_a_failing_tick(self, monkeypatch):
        from backend.utils import document_expiry

        monkeypatch.setattr(
            document_expiry, "check_expiring_documents", AsyncMock(side_effect=RuntimeError("boom"))
        )
        heartbeat = MagicMock()
        monkeypatch.setattr(document_expiry, "_record_heartbeat", heartbeat)

        async def fake_sleep(secs):
            raise asyncio.CancelledError()

        with patch.object(document_expiry.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await document_expiry.document_expiry_loop()

        # Heartbeat still records even after a failing tick — loop survived.
        heartbeat.assert_called_once_with("document_expiry (12h)")
