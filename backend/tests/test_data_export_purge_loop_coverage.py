"""Additional coverage for utils/data_export_purge.py.

Complements tests/test_data_export_purge.py (which covers the _tick happy
path, the no-expired-rows no-op, and per-row failure isolation) with:

  - the `supabase is None` early-out in _tick
  - a row missing `storage_path` / `id` (skipped without ever touching
    Storage or the DB -- the PIPEDA-relevant purge must never guess a path)
  - the outer data_export_purge_loop: both tables ticked per iteration,
    each tick's own exception guard is independent (a failure purging
    `data_export_objects` must not skip the `data_transfer_export_jobs`
    sweep), and the heartbeat is recorded every iteration.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

_STUBS = [
    "supabase",
    "stripe",
    "gotrue",
    "postgrest",
    "realtime",
    "firebase_admin",
    "firebase_admin.auth",
    "firebase_admin.credentials",
    "firebase_admin.messaging",
    "twilio",
    "twilio.rest",
    "slowapi",
    "slowapi.errors",
    "slowapi.util",
    "redis",
    "redis.asyncio",
    "jwt",
]
for _m in _STUBS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()


async def test_tick_noop_when_supabase_none():
    from backend.utils import data_export_purge as mod

    with patch.object(mod, "supabase", None):
        # Must return cleanly without ever calling run_sync.
        with patch.object(mod, "run_sync", AsyncMock()) as run_sync:
            await mod._tick("data_export_objects", mod._BUCKET)
        run_sync.assert_not_awaited()


async def test_tick_skips_row_missing_storage_path_or_id():
    from backend.utils import data_export_purge as mod

    fake_sb = MagicMock()
    rows = [
        {"id": "no-path"},  # missing storage_path
        {"storage_path": "exports/u1/x.zip"},  # missing id
    ]
    (
        fake_sb.table.return_value.select.return_value.is_.return_value.not_.is_.return_value.lt.return_value.limit.return_value.execute.return_value.data
    ) = rows

    async def fake_run_sync(fn):
        return fn()

    with (
        patch.object(mod, "supabase", fake_sb),
        patch.object(mod, "run_sync", AsyncMock(side_effect=fake_run_sync)),
    ):
        await mod._tick("data_export_objects", mod._BUCKET)

    # Neither malformed row should ever reach Storage removal or a DB update
    # -- a purge loop must never guess at a path or mark an unidentified row.
    fake_sb.storage.from_.return_value.remove.assert_not_called()
    fake_sb.table.return_value.update.assert_not_called()


async def test_loop_ticks_both_tables_each_iteration_and_records_heartbeat():
    from backend.utils import data_export_purge as mod

    tick_calls = []

    async def fake_tick(table, bucket):
        tick_calls.append((table, bucket))

    sleep_calls = {"n": 0}

    async def fake_sleep(_seconds):
        sleep_calls["n"] += 1
        raise asyncio.CancelledError()

    with (
        patch.object(mod, "_tick", AsyncMock(side_effect=fake_tick)),
        patch.object(mod, "_record_heartbeat") as heartbeat,
        patch.object(mod.asyncio, "sleep", AsyncMock(side_effect=fake_sleep)),
    ):
        try:
            await mod.data_export_purge_loop()
        except asyncio.CancelledError:
            pass

    assert tick_calls == [
        ("data_export_objects", mod._BUCKET),
        (mod._DATA_TRANSFER_TABLE, mod._DATA_TRANSFER_BUCKET),
    ]
    heartbeat.assert_called_once_with("data_export_purge (1h)")
    assert sleep_calls["n"] == 1


async def test_loop_second_tick_still_runs_when_first_tick_raises():
    """The two _tick calls are wrapped in independent try/except blocks --
    a failure purging data_export_objects must not skip the data-transfer
    sweep on the same iteration."""
    from backend.utils import data_export_purge as mod

    tick_calls = []

    async def fake_tick(table, bucket):
        tick_calls.append(table)
        if table == "data_export_objects":
            raise RuntimeError("storage down")

    sleep_calls = {"n": 0}

    async def fake_sleep(_seconds):
        sleep_calls["n"] += 1
        raise asyncio.CancelledError()

    with (
        patch.object(mod, "_tick", AsyncMock(side_effect=fake_tick)),
        patch.object(mod, "_record_heartbeat"),
        patch.object(mod.asyncio, "sleep", AsyncMock(side_effect=fake_sleep)),
    ):
        try:
            await mod.data_export_purge_loop()
        except asyncio.CancelledError:
            pass

    assert tick_calls == ["data_export_objects", mod._DATA_TRANSFER_TABLE]


async def test_loop_records_heartbeat_when_second_tick_raises():
    """Symmetric to the case above: a failure purging the data-transfer
    table's own exception guard must still let the loop reach the heartbeat
    and the sleep, not propagate out of data_export_purge_loop."""
    from backend.utils import data_export_purge as mod

    tick_calls = []

    async def fake_tick(table, bucket):
        tick_calls.append(table)
        if table == mod._DATA_TRANSFER_TABLE:
            raise RuntimeError("data-transfer storage down")

    sleep_calls = {"n": 0}

    async def fake_sleep(_seconds):
        sleep_calls["n"] += 1
        raise asyncio.CancelledError()

    with (
        patch.object(mod, "_tick", AsyncMock(side_effect=fake_tick)),
        patch.object(mod, "_record_heartbeat") as heartbeat,
        patch.object(mod.asyncio, "sleep", AsyncMock(side_effect=fake_sleep)),
    ):
        try:
            await mod.data_export_purge_loop()
        except asyncio.CancelledError:
            pass

    assert tick_calls == ["data_export_objects", mod._DATA_TRANSFER_TABLE]
    heartbeat.assert_called_once_with("data_export_purge (1h)")
