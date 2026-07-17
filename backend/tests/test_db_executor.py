"""
B-P2-7: tests for the explicit DB ThreadPoolExecutor.

Contract:
  - run_sync uses _DB_EXECUTOR (a dedicated pool), not the asyncio default.
  - The pool's max_workers honours DB_THREAD_POOL_SIZE (default 64), with
    DB_THREAD_POOL_MAX accepted as a legacy fallback. There is exactly ONE
    pool — the old second 64-worker executor that existed only to feed the
    thread gauge is gone.
  - Threads in the pool carry the "spinr-db" name prefix so they're
    identifiable in profiling tools.
"""

from __future__ import annotations

import asyncio
import threading

import pytest


def test_db_executor_has_explicit_size_default_64():
    from repositories import _base

    # _DB_EXECUTOR is a ThreadPoolExecutor — its max_workers is what we set.
    # ThreadPoolExecutor stores it in private attribute `_max_workers`.
    assert _base._DB_THREAD_POOL_SIZE == 64
    assert _base._DB_EXECUTOR._max_workers == 64


def test_single_db_executor_no_gauge_only_twin():
    """The 32-vs-64 mismatch: run_sync ran on a 32-worker pool while a second
    64-worker pool existed only to feed spinr_db_thread_pool_threads (always 0,
    since it never ran work). Pin that the twin is gone."""
    from repositories import _base

    assert not hasattr(_base, "_db_executor")
    assert not hasattr(_base, "_DB_POOL_MAX")


def test_db_executor_threads_have_spinr_prefix():
    """When work is submitted, the thread name should start with
    'spinr-db' — confirms the pool wires through and helps on-call
    identify thread origin in stack dumps."""
    import db_supabase

    captured_name: dict[str, str] = {}

    def _record_thread_name():
        captured_name["name"] = threading.current_thread().name

    fut = db_supabase._DB_EXECUTOR.submit(_record_thread_name)
    fut.result(timeout=2.0)

    assert captured_name["name"].startswith("spinr-db"), (
        f"DB executor thread should start with 'spinr-db', got {captured_name['name']!r}"
    )


@pytest.mark.anyio
async def test_run_sync_dispatches_to_db_executor(monkeypatch):
    """run_sync must hand the function to _DB_EXECUTOR specifically,
    not the default executor (None)."""
    import db_supabase

    seen: dict[str, object] = {"executor": "<not called>"}

    real_run_in_executor = None

    async def fake_run_in_executor(self_loop, executor, fn, *args, **kwargs):
        seen["executor"] = executor
        return fn(*args)

    # Patch the bound method on the running loop. Easiest path: monkey-patch
    # asyncio.get_running_loop().run_in_executor to capture the executor arg.
    real_loop = asyncio.get_running_loop()
    real_run_in_executor = real_loop.run_in_executor

    async def captured(executor, fn, *args, **kwargs):
        seen["executor"] = executor
        # Still run the fn synchronously so the test sees the result.
        return fn(*args, **kwargs)

    real_loop.run_in_executor = captured  # type: ignore[method-assign]
    try:
        result = await db_supabase.run_sync(lambda: 42)
    finally:
        real_loop.run_in_executor = real_run_in_executor  # type: ignore[method-assign]

    assert result == 42
    assert seen["executor"] is db_supabase._DB_EXECUTOR, f"run_sync should pass _DB_EXECUTOR, got {seen['executor']!r}"


@pytest.mark.anyio
async def test_expired_deadline_rejects_before_executor_submission(monkeypatch):
    from repositories import _base
    from utils.error_handling import ServiceUnavailableException

    _base._breaker.record_success()
    monkeypatch.setattr(_base, "_remaining_seconds", lambda: 0.0, raising=False)
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor

    def unexpected_submission(*_args, **_kwargs):
        raise AssertionError("expired work must not enter the DB queue")

    loop.run_in_executor = unexpected_submission  # type: ignore[method-assign]
    try:
        with pytest.raises(ServiceUnavailableException):
            await _base.run_sync(lambda: 42)
    finally:
        loop.run_in_executor = original  # type: ignore[method-assign]

    assert _base._breaker._state == "closed"


@pytest.mark.anyio
async def test_slow_executor_wait_respects_deadline_without_tripping_breaker(monkeypatch):
    from repositories import _base
    from utils.error_handling import ServiceUnavailableException

    _base._breaker.record_success()
    monkeypatch.setattr(_base, "_remaining_seconds", lambda: 0.01, raising=False)
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    pending = loop.create_future()
    loop.run_in_executor = lambda *_args, **_kwargs: pending  # type: ignore[method-assign]
    try:
        with pytest.raises(ServiceUnavailableException):
            await asyncio.wait_for(_base.run_sync(lambda: 42), timeout=0.1)
    finally:
        loop.run_in_executor = original  # type: ignore[method-assign]

    assert pending.cancelled()
    assert _base._breaker._state == "closed"


@pytest.mark.anyio
async def test_provider_timeout_is_not_mislabeled_as_client_deadline(monkeypatch):
    from repositories import _base
    from utils.error_handling import DatabaseError

    _base._breaker.record_success()
    monkeypatch.setattr(_base, "_remaining_seconds", lambda: 1.0)
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor
    failed = loop.create_future()
    failed.set_exception(TimeoutError("provider operation timed out"))
    loop.run_in_executor = lambda *_args, **_kwargs: failed  # type: ignore[method-assign]
    try:
        with pytest.raises(DatabaseError) as exc_info:
            await _base.run_sync(lambda: 42, retry_policy="write")
    finally:
        loop.run_in_executor = original  # type: ignore[method-assign]

    assert exc_info.value.details["original"] == "provider operation timed out"


@pytest.mark.anyio
async def test_run_sync_emits_queue_depth_gauge(monkeypatch):
    from repositories import _base

    _base._breaker.record_success()
    monkeypatch.setattr(_base, "_remaining_seconds", lambda: None, raising=False)
    gauges = []
    monkeypatch.setattr(_base, "_metric_gauge", lambda name, value, labels=None: gauges.append((name, value)))

    assert await _base.run_sync(lambda: 42) == 42
    assert any(name == "spinr_db_thread_pool_queue_depth" for name, _value in gauges)
