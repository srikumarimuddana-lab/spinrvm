"""Tests for utils/retention_purge.py — B-P1-6 daily PII purge loop.

The Postgres function `purge_pii_retention()` is exercised via migration
+ integration tests; here we verify the Python wrapper:

    - parses the JSONB rpc response correctly
    - forwards `dry_run` to the SQL parameter
    - returns None (not crashes) when supabase is unconfigured (dev/test)
    - returns None on unexpected response shape (defensive)
    - re-raises on DB error so the loop wrapper logs it (CLAUDE.md:
      never silently swallow DB errors)
    - leader lock acquires once and blocks subsequent acquirers
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


@pytest.mark.asyncio
async def test_run_tick_parses_jsonb_result_and_forwards_dry_run():
    """Happy path: rpc returns a JSONB dict, the function returns it
    verbatim and forwards p_dry_run=False to the SQL call."""
    expected = {
        "started_at": "2026-04-26T03:00:00+00:00",
        "completed_at": "2026-04-26T03:00:01+00:00",
        "dry_run": False,
        "rides_anonymized": 12,
        "rides_deleted": 3,
        "driver_location_deleted": 4500,
        "ride_messages_deleted": 87,
        "refresh_tokens_deleted": 21,
        "stripe_events_deleted": 9,
        "audit_logs_deleted": 0,
    }

    rpc_mock = MagicMock()
    rpc_mock.execute.return_value = MagicMock(data=expected)
    supa = MagicMock()
    supa.rpc.return_value = rpc_mock

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        # Exercises only the purge_pii_retention/purge_trip_route_geometry
        # JSONB-parsing path this test pins; the route-snapshot object purge
        # (Supabase Storage removal) is a separate sub-feature with its own
        # coverage and would need its own supa.table() chain mocked here.
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=0)),
    ):
        from utils.retention_purge import run_retention_purge_tick

        result = await run_retention_purge_tick(dry_run=False)

    assert result == expected
    assert supa.rpc.call_args_list == [
        call("purge_pii_retention", {"p_dry_run": False}),
        call("purge_trip_route_geometry", {"p_dry_run": False}),
    ]


@pytest.mark.asyncio
async def test_run_tick_dry_run_true_passed_to_sql():
    rpc_mock = MagicMock()
    rpc_mock.execute.return_value = MagicMock(data={"dry_run": True, "rides_deleted": 0})
    supa = MagicMock()
    supa.rpc.return_value = rpc_mock

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
    ):
        from utils.retention_purge import run_retention_purge_tick

        result = await run_retention_purge_tick(dry_run=True)

    assert supa.rpc.call_args_list == [
        call("purge_pii_retention", {"p_dry_run": True}),
        call("purge_trip_route_geometry", {"p_dry_run": True}),
    ]
    assert result["dry_run"] is True


@pytest.mark.asyncio
async def test_run_tick_returns_none_when_supabase_unconfigured():
    """Dev/test without SUPABASE_URL — should not raise, returns None."""
    with patch("utils.retention_purge.supabase", None):
        from utils.retention_purge import run_retention_purge_tick

        result = await run_retention_purge_tick()

    assert result is None


@pytest.mark.asyncio
async def test_run_tick_raises_on_unexpected_response_shape():
    """If the rpc response is shaped differently (PostgREST upgrade,
    schema drift), the tick now raises instead of silently returning None
    — fixed 2026-08-03 (see docs/change-log/2026-08-03-a1c-found-not-fixed-
    bugfixes.md, Entry 7). The daily regulatory PII/DSAR purge must not
    silently stop running with only a log line and no alarm; the loop
    wrapper (retention_purge_loop) catches this and increments the
    bgloop-error metric, matching purge_trip_route_geometry's existing
    behavior for the identical failure mode."""
    rpc_mock = MagicMock()
    rpc_mock.execute.return_value = MagicMock(data="not a dict")
    supa = MagicMock()
    supa.rpc.return_value = rpc_mock

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
    ):
        from utils.retention_purge import run_retention_purge_tick

        with pytest.raises(RuntimeError, match="purge_pii_retention returned an invalid response"):
            await run_retention_purge_tick()


@pytest.mark.asyncio
async def test_run_tick_reraises_db_error():
    """CLAUDE.md: never silently swallow DB errors. The wrapper logs
    via logger.exception and re-raises so the loop's outer try/except
    can record it without losing the stack."""
    supa = MagicMock()
    supa.rpc.side_effect = RuntimeError("PostgREST connection refused")

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        pytest.raises(RuntimeError, match="PostgREST connection refused"),
    ):
        from utils.retention_purge import run_retention_purge_tick

        await run_retention_purge_tick()


def test_seconds_until_next_returns_positive():
    """Scheduling math: should always return a positive sleep duration,
    never sleep through the same target hour twice."""
    from utils.retention_purge import _seconds_until_next

    for hour in (0, 3, 12, 23):
        assert _seconds_until_next(hour) > 0
        assert _seconds_until_next(hour) <= 24 * 3600


def test_expired_route_snapshot_ledger_deletes_every_revision_before_marking_it_deleted(monkeypatch):
    from utils import retention_purge

    removed_paths = []
    updates = []
    pending = [
        {
            "ride_id": "ride_1",
            "storage_bucket": "ride-route-snapshots",
            "object_path": "ride_1/route-v1.png",
        },
        {
            "ride_id": "ride_1",
            "storage_bucket": "ride-route-snapshots",
            "object_path": "ride_1/route-v2.png",
        },
    ]

    class Query:
        def __init__(self, table):
            self.table = table

        def select(self, _columns):
            return self

        def is_(self, _column, _value):
            return self

        def lte(self, _column, _value):
            return self

        def limit(self, _size):
            return self

        def update(self, payload):
            updates.append((self.table, payload))
            return self

        def eq(self, _column, _value):
            return self

        def execute(self):
            return SimpleNamespace(data=pending)

    class Bucket:
        def remove(self, paths):
            removed_paths.extend(paths)
            return SimpleNamespace(data=[])

    class Storage:
        def from_(self, bucket):
            assert bucket == "ride-route-snapshots"
            return Bucket()

    class Supabase:
        storage = Storage()

        def table(self, table):
            assert table in {"ride_routes", "ride_route_snapshot_objects"}
            return Query(table)

    async def run_sync(operation):
        return operation()

    monkeypatch.setattr(retention_purge, "supabase", Supabase())
    monkeypatch.setattr(retention_purge, "run_sync", run_sync)

    deleted = asyncio.run(retention_purge._delete_expired_route_snapshot_objects())

    assert deleted == 2
    assert removed_paths == ["ride_1/route-v1.png", "ride_1/route-v2.png"]
    ledger_updates = [update for update in updates if update[0] == "ride_route_snapshot_objects"]
    assert len(ledger_updates) == 2
    assert all("deleted_at" in payload for _, payload in ledger_updates)


@pytest.mark.asyncio
async def test_leader_lock_blocks_second_acquirer(mock_redis):
    """Two replicas calling redis_set_nx with the same key — the first
    wins, the second sees False and skips its run. Uses the in-process
    fallback (mock_redis fixture isolates _local)."""
    from utils.redis_client import redis_set_nx

    first = await redis_set_nx("spinr:retention:purge:lock", "pod-A", 60)
    second = await redis_set_nx("spinr:retention:purge:lock", "pod-B", 60)

    assert first is True
    assert second is False
