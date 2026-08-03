"""Additional coverage for utils/offer_expiry_reaper.py (A1c, Sub-tier C).

test_offer_expiry_reaper.py already covers the happy path of `_reap_tick`
(expire + re-dispatch, no-op when nothing is overdue, and the
non-searching-ride redispatch guard). This file targets the lines that were
still missing at 61% coverage (66 stmts / 26 missing): `_pod_id`, the
`ride_offers` fetch-failure early return, the `_CANDIDATE_LIMIT` backlog
warning, the `get_app_settings` fallback, the per-ride redispatch
try/except, and the whole `offer_expiry_reaper_loop` (Redis leader-lock
gate, tick-exception handling, jittered sleep).

Uses patch.object on the imported module, matching test_offer_expiry_reaper.py's
convention (robust to the repo's backend./non-backend dual-import module
identity).

Test-only change -- no application code modified.

Fixed (2026-08-03, application code change — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 5): the
`get_app_settings` fallback in `_reap_tick` previously swallowed any
exception with a bare `except Exception: miss_threshold = 3` and emitted
no log at all, violating CLAUDE.md's "Do not silently swallow errors"
rule. Now logs via `logger.error(..., exc_info=True)` before defaulting.
See `test_reap_tick_settings_failure_logs_error_and_defaults` below.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils import offer_expiry_reaper as reaper

pytestmark = pytest.mark.unit


# ── _pod_id ──────────────────────────────────────────────────────────────────


def test_pod_id_combines_hostname_and_pid():
    with (
        patch.object(reaper.socket, "gethostname", return_value="worker-7"),
        patch.object(reaper.os, "getpid", return_value=4242),
    ):
        assert reaper._pod_id() == "worker-7:4242"


# ── _reap_tick: fetch failure (lines 83-85) ─────────────────────────────────


@pytest.mark.anyio
async def test_reap_tick_fetch_failure_is_logged_and_returns_without_raising(caplog):
    get_rows = AsyncMock(side_effect=RuntimeError("connection reset"))
    proc = AsyncMock()
    redispatch = AsyncMock()

    with (
        patch.object(reaper.db, "get_rows", get_rows),
        patch.object(reaper, "process_expired_offer", proc),
        patch.object(reaper, "match_driver_to_ride", redispatch),
        caplog.at_level(logging.ERROR),
    ):
        # Must not raise -- the DB error is caught, logged loudly, and the
        # tick soft-returns (per CLAUDE.md: DB errors must surface loudly,
        # not be swallowed silently -- this is the "surface loudly" half:
        # logger.error with exc_info, not a warning).
        await reaper._reap_tick()

    proc.assert_not_awaited()
    redispatch.assert_not_awaited()
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("failed to fetch expired offers" in r.message for r in error_records)


# ── _reap_tick: backlog warning at the scan cap (line 92) ──────────────────


@pytest.mark.anyio
async def test_reap_tick_logs_backlog_warning_when_batch_hits_candidate_limit(caplog):
    expired = [{"ride_id": f"r{i}", "driver_id": f"d{i}"} for i in range(reaper._CANDIDATE_LIMIT)]

    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return expired
        if table == "rides":
            return [{"status": "driver_accepted"}]  # skip redispatch, keep this test focused
        return []

    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(return_value={})),
        patch.object(reaper, "process_expired_offer", AsyncMock(return_value=False)),
        patch.object(reaper, "match_driver_to_ride", AsyncMock()),
        caplog.at_level(logging.WARNING),
    ):
        await reaper._reap_tick()

    assert any(
        "hit the" in r.message and "overdue-offer backlog" in r.message
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


@pytest.mark.anyio
async def test_reap_tick_no_backlog_warning_when_batch_is_below_candidate_limit(caplog):
    expired = [{"ride_id": "r1", "driver_id": "d1"}]

    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return expired
        return [{"status": "driver_accepted"}]

    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(return_value={})),
        patch.object(reaper, "process_expired_offer", AsyncMock(return_value=False)),
        patch.object(reaper, "match_driver_to_ride", AsyncMock()),
        caplog.at_level(logging.WARNING),
    ):
        await reaper._reap_tick()

    assert not any("overdue-offer backlog" in r.message for r in caplog.records)


# ── _reap_tick: won-count warning (already-partly-covered branch, line 112) ─


@pytest.mark.anyio
async def test_reap_tick_logs_won_count_when_offers_were_actually_expired(caplog):
    expired = [{"ride_id": "r1", "driver_id": "d1"}]

    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return expired
        return [{"status": "driver_accepted"}]

    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(return_value={})),
        patch.object(reaper, "process_expired_offer", AsyncMock(return_value=True)),
        patch.object(reaper, "match_driver_to_ride", AsyncMock()),
        caplog.at_level(logging.WARNING),
    ):
        await reaper._reap_tick()

    assert any("expired 1 orphaned offer" in r.message for r in caplog.records)


# ── _reap_tick: get_app_settings fallback (lines 100-101) ──────────────────


@pytest.mark.anyio
async def test_reap_tick_settings_failure_logs_error_and_defaults():
    """Fixed (2026-08-03): any exception from get_app_settings is now
    logged via `logger.error(..., exc_info=True)` before falling back to
    the hardcoded default of 3, per CLAUDE.md's "do not silently swallow
    errors" rule. Asserts via a direct `logger.error` mock rather than
    `caplog` -- see the same note in test_location_integrity_coverage.py
    on why caplog is unreliable for this repo's full-suite logging setup.
    """
    expired = [{"ride_id": "r1", "driver_id": "d1"}]

    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return expired
        return [{"status": "driver_accepted"}]

    proc = AsyncMock(return_value=False)
    mock_error = MagicMock()

    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(side_effect=RuntimeError("settings service down"))),
        patch.object(reaper, "process_expired_offer", proc),
        patch.object(reaper, "match_driver_to_ride", AsyncMock()),
        patch.object(reaper.logger, "error", mock_error),
    ):
        await reaper._reap_tick()

    # Falls back to the hardcoded default of 3.
    proc.assert_awaited_once_with("r1", "d1", 3)
    # The settings failure is now logged loudly.
    assert any("settings" in str(c.args[0]).lower() for c in mock_error.call_args_list)


@pytest.mark.anyio
async def test_reap_tick_uses_configured_miss_threshold_when_settings_available():
    expired = [{"ride_id": "r1", "driver_id": "d1"}]

    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return expired
        return [{"status": "driver_accepted"}]

    proc = AsyncMock(return_value=False)

    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(return_value={"auto_offline_miss_threshold": 5})),
        patch.object(reaper, "process_expired_offer", proc),
        patch.object(reaper, "match_driver_to_ride", AsyncMock()),
    ):
        await reaper._reap_tick()

    proc.assert_awaited_once_with("r1", "d1", 5)


# ── _reap_tick: per-ride redispatch failure isolation (lines 122-123) ──────


@pytest.mark.anyio
async def test_reap_tick_redispatch_lookup_failure_is_logged_and_does_not_raise(caplog):
    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return [{"ride_id": "r1", "driver_id": "d1"}]
        if table == "rides":
            raise RuntimeError("rides table unavailable")
        return []

    redispatch = AsyncMock()

    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(return_value={"auto_offline_miss_threshold": 3})),
        patch.object(reaper, "process_expired_offer", AsyncMock(return_value=True)),
        patch.object(reaper, "match_driver_to_ride", redispatch),
        caplog.at_level(logging.ERROR),
    ):
        # Must not raise -- the per-ride redispatch failure is caught and
        # logged, not propagated up out of the tick.
        await reaper._reap_tick()

    redispatch.assert_not_awaited()
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("re-dispatch failed for ride r1" in r.message for r in error_records)


@pytest.mark.anyio
async def test_reap_tick_match_driver_to_ride_failure_is_logged_and_does_not_raise(caplog):
    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return [{"ride_id": "r1", "driver_id": "d1"}]
        if table == "rides":
            return [{"status": "searching"}]
        return []

    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(return_value={"auto_offline_miss_threshold": 3})),
        patch.object(reaper, "process_expired_offer", AsyncMock(return_value=True)),
        patch.object(reaper, "match_driver_to_ride", AsyncMock(side_effect=RuntimeError("dispatch exploded"))),
        caplog.at_level(logging.ERROR),
    ):
        await reaper._reap_tick()

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("re-dispatch failed for ride r1" in r.message for r in error_records)


@pytest.mark.anyio
async def test_reap_tick_multiple_rides_one_redispatch_failure_does_not_block_the_other(caplog):
    async def _get_rows(table, filt, **kw):
        if table == "ride_offers":
            return [
                {"ride_id": "r-bad", "driver_id": "d1"},
                {"ride_id": "r-good", "driver_id": "d2"},
            ]
        if table == "rides":
            row_id = filt.get("id")
            if row_id == "r-bad":
                raise RuntimeError("boom")
            return [{"status": "searching"}]
        return []

    redispatch = AsyncMock()

    with (
        patch.object(reaper.db, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(reaper, "get_app_settings", AsyncMock(return_value={"auto_offline_miss_threshold": 3})),
        patch.object(reaper, "process_expired_offer", AsyncMock(return_value=True)),
        patch.object(reaper, "match_driver_to_ride", redispatch),
        caplog.at_level(logging.ERROR),
    ):
        await reaper._reap_tick()

    # The good ride still gets redispatched despite the bad one's lookup failure.
    redispatch.assert_awaited_once_with("r-good")
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("re-dispatch failed for ride r-bad" in r.message for r in error_records)


# ── offer_expiry_reaper_loop (lines 126-141) ────────────────────────────────


class TestOfferExpiryReaperLoop:
    @pytest.mark.anyio
    async def test_lock_acquired_runs_tick_records_heartbeat_and_sleeps_with_jitter(self):
        tick = AsyncMock()
        heartbeat = MagicMock()
        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        with (
            patch.object(reaper, "redis_set_nx", AsyncMock(return_value=True)),
            patch.object(reaper, "_reap_tick", tick),
            patch.object(reaper, "_record_heartbeat", heartbeat),
            patch.object(reaper.asyncio, "sleep", fake_sleep),
            patch.object(reaper.random, "uniform", lambda a, b: 0),
            pytest.raises(asyncio.CancelledError),
        ):
            await reaper.offer_expiry_reaper_loop()

        tick.assert_awaited_once()
        heartbeat.assert_called_once_with(reaper._LOOP_NAME)
        assert sleep_calls == [reaper.REAP_INTERVAL_SECONDS]

    @pytest.mark.anyio
    async def test_lock_not_acquired_skips_tick_still_heartbeats_and_sleeps_the_plain_interval(self):
        tick = AsyncMock()
        heartbeat = MagicMock()
        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        with (
            patch.object(reaper, "redis_set_nx", AsyncMock(return_value=False)),
            patch.object(reaper, "_reap_tick", tick),
            patch.object(reaper, "_record_heartbeat", heartbeat),
            patch.object(reaper.asyncio, "sleep", fake_sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await reaper.offer_expiry_reaper_loop()

        # Lost the leader lock to another replica this tick -- must not
        # duplicate the reap work.
        tick.assert_not_awaited()
        heartbeat.assert_called_once_with(reaper._LOOP_NAME)
        # No jitter applied on the "someone else holds the lock" path.
        assert sleep_calls == [reaper.REAP_INTERVAL_SECONDS]

    @pytest.mark.anyio
    async def test_redis_set_nx_called_with_lock_key_pod_id_and_double_interval_ttl(self):
        set_nx = AsyncMock(return_value=True)

        async def fake_sleep(secs):
            raise asyncio.CancelledError()

        with (
            patch.object(reaper, "redis_set_nx", set_nx),
            patch.object(reaper, "_reap_tick", AsyncMock()),
            patch.object(reaper, "_record_heartbeat", MagicMock()),
            patch.object(reaper.asyncio, "sleep", fake_sleep),
            patch.object(reaper.random, "uniform", lambda a, b: 0),
            patch.object(reaper, "_pod_id", return_value="host:123"),
            pytest.raises(asyncio.CancelledError),
        ):
            await reaper.offer_expiry_reaper_loop()

        set_nx.assert_awaited_once_with("spinr:offer_expiry_reaper:lock", "host:123", reaper.REAP_INTERVAL_SECONDS * 2)

    @pytest.mark.anyio
    async def test_tick_exception_is_logged_and_loop_still_heartbeats_and_sleeps(self, caplog):
        heartbeat = MagicMock()
        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        with (
            patch.object(reaper, "redis_set_nx", AsyncMock(return_value=True)),
            patch.object(reaper, "_reap_tick", AsyncMock(side_effect=RuntimeError("tick blew up"))),
            patch.object(reaper, "_record_heartbeat", heartbeat),
            patch.object(reaper.asyncio, "sleep", fake_sleep),
            patch.object(reaper.random, "uniform", lambda a, b: 0),
            caplog.at_level(logging.ERROR),
            pytest.raises(asyncio.CancelledError),
        ):
            await reaper.offer_expiry_reaper_loop()

        # Loop survives the tick exception -- heartbeat + sleep still run.
        heartbeat.assert_called_once_with(reaper._LOOP_NAME)
        assert sleep_calls == [reaper.REAP_INTERVAL_SECONDS]
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("Offer expiry reaper loop error" in r.message for r in error_records)

    @pytest.mark.anyio
    async def test_cancelled_error_from_tick_propagates_through_sleep(self):
        """A CancelledError raised by _reap_tick itself is still an
        `Exception`-family... actually asyncio.CancelledError is a
        BaseException (not Exception) in modern Python, so the module's
        `except Exception` does NOT catch it -- it propagates immediately,
        without ever reaching the heartbeat/sleep lines below the try block.
        """
        heartbeat = MagicMock()

        async def fake_sleep(secs):
            pass  # should never be reached on this path

        with (
            patch.object(reaper, "redis_set_nx", AsyncMock(return_value=True)),
            patch.object(reaper, "_reap_tick", AsyncMock(side_effect=asyncio.CancelledError())),
            patch.object(reaper, "_record_heartbeat", heartbeat),
            patch.object(reaper.asyncio, "sleep", fake_sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await reaper.offer_expiry_reaper_loop()

        # The except Exception block does not catch CancelledError, so the
        # post-tick heartbeat call is skipped on this path.
        heartbeat.assert_not_called()

    @pytest.mark.anyio
    async def test_multiple_ticks_before_cancellation(self):
        tick = AsyncMock()
        heartbeat = MagicMock()
        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 3:
                raise asyncio.CancelledError()

        with (
            patch.object(reaper, "redis_set_nx", AsyncMock(return_value=True)),
            patch.object(reaper, "_reap_tick", tick),
            patch.object(reaper, "_record_heartbeat", heartbeat),
            patch.object(reaper.asyncio, "sleep", fake_sleep),
            patch.object(reaper.random, "uniform", lambda a, b: 0),
            pytest.raises(asyncio.CancelledError),
        ):
            await reaper.offer_expiry_reaper_loop()

        assert tick.await_count == 3
        assert heartbeat.call_count == 3

    @pytest.mark.anyio
    async def test_sleep_duration_includes_jitter_window(self):
        """`await asyncio.sleep(REAP_INTERVAL_SECONDS + random.uniform(-delta,
        delta))` -- verify the jitter offset is actually threaded through to
        the sleep call (as opposed to random.uniform being called but
        discarded)."""
        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        with (
            patch.object(reaper, "redis_set_nx", AsyncMock(return_value=True)),
            patch.object(reaper, "_reap_tick", AsyncMock()),
            patch.object(reaper, "_record_heartbeat", MagicMock()),
            patch.object(reaper.asyncio, "sleep", fake_sleep),
            patch.object(reaper.random, "uniform", lambda a, b: 1.5),
            pytest.raises(asyncio.CancelledError),
        ):
            await reaper.offer_expiry_reaper_loop()

        assert sleep_calls == [reaper.REAP_INTERVAL_SECONDS + 1.5]
