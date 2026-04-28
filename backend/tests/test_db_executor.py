"""
B-P2-7: tests for the explicit DB ThreadPoolExecutor.

Contract:
  - run_sync uses _DB_EXECUTOR (a dedicated pool), not the asyncio default.
  - The pool's max_workers honours DB_THREAD_POOL_SIZE (default 32).
  - Threads in the pool carry the "spinr-db" name prefix so they're
    identifiable in profiling tools.
"""

from __future__ import annotations

import threading

import pytest


def test_db_executor_has_explicit_size_default_32():
    import db_supabase

    # _DB_EXECUTOR is a ThreadPoolExecutor — its max_workers is what we set.
    # ThreadPoolExecutor stores it in private attribute `_max_workers`.
    assert db_supabase._DB_THREAD_POOL_SIZE == 32
    assert db_supabase._DB_EXECUTOR._max_workers == 32


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
    import asyncio

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
