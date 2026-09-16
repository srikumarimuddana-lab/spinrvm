"""Bound queued work without letting cancelled callers bypass admission limits."""

from concurrent.futures import Future, ThreadPoolExecutor
from threading import BoundedSemaphore, Lock


class ExecutorSaturated(RuntimeError):
    """All running and queued work slots are occupied."""


class BoundedExecutor(ThreadPoolExecutor):
    def __init__(self, *, max_workers: int, queue_size: int, thread_name_prefix: str = ""):
        if queue_size < 0:
            raise ValueError("queue_size must be nonnegative")
        super().__init__(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
        self._slots = BoundedSemaphore(max_workers + queue_size)

    def submit(self, fn, /, *args, **kwargs):
        if not self._slots.acquire(blocking=False):
            raise ExecutorSaturated("executor capacity exhausted")
        result = Future()
        release_lock = Lock()
        released = False

        def release_slot():
            nonlocal released
            with release_lock:
                if not released:
                    released = True
                    self._slots.release()

        def run():
            try:
                # Only dequeuing work releases a cancelled queued call's slot.
                # Exposing the inner future would permit cancellation storms to
                # accumulate cancelled WorkItems in ThreadPoolExecutor's queue.
                if result.set_running_or_notify_cancel():
                    try:
                        value = fn(*args, **kwargs)
                    except BaseException as error:
                        result.set_exception(error)
                    else:
                        result.set_result(value)
            finally:
                release_slot()

        try:
            inner = super().submit(run)
        except BaseException:
            # submit can enqueue before thread creation fails. Close admission
            # and drain retained work rather than reopen a slot over that item.
            result.cancel()
            super().shutdown(wait=False, cancel_futures=True)
            release_slot()
            raise

        def on_shutdown(future):
            if future.cancelled():
                # shutdown(cancel_futures=True) removed this item from the queue.
                result.cancel()
                release_slot()

        inner.add_done_callback(on_shutdown)
        return result
