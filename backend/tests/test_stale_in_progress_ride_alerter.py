"""Tests for utils/stale_in_progress_ride_alerter.py (P2 task #16).

The alerter must detect `in_progress` rides whose driver has produced no
location update within STALE_MINUTES, escalate to Sentry + a structured
error log (domain=dispatch), dedupe repeat alerts for the same ride via
Redis, and — this is the load-bearing invariant — never mutate ride status,
driver rows, or insurance-period rows. `FakeDB` below intentionally has no
`update_one`/`insert_one`/`delete_many` methods: any attempt by the module
under test to call one raises AttributeError and fails the test, which is a
stronger guarantee than asserting a mock "was not called" after the fact.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


class FakeDB:
    """Deliberately exposes only get_rows. No update_one / insert_one /
    delete_many — see module docstring for why that's the point."""

    def __init__(self, rides=None, drivers=None):
        self.rides = rides if rides is not None else []
        self.drivers = drivers if drivers is not None else []
        self.rides_queries: list[dict] = []
        self.drivers_queries: list[dict] = []

    async def get_rows(self, table: str, filters: dict | None = None, **kwargs):
        if table == "rides":
            self.rides_queries.append(filters or {})
            return self.rides
        if table == "drivers":
            self.drivers_queries.append(filters or {})
            return self.drivers
        raise AssertionError(f"unexpected table queried: {table}")


def _ride(**overrides) -> dict:
    base = {
        "id": "ride-1",
        "driver_id": "driver-1",
        "ride_started_at": (NOW - timedelta(minutes=30)).isoformat(),
    }
    base.update(overrides)
    return base


def _driver(**overrides) -> dict:
    base = {"id": "driver-1", "updated_at": (NOW - timedelta(minutes=30)).isoformat()}
    base.update(overrides)
    return base


@pytest.fixture
def mod(monkeypatch):
    from utils import stale_in_progress_ride_alerter as m

    monkeypatch.setattr(m, "get_app_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(m, "redis_set_nx", AsyncMock(return_value=True))
    monkeypatch.setattr(m, "_metric_inc", MagicMock())
    monkeypatch.setattr(m, "_record_heartbeat", MagicMock())
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Core detection: stale vs. fresh
# ─────────────────────────────────────────────────────────────────────────────


class TestDetection:
    @pytest.mark.anyio
    async def test_in_progress_ride_past_threshold_gets_alerted(self, mod, monkeypatch):
        db = FakeDB(rides=[_ride()], drivers=[_driver()])
        monkeypatch.setattr(mod, "db", db)
        sentry_capture = MagicMock()
        monkeypatch.setattr("sentry_sdk.capture_message", sentry_capture, raising=False)

        stats = await mod._check(now_utc=NOW)

        assert stats == {"candidates": 1, "alerted": 1, "deduped": 0}
        sentry_capture.assert_called_once()
        tags = sentry_capture.call_args.kwargs["tags"]
        assert tags["domain"] == "dispatch"
        assert tags["surface"] == "backend"
        assert tags["ride_id"] == "ride-1"
        assert tags["driver_id"] == "driver-1"
        mod._metric_inc.assert_any_call("spinr_dispatch_stale_in_progress_ride_alert_total")

    @pytest.mark.anyio
    async def test_ride_with_recent_driver_location_is_not_alerted(self, mod, monkeypatch):
        """Driver pinged 1 minute ago (well within the 10-minute threshold and
        the normal 5-15s outbox cadence) — must not fire."""
        fresh_driver = _driver(updated_at=(NOW - timedelta(minutes=1)).isoformat())
        db = FakeDB(rides=[_ride()], drivers=[fresh_driver])
        monkeypatch.setattr(mod, "db", db)
        sentry_capture = MagicMock()
        monkeypatch.setattr("sentry_sdk.capture_message", sentry_capture, raising=False)

        stats = await mod._check(now_utc=NOW)

        assert stats == {"candidates": 1, "alerted": 0, "deduped": 0}
        sentry_capture.assert_not_called()

    @pytest.mark.anyio
    async def test_driver_location_exactly_at_threshold_boundary_is_not_alerted(self, mod, monkeypatch):
        """>= cutoff counts as fresh (matches stale_intent_reconciler's `$lt`
        convention: the cutoff itself is the last still-fresh instant)."""
        boundary_driver = _driver(updated_at=(NOW - timedelta(minutes=10)).isoformat())
        db = FakeDB(rides=[_ride()], drivers=[boundary_driver])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._check(now_utc=NOW)

        assert stats["alerted"] == 0

    @pytest.mark.anyio
    async def test_no_candidates_is_a_clean_noop(self, mod, monkeypatch):
        db = FakeDB(rides=[], drivers=[])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._check(now_utc=NOW)

        assert stats == {"candidates": 0, "alerted": 0, "deduped": 0}

    @pytest.mark.anyio
    async def test_missing_driver_row_is_treated_as_stale_and_alerted(self, mod, monkeypatch):
        """No driver row at all (e.g. deleted) is itself worth a human look —
        treated as stale with an 'unknown' staleness duration."""
        db = FakeDB(rides=[_ride()], drivers=[])
        monkeypatch.setattr(mod, "db", db)
        sentry_capture = MagicMock()
        monkeypatch.setattr("sentry_sdk.capture_message", sentry_capture, raising=False)

        stats = await mod._check(now_utc=NOW)

        assert stats["alerted"] == 1
        ctx = sentry_capture.call_args.kwargs["contexts"]["stale_in_progress_ride"]
        assert ctx["minutes_since_last_location"] is None

    @pytest.mark.anyio
    async def test_ride_without_driver_id_is_skipped(self, mod, monkeypatch):
        db = FakeDB(rides=[_ride(driver_id=None)], drivers=[])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._check(now_utc=NOW)

        assert stats["alerted"] == 0

    @pytest.mark.anyio
    async def test_multiple_stale_rides_each_alerted_independently(self, mod, monkeypatch):
        rides = [_ride(id="ride-a", driver_id="driver-a"), _ride(id="ride-b", driver_id="driver-b")]
        drivers = [_driver(id="driver-a"), _driver(id="driver-b")]
        db = FakeDB(rides=rides, drivers=drivers)
        monkeypatch.setattr(mod, "db", db)
        sentry_capture = MagicMock()
        monkeypatch.setattr("sentry_sdk.capture_message", sentry_capture, raising=False)

        stats = await mod._check(now_utc=NOW)

        assert stats == {"candidates": 2, "alerted": 2, "deduped": 0}
        assert sentry_capture.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# Dedupe (Redis SET NX)
# ─────────────────────────────────────────────────────────────────────────────


class TestDedupe:
    @pytest.mark.anyio
    async def test_dedupe_prevents_repeat_alert_within_ttl(self, mod, monkeypatch):
        db = FakeDB(rides=[_ride()], drivers=[_driver()])
        monkeypatch.setattr(mod, "db", db)
        # First tick "acquires" the dedupe key (NX succeeds).
        set_nx = AsyncMock(side_effect=[True, False])
        monkeypatch.setattr(mod, "redis_set_nx", set_nx)
        sentry_capture = MagicMock()
        monkeypatch.setattr("sentry_sdk.capture_message", sentry_capture, raising=False)

        first = await mod._check(now_utc=NOW)
        second = await mod._check(now_utc=NOW + timedelta(minutes=1))

        assert first == {"candidates": 1, "alerted": 1, "deduped": 0}
        assert second == {"candidates": 1, "alerted": 0, "deduped": 1}
        sentry_capture.assert_called_once()
        assert set_nx.call_count == 2
        # Same dedupe key both times, correct TTL.
        first_call, second_call = set_nx.call_args_list
        assert first_call.args[0] == second_call.args[0] == "spinr:alert:stale_in_progress_ride:ride-1"
        assert first_call.args[2] == mod._ALERT_DEDUPE_TTL_SECONDS

    @pytest.mark.anyio
    async def test_redis_error_on_dedupe_fails_open_and_still_alerts(self, mod, monkeypatch):
        """2026-08-11 P1 fix: redis_set_nx now raises on a real Redis error.
        Missing a dedupe (possible double alert) is far cheaper than a
        crashed tick or a silently skipped alert, so this must fail OPEN."""
        db = FakeDB(rides=[_ride()], drivers=[_driver()])
        monkeypatch.setattr(mod, "db", db)
        monkeypatch.setattr(mod, "redis_set_nx", AsyncMock(side_effect=ConnectionError("redis down")))
        sentry_capture = MagicMock()
        monkeypatch.setattr("sentry_sdk.capture_message", sentry_capture, raising=False)

        stats = await mod._check(now_utc=NOW)

        assert stats["alerted"] == 1
        sentry_capture.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Feature flag
# ─────────────────────────────────────────────────────────────────────────────


class TestFeatureFlag:
    @pytest.mark.anyio
    async def test_flag_disabled_skips_the_tick_entirely(self, mod, monkeypatch):
        monkeypatch.setattr(
            mod, "get_app_settings", AsyncMock(return_value={"stale_in_progress_ride_alert_enabled": False})
        )
        db = FakeDB(rides=[_ride()], drivers=[_driver()])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._check(now_utc=NOW)

        assert stats == {"candidates": 0, "alerted": 0, "deduped": 0}
        assert db.rides_queries == []  # never even queried

    @pytest.mark.anyio
    async def test_flag_absent_defaults_enabled(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "get_app_settings", AsyncMock(return_value={}))
        db = FakeDB(rides=[_ride()], drivers=[_driver()])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._check(now_utc=NOW)

        assert stats["alerted"] == 1

    @pytest.mark.anyio
    async def test_settings_read_failure_fails_open_enabled(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "get_app_settings", AsyncMock(side_effect=RuntimeError("settings down")))
        db = FakeDB(rides=[_ride()], drivers=[_driver()])
        monkeypatch.setattr(mod, "db", db)

        stats = await mod._check(now_utc=NOW)

        assert stats["alerted"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# No mutation — the load-bearing invariant for this ALERT-ONLY loop
# ─────────────────────────────────────────────────────────────────────────────


class TestNoMutationSideEffects:
    @pytest.mark.anyio
    async def test_alerting_never_calls_db_write_methods(self, mod, monkeypatch):
        """FakeDB exposes only get_rows. If `_check` ever tried
        db.update_one/insert_one/delete_many (ride status, driver row, or an
        insurance-period write), this would raise AttributeError and fail
        the test -- a stronger guarantee than a mock call-count assertion."""
        db = FakeDB(
            rides=[_ride(), _ride(id="ride-2", driver_id="driver-2")], drivers=[_driver(), _driver(id="driver-2")]
        )
        monkeypatch.setattr(mod, "db", db)
        monkeypatch.setattr("sentry_sdk.capture_message", MagicMock(), raising=False)

        stats = await mod._check(now_utc=NOW)

        assert stats["alerted"] == 2  # got this far without ever needing a write method

    def test_module_never_imports_insurance_period_or_driver_mutation_helpers(self, mod):
        """Belt-and-braces static check: the alerter module must not even
        hold a reference to record_period_transition / set_driver_available
        / update_ride -- if a future edit imports one, this fails immediately
        rather than relying on a runtime call never happening to fire."""
        forbidden = ("record_period_transition", "set_driver_available", "update_ride", "update_one")
        for name in forbidden:
            assert not hasattr(mod, name), f"alerter module must not reference {name}"


# ─────────────────────────────────────────────────────────────────────────────
# Loop wrapper
# ─────────────────────────────────────────────────────────────────────────────


class TestLoop:
    @pytest.mark.anyio
    async def test_happy_tick_runs_records_heartbeat_and_sleeps(self, mod, monkeypatch):
        import asyncio

        check = AsyncMock()
        monkeypatch.setattr(mod, "_check", check)

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0)

        with pytest.raises(asyncio.CancelledError):
            await mod.stale_in_progress_ride_alert_loop()

        check.assert_awaited_once()
        mod._record_heartbeat.assert_called_once_with(mod._LOOP_NAME)

    @pytest.mark.anyio
    async def test_tick_failure_is_logged_metric_incremented_loop_survives(self, mod, monkeypatch, caplog):
        import asyncio
        import logging

        monkeypatch.setattr(mod, "_check", AsyncMock(side_effect=RuntimeError("boom")))

        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0)

        with caplog.at_level(logging.ERROR):
            with pytest.raises(asyncio.CancelledError):
                await mod.stale_in_progress_ride_alert_loop()

        mod._metric_inc.assert_any_call("spinr_bgloop_errors_total", {"loop": "stale_in_progress_ride_alerter"})
        assert any("tick failed" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_cancelled_error_propagates_without_error_metric(self, mod, monkeypatch):
        import asyncio

        monkeypatch.setattr(mod, "_check", AsyncMock(side_effect=asyncio.CancelledError()))

        async def fake_sleep(secs):
            pass

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0)

        with pytest.raises(asyncio.CancelledError):
            await mod.stale_in_progress_ride_alert_loop()

        mod._metric_inc.assert_not_called()
