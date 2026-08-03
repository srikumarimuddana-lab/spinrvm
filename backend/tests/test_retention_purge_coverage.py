"""Coverage top-up for utils/retention_purge.py — B-P1-6 daily PII purge loop.

`tests/test_retention_purge.py` already covers the documented happy paths
(JSONB parsing, dry_run forwarding, unconfigured-supabase no-op, DB-error
re-raise, the leader lock, and the route-snapshot-ledger happy path). This
file targets the *branches that file doesn't reach* — every remaining
`except` clause, response-shape guard, and the `_tick()` lock-acquired /
lock-not-acquired split — so the module gets as close to full coverage as
is reasonably achievable without touching application code.

Test-only change — no application code modified.

Regulatory context (see module docstring + CLAUDE.md Saskatchewan
regulatory / PIPEDA sections): this loop is what actually executes the
7-year trip/DSAR retention window, the 3-year GPS-trace ceiling, and the
7-year insurance-period audit trail. A defect here is a compliance defect,
not just a bug — an asymmetric error-handling gap found while reading the
source was fixed on 2026-08-03 (see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 7, and
`test_run_tick_raises_consistently_for_both_malformed_responses` below).
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _identity_run_sync():
    return AsyncMock(side_effect=lambda fn: fn())


def _make_supa(responses):
    """A `supabase` stand-in whose `.rpc(name, params).execute()` returns
    (or raises) the next item from `responses`, in call order — lets a
    single test drive the 2nd/3rd rpc() call in `run_retention_purge_tick`
    independently of the 1st."""
    call_index = {"n": -1}
    supa = MagicMock()

    def _rpc(_name, _params):
        call_index["n"] += 1
        outcome = responses[call_index["n"]]
        rpc_mock = MagicMock()
        if isinstance(outcome, BaseException):
            rpc_mock.execute.side_effect = outcome
        else:
            rpc_mock.execute.return_value = outcome
        return rpc_mock

    supa.rpc.side_effect = _rpc
    return supa


def _build_snapshot_supabase(pending, fail_at=None):
    """Fake supabase client for `_delete_expired_route_snapshot_objects`.

    fail_at: None (success), "ledger_query" (initial pending-rows select
    raises), "remove" (Storage.remove raises), "ledger_ack" (per-row
    ledger update raises), or "route_ref" (ride_routes reference-clear
    update raises).
    """
    updates: list[tuple[str, dict]] = []
    removed_paths: list[str] = []

    class Query:
        def __init__(self, table):
            self.table = table
            self.mode = None
            self._payload = None

        def select(self, _columns):
            self.mode = "select"
            return self

        def is_(self, _column, _value):
            return self

        def lte(self, _column, _value):
            return self

        def limit(self, _size):
            return self

        def update(self, payload):
            self.mode = "update"
            self._payload = payload
            return self

        def eq(self, _column, _value):
            return self

        def execute(self):
            if self.mode == "select":
                if fail_at == "ledger_query":
                    raise RuntimeError("ledger query failed")
                return SimpleNamespace(data=pending)
            # update mode
            if self.table == "ride_route_snapshot_objects" and fail_at == "ledger_ack":
                raise RuntimeError("ledger ack failed")
            if self.table == "ride_routes" and fail_at == "route_ref":
                raise RuntimeError("route ref clear failed")
            updates.append((self.table, self._payload))
            return SimpleNamespace(data=[{"ok": True}])

    class Bucket:
        def remove(self, paths):
            if fail_at == "remove":
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

    return Supabase(), updates, removed_paths


# ---------------------------------------------------------------------------
# _pod_id
# ---------------------------------------------------------------------------


def test_pod_id_returns_hostname_colon_pid():
    import os
    import socket

    from utils.retention_purge import _pod_id

    assert _pod_id() == f"{socket.gethostname()}:{os.getpid()}"


# ---------------------------------------------------------------------------
# _delete_expired_route_snapshot_objects — guard / failure branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_snapshot_objects_raises_when_supabase_unconfigured():
    from utils.retention_purge import _delete_expired_route_snapshot_objects

    with patch("utils.retention_purge.supabase", None):
        with pytest.raises(RuntimeError, match="Supabase client not configured"):
            await _delete_expired_route_snapshot_objects()


@pytest.mark.asyncio
async def test_delete_snapshot_objects_raises_on_non_list_ledger_response():
    """PostgREST/schema drift defence: `.data` on the ledger query isn't a
    list at all (not even an empty one) — must raise loudly, not silently
    treat it as "nothing pending"."""
    from utils.retention_purge import _delete_expired_route_snapshot_objects

    query = MagicMock()
    query.select.return_value = query
    query.is_.return_value = query
    query.lte.return_value = query
    query.limit.return_value = query
    query.execute.return_value = SimpleNamespace(data="not-a-list")
    supa = MagicMock()
    supa.table.return_value = query

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        with pytest.raises(RuntimeError, match="route snapshot ledger query returned an invalid response"):
            await _delete_expired_route_snapshot_objects()


@pytest.mark.asyncio
async def test_delete_snapshot_objects_reraises_on_ledger_query_exception():
    from utils.retention_purge import _delete_expired_route_snapshot_objects

    supa, _updates, _removed = _build_snapshot_supabase(pending=[], fail_at="ledger_query")

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        with pytest.raises(RuntimeError, match="ledger query failed"):
            await _delete_expired_route_snapshot_objects()


@pytest.mark.asyncio
async def test_delete_snapshot_objects_returns_zero_when_nothing_pending():
    from utils.retention_purge import _delete_expired_route_snapshot_objects

    supa, updates, removed = _build_snapshot_supabase(pending=[])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        deleted = await _delete_expired_route_snapshot_objects()

    assert deleted == 0
    assert updates == []
    assert removed == []


@pytest.mark.asyncio
async def test_delete_snapshot_objects_reraises_on_storage_removal_failure():
    from utils.retention_purge import _delete_expired_route_snapshot_objects

    pending = [
        {"ride_id": "ride_1", "storage_bucket": "ride-route-snapshots", "object_path": "ride_1/v1.png"},
    ]
    supa, _updates, removed = _build_snapshot_supabase(pending=pending, fail_at="remove")

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        with pytest.raises(RuntimeError, match="storage remove failed"):
            await _delete_expired_route_snapshot_objects()

    # Never got to record anything removed since Storage.remove raised.
    assert removed == []


@pytest.mark.asyncio
async def test_delete_snapshot_objects_reraises_on_ledger_ack_failure():
    """Storage delete succeeded but the ledger acknowledgement update
    fails — must raise (not lose track: `deleted_at` stays NULL, next
    day's run retries, per the docstring) rather than silently move on."""
    from utils.retention_purge import _delete_expired_route_snapshot_objects

    pending = [
        {"ride_id": "ride_1", "storage_bucket": "ride-route-snapshots", "object_path": "ride_1/v1.png"},
    ]
    supa, updates, removed = _build_snapshot_supabase(pending=pending, fail_at="ledger_ack")

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        with pytest.raises(RuntimeError, match="ledger ack failed"):
            await _delete_expired_route_snapshot_objects()

    assert removed == ["ride_1/v1.png"]  # Storage delete DID happen
    assert updates == []  # but the ledger ack never landed


@pytest.mark.asyncio
async def test_delete_snapshot_objects_reraises_on_route_reference_clear_failure():
    """Ledger ack succeeded, but clearing the live `ride_routes` pointer
    to the now-deleted snapshot object fails — must raise so the stale
    reference isn't left dangling silently."""
    from utils.retention_purge import _delete_expired_route_snapshot_objects

    pending = [
        {"ride_id": "ride_1", "storage_bucket": "ride-route-snapshots", "object_path": "ride_1/v1.png"},
    ]
    supa, updates, removed = _build_snapshot_supabase(pending=pending, fail_at="route_ref")

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        with pytest.raises(RuntimeError, match="route ref clear failed"):
            await _delete_expired_route_snapshot_objects()

    assert removed == ["ride_1/v1.png"]
    ledger_updates = [u for u in updates if u[0] == "ride_route_snapshot_objects"]
    assert len(ledger_updates) == 1  # ack landed before the route-ref update blew up


@pytest.mark.asyncio
async def test_delete_snapshot_objects_skips_route_ref_clear_when_no_ride_id():
    """A pending row with no `ride_id` (e.g. orphaned object) still gets
    Storage-deleted and ledger-acked, but there's no `ride_routes` row to
    clear a pointer on — must not attempt it (and must not raise)."""
    from utils.retention_purge import _delete_expired_route_snapshot_objects

    pending = [
        {"ride_id": None, "storage_bucket": "ride-route-snapshots", "object_path": "orphan/v1.png"},
    ]
    supa, updates, removed = _build_snapshot_supabase(pending=pending)

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        deleted = await _delete_expired_route_snapshot_objects()

    assert deleted == 1
    assert removed == ["orphan/v1.png"]
    assert [u[0] for u in updates] == ["ride_route_snapshot_objects"]


# ---------------------------------------------------------------------------
# run_retention_purge_tick — response-shape fallback branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_tick_extracts_data_from_plain_dict_response():
    """`getattr(res, "data", None)` is None when `res` itself is a plain
    dict (not an APIResponse-like object) — the `isinstance(res, dict)`
    fallback picks up `res["data"]` instead. Exercised for both the main
    purge and the trip-route-geometry call."""
    from utils.retention_purge import run_retention_purge_tick

    main_payload = {"data": {"dry_run": True, "rides_deleted": 0, "dsar_users_skipped_fk": 0}}
    route_payload = {"data": {"dry_run": True, "ride_routes_anonymized": 0}}
    supa = _make_supa([main_payload, route_payload])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        result = await run_retention_purge_tick(dry_run=True)

    assert result == main_payload["data"]


@pytest.mark.asyncio
async def test_run_tick_reraises_on_trip_route_geometry_rpc_error():
    from utils.retention_purge import run_retention_purge_tick

    main_ok = MagicMock(data={"dry_run": True, "rides_deleted": 0})
    supa = _make_supa([main_ok, RuntimeError("route geometry rpc down")])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        with pytest.raises(RuntimeError, match="route geometry rpc down"):
            await run_retention_purge_tick(dry_run=True)


@pytest.mark.asyncio
async def test_run_tick_raises_on_invalid_trip_route_geometry_response_shape():
    """`purge_trip_route_geometry`'s response-shape guard raises for a
    malformed response — see
    `test_run_tick_raises_consistently_for_both_malformed_responses` for
    the side-by-side comparison confirming `purge_pii_retention`'s guard
    now does the same (fixed 2026-08-03)."""
    from utils.retention_purge import run_retention_purge_tick

    main_ok = MagicMock(data={"dry_run": True, "rides_deleted": 0})
    route_bad = MagicMock(data="not a dict")
    supa = _make_supa([main_ok, route_bad])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        with pytest.raises(RuntimeError, match="purge_trip_route_geometry returned an invalid response"):
            await run_retention_purge_tick(dry_run=True)


@pytest.mark.asyncio
async def test_run_tick_raises_consistently_for_both_malformed_responses():
    """Fixed (2026-08-03, application code change — see
    docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 7):
    `run_retention_purge_tick` validates two RPC responses with the same
    shape check (`isinstance(data, dict)`). Previously `purge_pii_retention`
    malformed -> logged and returned None silently (no raise, no metric,
    loop looked "green"), while `purge_trip_route_geometry` malformed ->
    logged AND raised (loop's except-handler fires, metric increments).
    That asymmetry meant the *regulatory* PII/DSAR purge could silently
    stop running with only a single ERROR log line. Both halves now raise
    consistently.
    """
    from utils.retention_purge import run_retention_purge_tick

    # Half 1: main purge_pii_retention malformed -> now raises too.
    main_bad = MagicMock(data="not a dict")
    supa_soft = _make_supa([main_bad])
    with (
        patch("utils.retention_purge.supabase", supa_soft),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        with pytest.raises(RuntimeError, match="purge_pii_retention returned an invalid response"):
            await run_retention_purge_tick(dry_run=True)

    # Half 2: trip-route-geometry malformed (main purge is fine) -> still raises.
    main_ok = MagicMock(data={"dry_run": True, "rides_deleted": 0})
    route_bad = MagicMock(data="not a dict")
    supa_hard = _make_supa([main_ok, route_bad])
    with (
        patch("utils.retention_purge.supabase", supa_hard),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
    ):
        with pytest.raises(RuntimeError):
            await run_retention_purge_tick(dry_run=True)


@pytest.mark.asyncio
async def test_run_tick_logs_skipped_fk_count_without_raising():
    """A DSAR account past its 7y hard-delete window that's still blocked
    by a residual FK is a retention gap that must be surfaced loudly
    (`logger.error`) but must not abort the tick — the rest of the purge
    (rides/location/messages/tokens) already committed."""
    from utils.retention_purge import run_retention_purge_tick

    main_ok = MagicMock(data={"dry_run": True, "rides_deleted": 5, "dsar_users_skipped_fk": 2})
    route_ok = MagicMock(data={"dry_run": True, "ride_routes_anonymized": 0})
    supa = _make_supa([main_ok, route_ok])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
        patch("utils.retention_purge.logger") as mock_logger,
    ):
        result = await run_retention_purge_tick(dry_run=True)

    assert result == main_ok.data
    error_calls = [c for c in mock_logger.error.call_args_list if "skipped on a residual FK" in c.args[0]]
    assert len(error_calls) == 1
    assert error_calls[0].args[1] == 2


@pytest.mark.asyncio
async def test_run_tick_no_skipped_fk_log_when_zero():
    from utils.retention_purge import run_retention_purge_tick

    main_ok = MagicMock(data={"dry_run": True, "rides_deleted": 5, "dsar_users_skipped_fk": 0})
    route_ok = MagicMock(data={"dry_run": True, "ride_routes_anonymized": 0})
    supa = _make_supa([main_ok, route_ok])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
        patch("utils.retention_purge.logger") as mock_logger,
    ):
        await run_retention_purge_tick(dry_run=True)

    error_calls = [c for c in mock_logger.error.call_args_list if "residual FK" in c.args[0]]
    assert error_calls == []


# ---------------------------------------------------------------------------
# run_retention_purge_tick — post-storage-delete second geometry purge
# (only reachable when dry_run=False AND snapshot objects were deleted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_tick_reraises_on_post_storage_geometry_rpc_error():
    from utils.retention_purge import run_retention_purge_tick

    main_ok = MagicMock(data={"dry_run": False, "rides_deleted": 0})
    route_ok = MagicMock(data={"dry_run": False, "ride_routes_anonymized": 0})
    supa = _make_supa([main_ok, route_ok, RuntimeError("post-storage rpc down")])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=3)),
    ):
        with pytest.raises(RuntimeError, match="post-storage rpc down"):
            await run_retention_purge_tick(dry_run=False)


@pytest.mark.asyncio
async def test_run_tick_raises_on_invalid_post_storage_geometry_response_shape():
    from utils.retention_purge import run_retention_purge_tick

    main_ok = MagicMock(data={"dry_run": False, "rides_deleted": 0})
    route_ok = MagicMock(data={"dry_run": False, "ride_routes_anonymized": 0})
    route_bad_post_storage = MagicMock(data=None)
    supa = _make_supa([main_ok, route_ok, route_bad_post_storage])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=1)),
    ):
        with pytest.raises(RuntimeError, match="post-storage purge_trip_route_geometry returned an invalid response"):
            await run_retention_purge_tick(dry_run=False)


@pytest.mark.asyncio
async def test_run_tick_reruns_geometry_purge_after_successful_snapshot_deletion():
    """Happy path for the `dry_run=False and deleted_snapshot_objects`
    branch: the geometry purge is invoked a *second* time so `ride_routes`
    rows only get marked anonymous after their Storage objects are
    confirmed gone (per the in-code comment at line ~202-203)."""
    from utils.retention_purge import run_retention_purge_tick

    main_ok = MagicMock(data={"dry_run": False, "rides_deleted": 1})
    route_first = MagicMock(data={"dry_run": False, "ride_routes_anonymized": 0})
    route_second = MagicMock(data={"dry_run": False, "ride_routes_anonymized": 4})
    supa = _make_supa([main_ok, route_first, route_second])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=2)) as del_mock,
    ):
        result = await run_retention_purge_tick(dry_run=False)

    assert result == main_ok.data
    del_mock.assert_awaited_once()
    assert supa.rpc.call_count == 3  # main, first geometry, post-storage geometry re-run


@pytest.mark.asyncio
async def test_run_tick_skips_snapshot_deletion_entirely_in_dry_run():
    """dry_run=True must never touch Storage — `_delete_expired_route_snapshot_objects`
    is only called when dry_run is False."""
    from utils.retention_purge import run_retention_purge_tick

    main_ok = MagicMock(data={"dry_run": True, "rides_deleted": 0})
    route_ok = MagicMock(data={"dry_run": True, "ride_routes_anonymized": 0})
    supa = _make_supa([main_ok, route_ok])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock()) as del_mock,
    ):
        await run_retention_purge_tick(dry_run=True)

    del_mock.assert_not_awaited()
    assert supa.rpc.call_count == 2  # only main + first geometry call, no post-storage re-run


@pytest.mark.asyncio
async def test_run_tick_skips_post_storage_rerun_when_nothing_was_deleted():
    """dry_run=False but zero snapshot objects were actually deleted this
    tick — the post-storage re-run is skipped (nothing changed that would
    require re-marking `ride_routes` anonymous)."""
    from utils.retention_purge import run_retention_purge_tick

    main_ok = MagicMock(data={"dry_run": False, "rides_deleted": 0})
    route_ok = MagicMock(data={"dry_run": False, "ride_routes_anonymized": 0})
    supa = _make_supa([main_ok, route_ok])

    with (
        patch("utils.retention_purge.supabase", supa),
        patch("utils.retention_purge.run_sync", _identity_run_sync()),
        patch("utils.retention_purge._delete_expired_route_snapshot_objects", AsyncMock(return_value=0)),
    ):
        await run_retention_purge_tick(dry_run=False)

    assert supa.rpc.call_count == 2  # no third (post-storage) rpc call


# ---------------------------------------------------------------------------
# _tick — leader-lock acquired / not-acquired split
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_runs_purge_when_lock_acquired():
    from utils import retention_purge

    with (
        patch("utils.retention_purge.redis_set_nx", AsyncMock(return_value=True)),
        patch("utils.retention_purge.run_retention_purge_tick", AsyncMock()) as run_tick,
        patch("utils.retention_purge._pod_id", return_value="pod-A:123"),
    ):
        await retention_purge._tick()

    run_tick.assert_awaited_once_with(dry_run=False)


@pytest.mark.asyncio
async def test_tick_skips_purge_when_lock_not_acquired():
    from utils import retention_purge

    with (
        patch("utils.retention_purge.redis_set_nx", AsyncMock(return_value=False)),
        patch("utils.retention_purge.run_retention_purge_tick", AsyncMock()) as run_tick,
    ):
        await retention_purge._tick()

    run_tick.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_passes_pod_id_and_lock_key_to_redis_set_nx():
    from utils import retention_purge

    set_nx = AsyncMock(return_value=True)
    with (
        patch("utils.retention_purge.redis_set_nx", set_nx),
        patch("utils.retention_purge.run_retention_purge_tick", AsyncMock()),
    ):
        await retention_purge._tick()

    args = set_nx.await_args.args
    assert args[0] == retention_purge._LOCK_KEY
    assert args[2] == retention_purge._LOCK_TTL_SECONDS


# ---------------------------------------------------------------------------
# retention_purge_loop — thin sanity check that _tick's exceptions and the
# heartbeat still work end to end (test_p3_loop_jitter_metrics.py owns the
# detailed jitter/metric assertions; this just closes any remaining gap in
# the loop body itself).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_survives_tick_exception_and_records_heartbeat():
    from utils import retention_purge

    async def _raise(*_a, **_kw):
        raise RuntimeError("tick blew up")

    heartbeat = MagicMock()

    async def fake_sleep(_secs):
        raise asyncio.CancelledError()

    with (
        patch("utils.retention_purge._tick", _raise),
        patch("utils.retention_purge._record_heartbeat", heartbeat),
        patch("utils.retention_purge.asyncio.sleep", fake_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await retention_purge.retention_purge_loop()

    heartbeat.assert_called_once_with("retention_purge (24h)")


# ---------------------------------------------------------------------------
# Module-load-time import fallbacks (lines 37-39, 45)
# ---------------------------------------------------------------------------


class TestLoopMonitorImportFallback:
    """Lines 37-39: `_record_heartbeat` resolution at module import time.

    retention_purge.py tries only the absolute `utils.loop_monitor` import
    (unlike some sibling modules that also try a relative `.loop_monitor`
    first) — if that fails, a local no-op stub is defined instead.
    `sys.modules["utils.loop_monitor"] = None` is the standard trick
    (already used elsewhere in this suite, e.g. test_data_export_purge_coverage.py,
    test_push_retry_coverage.py, test_allowance_reset_coverage.py) to force
    the import machinery to raise ImportError for a name without the module
    needing to be genuinely absent from the environment.
    """

    @contextmanager
    def _reloaded_with(self, utils_loop_monitor):
        import backend.utils.retention_purge as mod

        orig = sys.modules.get("utils.loop_monitor")
        sys.modules["utils.loop_monitor"] = utils_loop_monitor
        try:
            importlib.reload(mod)
            yield mod
        finally:
            if orig is not None:
                sys.modules["utils.loop_monitor"] = orig
            else:
                sys.modules.pop("utils.loop_monitor", None)
            importlib.reload(mod)  # restore real state for every later test

    def test_local_noop_stub_used_when_loop_monitor_import_fails(self):
        with self._reloaded_with(utils_loop_monitor=None) as reloaded:
            # Must be the locally-defined stub, not left unbound.
            assert reloaded._record_heartbeat.__module__ == reloaded.__name__
            assert reloaded._record_heartbeat("retention_purge (24h)") is None  # covers the `pass` body


class TestPackagedImportPath:
    """Line 45 (and the try-block success path at line 44): when this
    module is imported via its full package path
    (`backend.utils.retention_purge`, i.e. how `python -m backend.server`
    loads it per lifespan.py), the *relative* `from ..db_supabase import
    run_sync, supabase` and `from .redis_client import redis_set_nx`
    imports both succeed — unlike the bare `utils.retention_purge` import
    path used throughout the rest of this file, where the relative import
    has no parent package and falls through to the absolute fallback
    import instead (lines 46-48)."""

    def test_full_package_import_resolves_run_sync_and_redis_set_nx(self):
        from backend.db_supabase import run_sync as expected_run_sync
        from backend.utils import retention_purge as packaged
        from backend.utils.redis_client import redis_set_nx as expected_redis_set_nx

        assert packaged.run_sync is expected_run_sync
        assert packaged.redis_set_nx is expected_redis_set_nx
