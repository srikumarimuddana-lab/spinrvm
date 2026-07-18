# ruff: noqa: E402, I001
"""Replay-safety tests for the durable route finalizer worker."""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test_key")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-ci-only-32chars!!")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdminPass123!")

from backend.utils import route_finalizer


def _run(coroutine):
    return asyncio.run(coroutine)


def test_two_concurrent_claims_have_one_winner(monkeypatch):
    route = {"ride_id": "ride_1", "processing_status": "pending", "next_retry_at": None}
    updates = []

    async def update_one(_table, _filters, _payload, **_kwargs):
        updates.append(_filters)
        return {"ride_id": "ride_1"} if len(updates) == 1 else None

    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update_one)

    async def claim_twice():
        return await asyncio.gather(
            route_finalizer.claim_next_pending_route([route]),
            route_finalizer.claim_next_pending_route([route]),
        )

    first, second = _run(claim_twice())
    assert sorted([first, second], key=lambda value: value is not None) == [None, "ride_1"]
    assert all(filters["processing_status"] == "pending" for filters in updates)


def test_stale_processing_claims_return_to_pending(monkeypatch):
    stale_at = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    update = AsyncMock()
    monkeypatch.setattr(
        route_finalizer.db_supabase,
        "get_rows",
        AsyncMock(return_value=[{"ride_id": "ride_1", "processing_claimed_at": stale_at}]),
    )
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update)

    recovered = _run(route_finalizer.recover_stale_route_claims())

    assert recovered == 1
    assert update.await_args.args[1] == {
        "ride_id": "ride_1",
        "processing_status": "processing",
        "processing_claimed_at": stale_at,
    }
    assert update.await_args.args[2]["processing_status"] == "pending"


def test_future_retry_is_not_claimed(monkeypatch):
    route = {
        "ride_id": "ride_1",
        "processing_status": "pending",
        "next_retry_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
    }
    update = AsyncMock()
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", update)

    assert _run(route_finalizer.claim_next_pending_route([route])) is None
    update.assert_not_awaited()


def test_tick_drains_every_pending_route_in_one_cycle(monkeypatch):
    claims = ["ride_1", "ride_2", "ride_3", None]
    finalized: list[str] = []
    recovered: list[bool] = []

    async def recover():
        recovered.append(True)
        return 0

    async def claim(candidates=None):
        return claims.pop(0)

    async def finalize(ride_id):
        finalized.append(ride_id)
        return {"processing_status": "complete", "route_revision": 1}

    monkeypatch.setattr(route_finalizer, "recover_stale_route_claims", recover)
    monkeypatch.setattr(route_finalizer, "claim_next_pending_route", claim)
    monkeypatch.setattr(route_finalizer, "finalize_route", finalize)

    count = _run(route_finalizer.route_finalizer_tick())

    assert count == 3
    assert finalized == ["ride_1", "ride_2", "ride_3"]
    # Stale-claim recovery still runs, once per cycle rather than per claim.
    assert recovered == [True]


def test_tick_returns_zero_when_no_route_is_pending(monkeypatch):
    async def recover():
        return 0

    async def claim(candidates=None):
        return None

    async def finalize(_ride_id):
        raise AssertionError("nothing pending — finalize must not run")

    monkeypatch.setattr(route_finalizer, "recover_stale_route_claims", recover)
    monkeypatch.setattr(route_finalizer, "claim_next_pending_route", claim)
    monkeypatch.setattr(route_finalizer, "finalize_route", finalize)

    assert _run(route_finalizer.route_finalizer_tick()) == 0


def test_drain_is_bounded_so_a_requeue_storm_cannot_starve_other_loops(monkeypatch):
    finalized: list[str] = []

    async def recover():
        return 0

    async def claim(candidates=None):
        return "ride_x"  # always pending — a pathological requeue

    async def finalize(ride_id):
        finalized.append(ride_id)
        return {"processing_status": "complete"}

    monkeypatch.setattr(route_finalizer, "recover_stale_route_claims", recover)
    monkeypatch.setattr(route_finalizer, "claim_next_pending_route", claim)
    monkeypatch.setattr(route_finalizer, "finalize_route", finalize)
    monkeypatch.setattr(route_finalizer, "ROUTE_FINALIZER_MAX_PER_CYCLE", 5)

    count = _run(route_finalizer.route_finalizer_tick())

    assert count == 5
    assert len(finalized) == 5


def test_finalizer_loop_interval_is_reduced_to_five_seconds():
    assert route_finalizer.ROUTE_FINALIZER_INTERVAL_SECONDS == 5


def test_loop_survives_tick_failure_and_lifespan_registers_it(monkeypatch):
    ticks = []

    async def tick():
        ticks.append("tick")
        if len(ticks) == 1:
            raise RuntimeError("temporary failure")
        raise asyncio.CancelledError()

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(route_finalizer, "route_finalizer_tick", tick)
    monkeypatch.setattr(route_finalizer.asyncio, "sleep", no_wait)

    with pytest.raises(asyncio.CancelledError):
        _run(route_finalizer.route_finalizer_loop(interval_seconds=0))

    assert ticks == ["tick", "tick"]
    lifespan_source = (Path(__file__).resolve().parents[1] / "core" / "lifespan.py").read_text()
    assert '_spawn("route_finalizer (5s)", route_finalizer_loop)' in lifespan_source
