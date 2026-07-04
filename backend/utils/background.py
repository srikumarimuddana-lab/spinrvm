"""Strong-reference registry for fire-and-forget asyncio tasks.

The event loop keeps only a weak reference to tasks (see the
asyncio.create_task docs): a task nobody references can be garbage-collected
mid-flight, silently dropping the work. For dispatch that means a booked ride
whose match_driver_to_ride never runs, with zero log evidence. Route handlers
that background work without awaiting it must go through spawn() so the task
stays referenced until it completes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine

_BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> "asyncio.Task | None":
    """asyncio.create_task + retain a strong reference until the task is done."""
    task = asyncio.create_task(coro)
    # Tests intercept the asyncio.create_task seam with doubles that return
    # None or a MagicMock; only real tasks join the registry.
    if isinstance(task, asyncio.Task):
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task
