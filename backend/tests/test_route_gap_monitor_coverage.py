"""Coverage-gap tests for backend/utils/route_gap_monitor.py (A1c Sub-tier C,
batch locintegrity-routegap-routedist).

`tests/test_route_gap_monitor.py` already covers `assess_location_gap`'s
state branches, one idempotent-open and one resolve tick, the lifespan-loop
registration check, the migration DDL shape, and the driver location-health
nudge. This file closes the remaining branches: `_now()`, the two
`_configured_threshold_seconds` validation failures (non-integer setting,
non-positive setting), `_open_gap_event`'s no-gap-start no-op, a tick that
scans a ride with no id and one whose state is `unknown`, and the
`route_gap_monitor_loop` wrapper's success/error/cancellation paths.

No raw GPS coordinates appear anywhere in this module or these tests — the
monitor is deliberately timestamp-only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from backend.utils import route_gap_monitor
from backend.utils.route_gap_monitor import (
    GapDecision,
    _configured_threshold_seconds,
    _now,
    _open_gap_event,
)

NOW = datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc)


def test_now_returns_a_timezone_aware_utc_datetime():
    result = _now()
    assert result.tzinfo is not None
    assert result.tzinfo.utcoffset(result) == timedelta(0)


# ── _configured_threshold_seconds validation ──────────────────────────────────


def test_configured_threshold_seconds_reads_the_configured_value():
    assert _configured_threshold_seconds({"route_location_gap_alert_seconds": 45}) == 45


def test_configured_threshold_seconds_defaults_when_unset():
    assert _configured_threshold_seconds({}) == route_gap_monitor.DEFAULT_GAP_ALERT_SECONDS


def test_configured_threshold_seconds_rejects_a_non_integer_setting():
    with pytest.raises(ValueError, match="must be an integer"):
        _configured_threshold_seconds({"route_location_gap_alert_seconds": "not-a-number"})


def test_configured_threshold_seconds_rejects_a_non_positive_setting():
    with pytest.raises(ValueError, match="must be positive"):
        _configured_threshold_seconds({"route_location_gap_alert_seconds": 0})

    with pytest.raises(ValueError, match="must be positive"):
        _configured_threshold_seconds({"route_location_gap_alert_seconds": -5})


# ── _open_gap_event: no-op when there is nothing to open ─────────────────────


@pytest.mark.asyncio
async def test_open_gap_event_is_a_noop_when_the_decision_has_no_gap_start(monkeypatch):
    decision = GapDecision(state="unknown", gap_started_at=None, gap_seconds=0)
    insert = AsyncMock()
    # monkeypatch (not a manual assign/del) so the real db_supabase module
    # attribute is restored after the test, even on failure — a stray `del`
    # here previously removed insert_many_ignore_conflicts from the shared
    # db_supabase module for the rest of the process.
    monkeypatch.setattr(route_gap_monitor.db_supabase, "insert_many_ignore_conflicts", insert)

    opened = await _open_gap_event({"id": "ride_1"}, decision, 30, NOW)

    assert opened is False
    insert.assert_not_called()


# ── route_gap_monitor_tick: id-less ride and unknown-state ride ──────────────


@pytest.mark.asyncio
async def test_tick_skips_a_ride_without_an_id_and_still_scans_the_rest(monkeypatch):
    async def get_rows(table, _filters, **_kwargs):
        if table == "rides":
            return [
                {"driver_id": "driver_0", "ride_started_at": (NOW - timedelta(minutes=1)).isoformat()},  # no id
                {"id": "ride_1", "driver_id": "driver_1", "ride_started_at": (NOW - timedelta(seconds=5)).isoformat()},
            ]
        if table == "driver_location_history":
            return [{"captured_at": (NOW - timedelta(seconds=1)).isoformat()}]
        raise AssertionError(f"unexpected table {table}")

    monkeypatch.setattr(route_gap_monitor.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(route_gap_monitor.db_supabase, "update_one", AsyncMock(return_value=None))
    monkeypatch.setattr(route_gap_monitor, "get_app_settings", lambda: _settings(30))
    monkeypatch.setattr(route_gap_monitor, "_now", lambda: NOW)
    monkeypatch.setattr(route_gap_monitor, "_metric_gauge", lambda *_args: None)
    monkeypatch.setattr(route_gap_monitor, "_metric_inc", lambda *_args: None)

    result = await route_gap_monitor.route_gap_monitor_tick()

    # Only the id-bearing ride is scanned; it is healthy (1s < 30s threshold),
    # and there is no pre-existing open gap event to resolve.
    assert result == {"scanned": 1, "opened": 0, "resolved": 0, "unknown": 0}


@pytest.mark.asyncio
async def test_tick_counts_a_ride_with_no_start_time_and_no_capture_as_unknown(monkeypatch):
    async def get_rows(table, _filters, **_kwargs):
        if table == "rides":
            return [{"id": "ride_1", "driver_id": "driver_1", "ride_started_at": None}]
        if table == "driver_location_history":
            return []
        raise AssertionError(f"unexpected table {table}")

    monkeypatch.setattr(route_gap_monitor.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(route_gap_monitor, "get_app_settings", lambda: _settings(30))
    monkeypatch.setattr(route_gap_monitor, "_now", lambda: NOW)
    monkeypatch.setattr(route_gap_monitor, "_metric_gauge", lambda *_args: None)
    monkeypatch.setattr(route_gap_monitor, "_metric_inc", lambda *_args: None)

    result = await route_gap_monitor.route_gap_monitor_tick()

    assert result == {"scanned": 1, "opened": 0, "resolved": 0, "unknown": 1}


# ── route_gap_monitor_loop: success / error / cancellation ───────────────────


@pytest.mark.asyncio
async def test_loop_ticks_records_a_heartbeat_then_sleeps(monkeypatch):
    tick = AsyncMock(return_value={"scanned": 0, "opened": 0, "resolved": 0, "unknown": 0})
    heartbeats: list[str] = []
    sleeps: list[int] = []

    async def fake_sleep(_seconds):
        raise asyncio.CancelledError  # stop the loop after one iteration

    monkeypatch.setattr(route_gap_monitor, "route_gap_monitor_tick", tick)
    monkeypatch.setattr(route_gap_monitor, "_record_heartbeat", lambda name: heartbeats.append(name))
    monkeypatch.setattr(route_gap_monitor.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await route_gap_monitor.route_gap_monitor_loop(interval_seconds=15)

    tick.assert_awaited_once()
    assert heartbeats == ["route_gap_monitor (15s)"]


@pytest.mark.asyncio
async def test_loop_records_a_bgloop_error_metric_and_keeps_running_after_a_tick_failure(monkeypatch):
    tick = AsyncMock(side_effect=RuntimeError("db down"))
    metrics: list[tuple] = []

    async def fake_sleep(_seconds):
        raise asyncio.CancelledError  # stop after the failed tick is handled

    monkeypatch.setattr(route_gap_monitor, "route_gap_monitor_tick", tick)
    monkeypatch.setattr(route_gap_monitor, "_record_heartbeat", lambda _name: None)
    monkeypatch.setattr(route_gap_monitor, "_metric_inc", lambda name, *args: metrics.append((name, args)))
    monkeypatch.setattr(route_gap_monitor.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await route_gap_monitor.route_gap_monitor_loop(interval_seconds=15)

    tick.assert_awaited_once()
    assert metrics == [("spinr_bgloop_errors_total", ({"loop": "route_gap_monitor"},))]


@pytest.mark.asyncio
async def test_loop_propagates_cancellation_from_the_tick_itself_without_recording_an_error_metric(monkeypatch):
    tick = AsyncMock(side_effect=asyncio.CancelledError)
    metrics: list[tuple] = []

    monkeypatch.setattr(route_gap_monitor, "route_gap_monitor_tick", tick)
    monkeypatch.setattr(route_gap_monitor, "_metric_inc", lambda name, *args: metrics.append((name, args)))

    with pytest.raises(asyncio.CancelledError):
        await route_gap_monitor.route_gap_monitor_loop(interval_seconds=15)

    tick.assert_awaited_once()
    # Cancellation is re-raised as-is — it is not treated as a tick failure.
    assert metrics == []


async def _settings(threshold: int) -> dict:
    return {"route_location_gap_alert_seconds": threshold}
