from datetime import datetime, timedelta, timezone
from pathlib import Path

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
