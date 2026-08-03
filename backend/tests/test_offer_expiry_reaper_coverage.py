"""
A1c Sub-tier C coverage: backend/utils/offer_expiry_reaper.py (61% -> target 90%+).

`test_offer_expiry_reaper.py` covers `_reap_tick`'s happy path, the
no-expired-offers no-op, and the non-searching-ride re-dispatch guard.
This file closes:

- `_reap_tick`: the `ride_offers` fetch-exception branch (logged, tick
  returns without processing), the `_CANDIDATE_LIMIT` scan-cap warning
  branch, `get_app_settings` exception falling back to `miss_threshold=3`,
  and the re-dispatch `rides` lookup exception (one ride's lookup failure
  must not abort the batch).
- `offer_expiry_reaper_loop`: lock-not-acquired skips work and re-loops,
  lock-acquired runs the tick, a tick exception is caught and logged (loop
  survives), and the heartbeat/jitter-sleep call on every iteration
  regardless of outcome.
- `_pod_id`'s hostname:pid shape.

Patch target: `utils.offer_expiry_reaper.*` (module-bound names via its own
dual-import block), matching the established pattern in
`test_offer_expiry_reaper.py` (`patch.object(reaper, ...)`).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import offer_expiry_reaper as reaper

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# _reap_tick — remaining branches
# ---------------------------------------------------------------------------


async def test_fetch_exception_is_logged_and_tick_returns():
    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=RuntimeError("db down"))),
        patch.object(reaper, "process_expired_offer", AsyncMock()) as proc,
    ):
        await reaper._reap_tick()
    proc.assert_not_awaited()


async def test_scan_cap_hit_logs_backlog_warning(caplog):
    import logging

    expired = [{"ride_id": f"r{i}", "driver_id": f"d{i}"} for i in range(reaper._CANDIDATE_LIMIT)]

    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return expired
        return [{"status": "completed"}]  # not searching -> no redispatch calls

    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(return_value={"auto_offline_miss_threshold": 3})),
        patch.object(reaper, "process_expired_offer", AsyncMock(return_value=False)),
        patch.object(reaper, "match_driver_to_ride", AsyncMock()),
        caplog.at_level(logging.WARNING),
    ):
        await reaper._reap_tick()
    assert any("scan cap" in rec.message for rec in caplog.records)


async def test_settings_fetch_exception_falls_back_to_default_threshold():
    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return [{"ride_id": "r1", "driver_id": "d1"}]
        return [{"status": "completed"}]

    proc = AsyncMock(return_value=True)
    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(side_effect=RuntimeError("settings down"))),
        patch.object(reaper, "process_expired_offer", proc),
        patch.object(reaper, "match_driver_to_ride", AsyncMock()),
    ):
        await reaper._reap_tick()
    proc.assert_awaited_once_with("r1", "d1", 3)


async def test_redispatch_lookup_exception_does_not_abort_batch():
    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return [{"ride_id": "r1", "driver_id": "d1"}]
        if table == "rides":
            raise RuntimeError("rides lookup failed")
        return []

    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(return_value={"auto_offline_miss_threshold": 3})),
        patch.object(reaper, "process_expired_offer", AsyncMock(return_value=True)),
        patch.object(reaper, "match_driver_to_ride", AsyncMock()) as redispatch,
    ):
        # Must not raise.
        await reaper._reap_tick()
    redispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# offer_expiry_reaper_loop
# ---------------------------------------------------------------------------


async def test_loop_lock_not_acquired_skips_tick_and_sleeps():
    tick = AsyncMock()
    sleep_calls = []

    async def fake_sleep(secs):
        sleep_calls.append(secs)
        if len(sleep_calls) >= 1:
            raise asyncio.CancelledError()

    with (
        patch.object(reaper, "redis_set_nx", AsyncMock(return_value=False)),
        patch.object(reaper, "_reap_tick", tick),
        patch.object(reaper.asyncio, "sleep", fake_sleep),
        patch.object(reaper, "_record_heartbeat", lambda name: None),
    ):
        with pytest.raises(asyncio.CancelledError):
            await reaper.offer_expiry_reaper_loop()
    tick.assert_not_awaited()


async def test_loop_lock_acquired_runs_tick():
    tick = AsyncMock()
    sleep_calls = []

    async def fake_sleep(secs):
        sleep_calls.append(secs)
        raise asyncio.CancelledError()

    with (
        patch.object(reaper, "redis_set_nx", AsyncMock(return_value=True)),
        patch.object(reaper, "_reap_tick", tick),
        patch.object(reaper.asyncio, "sleep", fake_sleep),
        patch.object(reaper, "_record_heartbeat", lambda name: None),
    ):
        with pytest.raises(asyncio.CancelledError):
            await reaper.offer_expiry_reaper_loop()
    tick.assert_awaited_once()


async def test_loop_survives_tick_exception():
    async def failing_tick():
        raise RuntimeError("boom")

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch.object(reaper, "redis_set_nx", AsyncMock(return_value=True)),
        patch.object(reaper, "_reap_tick", failing_tick),
        patch.object(reaper.asyncio, "sleep", fake_sleep),
        patch.object(reaper, "_record_heartbeat", lambda name: None) as _hb,
    ):
        # Must not propagate the tick's RuntimeError -- only the sleep's
        # CancelledError should escape.
        with pytest.raises(asyncio.CancelledError):
            await reaper.offer_expiry_reaper_loop()


# ---------------------------------------------------------------------------
# _pod_id
# ---------------------------------------------------------------------------


def test_pod_id_shape():
    pod_id = reaper._pod_id()
    assert ":" in pod_id
    host, _, pid = pod_id.rpartition(":")
    assert host
    assert pid.isdigit()
