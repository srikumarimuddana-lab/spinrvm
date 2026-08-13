"""Coverage tests for backend/routes/admin/maintenance.py.

Covers GPS-history cleanup, the driver_daily_stats rollup job (target-date
validation, upsert branches, RPC failure handling), audit-log listing
(filters + search regex), and the PII-reveal audit endpoint.

TEST-ONLY change; no application code modified.

NOTE (found, not fixed — see PR description / change log "Bugs found but not
fixed"): under this repo's test harness, ``backend.routes.admin.maintenance``
gets aliased to whichever module instance ("routes.admin.maintenance" bare,
or "backend.routes.admin.maintenance" qualified) loaded first — see
conftest.py's ``_BareModuleAliasFinder``. When the bare spelling loads first,
its 3-dot relative import (``from ... import db_supabase`` /
``from ...db_supabase import run_sync as _run_sync`` /
``from ...supabase_client import supabase as _supabase_client``) has no
parent package to resolve against and raises ImportError, so maintenance.py
falls into its ``except ImportError:`` branch — which only does
``import db_supabase`` and never binds ``_run_sync`` / ``_supabase_client``
at all. ``admin_rollup_driver_daily`` then hits a bare ``NameError`` the
first time it reaches ``_run_sync(_rpc)`` for any driver with day-activity
(nightly rollup with >=1 active driver). This reproduces deterministically
in this test environment; the module-level ``_ensure_maint_rpc_bindings``
fixture below works around it for these tests without touching app code.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.routes.admin import maintenance as maint

ADMIN = {"id": "admin-1", "role": "super_admin"}


@pytest.fixture(autouse=True)
def _ensure_maint_rpc_bindings():
    """Work around the ImportError-fallback gap described in the module
    docstring above (test-harness workaround only — application code is
    untouched). Without this, every test that reaches the RPC branch of
    admin_rollup_driver_daily fails with AttributeError/NameError purely as
    an artifact of module-alias load order, not because of anything these
    tests are verifying.
    """
    added = []
    if not hasattr(maint, "_run_sync"):
        maint._run_sync = maint.db_supabase.run_sync
        added.append("_run_sync")
    if not hasattr(maint, "_supabase_client"):
        maint._supabase_client = maint.db_supabase.supabase
        added.append("_supabase_client")
    yield
    for name in added:
        delattr(maint, name)


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

        with patch.object(maint.db_supabase, "delete_many", AsyncMock(side_effect=_capture)):
            result = await maint.admin_cleanup_location_history(days=30)

        assert len(calls) == 2
        assert "historical_cutoff" in result
        assert "idle_cutoff" in result

    @pytest.mark.asyncio
    async def test_cleanup_continues_when_historical_delete_fails(self):
        """Historical-delete failure must not block the idle-delete branch."""
        call_count = {"n": 0}

        async def _side_effect(table, filters):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("db error")

        with patch.object(maint.db_supabase, "delete_many", AsyncMock(side_effect=_side_effect)):
            result = await maint.admin_cleanup_location_history(days=30)

        assert call_count["n"] == 2
        assert result["deleted_historical"] == -1

    @pytest.mark.asyncio
    async def test_cleanup_continues_when_idle_delete_fails(self):
        call_count = {"n": 0}

        async def _side_effect(table, filters):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("db error")

        with patch.object(maint.db_supabase, "delete_many", AsyncMock(side_effect=_side_effect)):
            result = await maint.admin_cleanup_location_history(days=30)

        assert call_count["n"] == 2
        assert result["deleted_idle"] == -1


# ---------------------------------------------------------------------------
# driver-daily rollup
# ---------------------------------------------------------------------------


class TestRollupDriverDaily:
    @pytest.mark.asyncio
    async def test_rejects_today_or_future_target_date(self):
        today = datetime.now(timezone.utc).date().isoformat()
        with pytest.raises(HTTPException) as exc:
            await maint.admin_rollup_driver_daily(target_date=today)
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_defaults_to_yesterday_when_no_target_date(self):
        # NOTE: `maint.db` is `maint.db_supabase` under a legacy alias (see
        # staff.py-style "db = db_supabase" pattern) — they are the *same*
        # object, so only one get_rows patch is needed/possible per test.
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[])):
            result = await maint.admin_rollup_driver_daily(target_date=None)
        assert result["stat_date"] == yesterday.isoformat()
        assert result["drivers_processed"] == 0

    @pytest.mark.asyncio
    async def test_rollup_creates_new_stat_row_for_new_driver(self):
        target_date = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
        ride = {"driver_id": "d1", "status": "completed", "driver_earnings": "10.00", "tip_amount": "2.00"}

        async def _get_rows(table, filters=None, **kwargs):
            if table == "rides":
                return [ride]
            if table == "driver_location_history":
                return [{"driver_id": "d1"}]
            if table == "driver_daily_stats":
                return []  # no existing row -> insert branch
            if table == "audit_logs":
                return []
            return []

        rpc_resp = AsyncMock()
        rpc_resp.data = [
            {
                "idle_km": 1.0,
                "navigating_km": 2.0,
                "trip_km": 3.0,
                "online_minutes": 60,
                "first_online_at": "2026-01-01T00:00:00Z",
                "last_online_at": "2026-01-01T01:00:00Z",
            }
        ]

        with (
            patch.object(maint.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(maint, "_run_sync", AsyncMock(return_value=rpc_resp)),
            patch.object(maint.db_supabase, "get_driver_by_id", AsyncMock(return_value={"service_area_id": "a1"})),
            patch.object(maint.db_supabase, "insert_one", AsyncMock()) as insert_mock,
            patch.object(maint.db_supabase, "update_one", AsyncMock()) as update_mock,
        ):
            result = await maint.admin_rollup_driver_daily(target_date=target_date)

        assert result["created"] == 1
        assert result["updated"] == 0
        insert_mock.assert_awaited_once()
        update_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rollup_updates_existing_stat_row(self):
        target_date = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
        ride = {"driver_id": "d1", "status": "cancelled"}

        async def _get_rows(table, filters=None, **kwargs):
            if table == "rides":
                return [ride]
            if table == "driver_location_history":
                return [{"driver_id": "d1"}]
            if table == "driver_daily_stats":
                return [{"id": f"d1_{target_date}"}]  # existing -> update branch
            if table == "audit_logs":
                return []
            return []

        rpc_resp = AsyncMock()
        rpc_resp.data = []  # empty rows -> gps_stats defaults

        with (
            patch.object(maint.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(maint, "_run_sync", AsyncMock(return_value=rpc_resp)),
            patch.object(maint.db_supabase, "get_driver_by_id", AsyncMock(return_value=None)),
            patch.object(maint.db_supabase, "insert_one", AsyncMock()) as insert_mock,
            patch.object(maint.db_supabase, "update_one", AsyncMock()) as update_mock,
        ):
            result = await maint.admin_rollup_driver_daily(target_date=target_date)

        assert result["created"] == 0
        assert result["updated"] == 1
        update_mock.assert_awaited_once()
        insert_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rollup_rpc_failure_falls_back_to_zero_gps_stats(self):
        target_date = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
        ride = {"driver_id": "d1", "status": "completed", "driver_earnings": "5", "tip_amount": "0"}

        async def _get_rows(table, filters=None, **kwargs):
            if table == "rides":
                return [ride]
            if table == "driver_location_history":
                return []
            if table == "driver_daily_stats":
                return []
            if table == "audit_logs":
                return []
            return []

        with (
            patch.object(maint.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(maint, "_run_sync", AsyncMock(side_effect=RuntimeError("rpc down"))),
            patch.object(maint.db_supabase, "get_driver_by_id", AsyncMock(return_value=None)),
            patch.object(maint.db_supabase, "insert_one", AsyncMock()) as insert_mock,
        ):
            result = await maint.admin_rollup_driver_daily(target_date=target_date)

        assert result["created"] == 1
        stat_row = insert_mock.call_args.args[1]
        assert stat_row["online_minutes"] == 0
        assert stat_row["total_km"] == 0

    @pytest.mark.asyncio
    async def test_rollup_counts_declines_by_actor_id(self):
        target_date = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()

        async def _get_rows(table, filters=None, **kwargs):
            if table == "rides":
                return []
            if table == "driver_location_history":
                return [{"driver_id": "d1"}]
            if table == "driver_daily_stats":
                return []
            if table == "audit_logs":
                return [{"actor_id": "d1"}, {"actor_id": "d1"}]
            return []

        rpc_resp = AsyncMock()
        rpc_resp.data = []

        with (
            patch.object(maint.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(maint, "_run_sync", AsyncMock(return_value=rpc_resp)),
            patch.object(maint.db_supabase, "get_driver_by_id", AsyncMock(return_value=None)),
            patch.object(maint.db_supabase, "insert_one", AsyncMock()) as insert_mock,
        ):
            await maint.admin_rollup_driver_daily(target_date=target_date)

        stat_row = insert_mock.call_args.args[1]
        assert stat_row["rides_declined"] == 2


# ---------------------------------------------------------------------------
# audit logs
# ---------------------------------------------------------------------------


class TestAuditLogs:
    @pytest.mark.asyncio
    async def test_get_audit_logs_no_filters(self):
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "log1"}])) as mock:
            result = await maint.get_audit_logs(
                limit=10,
                offset=0,
                action=None,
                entity_type=None,
                search=None,
                start=None,
                end=None,
                _admin=ADMIN,
            )
        assert result == [{"id": "log1"}]
        _, filters = mock.call_args.args
        assert filters == {}

    @pytest.mark.asyncio
    async def test_get_audit_logs_applies_action_and_entity_filters(self):
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock:
            await maint.get_audit_logs(
                limit=10,
                offset=0,
                action="staff_created",
                entity_type="staff",
                search=None,
                start=None,
                end=None,
                _admin=ADMIN,
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
# audit log date-range window + facets
# ---------------------------------------------------------------------------


class TestAuditLogDateRange:
    """An incident investigation is "what happened between these two
    timestamps"; without a window that needed raw SQL."""

    @pytest.mark.asyncio
    async def test_start_and_end_bound_created_at(self):
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock:
            await maint.get_audit_logs(
                limit=10,
                offset=0,
                action=None,
                entity_type=None,
                search=None,
                start="2026-08-13T19:00:00+00:00",
                end="2026-08-13T21:00:00+00:00",
                _admin=ADMIN,
            )
        _, filters = mock.call_args.args
        assert filters["created_at"] == {
            "$gte": "2026-08-13T19:00:00+00:00",
            "$lte": "2026-08-13T21:00:00+00:00",
        }

    @pytest.mark.asyncio
    async def test_open_ended_ranges_supported(self):
        for kwargs, expected in (
            ({"start": "2026-08-13T19:00:00+00:00"}, {"$gte": "2026-08-13T19:00:00+00:00"}),
            ({"end": "2026-08-13T21:00:00+00:00"}, {"$lte": "2026-08-13T21:00:00+00:00"}),
        ):
            with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock:
                await maint.get_audit_logs(
                    limit=10,
                    offset=0,
                    action=None,
                    entity_type=None,
                    search=None,
                    start=kwargs.get("start"),
                    end=kwargs.get("end"),
                    _admin=ADMIN,
                )
            _, filters = mock.call_args.args
            assert filters["created_at"] == expected

    @pytest.mark.asyncio
    async def test_no_range_leaves_created_at_unfiltered(self):
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock:
            await maint.get_audit_logs(
                limit=10,
                offset=0,
                action=None,
                entity_type=None,
                search=None,
                start=None,
                end=None,
                _admin=ADMIN,
            )
        _, filters = mock.call_args.args
        assert "created_at" not in filters


class TestAuditLogFacets:
    """The filter dropdowns were a hand-written list that matched almost
    nothing in the table — every action option and most entity options
    returned zero rows silently. Facets come from the data instead."""

    @pytest.mark.asyncio
    async def test_counts_distinct_actions_and_entities_descending(self):
        rows = [
            {"action": "driver_approve", "entity_type": "drivers"},
            {"action": "driver_approve", "entity_type": "drivers"},
            {"action": "otp_sent", "entity_type": "users"},
        ]
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=rows)):
            result = await maint.get_audit_log_facets(days=90, _admin=ADMIN)
        assert result["actions"][0] == {"value": "driver_approve", "count": 2}
        assert {"value": "otp_sent", "count": 1} in result["actions"]
        assert result["entity_types"][0] == {"value": "drivers", "count": 2}
        assert result["rows_scanned"] == 3
        assert result["rows_scanned_capped"] is False

    @pytest.mark.asyncio
    async def test_real_world_action_names_survive_verbatim(self):
        """Regression: the old hardcoded list assumed generic verbs like
        "updated". Writers emit specific ones — don't normalise them away."""
        rows = [{"action": "zoho_desk_config_updated", "entity_type": "zoho_desk_config"}]
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=rows)):
            result = await maint.get_audit_log_facets(days=90, _admin=ADMIN)
        assert result["actions"] == [{"value": "zoho_desk_config_updated", "count": 1}]
        assert result["entity_types"] == [{"value": "zoho_desk_config", "count": 1}]

    @pytest.mark.asyncio
    async def test_blank_values_skipped(self):
        rows = [{"action": None, "entity_type": ""}, {"action": "otp_sent", "entity_type": "users"}]
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=rows)):
            result = await maint.get_audit_log_facets(days=90, _admin=ADMIN)
        assert result["actions"] == [{"value": "otp_sent", "count": 1}]
        assert result["entity_types"] == [{"value": "users", "count": 1}]

    @pytest.mark.asyncio
    async def test_flags_scan_cap_so_counts_are_not_read_as_complete(self):
        rows = [{"action": "x", "entity_type": "y"} for _ in range(maint._FACET_SCAN_CAP)]
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=rows)):
            result = await maint.get_audit_log_facets(days=90, _admin=ADMIN)
        assert result["rows_scanned_capped"] is True

    @pytest.mark.asyncio
    async def test_days_window_passed_to_query(self):
        with patch.object(maint.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock:
            result = await maint.get_audit_log_facets(days=30, _admin=ADMIN)
        assert result["days"] == 30
        _, filters = mock.call_args.args
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
