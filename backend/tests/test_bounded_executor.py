"""Real threads prove cancellation cannot reopen an unbounded submission queue."""

import asyncio
import time
import unittest
from threading import Event
from unittest.mock import patch

from backend.utils.bounded_executor import BoundedExecutor, ExecutorSaturated


class ExecutorTests(unittest.TestCase):
    def test_queue_is_bounded_even_when_queued_futures_are_cancelled(self):
        entered, release = Event(), Event()
        pool = BoundedExecutor(max_workers=1, queue_size=1)
        ran_cancelled = Event()

        def blocked():
            entered.set()
            release.wait(2)

        try:
            running = pool.submit(blocked)
            self.assertTrue(entered.wait(1))
            queued = pool.submit(ran_cancelled.set)
            self.assertTrue(queued.cancel())
            for _ in range(100):
                with self.assertRaises(ExecutorSaturated):
                    pool.submit(lambda: None)
            release.set()
            running.result(timeout=1)
        finally:
            release.set()
            pool.shutdown(wait=True)
        self.assertFalse(ran_cancelled.is_set())

    def test_success_and_exception_release_capacity(self):
        with BoundedExecutor(max_workers=1, queue_size=0) as pool:
            self.assertEqual(pool.submit(lambda: 42).result(timeout=1), 42)
            deadline = time.monotonic() + 1
            while True:
                try:
                    result = pool.submit(lambda: 1 / 0)
                    break
                except ExecutorSaturated:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.001)
            with self.assertRaises(ZeroDivisionError):
                result.result(timeout=1)

    def test_shutdown_cancels_queued_proxy_and_rejects_new_work(self):
        entered, release = Event(), Event()
        pool = BoundedExecutor(max_workers=1, queue_size=1)
        try:
            pool.submit(lambda: (entered.set(), release.wait(2)))
            self.assertTrue(entered.wait(1))
            queued = pool.submit(lambda: 1)
            pool.shutdown(wait=False, cancel_futures=True)
            self.assertTrue(queued.cancelled())
            with self.assertRaises(RuntimeError):
                pool.submit(lambda: 1)
        finally:
            release.set()
            pool.shutdown(wait=True)

    def test_worker_creation_failure_closes_and_drains_pool(self):
        pool = BoundedExecutor(max_workers=1, queue_size=0)
        try:
            with patch.object(pool, "_adjust_thread_count", side_effect=RuntimeError("cannot start new thread")):
                for _ in range(3):
                    with self.assertRaises(RuntimeError):
                        pool.submit(lambda: None)
            self.assertTrue(pool._shutdown)
            # shutdown may leave a sentinel, but no retained call objects.
            while not pool._work_queue.empty():
                self.assertIsNone(pool._work_queue.get_nowait())
        finally:
            pool.shutdown(wait=True)

    def test_initializer_is_not_supported(self):
        with self.assertRaises(TypeError):
            BoundedExecutor(max_workers=1, queue_size=0, initializer=lambda: None)

    def test_negative_queue_is_rejected(self):
        with self.assertRaises(ValueError):
            BoundedExecutor(max_workers=1, queue_size=-1)


class AsyncCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_waiter_does_not_release_running_thread_slot(self):
        entered, release = Event(), Event()
        pool = BoundedExecutor(max_workers=1, queue_size=0)
        try:
            future = asyncio.get_running_loop().run_in_executor(pool, lambda: (entered.set(), release.wait(2)))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            future.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await future
            with self.assertRaises(ExecutorSaturated):
                pool.submit(lambda: 1)
        finally:
            release.set()
            pool.shutdown(wait=True)
