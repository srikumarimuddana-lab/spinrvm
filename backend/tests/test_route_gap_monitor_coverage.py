"""Coverage top-up for backend/utils/route_gap_monitor.py.

Complements test_route_gap_monitor.py by exercising the branches that file
leaves untouched: `_now`, the bad-settings paths of
`_configured_threshold_seconds`, the defensive `gap_started_at is None`
early-return in `_open_gap_event`, the "ride without an id" and "unknown"
branches inside `route_gap_monitor_tick`, and the full
`route_gap_monitor_loop` background-loop body (success, CancelledError
propagation, and generic-exception swallow-and-continue).
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.utils import route_gap_monitor
from backend.utils.route_gap_monitor import GapDecision

NOW = datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


async def _settings(threshold: int) -> dict:
    return {"route_location_gap_alert_seconds": threshold}


class _StopLoop(Exception):
    """Sentinel used to escape the infinite `while True` in the loop under test."""


# --- _now (line 52) -----------------------------------------------------------


def test_now_returns_a_utc_aware_datetime() -> None:
    result = route_gap_monitor._now()
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


# --- _configured_threshold_seconds (lines 87-88, 90) --------------------------


def test_configured_threshold_uses_the_default_when_unset() -> None:
    assert route_gap_monitor._configured_threshold_seconds({}) == route_gap_monitor.DEFAULT_GAP_ALERT_SECONDS


def test_configured_threshold_parses_a_numeric_string() -> None:
    assert route_gap_monitor._configured_threshold_seconds({"route_location_gap_alert_seconds": "45"}) == 45


def test_configured_threshold_rejects_a_non_integer_value() -> None:
    with pytest.raises(ValueError, match="must be an integer"):
        route_gap_monitor._configured_threshold_seconds({"route_location_gap_alert_seconds": "not-a-number"})


def test_configured_threshold_rejects_none() -> None:
    # int(None) raises TypeError, which the function maps to the same ValueError.
    with pytest.raises(ValueError, match="must be an integer"):
        route_gap_monitor._configured_threshold_seconds({"route_location_gap_alert_seconds": None})


def test_configured_threshold_rejects_zero() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        route_gap_monitor._configured_threshold_seconds({"route_location_gap_alert_seconds": 0})


def test_configured_threshold_rejects_a_negative_value() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        route_gap_monitor._configured_threshold_seconds({"route_location_gap_alert_seconds": -5})


# --- _open_gap_event defensive guard (line 109) --------------------------------


def test_open_gap_event_returns_false_without_a_gap_start() -> None:
    # Direct unit test of the defensive guard: route_gap_monitor_tick only ever
    # calls _open_gap_event after decision.state == "gap" (which guarantees
    # gap_started_at is set), so this path is otherwise unreachable in practice.
    decision = GapDecision(state="unknown", gap_started_at=None, gap_seconds=0)

    result = _run(route_gap_monitor._open_gap_event({"id": "ride_1"}, decision, 30, NOW))

    assert result is False


# --- route_gap_monitor_tick: ride missing an id (lines 205-206) ----------------


def test_tick_skips_a_ride_row_with_no_id(monkeypatch) -> None:
    async def get_rows(table, _filters, **_kwargs):
        if table == "rides":
            return [{"driver_id": "driver_1", "ride_started_at": NOW.isoformat()}]
        raise AssertionError(f"unexpected table {table}")  # driver_location_history must not be queried

    monkeypatch.setattr(route_gap_monitor.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(route_gap_monitor, "get_app_settings", lambda: _settings(30))
    monkeypatch.setattr(route_gap_monitor, "_now", lambda: NOW)
    monkeypatch.setattr(route_gap_monitor, "_metric_gauge", lambda *_a: None)
    monkeypatch.setattr(route_gap_monitor, "_metric_inc", lambda *_a: None)

    result = _run(route_gap_monitor.route_gap_monitor_tick())

    assert result == {"scanned": 0, "opened": 0, "resolved": 0, "unknown": 0}


# --- route_gap_monitor_tick: unknown state (lines 215-216) ---------------------


def test_tick_counts_unknown_when_no_timestamps_exist_at_all(monkeypatch) -> None:
    async def get_rows(table, _filters, **_kwargs):
        if table == "rides":
            return [{"id": "ride_1", "driver_id": "driver_1", "ride_started_at": None}]
        if table == "driver_location_history":
            return []
        raise AssertionError(f"unexpected table {table}")

    monkeypatch.setattr(route_gap_monitor.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(route_gap_monitor, "get_app_settings", lambda: _settings(30))
    monkeypatch.setattr(route_gap_monitor, "_now", lambda: NOW)
    monkeypatch.setattr(route_gap_monitor, "_metric_gauge", lambda *_a: None)
    monkeypatch.setattr(route_gap_monitor, "_metric_inc", lambda *_a: None)

    result = _run(route_gap_monitor.route_gap_monitor_tick())

    assert result == {"scanned": 1, "opened": 0, "resolved": 0, "unknown": 1}


# --- route_gap_monitor_loop (lines 242-251) -------------------------------------


def test_loop_ticks_heartbeats_then_sleeps_for_the_configured_interval(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    tick_mock = AsyncMock(return_value={"scanned": 0, "opened": 0, "resolved": 0, "unknown": 0})
    heartbeats: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        raise _StopLoop()  # escape the `while True` after one full iteration

    monkeypatch.setattr(route_gap_monitor, "route_gap_monitor_tick", tick_mock)
    monkeypatch.setattr(route_gap_monitor, "_record_heartbeat", lambda msg: heartbeats.append(msg))
    monkeypatch.setattr(route_gap_monitor.asyncio, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        _run(route_gap_monitor.route_gap_monitor_loop(interval_seconds=7))

    tick_mock.assert_awaited_once()
    assert heartbeats == ["route_gap_monitor (15s)"]
    assert sleeps == [7]


def test_loop_propagates_cancelled_error_without_sleeping(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    tick_mock = AsyncMock(side_effect=asyncio.CancelledError())
    sleep_mock = AsyncMock()

    monkeypatch.setattr(route_gap_monitor, "route_gap_monitor_tick", tick_mock)
    monkeypatch.setattr(route_gap_monitor.asyncio, "sleep", sleep_mock)

    with pytest.raises(asyncio.CancelledError):
        _run(route_gap_monitor.route_gap_monitor_loop())

    sleep_mock.assert_not_awaited()


def test_loop_logs_and_continues_after_a_generic_tick_failure(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    tick_mock = AsyncMock(side_effect=RuntimeError("boom"))
    metric_calls: list[tuple] = []

    async def fake_sleep(seconds):
        raise _StopLoop()

    monkeypatch.setattr(route_gap_monitor, "route_gap_monitor_tick", tick_mock)
    monkeypatch.setattr(route_gap_monitor, "_metric_inc", lambda name, *rest: metric_calls.append((name, *rest)))
    monkeypatch.setattr(route_gap_monitor.asyncio, "sleep", fake_sleep)

    # The generic-exception branch must swallow the error (never re-raise it)
    # and still reach `asyncio.sleep` — our _StopLoop from fake_sleep is what
    # ultimately escapes, proving the loop did not crash on RuntimeError.
    with pytest.raises(_StopLoop):
        _run(route_gap_monitor.route_gap_monitor_loop())

    tick_mock.assert_awaited_once()
    assert metric_calls == [("spinr_bgloop_errors_total", {"loop": "route_gap_monitor"})]


# --- top-level import block (lines 17-22) ---------------------------------------


def test_reloading_the_module_exercises_the_primary_import_block() -> None:
    """The primary `try` import block (lines 17-22) only runs once per process,
    the first time this module is imported — every other test in this suite
    observes it already cached in `sys.modules`. Force a reload so pytest-cov
    sees those lines execute at least once under instrumentation. All of the
    imported names (db_supabase, socket_manager, get_app_settings,
    parse_iso_utc, record_heartbeat, metrics.inc/set_gauge) are already
    resolvable at this point in the suite, so the reload is expected to
    succeed via the primary path, not fall back to the `except ImportError`
    branch (which is `# pragma: no cover` and intentionally excluded).
    """
    reloaded = importlib.reload(route_gap_monitor)

    assert reloaded.ROUTE_GAP_MONITOR_INTERVAL_SECONDS == 15
    assert reloaded.DEFAULT_GAP_ALERT_SECONDS == 30
