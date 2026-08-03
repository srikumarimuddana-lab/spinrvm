"""Additional coverage for utils/driver_claim_reaper.py (A1c, Sub-tier C).

`test_driver_claim_reaper.py` already covers the core `_reap_tick` orphan
logic (recent claim / null stamp / pending offer / active ride / release).
This file closes the remaining gap reported at 65% (68 stmts, 24 missing:
lines 32-34, 58, 103-105, 124-126, 137-152):

  - `_pod_id()` (line 58) — the Redis lock-holder identity string.
  - `_reap_tick`'s two `except Exception` guards (103-105, 124-126): the
    `drivers` fetch failing, and `set_driver_available` failing for one
    driver (and NOT aborting the batch for the rest — the loop `continue`s
    past it, matching the stuck-ride sweeper's per-item failure isolation
    convention).
  - `driver_claim_reaper_loop()` (137-152) — the full background-loop body:
    Redis leader-lock skip branch, happy tick, tick-raises-but-loop-survives
    branch, and the jittered post-tick sleep.

Lines 32-34 (the package-relative import branch of the dual-import
try/except at module top) are inherent to *which* import path Python takes
when this module is first imported in the interpreter session — not
something a test body can select between at call time — so they are not
independently exercised here; whichever branch fires is already exercised
for free by importing the module below, same as `test_driver_claim_reaper.py`
already does.

Fixed (2026-08-03, application code change — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 5):
`_reap_tick`'s `_has_pending_offer` / `_has_active_ride` check previously
had no try/except, so a failure on one driver's lookup aborted the whole
tick's batch — every other already-fetched, otherwise-reapable driver was
skipped too, not just the one that errored. Now isolated per-driver,
matching the release-failure guard right below it and the stuck-ride
sweeper's per-ride isolation convention. See
`test_offer_check_error_isolated_to_one_driver` below.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

MOD = "backend.utils.driver_claim_reaper."


def _driver(driver_id="drv_1", minutes_ago=5):
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "id": driver_id,
        "user_id": f"u_{driver_id}",
        "is_online": True,
        "is_available": False,
        "availability_claimed_at": stamp,
    }


class TestPodId:
    def test_pod_id_format(self):
        from backend.utils.driver_claim_reaper import _pod_id

        pod_id = _pod_id()
        assert re.match(r"^.+:\d+$", pod_id)

    def test_pod_id_uses_hostname_and_pid(self):
        from backend.utils.driver_claim_reaper import _pod_id

        with patch(MOD + "socket.gethostname", return_value="host-a"), patch(MOD + "os.getpid", return_value=4242):
            assert _pod_id() == "host-a:4242"


class TestReapTickDriverFetchFailure:
    async def test_drivers_fetch_error_is_logged_and_returns_without_raising(self, caplog):
        from backend.utils.driver_claim_reaper import _reap_tick

        get_rows = AsyncMock(side_effect=RuntimeError("postgrest 503"))
        release = AsyncMock()
        with (
            patch(MOD + "db.get_rows", get_rows),
            patch(MOD + "set_driver_available", release),
            caplog.at_level(logging.ERROR),
        ):
            await _reap_tick()  # must not raise

        release.assert_not_called()
        assert any("failed to fetch candidate drivers" in r.message for r in caplog.records)
        assert any(r.levelno == logging.ERROR for r in caplog.records)


class TestReapTickReleaseFailure:
    async def test_release_failure_for_one_driver_is_logged_and_others_still_processed(self, caplog):
        """The `set_driver_available` except-block (124-126) logs and
        `continue`s rather than aborting the whole tick — the second driver
        in the batch must still be released even though the first errored."""
        from backend.utils.driver_claim_reaper import _reap_tick

        drivers = [_driver("drv_bad"), _driver("drv_good")]

        async def _get_rows(table, flt, **kw):
            if table == "drivers":
                return drivers
            if table in ("ride_offers", "rides"):
                return []
            return []

        release = AsyncMock(side_effect=[RuntimeError("row locked"), {"is_available": True}])

        with (
            patch(MOD + "db.get_rows", AsyncMock(side_effect=_get_rows)),
            patch(MOD + "set_driver_available", release),
            caplog.at_level(logging.ERROR),
        ):
            await _reap_tick()  # must not raise

        assert release.await_count == 2
        release.assert_any_await("drv_bad", available=True)
        release.assert_any_await("drv_good", available=True)
        assert any("release failed for driver drv_bad" in r.message for r in caplog.records)

    async def test_release_returning_falsy_is_not_logged_as_success(self, caplog):
        """`released.get("is_available")` false-y (e.g. clamp left it
        unavailable) must not emit the 'released orphaned claim' log line."""
        from backend.utils.driver_claim_reaper import _reap_tick

        async def _get_rows(table, flt, **kw):
            if table == "drivers":
                return [_driver()]
            return []

        release = AsyncMock(return_value={"is_available": False})

        with (
            patch(MOD + "db.get_rows", AsyncMock(side_effect=_get_rows)),
            patch(MOD + "set_driver_available", release),
            caplog.at_level(logging.WARNING),
        ):
            await _reap_tick()

        release.assert_awaited_once()
        assert not any("released orphaned claim" in r.message for r in caplog.records)

    async def test_offer_check_error_isolated_to_one_driver(self):
        """Fixed (2026-08-03): `_has_pending_offer`/`_has_active_ride` are
        now individually try/excepted per-driver, so an error on the FIRST
        driver's lookup is logged and skipped, and the SECOND (otherwise
        reapable) driver still gets processed in the same tick."""
        from backend.utils.driver_claim_reaper import _reap_tick

        drivers = [_driver("drv_first"), _driver("drv_second")]

        async def _get_rows(table, flt, **kw):
            if table == "drivers":
                return drivers
            if table == "ride_offers" and flt.get("driver_id") == "drv_first":
                raise RuntimeError("transient postgrest error")
            return []

        release = AsyncMock(return_value={"is_available": True})

        with patch(MOD + "db.get_rows", AsyncMock(side_effect=_get_rows)), patch(MOD + "set_driver_available", release):
            # Must not raise -- drv_first's lookup failure is isolated.
            await _reap_tick()

        # drv_second was still reaped despite drv_first's lookup error.
        release.assert_awaited_once_with("drv_second", available=True)


class TestDriverClaimReaperLoop:
    async def test_lock_not_acquired_skips_tick_heartbeats_and_sleeps(self):
        from backend.utils.driver_claim_reaper import driver_claim_reaper_loop

        tick = AsyncMock()
        heartbeat = MagicMock()
        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 1:
                raise asyncio.CancelledError()

        with (
            patch(MOD + "redis_set_nx", AsyncMock(return_value=False)),
            patch(MOD + "_reap_tick", tick),
            patch(MOD + "_record_heartbeat", heartbeat),
            patch(MOD + "asyncio.sleep", fake_sleep),
        ):
            with pytest.raises(asyncio.CancelledError):
                await driver_claim_reaper_loop()

        tick.assert_not_awaited()
        heartbeat.assert_called_once_with("driver_claim_reaper (60s)")
        assert sleep_calls == [60]

    async def test_lock_acquired_happy_tick_records_heartbeat_and_sleeps_with_jitter(self):
        from backend.utils.driver_claim_reaper import driver_claim_reaper_loop

        tick = AsyncMock()
        heartbeat = MagicMock()
        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        with (
            patch(MOD + "redis_set_nx", AsyncMock(return_value=True)),
            patch(MOD + "_reap_tick", tick),
            patch(MOD + "_record_heartbeat", heartbeat),
            patch(MOD + "asyncio.sleep", fake_sleep),
            patch(MOD + "random.uniform", return_value=0),
        ):
            with pytest.raises(asyncio.CancelledError):
                await driver_claim_reaper_loop()

        tick.assert_awaited_once()
        heartbeat.assert_called_once_with("driver_claim_reaper (60s)")
        # 60s +/- jitter, pinned to exactly 60 since random.uniform is patched to 0.
        assert sleep_calls == [60]

    async def test_tick_exception_is_logged_and_loop_still_heartbeats_and_sleeps(self, caplog):
        from backend.utils.driver_claim_reaper import driver_claim_reaper_loop

        tick = AsyncMock(side_effect=RuntimeError("boom"))
        heartbeat = MagicMock()
        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            raise asyncio.CancelledError()

        with (
            patch(MOD + "redis_set_nx", AsyncMock(return_value=True)),
            patch(MOD + "_reap_tick", tick),
            patch(MOD + "_record_heartbeat", heartbeat),
            patch(MOD + "asyncio.sleep", fake_sleep),
            patch(MOD + "random.uniform", return_value=0),
            caplog.at_level(logging.ERROR),
        ):
            with pytest.raises(asyncio.CancelledError):
                await driver_claim_reaper_loop()

        tick.assert_awaited_once()
        heartbeat.assert_called_once_with("driver_claim_reaper (60s)")
        assert sleep_calls == [60]
        assert any("Driver claim reaper loop error" in r.message for r in caplog.records)

    async def test_multiple_lock_misses_then_acquire_across_iterations(self):
        """Exercises the `continue` branch more than once before falling
        through to the locked branch, covering both paths in one run."""
        from backend.utils.driver_claim_reaper import driver_claim_reaper_loop

        tick = AsyncMock()
        heartbeat = MagicMock()
        lock_results = [False, False, True]
        sleep_calls = []

        async def fake_lock(*args, **kwargs):
            return lock_results.pop(0)

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 3:
                raise asyncio.CancelledError()

        with (
            patch(MOD + "redis_set_nx", fake_lock),
            patch(MOD + "_reap_tick", tick),
            patch(MOD + "_record_heartbeat", heartbeat),
            patch(MOD + "asyncio.sleep", fake_sleep),
            patch(MOD + "random.uniform", return_value=0),
        ):
            with pytest.raises(asyncio.CancelledError):
                await driver_claim_reaper_loop()

        # Locked only on the 3rd iteration -> exactly one tick.
        tick.assert_awaited_once()
        assert heartbeat.call_count == 3
        assert sleep_calls == [60, 60, 60]
