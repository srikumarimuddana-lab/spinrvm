"""
A1c Sub-tier C coverage: backend/utils/presence_sweeper.py (73% -> target 95%+).

This module is a documented RETIRED no-op (see its module docstring): the
lifespan startup no longer schedules `presence_sweeper_loop`, and
`_sweep_once` is a permanent no-op kept only so
`test_p3_loop_jitter_metrics.py`'s loop-jitter/metrics-shape tests keep a
stable symbol to import. Those two existing tests cover the loop's
successful-tick metric emission and two-sleep jitter shape. This file
closes the remaining gaps:

- `_sweep_once`'s actual return value, called directly (not mocked).
- `presence_sweeper_loop`'s exception branch: a `_sweep_once` failure is
  caught, logged, and counted via `spinr_bgloop_errors_total` (the loop
  survives) -- the existing metrics test only exercises the success path
  (0 `inc_calls`).
- `presence_sweeper_loop`'s `except asyncio.CancelledError: raise` branch:
  a cancellation raised from inside `_sweep_once` itself must propagate,
  not be swallowed by the broader `except Exception` below it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio

P = "backend.utils.presence_sweeper."


async def test_sweep_once_is_a_real_noop_returning_zero():
    from backend.utils.presence_sweeper import _sweep_once

    result = await _sweep_once()
    assert result == 0


async def test_loop_tick_exception_is_caught_logged_and_counted():
    from backend.utils import presence_sweeper as m

    async def failing_sweep():
        raise RuntimeError("boom")

    sleep_calls = []

    async def fake_sleep(secs):
        sleep_calls.append(secs)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    with (
        patch.object(m, "_sweep_once", failing_sweep),
        patch("asyncio.sleep", fake_sleep),
        patch.object(m, "_metric_gauge") as mock_gauge,
        patch.object(m, "_metric_inc") as mock_inc,
    ):
        with pytest.raises(asyncio.CancelledError):
            await m.presence_sweeper_loop()
    mock_gauge.assert_called_once()
    mock_inc.assert_called_once_with("spinr_bgloop_errors_total", {"loop": "presence_sweeper"})


async def test_loop_cancelled_error_from_sweep_propagates_not_swallowed():
    from backend.utils import presence_sweeper as m

    async def cancelling_sweep():
        raise asyncio.CancelledError()

    sleep_calls = []

    async def fake_sleep(secs):
        sleep_calls.append(secs)

    with (
        patch.object(m, "_sweep_once", cancelling_sweep),
        patch("asyncio.sleep", fake_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await m.presence_sweeper_loop()
    # Only the initial startup jitter sleep happened before cancellation.
    assert len(sleep_calls) == 1
