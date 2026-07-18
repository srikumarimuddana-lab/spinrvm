from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.utils import route_gap_monitor
from backend.utils.route_gap_monitor import assess_location_gap

NOW = datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc)
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "235_ride_location_gap_events.sql"


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

    assert result == {"scanned": 1, "opened": 1, "resolved": 0, "unknown": 0}
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
    assert gauges == [("spinr.routes.gps_gap_open.count", 1)]


def test_gap_monitor_tick_resolves_an_open_event_after_capture_resumes(monkeypatch) -> None:
    updates: list[tuple] = []

    async def get_rows(table, _filters, **_kwargs):
        if table == "rides":
            return [
                {"id": "ride_1", "driver_id": "driver_1", "ride_started_at": (NOW - timedelta(minutes=4)).isoformat()}
            ]
        if table == "driver_location_history":
            return [{"captured_at": (NOW - timedelta(seconds=5)).isoformat()}]
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

    assert result == {"scanned": 1, "opened": 0, "resolved": 1, "unknown": 0}
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
