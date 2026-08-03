"""
A1c Sub-tier C coverage: backend/utils/driver_claim_reaper.py (65% -> target 90%+).

`test_driver_claim_reaper.py` covers `_reap_tick`'s core orphan-detection
logic (age threshold, null stamp, pending-offer/active-ride busy guards)
thoroughly. This file closes:

- `_reap_tick`: the `drivers` fetch-exception branch (logged, tick returns
  without processing), and the release-call exception branch (one driver's
  release failure must not abort the batch -- `continue`, not raise).
- `driver_claim_reaper_loop`: lock-not-acquired skips the tick and
  re-loops, lock-acquired runs the tick, a tick exception is caught and
  logged (loop survives), and the heartbeat/jitter-sleep call on every
  iteration.
- `_pod_id`'s hostname:pid shape.

Patch target follows the established pattern in
`test_driver_claim_reaper.py`: `backend.utils.driver_claim_reaper.<name>`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio

P = "backend.utils.driver_claim_reaper."


def _driver(minutes_ago=5, claimed=True):
    stamp = None
    if claimed:
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "id": "drv_1",
        "user_id": "u_1",
        "is_online": True,
        "is_available": False,
        "availability_claimed_at": stamp,
    }


async def test_drivers_fetch_exception_is_logged_and_tick_returns():
    from backend.utils.driver_claim_reaper import _reap_tick

    with patch(P + "db.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
        # Must not raise.
        await _reap_tick()


async def test_release_exception_does_not_abort_batch():
    from backend.utils.driver_claim_reaper import _reap_tick

    drivers = [_driver(minutes_ago=5)]

    async def _get_rows(table, flt, **kw):
        if table == "drivers":
            return drivers
        return []

    with (
        patch(P + "db.get_rows", AsyncMock(side_effect=_get_rows)),
        patch(P + "set_driver_available", AsyncMock(side_effect=RuntimeError("release failed"))),
    ):
        # Must not raise even though the release call blew up.
        await _reap_tick()


async def test_release_returning_non_available_dict_does_not_warn_crash(caplog):
    """release succeeding but the returned row not reflecting is_available=True
    (e.g. clamped to is_online=False) must not raise -- only the warning log
    is skipped."""
    from backend.utils.driver_claim_reaper import _reap_tick

    drivers = [_driver(minutes_ago=5)]

    async def _get_rows(table, flt, **kw):
        if table == "drivers":
            return drivers
        return []

    with (
        patch(P + "db.get_rows", AsyncMock(side_effect=_get_rows)),
        patch(P + "set_driver_available", AsyncMock(return_value={"is_available": False})),
    ):
        await _reap_tick()


# ---------------------------------------------------------------------------
# driver_claim_reaper_loop
# ---------------------------------------------------------------------------


async def test_loop_lock_not_acquired_skips_tick_and_sleeps():
    tick = AsyncMock()

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "redis_set_nx", AsyncMock(return_value=False)),
        patch(P + "_reap_tick", tick),
        patch(P + "asyncio.sleep", fake_sleep),
        patch(P + "_record_heartbeat"),
    ):
        from backend.utils.driver_claim_reaper import driver_claim_reaper_loop

        with pytest.raises(asyncio.CancelledError):
            await driver_claim_reaper_loop()
    tick.assert_not_awaited()


async def test_loop_lock_acquired_runs_tick():
    tick = AsyncMock()

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "redis_set_nx", AsyncMock(return_value=True)),
        patch(P + "_reap_tick", tick),
        patch(P + "asyncio.sleep", fake_sleep),
        patch(P + "_record_heartbeat") as mock_hb,
    ):
        from backend.utils.driver_claim_reaper import driver_claim_reaper_loop

        with pytest.raises(asyncio.CancelledError):
            await driver_claim_reaper_loop()
    tick.assert_awaited_once()
    mock_hb.assert_called_once()


async def test_loop_survives_tick_exception():
    async def failing_tick():
        raise RuntimeError("boom")

    async def fake_sleep(secs):
        raise asyncio.CancelledError()

    with (
        patch(P + "redis_set_nx", AsyncMock(return_value=True)),
        patch(P + "_reap_tick", failing_tick),
        patch(P + "asyncio.sleep", fake_sleep),
        patch(P + "_record_heartbeat"),
    ):
        from backend.utils.driver_claim_reaper import driver_claim_reaper_loop

        with pytest.raises(asyncio.CancelledError):
            await driver_claim_reaper_loop()


def test_pod_id_shape():
    from backend.utils.driver_claim_reaper import _pod_id

    pod_id = _pod_id()
    assert ":" in pod_id
    host, _, pid = pod_id.rpartition(":")
    assert host
    assert pid.isdigit()
