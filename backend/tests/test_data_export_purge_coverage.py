"""Coverage top-up for utils/data_export_purge.py (A1c, Sub-tier C).

Hourly background loop that purges expired personal-data export ZIPs from the
`data-exports` Storage bucket (PIPEDA data minimization) and the admin Data
Transfer module's `data-transfer-exports` bucket, using the same shape. A
dedicated test file (`test_data_export_purge.py`) already covers the core
`_tick` happy-path / no-op / per-row-failure-isolation branches; this file
fills the remaining gap: the outer `data_export_purge_loop`, the
`supabase is None` short-circuit, the malformed-row skip, and the
`loop_monitor` import-fallback stanza (module-load-time try/except/try/except).

Test-only change — no application code modified.

FOUND NOT FIXED (compliance-adjacent, flagged per instructions, not fixed
here): `_tick`'s row loop (data_export_purge.py:94-95) does
`if not path or not row_id: continue` with NO log call at all — unlike the
per-row Storage/DB failure path a few lines below, which does
`logger.error(..., exc_info=True)`. CLAUDE.md's "Do not silently swallow
errors" section requires DB/auth/payment-adjacent errors to surface loudly;
a malformed tracking row (missing storage_path or id) is silently dropped on
the floor every single hourly tick, forever, with no error/warning log and no
metric. It never gets marked `deleted_at` (so it isn't "fixed" by attrition)
and it counts against the `_BATCH = 200` per-tick page limit, so enough
malformed rows could quietly starve legitimately-expired rows from ever being
processed in a given hour. `test_tick_skips_row_missing_path_or_id` below
pins this actual (silent) behavior; it does not add the missing log line.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _fake_supabase_with_rows(rows):
    fake = MagicMock()
    (
        fake.table.return_value.select.return_value.is_.return_value.not_.is_.return_value.lt.return_value.limit.return_value.execute.return_value.data
    ) = rows
    return fake


async def _fake_run_sync(fn):
    return fn()


class TestTickEdgeCases:
    @pytest.mark.anyio
    async def test_tick_returns_early_when_supabase_is_none(self, monkeypatch):
        """Line 70-71: supabase not configured -> no query attempted at all."""
        from backend.utils import data_export_purge as mod

        monkeypatch.setattr(mod, "supabase", None)
        run_sync = AsyncMock()
        monkeypatch.setattr(mod, "run_sync", run_sync)

        await mod._tick("data_export_objects", mod._BUCKET)

        run_sync.assert_not_awaited()

    @pytest.mark.anyio
    async def test_tick_skips_row_missing_path_or_id(self, monkeypatch):
        """Line 94-95: a malformed row (no storage_path, or no id) is silently
        skipped -- no Storage remove, no DB update, and (see module docstring
        FOUND NOT FIXED note) no log line either. A well-formed row later in
        the same batch is still processed normally."""
        from backend.utils import data_export_purge as mod

        rows = [
            {"id": "no-path", "storage_path": None},
            {"id": None, "storage_path": "exports/u1/orphan.zip"},
            {"id": "ok", "storage_path": "exports/u2/good.zip"},
        ]
        fake_sb = _fake_supabase_with_rows(rows)
        monkeypatch.setattr(mod, "supabase", fake_sb)
        monkeypatch.setattr(mod, "run_sync", AsyncMock(side_effect=_fake_run_sync))

        await mod._tick("data_export_objects", mod._BUCKET)

        remove = fake_sb.storage.from_.return_value.remove
        removed = [call.args[0] for call in remove.call_args_list]
        # Only the well-formed row's object was ever removed.
        assert removed == [["exports/u2/good.zip"]]
        # Only the well-formed row's tracking row was marked deleted.
        assert fake_sb.table.return_value.update.call_count == 1


class TestDataExportPurgeLoop:
    @pytest.mark.anyio
    async def test_loop_ticks_both_tables_and_sleeps(self, monkeypatch):
        """Lines 56-66: one loop iteration must tick both the rider
        data-export table/bucket and the admin data-transfer table/bucket,
        record a heartbeat, then sleep -- forced to exit via CancelledError
        from the mocked sleep so the (intentionally infinite) loop returns."""
        from backend.utils import data_export_purge as mod

        tick = AsyncMock()
        monkeypatch.setattr(mod, "_tick", tick)
        heartbeat = MagicMock()
        monkeypatch.setattr(mod, "_record_heartbeat", heartbeat)

        async def fake_sleep(secs):
            assert secs == mod._INTERVAL_SECONDS
            raise asyncio.CancelledError()

        with patch.object(mod.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await mod.data_export_purge_loop()

        assert tick.await_args_list == [
            (("data_export_objects", mod._BUCKET),),
            ((mod._DATA_TRANSFER_TABLE, mod._DATA_TRANSFER_BUCKET),),
        ]
        heartbeat.assert_called_once_with("data_export_purge (1h)")

    @pytest.mark.anyio
    async def test_loop_survives_both_ticks_failing(self, monkeypatch):
        """A tick failure on either table must be caught and logged per-table
        (not let one failure abort the other), and the heartbeat must still
        record so the loop-watchdog doesn't flag this loop as stuck."""
        from backend.utils import data_export_purge as mod

        tick = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(mod, "_tick", tick)
        heartbeat = MagicMock()
        monkeypatch.setattr(mod, "_record_heartbeat", heartbeat)

        async def fake_sleep(secs):
            raise asyncio.CancelledError()

        with patch.object(mod.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await mod.data_export_purge_loop()

        assert tick.await_count == 2
        heartbeat.assert_called_once_with("data_export_purge (1h)")

    @pytest.mark.anyio
    async def test_loop_survives_first_tick_failing_second_succeeding(self, monkeypatch):
        """The two ticks are independently try/except-guarded (lines 57-64):
        a failure on the first (rider export) table must not prevent the
        second (admin data-transfer) table from still being attempted."""
        from backend.utils import data_export_purge as mod

        tick = AsyncMock(side_effect=[RuntimeError("boom"), None])
        monkeypatch.setattr(mod, "_tick", tick)
        monkeypatch.setattr(mod, "_record_heartbeat", MagicMock())

        async def fake_sleep(secs):
            raise asyncio.CancelledError()

        with patch.object(mod.asyncio, "sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await mod.data_export_purge_loop()

        assert tick.await_args_list == [
            (("data_export_objects", mod._BUCKET),),
            ((mod._DATA_TRANSFER_TABLE, mod._DATA_TRANSFER_BUCKET),),
        ]


class TestLoopMonitorImportFallback:
    """Lines 33-41: module-load-time `_record_heartbeat` resolution.

    Order tried: relative `.loop_monitor` (package context) -> absolute
    `utils.loop_monitor` (top-level `python -m backend.server` context) ->
    a local no-op stub if both fail. `sys.modules[name] = None` is the
    standard trick (already used elsewhere in this suite, e.g.
    test_push_retry_coverage.py) to force the import machinery to raise
    ImportError for a given fully-qualified name without needing the module
    to be genuinely absent from the environment.
    """

    @contextmanager
    def _reloaded_with(self, backend_lm, utils_lm):
        """Reload data_export_purge with sys.modules['backend.utils.loop_monitor']
        and sys.modules['utils.loop_monitor'] forced to the given values
        (None to force ImportError, a fake module to force success), yield
        the reloaded module for the caller to assert against, then restore
        the real cache entries and reload again on exit so later tests see
        the real module state regardless of whether the assertions passed."""
        import backend.utils.data_export_purge as mod

        orig_backend_lm = sys.modules.get("backend.utils.loop_monitor")
        orig_utils_lm = sys.modules.get("utils.loop_monitor")
        sys.modules["backend.utils.loop_monitor"] = backend_lm
        sys.modules["utils.loop_monitor"] = utils_lm
        try:
            importlib.reload(mod)
            yield mod
        finally:
            if orig_backend_lm is not None:
                sys.modules["backend.utils.loop_monitor"] = orig_backend_lm
            else:
                sys.modules.pop("backend.utils.loop_monitor", None)
            if orig_utils_lm is not None:
                sys.modules["utils.loop_monitor"] = orig_utils_lm
            else:
                sys.modules.pop("utils.loop_monitor", None)
            importlib.reload(mod)

    def test_absolute_fallback_used_when_relative_import_fails(self):
        """Line 37: relative import fails, absolute `utils.loop_monitor`
        import succeeds -> that module's record_heartbeat is bound directly
        (no local stub defined)."""
        fake_hb = MagicMock()
        fake_utils_loop_monitor = types.ModuleType("utils.loop_monitor")
        fake_utils_loop_monitor.record_heartbeat = fake_hb

        with self._reloaded_with(backend_lm=None, utils_lm=fake_utils_loop_monitor) as reloaded:
            assert reloaded._record_heartbeat is fake_hb

    def test_local_noop_stub_used_when_both_imports_fail(self):
        """Lines 40-41: both the relative and absolute imports fail -> a
        local no-op `_record_heartbeat(name)` is defined and must not raise
        when called (covers the `pass` body too)."""
        with self._reloaded_with(backend_lm=None, utils_lm=None) as reloaded:
            # Must be the locally-defined stub, not left unbound / imported.
            assert reloaded._record_heartbeat.__module__ == reloaded.__name__
            assert reloaded._record_heartbeat("data_export_purge (1h)") is None
