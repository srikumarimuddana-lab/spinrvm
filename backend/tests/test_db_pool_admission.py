"""Pool overload rejects before any database call or retry can occur."""

import asyncio
from threading import Event
from unittest.mock import Mock

import pytest

from backend.repositories import _base
from backend.utils.bounded_executor import BoundedExecutor
from backend.utils.error_handling import ServiceUnavailableException

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("policy", ["read", "write", "idempotent_write"])
async def test_overload_is_503_without_running_or_retrying_call(monkeypatch, policy):
    entered, release = Event(), Event()
    pool = BoundedExecutor(max_workers=1, queue_size=0)
    breaker = _base._CircuitBreaker()
    breaker._state = "half_open"
    monkeypatch.setattr(_base, "_DB_EXECUTOR", pool)
    monkeypatch.setattr(_base, "_breaker", breaker)
    call = Mock()
    metrics = Mock()
    monkeypatch.setattr(_base, "_metric_inc", metrics)
    try:
        pool.submit(lambda: (entered.set(), release.wait(3)))
        assert await asyncio.to_thread(entered.wait, 1)
        with pytest.raises(ServiceUnavailableException) as error:
            await _base.run_sync(call, policy)
        assert error.value.status_code == 503
        call.assert_not_called()
        metrics.assert_any_call("spinr_db_calls_rejected_total", {"reason": "pool_full"})
        assert not breaker._probe_in_flight
        assert breaker._state == "open"
    finally:
        release.set()
        pool.shutdown(wait=True)


async def test_cancelled_queued_write_never_executes(monkeypatch):
    entered, release = Event(), Event()
    pool = BoundedExecutor(max_workers=1, queue_size=1)
    monkeypatch.setattr(_base, "_DB_EXECUTOR", pool)
    monkeypatch.setattr(_base, "_breaker", _base._CircuitBreaker())
    call = Mock()
    try:
        pool.submit(lambda: (entered.set(), release.wait(3)))
        assert await asyncio.to_thread(entered.wait, 1)
        task = asyncio.create_task(_base.run_sync(call, "write"))
        await asyncio.sleep(0)
        assert pool._work_queue.qsize() == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        pool.shutdown(wait=True)
    call.assert_not_called()
