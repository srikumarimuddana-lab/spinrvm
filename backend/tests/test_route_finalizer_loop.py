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


def test_queue_polls_project_claim_keys_not_geometry(monkeypatch):
    """The 15 s tick polls must never `select *` on ride_routes: the table
    carries the full GPS trace and OSRM polyline per ride as jsonb, and on
    the 2026-09-11 test ride these two polls were the slowest requests
    Supabase served (up to 9.2 s) while the SQL itself was single-digit ms."""
    calls = []

    async def get_rows(table, filters, **kwargs):
        calls.append((table, filters, kwargs))
        return []

    monkeypatch.setattr(route_finalizer.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", AsyncMock())

    assert _run(route_finalizer.claim_next_pending_route()) is None
    assert _run(route_finalizer.recover_stale_route_claims()) == 0

    assert len(calls) == 2
    for _table, _filters, kwargs in calls:
        cols = kwargs.get("columns", "*")
        assert cols != "*", "queue poll must project its columns"
        for heavy in ("phase_polylines", "road_polyline", "phase_distances", "phase_durations"):
            assert heavy not in cols
    claim_cols = calls[0][2]["columns"].split(",")
    recover_cols = calls[1][2]["columns"].split(",")
    # Everything the two loops actually read.
    assert {"ride_id", "next_retry_at"} <= set(claim_cols)
    assert {"ride_id", "processing_claimed_at"} <= set(recover_cols)


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
    # Always win the leader lock. This test drives two ticks back to back with
    # sleep mocked out, so no wall-clock passes and the real lock's TTL would
    # never expire between them — the loop would correctly skip the second tick
    # and this test would spin. Lock election is covered separately; what is
    # under test here is that a raising tick does not kill the loop.
    monkeypatch.setattr(route_finalizer, "try_acquire_leader_lock", AsyncMock(return_value=True))

    with pytest.raises(asyncio.CancelledError):
        _run(route_finalizer.route_finalizer_loop(interval_seconds=0))

    assert ticks == ["tick", "tick"]
    lifespan_source = (Path(__file__).resolve().parents[1] / "core" / "lifespan.py").read_text()
    assert '_spawn("route_finalizer (15s)", route_finalizer_loop)' in lifespan_source
