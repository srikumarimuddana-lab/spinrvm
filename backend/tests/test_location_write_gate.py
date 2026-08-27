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
    """A raising redis_set_nx must not suppress a REST write.

    REST wrote every time before the gate existed, so its degraded mode is
    full fail-open (the WS family degrades differently — see the floor tests
    below).
    """
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
async def test_degraded_ws_keeps_inprocess_floor(monkeypatch, counted):
    """Redis loss must not delete the WS throttle — the outage-amplification bug.

    Pre-gate, the WS 3 s throttle was in-process and survived any Redis
    outage. The first cut of the degraded branch returned True
    unconditionally, so Redis-down un-throttled the hottest write path (~3x
    sustained UPDATE volume) at exactly the moment the non-autoscaling DB
    tier could least absorb it — found by the architect review. Degraded WS
    now falls back to a module-level per-driver monotonic floor.
    """
    monkeypatch.setattr(gate, "redis_set_nx", AsyncMock(side_effect=RuntimeError("Redis down")))
    monkeypatch.setattr(gate, "_degraded_last_write", {})

    results = [
        await gate.should_write_marker("driver-ws", path="ws_single", unthrottled_before=False) for _ in range(5)
    ]
    assert results == [True, False, False, False, False]
    # A different driver gets its own floor.
    assert await gate.should_write_marker("driver-other", path="ws_single", unthrottled_before=False) is True
    # Every degraded evaluation is metered, allowed or not.
    assert sum(1 for n, _l in counted if n == "spinr_drivers_location_gate_failed_total") == 6


@pytest.mark.anyio
async def test_degraded_rest_still_writes_every_time(monkeypatch, counted):
    """The other half of the degraded contract: REST restores pre-gate reality."""
    monkeypatch.setattr(gate, "redis_set_nx", AsyncMock(side_effect=RuntimeError("Redis down")))
    monkeypatch.setattr(gate, "_degraded_last_write", {})

    results = [await gate.should_write_marker("driver-r", path="rest_v1") for _ in range(3)]
    assert results == [True, True, True]
    assert _outcomes(counted) == ["written", "written", "written"]


@pytest.mark.anyio
async def test_hung_redis_times_out_and_degrades(monkeypatch, counted):
    """A HUNG (not raising) Redis must not stall the SLA path behind the gate.

    The shared Redis client sets no socket timeouts, so an unbounded await on
    a black-holed socket would freeze the durable write — and the sequential
    WS message loop behind it — indefinitely. The gate bounds every Redis call
    with asyncio.wait_for and treats a timeout as the degraded branch.
    """
    import asyncio as _asyncio
    import time as _time

    async def _hung(*_a, **_k):
        await _asyncio.sleep(5)

    monkeypatch.setattr(gate, "redis_set_nx", _hung)
    monkeypatch.setattr(gate, "_GATE_REDIS_TIMEOUT_S", 0.05)
    monkeypatch.setattr(gate, "_degraded_last_write", {})

    t0 = _time.monotonic()
    assert await gate.should_write_marker("driver-h", path="rest_v1") is True
    assert _time.monotonic() - t0 < 1.0
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
    """A failed window claim/refresh costs coalescing, never the write itself."""
    monkeypatch.setattr(gate, "redis_set_nx", AsyncMock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(gate, "redis_set", AsyncMock(side_effect=RuntimeError("down")))

    assert await gate.should_write_marker("driver-1", path="rest_v2_idle", force=True) is True
    assert _outcomes(counted) == ["period1_forced"]


@pytest.mark.anyio
async def test_period1_force_counts_written_when_window_free(monkeypatch, counted):
    """Force with a FREE window is an ordinary write, not a forced one.

    Labelling every forced-path write period1_forced would inflate the forced
    share of the shadow measurement: with period1 tracking on, most idle
    flushes carry a delta, and an operator summing written+shadow_throttled
    would under-count real write volume. The force branch claims NX first —
    acquired means coalescing suppressed nothing, so it counts written; only
    a genuinely overridden (held) window counts period1_forced.
    """
    import utils.redis_client as rc

    rc._local.clear()
    monkeypatch.setattr(gate, "_gate_enabled", AsyncMock(return_value=True))

    # Free window: force is a plain write.
    assert await gate.should_write_marker("driver-6", path="rest_v1", force=True) is True
    # Held window: force genuinely overrides.
    assert await gate.should_write_marker("driver-6", path="rest_v1", force=True) is True
    assert _outcomes(counted) == ["written", "period1_forced"]


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
    # The Redis TTL is int(MARKER_WRITE_INTERVAL_S) seconds. A fractional
    # constant would silently diverge from the effective window (0.5 -> 1 s
    # TTL, 2.5 -> 2 s), so pin it integral and >= 1.
    assert gate.MARKER_WRITE_INTERVAL_S == int(gate.MARKER_WRITE_INTERVAL_S) >= 1


@pytest.mark.anyio
async def test_rest_and_ws_paths_share_one_window(monkeypatch):
    """The point of the gate: one window per driver across ingestion routes.

    Previously the WebSocket handler throttled on per-connection in-process
    state while the REST handlers had no throttle at all, so a driver flushing
    the REST outbox while pinging over WebSocket wrote the same ``drivers`` row
    from two uncoordinated paths. Exercised against the real ``redis_set_nx``
    (in-process fallback), not a mock, so this covers the actual keying.
    """
    import utils.redis_client as rc

    rc._local.clear()
    monkeypatch.setattr(gate, "_gate_enabled", AsyncMock(return_value=True))

    assert await gate.should_write_marker("driver-9", path="rest_v1") is True
    # Same driver, different ingestion route, inside the window.
    assert await gate.should_write_marker("driver-9", path="ws_single") is False
    # A different driver is unaffected.
    assert await gate.should_write_marker("driver-8", path="ws_single") is True


@pytest.mark.anyio
async def test_force_write_restarts_the_shared_window(monkeypatch):
    """A Period 1 forced write refreshes the window rather than bypassing it.

    The row genuinely was written, so the next caller should coalesce against
    it exactly as it would against an ordinary write.
    """
    import utils.redis_client as rc

    rc._local.clear()
    monkeypatch.setattr(gate, "_gate_enabled", AsyncMock(return_value=True))

    assert await gate.should_write_marker("driver-7", path="rest_v1", force=True) is True
    assert await gate.should_write_marker("driver-7", path="ws_single") is False


@pytest.mark.anyio
async def test_ws_path_throttles_even_in_shadow_mode(monkeypatch):
    """Shadow mode must not hand the WS path writes it previously refused.

    Regression: the first cut of this module let *every* caller fall through
    while the flag was off. That was sound for REST (no pre-existing throttle)
    but silently deleted the WebSocket handlers' unconditional 3 s throttle
    (`conn_state["last_loc_db_write"]`), so merging it would have INCREASED
    sustained write volume on the highest-frequency write path in the system —
    the exact opposite of this module's purpose, and invisible because the flag
    was off and the change looked inert.

    Caught by an independent dispatch audit, not by the original test suite:
    every earlier test called the gate exactly once, where "always writes" and
    "throttled to one per window" are indistinguishable. Hence N calls here.
    """
    import utils.redis_client as rc

    rc._local.clear()
    monkeypatch.setattr(gate, "_gate_enabled", AsyncMock(return_value=False))

    results = [
        await gate.should_write_marker("driver-ws", path="ws_single", unthrottled_before=False) for _ in range(5)
    ]
    assert results == [True, False, False, False, False]


@pytest.mark.anyio
async def test_rest_path_still_writes_in_shadow_mode(monkeypatch):
    """The other half of the contract: REST keeps writing while the flag is off.

    REST had no throttle before the gate, so falling through is what preserves
    its behaviour. If this ever returns False the gate has started changing
    production behaviour before anyone flipped the flag.
    """
    import utils.redis_client as rc

    rc._local.clear()
    monkeypatch.setattr(gate, "_gate_enabled", AsyncMock(return_value=False))

    results = [await gate.should_write_marker("driver-rest", path="rest_v1") for _ in range(5)]
    assert results == [True, True, True, True, True]


@pytest.mark.anyio
async def test_ws_and_rest_both_throttle_once_flag_is_on(monkeypatch):
    """With the flag on, both families coalesce against the one shared window."""
    import utils.redis_client as rc

    rc._local.clear()
    monkeypatch.setattr(gate, "_gate_enabled", AsyncMock(return_value=True))

    assert await gate.should_write_marker("driver-x", path="rest_v1") is True
    assert (await gate.should_write_marker("driver-x", path="ws_single", unthrottled_before=False)) is False
    assert await gate.should_write_marker("driver-x", path="rest_v1") is False


@pytest.mark.anyio
async def test_route_helper_writes_period1_through_an_active_throttle(monkeypatch):
    """End-to-end at the route layer: flag ON, window held, Period 1 still writes.

    The unit test above proves `should_write_marker(force=True)` returns True.
    This proves the route helper actually *detects* the accumulator columns and
    passes force, with the gate genuinely throttling — the earlier route tests
    all ran with the flag off, where "shadow mode writes anyway" and "force
    correctly overrode an active throttle" are indistinguishable.

    If this regresses, SGI insurance-period distances under-count silently.
    """
    import utils.redis_client as rc
    from routes.drivers import location as loc

    rc._local.clear()
    monkeypatch.setattr(gate, "_gate_enabled", AsyncMock(return_value=True))

    writes = []

    async def _fake_update_one(table, filt, data):
        writes.append((table, filt, data))
        return True

    monkeypatch.setattr(loc.db_supabase, "update_one", _fake_update_one)

    plain = {"lat": 50.4452, "lng": -104.6189}
    withp1 = {"lat": 50.4452, "lng": -104.6189, "period1_accum_km": 1.25}

    # First call takes the window.
    await loc._write_marker_if_due({"id": "d1"}, dict(plain), "d1", "rest_v1")
    assert len(writes) == 1

    # Second, inside the window, no accumulator — correctly coalesced away.
    await loc._write_marker_if_due({"id": "d1"}, dict(plain), "d1", "rest_v1")
    assert len(writes) == 1

    # Third, still inside the window, but carrying a Period 1 delta — must land.
    await loc._write_marker_if_due({"id": "d1"}, dict(withp1), "d1", "rest_v1")
    assert len(writes) == 2
    assert writes[-1][2]["period1_accum_km"] == 1.25
