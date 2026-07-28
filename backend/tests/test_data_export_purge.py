"""Unit tests for the data-export purge loop (utils/data_export_purge.py).

Asserts the hourly tick deletes expired export objects from the
`data-exports` Storage bucket and marks their tracking rows deleted, with all
heavy deps mocked (no live Supabase).
"""

from __future__ import annotations

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


def _fake_supabase_with_rows(rows):
    fake = MagicMock()
    # Configure the select chain used by _tick to return `rows`. Must mirror
    # _tick's actual chain exactly (.is_().not_.is_().lt()...) — MagicMock
    # auto-creates any unconfigured attribute access as a fresh child mock,
    # so a chain shape mismatch here silently no-ops instead of raising.
    (
        fake.table.return_value.select.return_value.is_.return_value.not_.is_.return_value.lt.return_value.limit.return_value.execute.return_value.data
    ) = rows
    return fake


async def test_tick_deletes_expired_objects_and_marks_rows():
    from backend.utils import data_export_purge as mod

    rows = [
        {"id": "o1", "storage_path": "exports/u1/a.zip"},
        {"id": "o2", "storage_path": "exports/u2/b.zip"},
    ]
    fake_sb = _fake_supabase_with_rows(rows)

    async def fake_run_sync(fn):
        return fn()

    with (
        patch.object(mod, "supabase", fake_sb),
        patch.object(mod, "run_sync", AsyncMock(side_effect=fake_run_sync)),
    ):
        await mod._tick("data_export_objects", mod._BUCKET)

    # Each object's Storage path was removed.
    remove = fake_sb.storage.from_.return_value.remove
    removed = [call.args[0] for call in remove.call_args_list]
    assert ["exports/u1/a.zip"] in removed
    assert ["exports/u2/b.zip"] in removed
    # Each tracking row was marked deleted (update called once per row).
    assert fake_sb.table.return_value.update.call_count == 2


async def test_tick_noop_when_no_expired_rows():
    from backend.utils import data_export_purge as mod

    fake_sb = _fake_supabase_with_rows([])

    async def fake_run_sync(fn):
        return fn()

    with (
        patch.object(mod, "supabase", fake_sb),
        patch.object(mod, "run_sync", AsyncMock(side_effect=fake_run_sync)),
    ):
        await mod._tick("data_export_objects", mod._BUCKET)

    fake_sb.storage.from_.return_value.remove.assert_not_called()


async def test_tick_isolates_per_row_failure():
    """A Storage-delete failure on one row must not abort the others, and must
    NOT mark that row deleted (so the next tick retries it)."""
    from backend.utils import data_export_purge as mod

    rows = [
        {"id": "bad", "storage_path": "exports/u1/bad.zip"},
        {"id": "good", "storage_path": "exports/u2/good.zip"},
    ]
    fake_sb = _fake_supabase_with_rows(rows)

    async def fake_run_sync(fn):
        return fn()

    # Make remove raise only for the bad path; the thunk's exception propagates
    # through run_sync and must be caught per-row inside _tick.
    def _remove(paths):
        if paths == ["exports/u1/bad.zip"]:
            raise RuntimeError("storage down")
        return None

    fake_sb.storage.from_.return_value.remove.side_effect = _remove

    with (
        patch.object(mod, "supabase", fake_sb),
        patch.object(mod, "run_sync", AsyncMock(side_effect=fake_run_sync)),
    ):
        await mod._tick("data_export_objects", mod._BUCKET)

    # The good row was still processed (update called for it) despite the bad one.
    assert fake_sb.table.return_value.update.called
