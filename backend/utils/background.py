"""Strong-reference registry for fire-and-forget asyncio tasks.

The event loop keeps only a weak reference to tasks (see the
asyncio.create_task docs): a task nobody references can be garbage-collected
mid-flight, silently dropping the work. For dispatch that means a booked ride
whose match_driver_to_ride never runs, with zero log evidence. Route handlers
that background work without awaiting it must go through spawn() so the task
stays referenced until it completes.

spawn() also DETACHES the task from the request's deadline. utils/deadline.py
stores the client's time budget in a ContextVar precisely so it propagates into
tasks — correct for work the caller awaits, wrong for work that outlives the
response. A backgrounded task inherits a budget measured from when the *client*
started waiting, so once the response is sent the budget is spent and every
run_sync() inside that task is rejected with `deadline_exhausted` before it
reaches the DB. The work is dropped, the counter climbs, and nothing links the
two. Concretely that is a lost audit row (routes/wallet.py, routes/payments.py)
or a dispatch retry that never fires (routes/rides/matching.py).

Backgrounded work is bounded by its own logic, not by how long a rider was
willing to wait for an HTTP response.
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine

try:
    from .deadline import set_request_deadline
except ImportError:  # pragma: no cover - exercised by the non-package entrypoint
    from utils.deadline import set_request_deadline  # type: ignore

_BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> "asyncio.Task | None":
    """asyncio.create_task + retain a strong reference until the task is done.

    The task runs with the request deadline cleared — see the module docstring.

    Detaching is done by clearing the ContextVar around the create_task call
    rather than by wrapping `coro`. asyncio copies the current context at task
    creation, so the task's copy sees None while the caller's own deadline is
    restored immediately afterwards. Wrapping would have been the obvious
    implementation and is wrong twice over: the caller's coroutine must reach
    asyncio.create_task unchanged (tests patch that seam and assert on the
    coroutine they are handed), and a wrapper coroutine left un-awaited by such
    a double leaks the real work with only a RuntimeWarning.
    """
    reset_token = set_request_deadline(None)
    try:
        task = asyncio.create_task(coro)
    finally:
        set_request_deadline(None, reset_token=reset_token)
    # Tests intercept the asyncio.create_task seam with doubles that return
    # None or a MagicMock; only real tasks join the registry.
    if isinstance(task, asyncio.Task):
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task
