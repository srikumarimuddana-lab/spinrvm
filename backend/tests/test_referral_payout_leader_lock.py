"""Leader election for the referral payout loop (utils/referral_payout).

The loop runs on every replica; without a leader lock each replica repeats the
full candidate scan + per-referee ride counts every 5 minutes. The per-interval
redis_set_nx bucket (surge_engine pattern) elects one replica per window; the
UNIQUE(referee_user_id) claim remains the correctness guarantee either way.

Tests are sync + asyncio.run (fresh loop per test) because the loop is
terminated by raising CancelledError from a patched sleep — the same pattern as
test_route_finalizer_loop.py.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import utils.referral_payout as rp


def _run(coro):
    return asyncio.run(coro)


def _cancel_after_first_sleep(monkeypatch):
    async def _sleep(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(rp.asyncio, "sleep", _sleep)


def test_leader_runs_the_tick(monkeypatch):
    _cancel_after_first_sleep(monkeypatch)
    tick = AsyncMock()
    with (
        patch.object(rp, "redis_set_nx", AsyncMock(return_value=True)) as lock,
        patch.object(rp, "_tick", tick),
    ):
        with pytest.raises(asyncio.CancelledError):
            _run(rp.referral_payout_loop())

    tick.assert_awaited_once()
    key = lock.await_args.args[0]
    assert key.startswith("spinr:referral_payout:lock:")
    assert lock.await_args.args[2] == rp.INTERVAL_SECONDS * 2


def test_non_leader_skips_the_tick_but_stays_alive(monkeypatch):
    _cancel_after_first_sleep(monkeypatch)
    tick = AsyncMock()
    heartbeat = MagicMock()
    with (
        patch.object(rp, "redis_set_nx", AsyncMock(return_value=False)),
        patch.object(rp, "_tick", tick),
        patch.object(rp, "_record_heartbeat", heartbeat),
    ):
        with pytest.raises(asyncio.CancelledError):
            _run(rp.referral_payout_loop())

    tick.assert_not_awaited()
    heartbeat.assert_called_once_with("referral_payout (5min)")


def test_lock_failure_degrades_to_running_the_tick(monkeypatch):
    # Redis outage must degrade to per-replica ticks (the UNIQUE claim still
    # prevents double-pays), never to a silently stopped payout loop.
    _cancel_after_first_sleep(monkeypatch)
    tick = AsyncMock()
    with (
        patch.object(rp, "redis_set_nx", AsyncMock(side_effect=RuntimeError("redis down"))),
        patch.object(rp, "_tick", tick),
    ):
        with pytest.raises(asyncio.CancelledError):
            _run(rp.referral_payout_loop())

    tick.assert_awaited_once()
