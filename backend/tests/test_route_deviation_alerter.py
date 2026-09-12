"""Tests for utils/route_deviation_alerter.py.

The alerter must detect an `in_progress` ride whose driver has been
sustained >500m off `planned_route_polyline` for >=60s, escalate exactly
once per deviation episode (safety_incidents row + notify_safety_team +
audit log), reset its tracking state the moment the driver is back
on-route (so a later, distinct episode on the same ride can escalate
again), and — this is the load-bearing invariant — never mutate `rides`
or `drivers` rows. `FakeDB` below intentionally has no `update_one` /
`delete_many` method (only `get_rows` and `insert_one`, the latter used
solely to create a *new* safety_incidents row, mirroring
safety_checkin_loop.py's own escalation shape): any attempt by the module
under test to call `update_one`/`delete_many` raises AttributeError and
fails the test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

# A short straight-line route along a fixed latitude.
ROUTE = [[50.45, -104.61], [50.45, -104.60], [50.45, -104.59]]
ON_ROUTE = (50.45, -104.60)  # an exact vertex -> distance 0
OFF_ROUTE = (50.46, -104.60)  # ~1.11km north of the route -> well over 500m


class FakeDB:
    """Exposes get_rows + insert_one only. No update_one / delete_many --
    see module docstring for why that's the point."""

    def __init__(self, rides=None, drivers=None):
        self.rides = rides if rides is not None else []
        self.drivers = drivers if drivers is not None else []
        self.rides_queries: list[dict] = []
        self.drivers_queries: list[dict] = []
        self.inserted: list[tuple[str, dict]] = []

    async def get_rows(self, table: str, filters: dict | None = None, **kwargs):
        if table == "rides":
            self.rides_queries.append(filters or {})
            return self.rides
        if table == "drivers":
            self.drivers_queries.append(filters or {})
            return self.drivers
        raise AssertionError(f"unexpected table queried: {table}")

    async def insert_one(self, table: str, row: dict):
        if table != "safety_incidents":
            raise AssertionError(f"unexpected insert into table: {table}")
        self.inserted.append((table, row))
        return row


class FakeRedisStore:
    """Minimal in-process stand-in for the real SET NX / GET / DELETE
    semantics the module relies on, so multi-tick scenarios (start
    tracking on tick 1, escalate on tick 2) behave the way real Redis
    would rather than needing a brittle sequence of side_effect mocks."""

    def __init__(self):
        self.data: dict[str, str] = {}

    async def set_nx(self, key, value, ttl=None):
        if key in self.data:
            return False
        self.data[key] = value
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)


def _ride(**overrides) -> dict:
    base = {
        "id": "ride-1",
        "driver_id": "driver-1",
        "rider_id": "rider-1",
        "planned_route_polyline": ROUTE,
    }
    base.update(overrides)
    return base


def _driver(lat=ON_ROUTE[0], lng=ON_ROUTE[1], updated_at=None, **overrides) -> dict:
    base = {
        "id": "driver-1",
        "lat": lat,
        "lng": lng,
        "updated_at": (updated_at or NOW).isoformat(),
    }
    base.update(overrides)
    return base


@pytest.fixture
def mod(monkeypatch):
    from utils import route_deviation_alerter as m

    monkeypatch.setattr(m, "get_app_settings", AsyncMock(return_value={"route_deviation_alert_enabled": True}))
    store = FakeRedisStore()
    monkeypatch.setattr(m, "redis_set_nx", store.set_nx)
    monkeypatch.setattr(m, "redis_get", store.get)
    monkeypatch.setattr(m, "redis_delete", store.delete)
    monkeypatch.setattr(m, "notify_safety_team", AsyncMock(return_value={}))
    monkeypatch.setattr(m, "_log_audit", AsyncMock())
    monkeypatch.setattr(m, "_metric_inc", MagicMock())
    monkeypatch.setattr(m, "_record_heartbeat", MagicMock())
    m._redis_store = store  # convenience handle for assertions
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Core detection: on-route vs. off-route
# ─────────────────────────────────────────────────────────────────────────────


class TestDetection:
    @pytest.mark.anyio
    async def test_ride_on_route_is_not_flagged(self, mod, monkeypatch):
        db = FakeDB(rides=[_ride()], drivers=[_driver()])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._tick(now_utc=NOW)

        assert stats == {"candidates": 1, "off_route": 0, "escalated": 0}
        assert db.inserted == []

    @pytest.mark.anyio
    async def test_first_tick_off_route_does_not_escalate_yet(self, mod, monkeypatch):
        db = FakeDB(rides=[_ride()], drivers=[_driver(*OFF_ROUTE)])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._tick(now_utc=NOW)

        assert stats == {"candidates": 1, "off_route": 1, "escalated": 0}
        assert db.inserted == []
        assert mod._first_seen_key("ride-1") in mod._redis_store.data

    @pytest.mark.anyio
    async def test_off_route_not_yet_sustained_does_not_escalate(self, mod, monkeypatch):
        db = FakeDB(rides=[_ride()], drivers=[_driver(*OFF_ROUTE)])
        monkeypatch.setattr(mod, "db", db)

        await mod._tick(now_utc=NOW)
        second = await mod._tick(now_utc=NOW + timedelta(seconds=30))

        assert second == {"candidates": 1, "off_route": 1, "escalated": 0}
        assert db.inserted == []

    @pytest.mark.anyio
    async def test_off_route_sustained_past_threshold_escalates(self, mod, monkeypatch):
        db = FakeDB(rides=[_ride()], drivers=[_driver(*OFF_ROUTE)])
        monkeypatch.setattr(mod, "db", db)

        await mod._tick(now_utc=NOW)
        second = await mod._tick(now_utc=NOW + timedelta(seconds=61))

        assert second == {"candidates": 1, "off_route": 1, "escalated": 1}
        assert len(db.inserted) == 1
        table, incident = db.inserted[0]
        assert table == "safety_incidents"
        assert incident["ride_id"] == "ride-1"
        assert incident["category"] == "route_deviation"
        assert incident["status"] == "open"
        mod.notify_safety_team.assert_awaited_once()
        mod._log_audit.assert_awaited_once()
        mod._metric_inc.assert_any_call("spinr_safety_route_deviation_alert_total")

    @pytest.mark.anyio
    async def test_escalation_happens_only_once_per_episode(self, mod, monkeypatch):
        db = FakeDB(rides=[_ride()], drivers=[_driver(*OFF_ROUTE)])
        monkeypatch.setattr(mod, "db", db)

        await mod._tick(now_utc=NOW)
        await mod._tick(now_utc=NOW + timedelta(seconds=61))
        third = await mod._tick(now_utc=NOW + timedelta(seconds=90))

        assert third["escalated"] == 0
        assert len(db.inserted) == 1  # not re-escalated on a later tick

    @pytest.mark.anyio
    async def test_returning_to_route_clears_state_for_a_future_episode(self, mod, monkeypatch):
        # Episode 1: off-route, then back on-route before sustain threshold.
        db = FakeDB(rides=[_ride()], drivers=[_driver(*OFF_ROUTE)])
        monkeypatch.setattr(mod, "db", db)
        await mod._tick(now_utc=NOW)

        db.drivers = [_driver(*ON_ROUTE, updated_at=NOW + timedelta(seconds=30))]
        back_on_route = await mod._tick(now_utc=NOW + timedelta(seconds=30))
        assert back_on_route["off_route"] == 0
        assert mod._first_seen_key("ride-1") not in mod._redis_store.data
        assert mod._escalated_key("ride-1") not in mod._redis_store.data

        # Episode 2: off-route again later -- must be tracked as a brand
        # new episode (not immediately escalated from stale episode-1 state).
        db.drivers = [_driver(*OFF_ROUTE, updated_at=NOW + timedelta(seconds=40))]
        restart = await mod._tick(now_utc=NOW + timedelta(seconds=40))
        assert restart == {"candidates": 1, "off_route": 1, "escalated": 0}

        db.drivers = [_driver(*OFF_ROUTE, updated_at=NOW + timedelta(seconds=101))]
        escalate_again = await mod._tick(now_utc=NOW + timedelta(seconds=101))
        assert escalate_again["escalated"] == 1
        assert len(db.inserted) == 1

    @pytest.mark.anyio
    async def test_stale_driver_position_is_skipped(self, mod, monkeypatch):
        """A drivers.updated_at older than the freshness guard is not
        evidence either way -- must not be treated as off-route."""
        stale_driver = _driver(*OFF_ROUTE, updated_at=NOW - timedelta(minutes=5))
        db = FakeDB(rides=[_ride()], drivers=[stale_driver])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._tick(now_utc=NOW)

        assert stats == {"candidates": 1, "off_route": 0, "escalated": 0}

    @pytest.mark.anyio
    async def test_missing_driver_row_is_skipped(self, mod, monkeypatch):
        db = FakeDB(rides=[_ride()], drivers=[])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._tick(now_utc=NOW)

        assert stats == {"candidates": 1, "off_route": 0, "escalated": 0}

    @pytest.mark.anyio
    async def test_ride_without_planned_route_polyline_is_not_a_candidate(self, mod, monkeypatch):
        db = FakeDB(rides=[_ride(planned_route_polyline=None)], drivers=[_driver(*OFF_ROUTE)])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._tick(now_utc=NOW)

        assert stats == {"candidates": 0, "off_route": 0, "escalated": 0}

    @pytest.mark.anyio
    async def test_ride_with_too_short_polyline_is_not_a_candidate(self, mod, monkeypatch):
        db = FakeDB(rides=[_ride(planned_route_polyline=[[50.45, -104.60]])], drivers=[_driver(*OFF_ROUTE)])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._tick(now_utc=NOW)

        assert stats["candidates"] == 0

    @pytest.mark.anyio
    async def test_no_candidates_is_a_clean_noop(self, mod, monkeypatch):
        db = FakeDB(rides=[], drivers=[])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._tick(now_utc=NOW)

        assert stats == {"candidates": 0, "off_route": 0, "escalated": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Feature flag -- defaults DISABLED (unlike stale_in_progress_ride_alerter,
# this is a brand-new alert path, so it fails CLOSED)
# ─────────────────────────────────────────────────────────────────────────────


class TestFeatureFlag:
    @pytest.mark.anyio
    async def test_flag_disabled_skips_the_tick_entirely(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "get_app_settings", AsyncMock(return_value={"route_deviation_alert_enabled": False}))
        db = FakeDB(rides=[_ride()], drivers=[_driver(*OFF_ROUTE)])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._tick(now_utc=NOW)

        assert stats == {"candidates": 0, "off_route": 0, "escalated": 0}
        assert db.rides_queries == []  # never even queried

    @pytest.mark.anyio
    async def test_flag_absent_defaults_disabled(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "get_app_settings", AsyncMock(return_value={}))
        db = FakeDB(rides=[_ride()], drivers=[_driver(*OFF_ROUTE)])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._tick(now_utc=NOW)

        assert stats == {"candidates": 0, "off_route": 0, "escalated": 0}

    @pytest.mark.anyio
    async def test_settings_read_failure_fails_closed_disabled(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "get_app_settings", AsyncMock(side_effect=RuntimeError("settings down")))
        db = FakeDB(rides=[_ride()], drivers=[_driver(*OFF_ROUTE)])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._tick(now_utc=NOW)

        assert stats == {"candidates": 0, "off_route": 0, "escalated": 0}
        assert db.rides_queries == []


# ─────────────────────────────────────────────────────────────────────────────
# No mutation of ride/driver state -- the load-bearing invariant
# ─────────────────────────────────────────────────────────────────────────────


class TestNoRideOrDriverMutation:
    @pytest.mark.anyio
    async def test_escalation_never_calls_ride_or_driver_write_methods(self, mod, monkeypatch):
        """FakeDB exposes only get_rows + insert_one (safety_incidents).
        If _tick/_escalate ever tried db.update_one/delete_many on rides or
        drivers, this raises AttributeError and fails the test."""
        db = FakeDB(rides=[_ride()], drivers=[_driver(*OFF_ROUTE)])
        monkeypatch.setattr(mod, "db", db)

        await mod._tick(now_utc=NOW)
        stats = await mod._tick(now_utc=NOW + timedelta(seconds=61))

        assert stats["escalated"] == 1  # got this far without ever needing a write method

    def test_module_never_imports_ride_or_insurance_mutation_helpers(self, mod):
        forbidden = ("record_period_transition", "set_driver_available", "update_ride", "update_one")
        for name in forbidden:
            assert not hasattr(mod, name), f"alerter module must not reference {name}"


# ─────────────────────────────────────────────────────────────────────────────
# Loop wrapper
# ─────────────────────────────────────────────────────────────────────────────


class TestLoop:
    @pytest.mark.anyio
    async def test_happy_tick_runs_and_records_heartbeat(self, mod, monkeypatch):
        import asyncio

        tick = AsyncMock()
        monkeypatch.setattr(mod, "_tick", tick)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 1:
                raise asyncio.CancelledError()

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await mod.route_deviation_alert_loop()

        tick.assert_awaited_once()
        mod._record_heartbeat.assert_called_once_with(mod._LOOP_NAME)

    @pytest.mark.anyio
    async def test_tick_failure_is_logged_metric_incremented_loop_survives(self, mod, monkeypatch, caplog):
        import asyncio
        import logging

        monkeypatch.setattr(mod, "_tick", AsyncMock(side_effect=RuntimeError("boom")))

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 1:
                raise asyncio.CancelledError()

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

        with caplog.at_level(logging.ERROR):
            with pytest.raises(asyncio.CancelledError):
                await mod.route_deviation_alert_loop()

        mod._metric_inc.assert_any_call("spinr_bgloop_errors_total", {"loop": "route_deviation_alerter"})
        assert any("tick failed" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_cancelled_error_propagates_without_error_metric(self, mod, monkeypatch):
        import asyncio

        monkeypatch.setattr(mod, "_tick", AsyncMock(side_effect=asyncio.CancelledError()))

        async def fake_sleep(secs):
            pass

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await mod.route_deviation_alert_loop()

        mod._metric_inc.assert_not_called()
