from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.utils import route_gap_monitor
from backend.utils.route_gap_monitor import assess_location_gap

NOW = datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc)
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "237_ride_location_gap_events.sql"


def test_gap_monitor_opens_an_alert_after_the_configured_interval() -> None:
    decision = assess_location_gap(
        now=NOW,
        trip_started_at=NOW - timedelta(minutes=4),
        last_captured_at=NOW - timedelta(seconds=45),
        threshold_seconds=30,
    )

    assert decision.state == "gap"
    assert decision.gap_started_at == NOW - timedelta(seconds=45)
    assert decision.gap_seconds == 45


def test_gap_monitor_does_not_alert_before_the_threshold() -> None:
    decision = assess_location_gap(
        now=NOW,
        trip_started_at=NOW - timedelta(minutes=4),
        last_captured_at=NOW - timedelta(seconds=29),
        threshold_seconds=30,
    )

    assert decision.state == "healthy"
    assert decision.gap_seconds == 29


def test_gap_monitor_uses_trip_start_when_no_point_has_arrived() -> None:
    decision = assess_location_gap(
        now=NOW,
        trip_started_at=NOW - timedelta(seconds=90),
        last_captured_at=None,
        threshold_seconds=30,
    )

    assert decision.state == "gap"
    assert decision.gap_started_at == NOW - timedelta(seconds=90)
    assert decision.gap_seconds == 90


def test_gap_monitor_does_not_alert_when_the_trip_start_time_is_unknown() -> None:
    decision = assess_location_gap(
        now=NOW,
        trip_started_at=None,
        last_captured_at=None,
        threshold_seconds=30,
    )

    assert decision.state == "unknown"
    assert decision.gap_started_at is None


def test_gap_events_are_durable_but_do_not_store_coordinates() -> None:
    sql = MIGRATION.read_text()

    assert "CREATE TABLE IF NOT EXISTS public.ride_location_gap_events" in sql
    assert "UNIQUE INDEX IF NOT EXISTS uq_ride_location_gap_events_ride_start" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    table_ddl = sql.split("CREATE UNIQUE INDEX", 1)[0].lower()
    assert " latitude" not in table_ddl
    assert " longitude" not in table_ddl


def test_gap_monitor_tick_opens_one_idempotent_event_without_coordinates(monkeypatch) -> None:
    inserted: list[dict] = []
    gauges: list[tuple[str, float]] = []

    async def get_rows(table, _filters, **_kwargs):
        if table == "rides":
            return [
                {"id": "ride_1", "driver_id": "driver_1", "ride_started_at": (NOW - timedelta(minutes=4)).isoformat()}
            ]
        if table == "driver_location_history":
            return [{"captured_at": (NOW - timedelta(seconds=45)).isoformat()}]
        if table == "ride_location_gap_events":
            return []  # no open events for the orphan-closure pass
        raise AssertionError(f"unexpected table {table}")

    async def insert_many_ignore_conflicts(_table, rows, _on_conflict):
        inserted.extend(rows)
        return rows

    async def update_one(*_args, **_kwargs):
        raise AssertionError("a current capture gap must not be resolved")

    monkeypatch.setattr(route_gap_monitor.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(route_gap_monitor.db_supabase, "insert_many_ignore_conflicts", insert_many_ignore_conflicts)
    monkeypatch.setattr(route_gap_monitor.db_supabase, "update_one", update_one)
    monkeypatch.setattr(route_gap_monitor, "get_app_settings", lambda: _settings(30))
    monkeypatch.setattr(route_gap_monitor, "_now", lambda: NOW)
    monkeypatch.setattr(route_gap_monitor, "_metric_gauge", lambda name, value: gauges.append((name, value)))
    monkeypatch.setattr(route_gap_monitor, "_metric_inc", lambda *_args: None)

    result = _run(route_gap_monitor.route_gap_monitor_tick())

    assert result == {"scanned": 1, "opened": 1, "resolved": 0, "unknown": 0, "orphaned_closed": 0}
    assert inserted == [
        {
            "ride_id": "ride_1",
            "driver_id": "driver_1",
            "gap_started_at": NOW - timedelta(seconds=45),
            "detected_at": NOW,
            "threshold_seconds": 30,
            "gap_seconds": 45,
            "status": "open",
            "source": "active_trip_monitor",
        }
    ]
    assert gauges == [("spinr_rides_gps_gap_open", 1)]


def test_gap_monitor_tick_resolves_an_open_event_after_capture_resumes(monkeypatch) -> None:
    updates: list[tuple] = []

    async def get_rows(table, _filters, **_kwargs):
        if table == "rides":
            return [
                {"id": "ride_1", "driver_id": "driver_1", "ride_started_at": (NOW - timedelta(minutes=4)).isoformat()}
            ]
        if table == "driver_location_history":
            return [{"captured_at": (NOW - timedelta(seconds=5)).isoformat()}]
        if table == "ride_location_gap_events":
            return []  # no open events for the orphan-closure pass
        raise AssertionError(f"unexpected table {table}")

    async def update_one(*args, **kwargs):
        updates.append((args, kwargs))
        return {"id": "event_1"}

    monkeypatch.setattr(route_gap_monitor.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(route_gap_monitor.db_supabase, "update_one", update_one)
    monkeypatch.setattr(
        route_gap_monitor.db_supabase, "insert_many_ignore_conflicts", lambda *_args: _unexpected_insert()
    )
    monkeypatch.setattr(route_gap_monitor, "get_app_settings", lambda: _settings(30))
    monkeypatch.setattr(route_gap_monitor, "_now", lambda: NOW)
    monkeypatch.setattr(route_gap_monitor, "_metric_gauge", lambda *_args: None)
    monkeypatch.setattr(route_gap_monitor, "_metric_inc", lambda *_args: None)

    result = _run(route_gap_monitor.route_gap_monitor_tick())

    assert result == {"scanned": 1, "opened": 0, "resolved": 1, "unknown": 0, "orphaned_closed": 0}
    assert updates[0][0] == (
        "ride_location_gap_events",
        {"ride_id": "ride_1", "status": "open"},
        {"status": "resolved", "gap_resolved_at": NOW},
    )


def test_lifespan_registers_the_route_gap_monitor_loop() -> None:
    source = (Path(__file__).resolve().parents[1] / "core" / "lifespan.py").read_text()

    assert "from utils.route_gap_monitor import route_gap_monitor_loop" in source
    assert '_spawn("route_gap_monitor (15s)", route_gap_monitor_loop)' in source


async def _settings(threshold: int) -> dict:
    return {"route_location_gap_alert_seconds": threshold}


async def _unexpected_insert():
    raise AssertionError("a healthy ride must not create a gap event")


def _run(coroutine):
    import asyncio

    return asyncio.run(coroutine)


# --- Driver location-health nudge (P3.1) -------------------------------------

import asyncio  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

from backend.utils.route_gap_monitor import (  # noqa: E402
    GapDecision,
    _notify_driver_location_health,
    location_health_payload,
)


def _run(coro):
    return asyncio.run(coro)


def _gap_decision():
    return GapDecision(state="gap", gap_started_at=NOW - timedelta(seconds=45), gap_seconds=45)


def test_location_health_payload_shape():
    payload = location_health_payload("ride_1", 45, 30)
    assert payload["type"] == "location_health"
    assert payload["ride_id"] == "ride_1"
    assert payload["action"] == "reacquire_gps"
    assert payload["gap_seconds"] == 45
    # No coordinates ever leave the monitor.
    assert "lat" not in payload and "lng" not in payload


def test_nudge_sends_to_driver_user_id():
    ride = {"id": "ride_1", "driver_id": "drv-1"}
    send = AsyncMock()
    with (
        patch.object(route_gap_monitor.db_supabase, "get_rows", AsyncMock(return_value=[{"user_id": "user-9"}])),
        patch.object(route_gap_monitor.socket_manager.manager, "send_personal_message", send),
    ):
        _run(_notify_driver_location_health(ride, _gap_decision(), 30))
    send.assert_awaited_once()
    _msg, client_id = send.await_args[0]
    assert client_id == "driver_user-9"
    assert _msg["type"] == "location_health"


def test_nudge_noop_without_driver():
    send = AsyncMock()
    with patch.object(route_gap_monitor.socket_manager.manager, "send_personal_message", send):
        _run(_notify_driver_location_health({"id": "ride_1"}, _gap_decision(), 30))
    send.assert_not_awaited()


def test_nudge_noop_when_driver_has_no_user_id():
    send = AsyncMock()
    with (
        patch.object(route_gap_monitor.db_supabase, "get_rows", AsyncMock(return_value=[])),
        patch.object(route_gap_monitor.socket_manager.manager, "send_personal_message", send),
    ):
        _run(_notify_driver_location_health({"id": "ride_1", "driver_id": "drv-1"}, _gap_decision(), 30))
    send.assert_not_awaited()


def test_nudge_swallows_send_failure():
    ride = {"id": "ride_1", "driver_id": "drv-1"}
    with (
        patch.object(route_gap_monitor.db_supabase, "get_rows", AsyncMock(return_value=[{"user_id": "user-9"}])),
        patch.object(
            route_gap_monitor.socket_manager.manager,
            "send_personal_message",
            AsyncMock(side_effect=RuntimeError("ws down")),
        ),
    ):
        # Must not raise — a nudge failure can never break the monitor tick.
        _run(_notify_driver_location_health(ride, _gap_decision(), 30))


# --- NULL captured_at blindness + orphaned open events (SPR-PE7TTB) -----------


def test_latest_capture_ignores_null_captured_at_rows():
    """ORDER BY captured_at DESC puts NULLs first in Postgres. One legacy WS
    breadcrumb row (captured_at NULL) blinded the monitor for the whole ride:
    every tick saw None, opened a phantom gap-since-trip-start, and the real
    mid-trip blackout was never distinguishable (ride SPR-PE7TTB)."""
    calls: list[dict] = []

    async def get_rows(table, filters, **_kwargs):
        assert table == "driver_location_history"
        calls.append(filters)
        return [{"captured_at": (NOW - timedelta(seconds=5)).isoformat()}]

    with patch.object(route_gap_monitor.db_supabase, "get_rows", get_rows):
        result = _run(route_gap_monitor._latest_capture_time("ride_1"))

    assert result == NOW - timedelta(seconds=5)
    assert calls[0]["captured_at"] == {"$notnull": True}


def test_latest_capture_falls_back_to_legacy_timestamp_column():
    async def get_rows(_table, filters, **_kwargs):
        if "captured_at" in filters:
            return []  # all rows are v1-shaped: captured_at NULL everywhere
        assert filters["timestamp"] == {"$notnull": True}
        return [{"timestamp": (NOW - timedelta(seconds=8)).isoformat()}]

    with patch.object(route_gap_monitor.db_supabase, "get_rows", get_rows):
        result = _run(route_gap_monitor._latest_capture_time("ride_1"))

    assert result == NOW - timedelta(seconds=8)


def test_orphaned_open_events_close_terminally_when_ride_no_longer_active():
    """An event left 'open' past ride completion is closed with a distinct
    terminal status (never 'resolved' — capture did not actually resume), so
    'open' always means an active ride is silent right now."""
    updates: list[tuple] = []

    async def get_rows(table, filters, **_kwargs):
        if table == "ride_location_gap_events":
            return [
                {"id": "evt_done", "ride_id": "ride_done"},
                {"id": "evt_live", "ride_id": "ride_live"},
            ]
        if table == "rides":
            assert set(filters["id"]["$in"]) == {"ride_done", "ride_live"}
            return [
                {"id": "ride_done", "status": "completed"},
                {"id": "ride_live", "status": "in_progress"},
            ]
        raise AssertionError(f"unexpected table {table}")

    async def update_one(*args, **kwargs):
        updates.append(args)
        return {"id": args[1]["id"]}

    with (
        patch.object(route_gap_monitor.db_supabase, "get_rows", get_rows),
        patch.object(route_gap_monitor.db_supabase, "update_one", update_one),
    ):
        closed = _run(route_gap_monitor._close_orphaned_open_events(NOW))

    assert closed == 1
    assert updates == [
        (
            "ride_location_gap_events",
            {"id": "evt_done", "status": "open"},
            {"status": "unresolved_at_completion"},
        )
    ]
