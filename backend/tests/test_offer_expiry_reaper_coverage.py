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


async def test_loop_survives_a_redis_lock_error_and_still_runs_the_tick():
    """2026-08-11 P1 fix: redis_set_nx now raises on a real Redis error
    instead of silently falling back per-replica. Previously this call sat
    directly in `while True:` with no surrounding try/except -- an
    unhandled exception here would have killed this durable-backstop loop
    task permanently, defeating its whole purpose."""
    tick = AsyncMock()

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch.object(reaper, "redis_set_nx", AsyncMock(side_effect=ConnectionError("redis down"))),
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
# ACTION_ITEMS B21: throttle-lock TTL must expire before the loop's own next
# wake, or the pod that ran the last tick fails its own SET NX and skips a
# full interval — see utils/ledger_projection.py's _LOCK_TTL_SECONDS for the
# sibling fix this mirrors.
# ---------------------------------------------------------------------------


def test_lock_ttl_expires_before_the_earliest_next_wake():
    """Stated as an invariant so a future tuning change to the interval or the
    jitter fraction can't silently re-break the cadence."""
    jitter_fraction = 0.1  # matches the loop's `delta = interval * 0.1`
    min_sleep = reaper.REAP_INTERVAL_SECONDS * (1 - jitter_fraction)
    lock_ttl = int(reaper.REAP_INTERVAL_SECONDS * 0.85)
    assert lock_ttl < min_sleep, (
        f"lock TTL {lock_ttl}s must expire before the shortest possible sleep "
        f"({min_sleep}s), or the loop skips its own next tick"
    )


async def test_loop_reacquires_its_own_lock_on_the_next_wake():
    """REGRESSION: with the old TTL = 2x interval against a 1x interval sleep,
    the pod that ran the last tick woke to find its OWN key still alive,
    failed SET NX, and slept another full interval — so a loop documented as
    "10s" actually ticked every ~20s.

    Simulated against a virtual clock with real SET NX EX semantics, jitter
    pinned to its most adverse value (the SHORTEST sleep) — the case the TTL
    has to survive. Mirrors ledger_projection.py's loop-cadence regression test.
    """
    clock = {"t": 0.0}
    expiries: dict[str, float] = {}
    wakes = {"n": 0}

    async def fake_set_nx(key, _value, ttl):
        exp = expiries.get(key)
        if exp is not None and exp > clock["t"]:
            return False
        expiries[key] = clock["t"] + ttl
        return True

    async def fake_sleep(secs):
        clock["t"] += secs
        wakes["n"] += 1
        if wakes["n"] >= 2:
            raise asyncio.CancelledError

    with (
        patch.object(reaper, "redis_set_nx", side_effect=fake_set_nx),
        patch.object(reaper, "_reap_tick", AsyncMock()) as tick,
        patch.object(reaper, "_record_heartbeat", lambda name: None),
        patch.object(reaper.asyncio, "sleep", side_effect=fake_sleep),
        # uniform(-delta, +delta) -> -delta: the shortest sleep the loop can take.
        patch.object(reaper.random, "uniform", side_effect=lambda lo, _hi: lo),
    ):
        try:
            await reaper.offer_expiry_reaper_loop()
        except asyncio.CancelledError:
            pass

    assert tick.await_count == 2, (
        "the single replica must tick once per interval; a TTL longer than the "
        "minimum sleep makes it skip its own next wake and halves the cadence"
    )


# ---------------------------------------------------------------------------
# _pod_id
# ---------------------------------------------------------------------------


def test_pod_id_shape():
    pod_id = reaper._pod_id()
    assert ":" in pod_id
    host, _, pid = pod_id.rpartition(":")
    assert host
    assert pid.isdigit()
