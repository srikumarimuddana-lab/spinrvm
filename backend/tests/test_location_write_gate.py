"""Contract tests for the driver location marker write gate.

The gate coalesces ``drivers``-row position UPDATEs across every GPS ingestion
route (REST v1/v2, WebSocket single/batch) onto one Redis-keyed window, so the
REST and WebSocket paths stop writing the same row from two uncoordinated
throttles.

The invariants worth defending here are the ones whose failure is silent:
  - it FAILS OPEN, so a Redis outage can never suppress a durable write;
  - a Period 1 insurance accumulator write is NEVER skipped;
  - while the flag is off it is pure measurement — every write still lands.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import utils.location_write_gate as gate


@pytest.fixture
def counted(monkeypatch):
    """Capture (metric_name, labels) tuples emitted by the gate."""
    calls: list[tuple] = []

    def _fake_inc(name, labels=None, by=1):
        calls.append((name, labels or {}))

    monkeypatch.setattr(gate, "_metric_inc", _fake_inc)
    return calls


def _outcomes(calls):
    return [lbl.get("outcome") for name, lbl in calls if name == "spinr_drivers_location_write_total"]


@pytest.mark.anyio
async def test_writes_when_window_is_free(monkeypatch, counted):
    """Acquiring the NX key means no recent write — caller proceeds."""
    monkeypatch.setattr(gate, "redis_set_nx", AsyncMock(return_value=True))

    assert await gate.should_write_marker("driver-1", path="rest_v2_trip") is True
    assert _outcomes(counted) == ["written"]


@pytest.mark.anyio
async def test_throttles_when_flag_enabled(monkeypatch, counted):
    """Losing the NX key with the flag ON skips the redundant write."""
    monkeypatch.setattr(gate, "redis_set_nx", AsyncMock(return_value=False))
    monkeypatch.setattr(gate, "_gate_enabled", AsyncMock(return_value=True))

    assert await gate.should_write_marker("driver-1", path="ws_single") is False
    assert _outcomes(counted) == ["throttled"]


@pytest.mark.anyio
async def test_shadow_mode_counts_but_still_writes(monkeypatch, counted):
    """Flag OFF is pure measurement: count the would-be skip, write anyway.

    This is the ship state. If it ever returns False the gate has started
    changing behaviour before anyone flipped the flag.
    """
    monkeypatch.setattr(gate, "redis_set_nx", AsyncMock(return_value=False))
    monkeypatch.setattr(gate, "_gate_enabled", AsyncMock(return_value=False))

    assert await gate.should_write_marker("driver-1", path="rest_v1") is True
    assert _outcomes(counted) == ["shadow_throttled"]


@pytest.mark.anyio
async def test_fails_open_when_redis_unavailable(monkeypatch, counted):
    """A raising redis_set_nx must not suppress the durable write."""
    monkeypatch.setattr(
        gate,
        "redis_set_nx",
        AsyncMock(side_effect=RuntimeError("Redis configured but unavailable")),
    )
    # Flag ON proves fail-open is not merely shadow mode in disguise.
    monkeypatch.setattr(gate, "_gate_enabled", AsyncMock(return_value=True))

    assert await gate.should_write_marker("driver-1", path="rest_v2_idle") is True
    assert _outcomes(counted) == ["written"]
    assert ("spinr_drivers_location_gate_failed_total", {}) in counted


@pytest.mark.anyio
async def test_period1_force_never_skipped(monkeypatch, counted):
    """A Period 1 accumulator write bypasses the gate and restarts the window.

    Skipping one of these silently under-counts a regulated SGI audit figure.
    """
    refresh = AsyncMock(return_value=None)
    monkeypatch.setattr(gate, "redis_set", refresh)
    # Even with the window held and the flag on, force must win.
    monkeypatch.setattr(gate, "redis_set_nx", AsyncMock(return_value=False))
    monkeypatch.setattr(gate, "_gate_enabled", AsyncMock(return_value=True))

    assert await gate.should_write_marker("driver-1", path="rest_v1", force=True) is True
    assert _outcomes(counted) == ["period1_forced"]
    refresh.assert_awaited_once()
    assert refresh.await_args.args[0] == "spinr:locwrite:driver-1"


@pytest.mark.anyio
async def test_period1_force_survives_redis_failure(monkeypatch, counted):
    """A failed window refresh costs coalescing, never the write itself."""
    monkeypatch.setattr(gate, "redis_set", AsyncMock(side_effect=RuntimeError("down")))

    assert await gate.should_write_marker("driver-1", path="rest_v2_idle", force=True) is True
    assert _outcomes(counted) == ["period1_forced"]


@pytest.mark.anyio
async def test_unreadable_settings_do_not_throttle(monkeypatch, counted):
    """If app_settings can't be read, degrade to today's behaviour (write)."""
    monkeypatch.setattr(gate, "redis_set_nx", AsyncMock(return_value=False))

    import settings_loader

    monkeypatch.setattr(
        settings_loader,
        "get_app_settings",
        AsyncMock(side_effect=RuntimeError("settings backend down")),
    )

    assert await gate.should_write_marker("driver-1", path="rest_v1") is True
    assert _outcomes(counted) == ["shadow_throttled"]


@pytest.mark.anyio
async def test_missing_driver_id_writes(monkeypatch):
    """No id to key on — never silently drop the write."""
    monkeypatch.setattr(gate, "redis_set_nx", AsyncMock(return_value=False))

    assert await gate.should_write_marker("", path="rest_v1") is True


def test_interval_stays_far_below_stale_intent_threshold():
    """Guard the invariant documented in the gate's module docstring.

    ``utils/stale_intent_reconciler.py`` flips drivers intent-offline when
    ``drivers.updated_at`` falls behind ``stale_intent_offline_hours``
    (default 4 h). The gate delays that column's refresh by at most one
    interval, which is only safe while the interval stays tiny relative to
    that window. If someone raises this into minutes, revisit that loop
    before deleting this test.
    """
    assert gate.MARKER_WRITE_INTERVAL_S <= 30.0
