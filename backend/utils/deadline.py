"""Per-request deadline propagation via a contextvar.

Clients send an `X-Deadline-Ms: <epoch-ms>` header derived from their
own timeout (axios `timeout: 15000` ≈ `Date.now() + 15000`). The
backend middleware in `core/middleware.py::DeadlineMiddleware` converts
that epoch to a monotonic-clock deadline and calls
``set_request_deadline(deadline_monotonic)`` before the request
coroutine runs.

Any code inside that request coroutine can then call
``remaining_seconds()`` to get the time budget left. The canonical
consumer is ``db_supabase.run_sync``: it rejects expired work before
queue submission, bounds executor waits by the remaining budget, and
skips retry sleeps that would outlive the request.

A contextvar is used (not ``request.state``) so the value is implicitly
carried through ``asyncio.create_task`` / background coroutines spawned
inside the request, and so ``run_sync`` doesn't need to accept a
``request`` parameter on every caller.
"""

from __future__ import annotations

import time as _time
from contextvars import ContextVar, Token
from typing import Optional

_request_deadline: ContextVar[Optional[float]] = ContextVar("spinr_request_deadline_monotonic", default=None)


def set_request_deadline(
    deadline_monotonic: Optional[float],
    reset_token: Optional[Token] = None,
) -> Token:
    """Set (or clear) the monotonic-clock deadline for this request.

    `deadline_monotonic` is the point in time (from `time.monotonic()`)
    at which the client will have given up. Pass `None` + the token
    returned from the initial set to reset; middleware uses this on
    finally-cleanup so the contextvar doesn't leak between requests.
    """
    if reset_token is not None:
        _request_deadline.reset(reset_token)
        return reset_token
    return _request_deadline.set(deadline_monotonic)


def get_request_deadline() -> Optional[float]:
    """Return the monotonic-clock deadline for this request, or None."""
    return _request_deadline.get()


def remaining_seconds() -> Optional[float]:
    """Return seconds remaining until the client gives up.

    Returns:
        - None if no deadline is set for this request (absent header).
        - A positive float if time remains.
        - A negative or zero float if the deadline has passed — callers
          should skip retries and fail fast.
    """
    deadline = _request_deadline.get()
    if deadline is None:
        return None
    return deadline - _time.monotonic()


def deadline_exhausted(now_margin_seconds: float = 0.0) -> bool:
    """Return True if the deadline is in the past (with optional margin).

    `now_margin_seconds` lets callers skip a retry whose sleep would
    overshoot the deadline — e.g. before a 1.5s retry sleep, pass 1.5
    so we only attempt the retry if at least 1.5s of budget remains.
    """
    remaining = remaining_seconds()
    if remaining is None:
        return False  # no deadline set — never treat as exhausted
    return remaining <= now_margin_seconds
