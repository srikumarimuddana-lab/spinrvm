"""Coverage tests for backend/routes/admin/maintenance.py.

Covers GPS-history cleanup, the driver_daily_stats rollup job (target-date
validation, upsert branches, RPC failure handling), audit-log listing
(filters + search regex), and the PII-reveal audit endpoint.

TEST-ONLY change; no application code modified.

NOTE: the rollup body moved to utils/driver_daily_rollup (shared with the
scheduled loop), so this file no longer exercises an inline RPC path — the
former ``_ensure_maint_rpc_bindings`` workaround for maintenance.py's
dual-import fallback went away with the ``_run_sync`` / ``_supabase_client``
imports it patched around.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.routes.admin import maintenance as maint

ADMIN = {"id": "admin-1", "role": "super_admin"}


# ---------------------------------------------------------------------------
# log_audit helper
# ---------------------------------------------------------------------------


class TestLogAudit:
    @pytest.mark.asyncio
    async def test_log_audit_writes_row(self):
        rows = []

        async def _capture(table, row):
            rows.append((table, row))

        with patch.object(maint.db_supabase, "insert_one", AsyncMock(side_effect=_capture)):
            await maint.log_audit("thing_created", "thing", "id-1", "admin-1", details="x")
        assert rows[0][0] == "audit_logs"
        assert rows[0][1]["action"] == "thing_created"

    @pytest.mark.asyncio
    async def test_log_audit_swallows_db_failure(self):
        """Non-raising by design — caller must not be blocked by an audit hiccup."""
        with patch.object(maint.db_supabase, "insert_one", AsyncMock(side_effect=RuntimeError("db down"))):
            await maint.log_audit("thing_created", "thing", "id-1", "admin-1")  # must not raise


# ---------------------------------------------------------------------------
# GPS location-history cleanup
# ---------------------------------------------------------------------------


class TestCleanupLocationHistory:
    @pytest.mark.asyncio
    async def test_cleanup_deletes_historical_and_idle(self):
        calls = []

        async def _capture(table, filters):
            calls.append(filters)

        audit_mock = AsyncMock(return_value="audit-1")
        with (
            patch.object(maint.db_supabase, "delete_many", AsyncMock(side_effect=_capture)),
            patch.object(maint, "log_admin_action", audit_mock),
        ):
            result = await maint.admin_cleanup_location_history(days=30, admin=ADMIN)

        assert len(calls) == 2
        assert "historical_cutoff" in result
        assert "idle_cutoff" in result
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "location_history_cleanup"

    @pytest.mark.asyncio
    async def test_cleanup_continues_when_historical_delete_fails(self):
        """Historical-delete failure must not block the idle-delete branch."""
        call_count = {"n": 0}

        async def _side_effect(table, filters):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("db error")

        with (
            patch.object(maint.db_supabase, "delete_many", AsyncMock(side_effect=_side_effect)),
            patch.object(maint, "log_admin_action", AsyncMock(return_value="audit-2")),
        ):
            result = await maint.admin_cleanup_location_history(days=30, admin=ADMIN)

        assert call_count["n"] == 2
        assert result["deleted_historical"] == -1

    @pytest.mark.asyncio
    async def test_cleanup_continues_when_idle_delete_fails(self):
        call_count = {"n": 0}

        async def _side_effect(table, filters):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("db error")

        with (
            patch.object(maint.db_supabase, "delete_many", AsyncMock(side_effect=_side_effect)),
            patch.object(maint, "log_admin_action", AsyncMock(return_value="audit-3")),
        ):
            result = await maint.admin_cleanup_location_history(days=30, admin=ADMIN)

        assert call_count["n"] == 2
        assert result["deleted_idle"] == -1


# ---------------------------------------------------------------------------
# driver-daily rollup
# ---------------------------------------------------------------------------


class TestRollupDriverDaily:
    """Endpoint-layer contract only.

    The rollup body moved to utils/driver_daily_rollup.rollup_driver_day so
    the manual admin trigger and the scheduled loop share one Regina-day
    definition. Discovery, per-driver upserts, decline counting and RPC
    failure handling are pinned against the real implementation in
    tests/test_driver_daily_rollup.py; what remains here is what the
    ENDPOINT owns: the completed-day guard, the delegated date, the
    passthrough result, and main's admin audit row.
    """

    @staticmethod
    def _regina_today():
        """The guard is Regina-date based: between 00:00 and 06:00 UTC the
        UTC calendar is already a day ahead of Saskatchewan, so asserting on
        UTC dates would make this suite flaky by wall clock."""
        try:
            from backend.utils.driver_activity import REGINA_TZ
        except ImportError:  # pragma: no cover - dual import path
            from utils.driver_activity import REGINA_TZ  # type: ignore
        return datetime.now(timezone.utc).astimezone(REGINA_TZ).date()

    @pytest.mark.asyncio
    async def test_rejects_today_or_future_target_date(self):
        today = self._regina_today().isoformat()
        with pytest.raises(HTTPException) as exc:
            await maint.admin_rollup_driver_daily(target_date=today, admin=ADMIN)
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_defaults_to_regina_yesterday_and_writes_the_audit_row(self):
        yesterday = self._regina_today() - timedelta(days=1)
        audit_mock = AsyncMock(return_value="audit-4")
        core_mock = AsyncMock(
            return_value={
                "stat_date": yesterday.isoformat(),
                "drivers_processed": 0,
                "created": 0,
                "updated": 0,
            }
        )
        with (
            patch("utils.driver_daily_rollup.rollup_driver_day", core_mock),
            patch.object(maint, "log_admin_action", audit_mock),
        ):
            result = await maint.admin_rollup_driver_daily(target_date=None, admin=ADMIN)

        core_mock.assert_awaited_once_with(yesterday)
        assert result["stat_date"] == yesterday.isoformat()
        assert result["drivers_processed"] == 0
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "driver_daily_rollup"

    @pytest.mark.asyncio
    async def test_rollup_delegates_to_shared_regina_core(self):
        """An explicit target_date is passed through verbatim as a date, and
        the core's own counters are returned unchanged."""
        target_date = self._regina_today() - timedelta(days=3)

        core_mock = AsyncMock(
            return_value={
                "stat_date": target_date.isoformat(),
                "drivers_processed": 2,
                "created": 1,
                "updated": 1,
            }
        )
        audit_mock = AsyncMock(return_value="audit-5")
        with (
            patch("utils.driver_daily_rollup.rollup_driver_day", core_mock),
            patch.object(maint, "log_admin_action", audit_mock),
        ):
            result = await maint.admin_rollup_driver_daily(target_date=target_date.isoformat(), admin=ADMIN)

        core_mock.assert_awaited_once_with(target_date)
        assert result["created"] == 1
        assert result["updated"] == 1
        # The audit payload carries the core's counters + the day definition,
        # so an operator-triggered rollup is attributable after the fact.
        audit_details = audit_mock.call_args[0][4]
        assert audit_details["drivers_processed"] == 2
        assert audit_details["day_tz"] == "regina"

    @pytest.mark.asyncio
    async def test_core_failure_propagates_and_writes_no_audit_row(self):
        """A failed rollup must not leave an audit row claiming it ran."""
        target_date = self._regina_today() - timedelta(days=2)
        audit_mock = AsyncMock(return_value="audit-6")
        with (
            patch(
                "utils.driver_daily_rollup.rollup_driver_day",
                AsyncMock(side_effect=RuntimeError("rollup exploded")),
            ),
            patch.object(maint, "log_admin_action", audit_mock),
        ):
            with pytest.raises(RuntimeError):
                await maint.admin_rollup_driver_daily(target_date=target_date.isoformat(), admin=ADMIN)

        audit_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# audit logs
# ---------------------------------------------------------------------------


class TestAuditLogs:
    @pytest.mark.asyncio
    async def test_get_audit_logs_no_filters(self):
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "log1"}])) as mock:
            result = await maint.get_audit_logs(
                limit=10, offset=0, action=None, entity_type=None, search=None, _admin=ADMIN
            )
        assert result == [{"id": "log1"}]
        _, filters = mock.call_args.args
        assert filters == {}

    @pytest.mark.asyncio
    async def test_get_audit_logs_applies_action_and_entity_filters(self):
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock:
            await maint.get_audit_logs(
                limit=10, offset=0, action="staff_created", entity_type="staff", search=None, _admin=ADMIN
            )
        _, filters = mock.call_args.args
        assert filters == {"action": "staff_created", "entity_type": "staff"}

    @pytest.mark.asyncio
    async def test_get_audit_logs_search_builds_or_regex(self):
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock:
            await maint.get_audit_logs(limit=10, offset=0, action=None, entity_type=None, search="abc", _admin=ADMIN)
        _, filters = mock.call_args.args
        assert "$or" in filters
        assert len(filters["$or"]) == 3

    @pytest.mark.asyncio
    async def test_get_audit_logs_search_matches_entity_id_not_legacy_resource_id(self):
        """SOC fix: every current writer (log_admin_action, log_user_action,
        the PII-reveal endpoint) populates audit_logs.entity_id, not the
        pre-migration-57 legacy resource_id column that nothing writes
        anymore. Searching resource_id silently matched zero modern rows —
        lock in entity_id so this doesn't regress."""
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock:
            await maint.get_audit_logs(limit=10, offset=0, action=None, entity_type=None, search="abc", _admin=ADMIN)
        _, filters = mock.call_args.args
        searched_fields = {list(clause.keys())[0] for clause in filters["$or"]}
        assert searched_fields == {"actor_id", "entity_id", "details"}
        assert "resource_id" not in searched_fields

    @pytest.mark.asyncio
    async def test_get_audit_logs_blank_search_after_strip_ignored(self):
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock:
            await maint.get_audit_logs(limit=10, offset=0, action=None, entity_type=None, search="   ", _admin=ADMIN)
        _, filters = mock.call_args.args
        assert "$or" not in filters


# ---------------------------------------------------------------------------
# audit log top-actors rollup
# ---------------------------------------------------------------------------


class TestAuditLogTopActors:
    """Corporate + admin portal review, round 2: "no 'who touched the most'
    rollup views — every threat hunt needs raw SQL." """

    @pytest.mark.asyncio
    async def test_aggregates_by_actor_sorted_descending(self):
        rows = [
            {"actor_id": "admin-a", "action": "staff_updated"},
            {"actor_id": "admin-a", "action": "staff_updated"},
            {"actor_id": "admin-a", "action": "settings_updated"},
            {"actor_id": "admin-b", "action": "staff_updated"},
        ]
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=rows)):
            result = await maint.get_audit_log_top_actors(days=7, limit=20, _admin=ADMIN)
        assert result["actors"][0]["actor_id"] == "admin-a"
        assert result["actors"][0]["action_count"] == 3
        assert result["actors"][1]["actor_id"] == "admin-b"
        assert result["actors"][1]["action_count"] == 1
        assert result["rows_scanned"] == 4
        assert result["rows_scanned_capped"] is False

    @pytest.mark.asyncio
    async def test_top_actions_breakdown_per_actor(self):
        rows = [
            {"actor_id": "admin-a", "action": "staff_updated"},
            {"actor_id": "admin-a", "action": "staff_updated"},
            {"actor_id": "admin-a", "action": "settings_updated"},
        ]
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=rows)):
            result = await maint.get_audit_log_top_actors(days=7, limit=20, _admin=ADMIN)
        top_actions = result["actors"][0]["top_actions"]
        assert top_actions[0] == {"action": "staff_updated", "count": 2}
        assert {"action": "settings_updated", "count": 1} in top_actions

    @pytest.mark.asyncio
    async def test_missing_actor_or_action_falls_back_to_unknown(self):
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[{}])):
            result = await maint.get_audit_log_top_actors(days=7, limit=20, _admin=ADMIN)
        assert result["actors"][0]["actor_id"] == "unknown"
        assert result["actors"][0]["top_actions"] == [{"action": "unknown", "count": 1}]

    @pytest.mark.asyncio
    async def test_respects_limit_and_flags_row_cap(self):
        rows = [{"actor_id": f"admin-{i}", "action": "x"} for i in range(5000)]
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=rows)):
            result = await maint.get_audit_log_top_actors(days=7, limit=3, _admin=ADMIN)
        assert len(result["actors"]) == 3
        assert result["rows_scanned_capped"] is True

    @pytest.mark.asyncio
    async def test_days_window_passed_to_query(self):
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock:
            result = await maint.get_audit_log_top_actors(days=30, limit=20, _admin=ADMIN)
        assert result["days"] == 30
        _, filters = mock.call_args.args
        assert "created_at" in filters
        assert "$gte" in filters["created_at"]


# ---------------------------------------------------------------------------
# PII reveal audit
# ---------------------------------------------------------------------------


class TestPiiRevealAudit:
    @pytest.mark.asyncio
    async def test_logs_pii_reveal_event(self):
        rows = []

        async def _capture(table, row):
            rows.append(row)

        body = maint.PiiRevealRequest(entity_type="driver", entity_id="d1")
        with patch.object(maint.db_supabase, "insert_one", AsyncMock(side_effect=_capture)):
            result = await maint.admin_log_pii_reveal(body, admin=ADMIN)

        assert result == {"ok": True}
        assert rows[0]["action"] == "pii_revealed"
        assert rows[0]["entity_type"] == "driver"
        assert rows[0]["entity_id"] == "d1"
        assert rows[0]["actor_id"] == ADMIN["id"]
