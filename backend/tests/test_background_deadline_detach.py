"""Backgrounded work must not inherit the request's time budget.

utils/deadline.py stores the client's budget in a ContextVar specifically so it
propagates into tasks spawned inside a request. That is right for work the
handler awaits and wrong for work that outlives the response: a fire-and-forget
task inherits a budget measured from when the *rider* started waiting, so once
the response is sent the budget is spent and every run_sync() inside that task
is rejected with `deadline_exhausted` before it reaches the DB.

The failure is silent and consequential — a lost audit row (routes/wallet.py,
routes/payments.py) or a dispatch retry that never fires
(routes/rides/matching.py) — and it also inflates the counter the capacity
watchdog alerts on.

See backend/utils/background.py::spawn.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from utils.background import spawn
from utils.deadline import remaining_seconds, set_request_deadline

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_spawned_task_does_not_inherit_the_request_deadline():
    observed: list = []

    async def _work():
        observed.append(remaining_seconds())

    token = set_request_deadline(time.monotonic() + 5.0)
    try:
        task = spawn(_work())
        await task
    finally:
        set_request_deadline(None, reset_token=token)

    assert observed == [None], f"background task inherited a deadline: {observed}"


@pytest.mark.asyncio
async def test_spawned_task_is_unaffected_by_an_already_expired_deadline():
    """The exact production shape: the response is sent, the budget is gone,
    and the backgrounded audit write still has to succeed."""
    observed: list = []

    async def _work():
        observed.append(remaining_seconds())

    token = set_request_deadline(time.monotonic() - 30.0)  # long expired
    try:
        await spawn(_work())
    finally:
        set_request_deadline(None, reset_token=token)

    assert observed == [None]


@pytest.mark.asyncio
async def test_spawn_does_not_clear_the_callers_own_deadline():
    """Clearing happens inside the task's own copy of the context. The request
    coroutine still needs its budget for the work it awaits itself."""
    deadline = time.monotonic() + 5.0
    token = set_request_deadline(deadline)
    try:
        await spawn(asyncio.sleep(0))
        still_set = remaining_seconds()
    finally:
        set_request_deadline(None, reset_token=token)

    assert still_set is not None, "spawn() leaked its detach into the caller"
    assert still_set > 0


@pytest.mark.asyncio
async def test_spawn_still_returns_the_task_and_propagates_the_result():
    """Detaching must not change spawn()'s contract."""

    async def _work():
        return "done"

    task = spawn(_work())
    assert isinstance(task, asyncio.Task)
    assert await task == "done"


@pytest.mark.asyncio
async def test_spawn_keeps_a_strong_reference_until_completion():
    """The original reason spawn() exists — a task nobody references can be GC'd
    mid-flight, silently dropping the work."""
    from utils.background import _BACKGROUND_TASKS

    started = asyncio.Event()
    release = asyncio.Event()

    async def _work():
        started.set()
        await release.wait()

    task = spawn(_work())
    await started.wait()
    assert task in _BACKGROUND_TASKS

    release.set()
    await task
    await asyncio.sleep(0)  # let the done-callback run
    assert task not in _BACKGROUND_TASKS
