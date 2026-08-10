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


async def test_offer_check_error_isolated_to_one_driver():
    """Fixed: `_has_pending_offer`/`_has_active_ride` are now individually
    try/excepted per-driver, so an error on the FIRST driver's lookup is
    logged and skipped, and the SECOND (otherwise reapable) driver still
    gets processed in the same tick."""
    from backend.utils.driver_claim_reaper import _reap_tick

    def _two_drivers(driver_id, minutes_ago=5):
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
        return {
            "id": driver_id,
            "user_id": f"u_{driver_id}",
            "is_online": True,
            "is_available": False,
            "availability_claimed_at": stamp,
        }

    drivers = [_two_drivers("drv_first"), _two_drivers("drv_second")]

    async def _get_rows(table, flt, **kw):
        if table == "drivers":
            return drivers
        if table == "ride_offers" and flt.get("driver_id") == "drv_first":
            raise RuntimeError("transient postgrest error")
        return []

    release = AsyncMock(return_value={"is_available": True})

    with (
        patch(P + "db.get_rows", AsyncMock(side_effect=_get_rows)),
        patch(P + "set_driver_available", release),
    ):
        # Must not raise -- drv_first's lookup failure is isolated.
        await _reap_tick()

    # drv_second was still reaped despite drv_first's lookup error.
    release.assert_awaited_once_with("drv_second", available=True)


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


# ---------------------------------------------------------------------------
# ACTION_ITEMS B21: throttle-lock TTL must expire before the loop's own next
# wake, or the pod that ran the last tick fails its own SET NX and skips a
# full interval — see utils/ledger_projection.py's _LOCK_TTL_SECONDS for the
# sibling fix this mirrors.
# ---------------------------------------------------------------------------


def test_lock_ttl_expires_before_the_earliest_next_wake():
    """Stated as an invariant so a future tuning change to the interval or the
    jitter fraction can't silently re-break the cadence."""
    from backend.utils import driver_claim_reaper as reaper

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
    "60s" actually ticked every ~120s.

    Simulated against a virtual clock with real SET NX EX semantics, jitter
    pinned to its most adverse value (the SHORTEST sleep) — the case the TTL
    has to survive. Mirrors ledger_projection.py's loop-cadence regression test.
    """
    from backend.utils import driver_claim_reaper as reaper

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
        patch(P + "redis_set_nx", side_effect=fake_set_nx),
        patch(P + "_reap_tick", AsyncMock()) as tick,
        patch(P + "_record_heartbeat"),
        patch(P + "asyncio.sleep", side_effect=fake_sleep),
        # uniform(-delta, +delta) -> -delta: the shortest sleep the loop can take.
        patch(P + "random.uniform", side_effect=lambda lo, _hi: lo),
    ):
        from backend.utils.driver_claim_reaper import driver_claim_reaper_loop

        try:
            await driver_claim_reaper_loop()
        except asyncio.CancelledError:
            pass

    assert tick.await_count == 2, (
        "the single replica must tick once per interval; a TTL longer than the "
        "minimum sleep makes it skip its own next wake and halves the cadence"
    )
