"""Unit tests for utils/loop_alert.py.

Pins:
  - No HTTP call when webhook_url is None
  - No HTTP call when no loops are stale
  - Posts webhook when a loop is stale
  - Posts during the first hour of uptime (regression: see below)
  - Throttles: second call within cooldown does not re-post
  - A failed POST does not consume the cooldown
  - Logs an error when the POST fails; does not raise
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Use a fixed "now" larger than COOLDOWN_SECONDS so the throttle window check
# is deterministic regardless of actual system uptime.
#
# NOTE: this choice is also what let the early-uptime bug survive review — every
# test ran at monotonic()=10000, comfortably past the cooldown, so none of them
# ever exercised the `now - 0.0 < COOLDOWN_SECONDS` path that suppressed alerts
# for the first hour of a process's life. test_posts_during_first_hour_of_uptime
# below deliberately runs at a small monotonic value to cover it.
_FAKE_NOW = 10_000.0  # 10 000 s >> COOLDOWN_SECONDS (3 600)


def _stale_status(name: str = "surge_engine (2min)") -> dict:
    return {
        "healthy": False,
        "loops": {
            name: {
                "status": "stale",
                "seconds_since_tick": 600.0,
                "threshold_seconds": 480.0,
            }
        },
    }


def _ok_status() -> dict:
    return {
        "healthy": True,
        "loops": {
            "surge_engine (2min)": {
                "status": "ok",
                "seconds_since_tick": 60.0,
                "threshold_seconds": 480.0,
            }
        },
    }


def _make_http_mocks(post_side_effect=None):
    """Return (cm_mock, inner_client_mock) for patching httpx.AsyncClient.

    httpx.AsyncClient is used as 'async with httpx.AsyncClient(...) as client:'.
    cm_mock   — what AsyncClient() returns (the context manager)
    inner     — what 'client' is bound to inside the 'async with' block
    """
    inner = AsyncMock()
    if post_side_effect is not None:
        inner.post.side_effect = post_side_effect
    else:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        inner.post.return_value = resp

    cm_mock = AsyncMock()
    cm_mock.__aenter__.return_value = inner
    return cm_mock, inner


@pytest.mark.anyio
async def test_no_call_when_no_webhook():
    import backend.utils.loop_alert as mod

    with patch.object(mod, "get_loop_status", return_value=_stale_status()):
        with patch("httpx.AsyncClient") as mock_cls:
            await mod.check_and_alert(webhook_url=None)
            mock_cls.assert_not_called()


@pytest.mark.anyio
async def test_no_call_when_no_stale_loops():
    import backend.utils.loop_alert as mod

    with patch.object(mod, "get_loop_status", return_value=_ok_status()):
        with patch("httpx.AsyncClient") as mock_cls:
            await mod.check_and_alert(webhook_url="https://hooks.example.com/T/B/x")
            mock_cls.assert_not_called()


@pytest.mark.anyio
async def test_posts_webhook_for_stale_loop():
    import backend.utils.loop_alert as mod

    mod._last_alerted.clear()
    cm_mock, inner = _make_http_mocks()

    with patch.object(mod, "get_loop_status", return_value=_stale_status()):
        with patch("backend.utils.loop_alert.time") as mock_time:
            mock_time.monotonic.return_value = _FAKE_NOW
            with patch("httpx.AsyncClient", return_value=cm_mock):
                await mod.check_and_alert(webhook_url="https://hooks.example.com/T/B/x")

    inner.post.assert_called_once()
    _, kwargs = inner.post.call_args
    assert "surge_engine" in kwargs["json"]["text"]


@pytest.mark.anyio
@pytest.mark.parametrize("uptime_seconds", [0.4, 12.0, 300.0, 3599.0])
async def test_posts_during_first_hour_of_uptime(uptime_seconds):
    """Regression: a stale loop must alert even seconds after process start.

    The cooldown used to compare against a 0.0 default, and time.monotonic()
    is near zero early in a process's life — so `now - 0.0 < 3600` held for the
    first hour of uptime and silently swallowed every alert. That window is
    precisely when a loop is most likely to have failed to start at all, on a
    fresh deploy, a restart, or a Fly machine waking from suspend. This is the
    only alerting path live in production, so the failure it exists to report
    was the one it could not report.
    """
    import backend.utils.loop_alert as mod

    mod._last_alerted.clear()
    cm_mock, inner = _make_http_mocks()

    with patch.object(mod, "get_loop_status", return_value=_stale_status()):
        with patch("backend.utils.loop_alert.time") as mock_time:
            mock_time.monotonic.return_value = uptime_seconds
            with patch("httpx.AsyncClient", return_value=cm_mock):
                await mod.check_and_alert(webhook_url="https://hooks.example.com/T/B/x")

    inner.post.assert_called_once(), f"alert suppressed at uptime={uptime_seconds}s"


@pytest.mark.anyio
async def test_failed_post_does_not_consume_the_cooldown():
    """One flaky webhook call must not silence a genuinely stale loop for the
    next hour — the cooldown is only stamped after a POST actually succeeds."""
    import backend.utils.loop_alert as mod

    mod._last_alerted.clear()
    cm_mock, _ = _make_http_mocks(post_side_effect=Exception("connection refused"))

    with patch.object(mod, "get_loop_status", return_value=_stale_status()):
        with patch("backend.utils.loop_alert.time") as mock_time:
            mock_time.monotonic.return_value = _FAKE_NOW
            with patch("httpx.AsyncClient", return_value=cm_mock):
                await mod.check_and_alert(webhook_url="https://hooks.example.com/T/B/x")

    assert "surge_engine (2min)" not in mod._last_alerted

    # And the very next check does retry rather than being throttled out.
    cm_mock_ok, inner_ok = _make_http_mocks()
    with patch.object(mod, "get_loop_status", return_value=_stale_status()):
        with patch("backend.utils.loop_alert.time") as mock_time:
            mock_time.monotonic.return_value = _FAKE_NOW + 1
            with patch("httpx.AsyncClient", return_value=cm_mock_ok):
                await mod.check_and_alert(webhook_url="https://hooks.example.com/T/B/x")

    inner_ok.post.assert_called_once()


@pytest.mark.anyio
async def test_throttles_repeat_alert():
    import backend.utils.loop_alert as mod

    mod._last_alerted.clear()
    # Simulate an alert already sent at fake_now - 100 s (within 1-hour cooldown)
    mod._last_alerted["surge_engine (2min)"] = _FAKE_NOW - 100

    cm_mock, inner = _make_http_mocks()

    with patch.object(mod, "get_loop_status", return_value=_stale_status()):
        with patch("backend.utils.loop_alert.time") as mock_time:
            mock_time.monotonic.return_value = _FAKE_NOW
            with patch("httpx.AsyncClient", return_value=cm_mock):
                await mod.check_and_alert(webhook_url="https://hooks.example.com/T/B/x")

    inner.post.assert_not_called()


@pytest.mark.anyio
async def test_logs_error_on_post_failure(caplog):
    import logging

    import backend.utils.loop_alert as mod

    mod._last_alerted.clear()
    cm_mock, _ = _make_http_mocks(post_side_effect=Exception("connection refused"))

    with patch.object(mod, "get_loop_status", return_value=_stale_status()):
        with patch("backend.utils.loop_alert.time") as mock_time:
            mock_time.monotonic.return_value = _FAKE_NOW
            with patch("httpx.AsyncClient", return_value=cm_mock):
                with caplog.at_level(logging.ERROR, logger="backend.utils.loop_alert"):
                    await mod.check_and_alert(webhook_url="https://hooks.example.com/T/B/x")

    assert any("failed to post" in r.message.lower() for r in caplog.records)
