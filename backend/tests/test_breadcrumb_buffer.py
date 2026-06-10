"""B3.3 — per-driver breadcrumb batching (utils/breadcrumb_buffer.py).

Contract: points buffer in-process and persist as one batch at 10 points /
10 s / ride-context change / explicit flush. The SGI-auditable trail must
never silently drop points: every buffered point reaches
persist_ride_breadcrumbs exactly once, under the ride context it was
captured on.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import breadcrumb_buffer as bb


@pytest.fixture(autouse=True)
def _clean_buffers():
    bb._buffers.clear()
    bb._pending_retry.clear()
    bb._flushing.clear()
    yield
    bb._buffers.clear()
    bb._pending_retry.clear()
    bb._flushing.clear()


def _point(i: int) -> dict:
    return {"lat": 52.13 + i * 1e-4, "lng": -106.67, "type": "driver_location"}


def _ride(ride_id: str = "ride-bb-1") -> dict:
    return {"id": ride_id, "status": "in_progress"}


@pytest.mark.anyio
async def test_buffers_until_max_points_then_single_batch_insert():
    persist = AsyncMock(return_value=10)
    with patch.object(bb, "persist_ride_breadcrumbs", persist):
        for i in range(9):
            assert await bb.buffer_ride_breadcrumb("drv-bb", _point(i), active_ride=_ride()) == 0
        persist.assert_not_awaited()
        inserted = await bb.buffer_ride_breadcrumb("drv-bb", _point(9), active_ride=_ride())

    assert inserted == 10
    persist.assert_awaited_once()
    args, kwargs = persist.call_args
    assert args[0] == "drv-bb"
    assert len(args[1]) == 10
    assert kwargs["active_ride"]["id"] == "ride-bb-1"


@pytest.mark.anyio
async def test_age_threshold_flushes(monkeypatch):
    persist = AsyncMock(return_value=2)
    fake_now = [1000.0]
    monkeypatch.setattr(bb.time, "monotonic", lambda: fake_now[0])
    with patch.object(bb, "persist_ride_breadcrumbs", persist):
        await bb.buffer_ride_breadcrumb("drv-bb-age", _point(0), active_ride=_ride())
        fake_now[0] += 11.0
        inserted = await bb.buffer_ride_breadcrumb("drv-bb-age", _point(1), active_ride=_ride())

    assert inserted == 2
    persist.assert_awaited_once()
    assert len(persist.call_args.args[1]) == 2


@pytest.mark.anyio
async def test_ride_change_flushes_old_batch_under_old_ride():
    persist = AsyncMock(return_value=3)
    with patch.object(bb, "persist_ride_breadcrumbs", persist):
        for i in range(3):
            await bb.buffer_ride_breadcrumb("drv-bb-rc", _point(i), active_ride=_ride("ride-old"))
        # Ride completed: context becomes None — old batch must flush under ride-old.
        await bb.buffer_ride_breadcrumb("drv-bb-rc", _point(3), active_ride=None)

    persist.assert_awaited_once()
    args, kwargs = persist.call_args
    assert len(args[1]) == 3
    assert kwargs["active_ride"]["id"] == "ride-old"
    # The new idle point is buffered, not lost.
    assert len(bb._buffers["drv-bb-rc"].points) == 1
    assert bb._buffers["drv-bb-rc"].ride is None


@pytest.mark.anyio
async def test_explicit_flush_drains_and_is_idempotent():
    persist = AsyncMock(return_value=2)
    with patch.object(bb, "persist_ride_breadcrumbs", persist):
        await bb.buffer_ride_breadcrumb("drv-bb-fl", _point(0), active_ride=_ride())
        await bb.buffer_ride_breadcrumb("drv-bb-fl", _point(1), active_ride=_ride())
        assert await bb.flush_driver_breadcrumbs("drv-bb-fl") == 2
        assert await bb.flush_driver_breadcrumbs("drv-bb-fl") == 0

    persist.assert_awaited_once()


@pytest.mark.anyio
async def test_failed_persist_keeps_points_for_next_flush():
    """Codex P2 (PR #1758): a transient DB failure must not drop the trip
    tail — the batch is stashed and persisted by the next flush, exactly once."""
    calls = []

    async def _persist(driver_id, points, *, persist_idle, active_ride):
        calls.append(list(points))
        if len(calls) == 1:
            raise RuntimeError("db down")
        return len(points)

    with patch.object(bb, "persist_ride_breadcrumbs", _persist):
        await bb.buffer_ride_breadcrumb("drv-bb-fail", _point(0), active_ride=_ride())
        await bb.buffer_ride_breadcrumb("drv-bb-fail", _point(1), active_ride=_ride())
        with pytest.raises(RuntimeError):
            await bb.flush_driver_breadcrumbs("drv-bb-fail")
        # Points survived the failure and persist on the retry — once.
        assert await bb.flush_driver_breadcrumbs("drv-bb-fail") == 2

    assert len(calls) == 2
    assert calls[0] == calls[1]


@pytest.mark.anyio
async def test_failed_batch_retried_under_its_own_ride_context():
    """A batch from ride X whose persist failed must not be re-attributed to
    the driver's later context (idle or a new ride) when it is retried."""
    seen = []
    fail_first = {"armed": True}

    async def _persist(driver_id, points, *, persist_idle, active_ride):
        if fail_first["armed"]:
            fail_first["armed"] = False
            raise RuntimeError("db down")
        seen.append((list(points), active_ride))
        return len(points)

    with patch.object(bb, "persist_ride_breadcrumbs", _persist):
        for i in range(3):
            await bb.buffer_ride_breadcrumb("drv-bb-ctx", _point(i), active_ride=_ride("ride-X"))
        # Context change triggers the flush, which fails and stashes ride-X points.
        with pytest.raises(RuntimeError):
            await bb.buffer_ride_breadcrumb("drv-bb-ctx", _point(3), active_ride=None)
        # New idle point still buffers; final flush retries ride-X first.
        await bb.buffer_ride_breadcrumb("drv-bb-ctx", _point(4), active_ride=None)
        await bb.flush_driver_breadcrumbs("drv-bb-ctx")

    assert len(seen) == 2
    first_points, first_ride = seen[0]
    assert len(first_points) == 3 and first_ride["id"] == "ride-X"
    second_points, second_ride = seen[1]
    assert len(second_points) == 1 and second_ride is None


def test_retry_stash_caps_and_drops_oldest():
    import time as _time

    for i in range(bb._MAX_PENDING_BATCHES + 2):
        bb._stash_failed_batch(
            "drv-bb-cap",
            bb._DriverBuffer(ride_id=f"ride-{i}", ride=None, opened_at=_time.monotonic(), points=[_point(i)]),
        )

    stash = bb._pending_retry["drv-bb-cap"]
    assert len(stash) == bb._MAX_PENDING_BATCHES
    # Oldest dropped, newest retained.
    assert stash[0].ride_id == "ride-2"
    assert stash[-1].ride_id == f"ride-{bb._MAX_PENDING_BATCHES + 1}"


@pytest.mark.anyio
async def test_repeated_db_failure_does_not_grow_stash_unbounded():
    """While the DB is down, pending-retry fails first on every flush, so the
    live buffer keeps accumulating and the stash holds a single batch."""
    failing = AsyncMock(side_effect=RuntimeError("db down"))
    with patch.object(bb, "persist_ride_breadcrumbs", failing):
        for i in range(4):
            await bb.buffer_ride_breadcrumb("drv-bb-down", _point(i), active_ride=_ride())
            with pytest.raises(RuntimeError):
                await bb.flush_driver_breadcrumbs("drv-bb-down")

    assert len(bb._pending_retry["drv-bb-down"]) == 1
    assert len(bb._buffers["drv-bb-down"].points) == 3


@pytest.mark.anyio
async def test_later_ride_row_wins_so_flush_sees_fresh_milestones():
    """A ping after ride_started_at lands carries the updated ride row; the
    flush must use it so earlier buffered points are phased correctly."""
    persist = AsyncMock(return_value=2)
    stale = {"id": "ride-bb-m", "status": "driver_arrived"}
    fresh = {"id": "ride-bb-m", "status": "in_progress", "ride_started_at": "2026-06-10T12:00:00+00:00"}
    with patch.object(bb, "persist_ride_breadcrumbs", persist):
        await bb.buffer_ride_breadcrumb("drv-bb-m", _point(0), active_ride=stale)
        await bb.buffer_ride_breadcrumb("drv-bb-m", _point(1), active_ride=fresh)
        await bb.flush_driver_breadcrumbs("drv-bb-m")

    assert persist.call_args.kwargs["active_ride"] is fresh
