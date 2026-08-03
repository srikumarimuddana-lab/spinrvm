"""Additional coverage for utils/retention_purge.py (A1c Sub-tier C batch 3).

test_retention_purge.py already covers the happy path, the dry-run flag, the
unconfigured-supabase no-op, unexpected-response-shape defensiveness, DB-error
re-raise, and the leader lock. This file closes the remaining gaps:

  - every error branch inside _delete_expired_route_snapshot_objects (each one
    must re-raise per CLAUDE.md "do not silently swallow DB errors" — a purge
    that silently drops a Storage failure would leave a PII object undeleted
    with no signal)
  - the alternate response-parsing path where the rpc call itself returns a
    plain dict rather than an object with a .data attribute
  - the trip-route-geometry rpc's own error/invalid-shape branches, including
    the post-storage-deletion re-fetch (only reached when snapshot objects
    were actually deleted this tick)
  - the skipped_fk loud-surfacing log line (PIPEDA: a DSAR account blocked on
    a residual FK must never fail silently)
  - _pod_id() and _tick()'s two branches (lock acquired / not acquired)
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── _delete_expired_route_snapshot_objects: error branches ──────────


def _make_supabase(pending, *, raise_on=None):
    """Build a minimal fake supabase client.

    ``raise_on`` is an optional (table_or_"storage", op) tuple identifying the
    single call that should raise — e.g. ("ride_route_snapshot_objects",
    "select") or ("storage", "remove").
    """
    removed_paths: list[str] = []

    class Query:
        def __init__(self, table):
            self.table = table
            self._op = None

        def select(self, _columns):
            self._op = "select"
            return self

        def is_(self, _column, _value):
            return self

        def lte(self, _column, _value):
            return self

        def limit(self, _size):
            return self

        def update(self, _payload):
            self._op = "update"
            return self

        def eq(self, _column, _value):
            return self

        def execute(self):
            if raise_on == (self.table, self._op):
                raise RuntimeError(f"{self.table}.{self._op} failed")
            if self._op == "select":
                return SimpleNamespace(data=pending)
            return SimpleNamespace(data=[])

    class Bucket:
        def remove(self, paths):
            if raise_on == ("storage", "remove"):
                raise RuntimeError("storage remove failed")
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

    return Supabase(), removed_paths


_PENDING_ONE = [
    {
        "ride_id": "ride_1",
        "storage_bucket": "ride-route-snapshots",
        "object_path": "ride_1/route-v1.png",
    }
]


async def _run_sync(operation):
    return operation()


def test_delete_route_snapshots_raises_when_supabase_not_configured():
    from utils import retention_purge

    with patch.object(retention_purge, "supabase", None):
        with pytest.raises(RuntimeError, match="Supabase client not configured"):
            asyncio.run(retention_purge._delete_expired_route_snapshot_objects())


def test_delete_route_snapshots_returns_zero_and_touches_nothing_when_no_pending_rows(monkeypatch):
    from utils import retention_purge

    supa, removed_paths = _make_supabase([])
    monkeypatch.setattr(retention_purge, "supabase", supa)
    monkeypatch.setattr(retention_purge, "run_sync", _run_sync)

    deleted = asyncio.run(retention_purge._delete_expired_route_snapshot_objects())

    assert deleted == 0
    assert removed_paths == []


def test_delete_route_snapshots_reraises_on_ledger_query_failure(monkeypatch, caplog):
    from utils import retention_purge

    supa, _ = _make_supabase(_PENDING_ONE, raise_on=("ride_route_snapshot_objects", "select"))
    monkeypatch.setattr(retention_purge, "supabase", supa)
    monkeypatch.setattr(retention_purge, "run_sync", _run_sync)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="select failed"):
            asyncio.run(retention_purge._delete_expired_route_snapshot_objects())

    assert "route snapshot ledger query failed" in caplog.text


def test_delete_route_snapshots_raises_on_invalid_ledger_response_shape(monkeypatch, caplog):
    """The ledger query guard: if PostgREST hands back something whose .data
    isn't a list (schema drift, malformed response), this must raise loudly
    rather than silently treating a non-list as "zero pending rows"."""
    from utils import retention_purge

    class Query:
        def select(self, _columns):
            return self

        def is_(self, _column, _value):
            return self

        def lte(self, _column, _value):
            return self

        def limit(self, _size):
            return self

        def execute(self):
            return SimpleNamespace(data=None)

    class Supabase:
        def table(self, _table):
            return Query()

    monkeypatch.setattr(retention_purge, "supabase", Supabase())
    monkeypatch.setattr(retention_purge, "run_sync", _run_sync)

    with pytest.raises(RuntimeError, match="route snapshot ledger query returned an invalid response"):
        asyncio.run(retention_purge._delete_expired_route_snapshot_objects())


def test_delete_route_snapshots_reraises_on_storage_removal_failure(monkeypatch, caplog):
    from utils import retention_purge

    supa, removed_paths = _make_supabase(_PENDING_ONE, raise_on=("storage", "remove"))
    monkeypatch.setattr(retention_purge, "supabase", supa)
    monkeypatch.setattr(retention_purge, "run_sync", _run_sync)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="storage remove failed"):
            asyncio.run(retention_purge._delete_expired_route_snapshot_objects())

    assert "private route snapshot storage deletion failed" in caplog.text
    assert removed_paths == []


def test_delete_route_snapshots_reraises_on_ledger_acknowledgement_failure(monkeypatch, caplog):
    """A Storage delete that succeeded but whose ledger row never got
    deleted_at stamped must be surfaced loudly — the next tick will otherwise
    re-attempt a Storage delete on an object that's already gone, or (worse)
    the row is silently lost from retry consideration."""
    from utils import retention_purge

    supa, removed_paths = _make_supabase(_PENDING_ONE, raise_on=("ride_route_snapshot_objects", "update"))
    monkeypatch.setattr(retention_purge, "supabase", supa)
    monkeypatch.setattr(retention_purge, "run_sync", _run_sync)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="update failed"):
            asyncio.run(retention_purge._delete_expired_route_snapshot_objects())

    assert "route snapshot ledger acknowledgement failed" in caplog.text
    # The Storage object WAS removed before the bookkeeping write failed.
    assert removed_paths == ["ride_1/route-v1.png"]


def test_delete_route_snapshots_reraises_on_route_reference_clear_failure(monkeypatch, caplog):
    from utils import retention_purge

    supa, removed_paths = _make_supabase(_PENDING_ONE, raise_on=("ride_routes", "update"))
    monkeypatch.setattr(retention_purge, "supabase", supa)
    monkeypatch.setattr(retention_purge, "run_sync", _run_sync)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="update failed"):
            asyncio.run(retention_purge._delete_expired_route_snapshot_objects())

    assert "current route snapshot reference clear failed" in caplog.text
    assert removed_paths == ["ride_1/route-v1.png"]


# ── run_retention_purge_tick: alternate response shapes ──────────────


def _rpc_sequence(responses):
    """supa.rpc(name, params) -> object whose .execute() yields responses in
    call order. Each response is either an object with .data, a raw dict, or
    an Exception instance to raise."""
    calls = list(responses)

    def _rpc(_name, _params):
        response = calls.pop(0)

        class _Call:
            def execute(self_inner):
                if isinstance(response, Exception):
                    raise response
                return response

        return _Call()

    supa = MagicMock()
    supa.rpc.side_effect = _rpc
    return supa


@pytest.mark.asyncio
async def test_run_tick_parses_data_from_a_plain_dict_response():
    """Some PostgREST client versions return a bare dict rather than an
    object exposing .data — the wrapper must still find the payload."""
    pii_response = {"data": {"dry_run": False, "rides_deleted": 1}}
    route_response = MagicMock(data={"dry_run": False, "ride_routes_anonymized": 0})

    supa = _rpc_sequence([pii_response, route_response])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=0)),
    ):
        from utils.retention_purge import run_retention_purge_tick

        result = await run_retention_purge_tick()

    assert result == {"dry_run": False, "rides_deleted": 1}


@pytest.mark.asyncio
async def test_run_tick_parses_route_data_from_a_plain_dict_response():
    pii_response = MagicMock(data={"dry_run": False, "rides_deleted": 0})
    route_response = {"data": {"dry_run": False, "ride_routes_anonymized": 5}}

    supa = _rpc_sequence([pii_response, route_response])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=0)),
    ):
        from utils.retention_purge import run_retention_purge_tick

        result = await run_retention_purge_tick()

    # Route-geometry parsing succeeded (no raise); the PII counters are still
    # what's returned to the caller per the established wrapper contract.
    assert result == {"dry_run": False, "rides_deleted": 0}


@pytest.mark.asyncio
async def test_run_tick_reraises_on_trip_route_geometry_rpc_error(caplog):
    pii_response = MagicMock(data={"dry_run": False})
    supa = _rpc_sequence([pii_response, RuntimeError("route geometry rpc down")])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        caplog.at_level(logging.ERROR),
    ):
        from utils.retention_purge import run_retention_purge_tick

        with pytest.raises(RuntimeError, match="route geometry rpc down"):
            await run_retention_purge_tick()

    assert "rpc(purge_trip_route_geometry) failed" in caplog.text


@pytest.mark.asyncio
async def test_run_tick_raises_on_trip_route_geometry_invalid_shape(caplog):
    pii_response = MagicMock(data={"dry_run": False})
    route_response = MagicMock(data="not a dict")
    supa = _rpc_sequence([pii_response, route_response])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        caplog.at_level(logging.ERROR),
    ):
        from utils.retention_purge import run_retention_purge_tick

        with pytest.raises(RuntimeError, match="purge_trip_route_geometry returned an invalid response"):
            await run_retention_purge_tick()

    assert "unexpected trip-route geometry response shape" in caplog.text


@pytest.mark.asyncio
async def test_run_tick_post_storage_refetch_succeeds_when_snapshots_were_deleted():
    """dry_run=False and snapshot objects were actually deleted this tick ->
    the wrapper must re-fetch the trip-route-geometry counters a second time
    so the anonymization reflects the just-deleted rows."""
    pii_response = MagicMock(data={"dry_run": False})
    route_response_1 = MagicMock(data={"dry_run": False, "ride_routes_anonymized": 0})
    # Plain dict on the post-storage refetch specifically — covers the
    # route_result.get("data") alt-parsing path (some PostgREST client
    # versions return a bare dict rather than an object with .data).
    route_response_2 = {"data": {"dry_run": False, "ride_routes_anonymized": 3}}
    supa = _rpc_sequence([pii_response, route_response_1, route_response_2])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=2)),
    ):
        from utils.retention_purge import run_retention_purge_tick

        result = await run_retention_purge_tick(dry_run=False)

    assert result == {"dry_run": False}
    assert supa.rpc.call_count == 3  # pii, route (pre), route (post-storage)


@pytest.mark.asyncio
async def test_run_tick_post_storage_refetch_reraises_on_error(caplog):
    pii_response = MagicMock(data={"dry_run": False})
    route_response_1 = MagicMock(data={"dry_run": False})
    supa = _rpc_sequence([pii_response, route_response_1, RuntimeError("post-storage rpc down")])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=1)),
        caplog.at_level(logging.ERROR),
    ):
        from utils.retention_purge import run_retention_purge_tick

        with pytest.raises(RuntimeError, match="post-storage rpc down"):
            await run_retention_purge_tick(dry_run=False)

    assert "post-storage trip-route geometry purge failed" in caplog.text


@pytest.mark.asyncio
async def test_run_tick_post_storage_refetch_raises_on_invalid_shape(caplog):
    pii_response = MagicMock(data={"dry_run": False})
    route_response_1 = MagicMock(data={"dry_run": False})
    route_response_2 = MagicMock(data=None)
    supa = _rpc_sequence([pii_response, route_response_1, route_response_2])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=1)),
        caplog.at_level(logging.ERROR),
    ):
        from utils.retention_purge import run_retention_purge_tick

        with pytest.raises(RuntimeError, match="post-storage purge_trip_route_geometry returned an invalid response"):
            await run_retention_purge_tick(dry_run=False)

    assert "unexpected post-storage trip-route geometry response shape" in caplog.text


@pytest.mark.asyncio
async def test_run_tick_never_refetches_when_dry_run_true():
    """dry_run must never touch Storage or trigger the post-storage refetch —
    it's the operator's "what would happen" preview."""
    pii_response = MagicMock(data={"dry_run": True})
    route_response = MagicMock(data={"dry_run": True})
    supa = _rpc_sequence([pii_response, route_response])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        patch(
            "utils.retention_purge._delete_expired_route_snapshot_objects",
            AsyncMock(side_effect=AssertionError("dry_run must never delete Storage objects")),
        ) as delete_mock,
    ):
        from utils.retention_purge import run_retention_purge_tick

        await run_retention_purge_tick(dry_run=True)

    delete_mock.assert_not_awaited()
    assert supa.rpc.call_count == 2  # no post-storage third call


@pytest.mark.asyncio
async def test_run_tick_surfaces_skipped_fk_loudly(caplog):
    """PIPEDA: a DSAR account blocked on a residual FK from a hard-delete
    must be logged at ERROR, not buried — Compliance needs to see it to add
    the offending table to purge_pii_retention's Step H."""
    pii_response = MagicMock(data={"dry_run": False, "dsar_users_skipped_fk": 3})
    route_response = MagicMock(data={"dry_run": False})
    supa = _rpc_sequence([pii_response, route_response])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=0)),
        caplog.at_level(logging.ERROR),
    ):
        from utils.retention_purge import run_retention_purge_tick

        result = await run_retention_purge_tick(dry_run=False)

    assert result["dsar_users_skipped_fk"] == 3
    assert "3 DSAR account(s) skipped on a residual FK" in caplog.text


@pytest.mark.asyncio
async def test_run_tick_no_skipped_fk_log_when_zero():
    pii_response = MagicMock(data={"dry_run": False, "dsar_users_skipped_fk": 0})
    route_response = MagicMock(data={"dry_run": False})
    supa = _rpc_sequence([pii_response, route_response])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=0)),
    ):
        from utils.retention_purge import run_retention_purge_tick

        result = await run_retention_purge_tick(dry_run=False)

    assert result["dsar_users_skipped_fk"] == 0


# ── _pod_id / _tick ────────────────────────────────────────────────


def test_pod_id_format():
    import os
    import socket

    from utils.retention_purge import _pod_id

    assert _pod_id() == f"{socket.gethostname()}:{os.getpid()}"


@pytest.mark.asyncio
async def test_tick_runs_the_purge_when_lock_acquired():
    from utils import retention_purge

    run_tick_mock = AsyncMock(return_value={"dry_run": False})
    with (
        patch.object(retention_purge, "redis_set_nx", AsyncMock(return_value=True)),
        patch.object(retention_purge, "run_retention_purge_tick", run_tick_mock),
    ):
        await retention_purge._tick()

    run_tick_mock.assert_awaited_once_with(dry_run=False)


@pytest.mark.asyncio
async def test_tick_skips_the_purge_when_another_replica_holds_the_lock(caplog):
    from utils import retention_purge

    run_tick_mock = AsyncMock()
    with (
        patch.object(retention_purge, "redis_set_nx", AsyncMock(return_value=False)),
        patch.object(retention_purge, "run_retention_purge_tick", run_tick_mock),
        caplog.at_level(logging.INFO),
    ):
        await retention_purge._tick()

    run_tick_mock.assert_not_awaited()
    assert "another replica holds the lock" in caplog.text
