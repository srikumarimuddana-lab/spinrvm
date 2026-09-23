"""Atomic driver availability epoch transitions against real PostgreSQL."""

from pathlib import Path

import pytest


@pytest.fixture()
def availability_db(pg_cur):
    from conftest import _apply_migration_sql

    migrations = Path(__file__).resolve().parents[2] / "migrations"
    # The extracted base drivers DDL lacks updated_at; later production
    # migrations assume it exists, so provide the same base column first.
    pg_cur.execute("ALTER TABLE drivers ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()")
    for migration_name in ("42_drivers_last_status_changed_at.sql", "97_driver_intent_timestamps.sql"):
        _apply_migration_sql(pg_cur, (migrations / migration_name).read_text(encoding="utf-8"))
    migration = migrations / "455_driver_availability_epoch.sql"
    _apply_migration_sql(pg_cur, migration.read_text(encoding="utf-8"))
    pg_cur.execute("UPDATE settings SET driver_availability_v2_enabled=false WHERE id='app_settings'")
    pg_cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_session_id text")
    pg_cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 0")
    pg_cur.execute("INSERT INTO users (id, phone, current_session_id) VALUES ('avail-user', '+13060000999', 'sess-A')")
    pg_cur.execute(
        "INSERT INTO drivers (id,user_id,name,phone,is_online,is_available,is_verified,status) "
        "VALUES ('avail-driver','avail-user','Driver','+13060000999',true,true,true,'active')"
    )
    return pg_cur


def _transition(cur, epoch, action, request_id, session="sess-A", driver="avail-driver"):
    cur.execute(
        "SELECT public.transition_driver_availability(%s,%s,%s,%s,%s)",
        (driver, epoch, session, action, request_id),
    )
    return cur.fetchone()[0]


def _snapshot(cur, user_id="avail-user"):
    cur.execute("SELECT public.get_driver_availability_snapshot(%s)", (user_id,))
    return cur.fetchone()[0]


def test_snapshot_is_single_authoritative_view_and_service_role_only(availability_db):
    cur = availability_db
    cur.execute("SELECT has_function_privilege('authenticated', 'get_driver_availability_snapshot(text)', 'EXECUTE')")
    assert cur.fetchone()[0] is False
    cur.execute("SELECT has_function_privilege('service_role', 'get_driver_availability_snapshot(text)', 'EXECUTE')")
    assert cur.fetchone()[0] is True

    snapshot = _snapshot(cur)
    assert snapshot["protocol_enabled"] is False
    assert snapshot["driver"]["online_epoch"] == 0
    assert snapshot["server_time"]
    assert snapshot["active_ride"] is None
    assert snapshot["pending_offer"] is None

    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    cur.execute(
        "INSERT INTO rides (id,driver_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES ('snapshot-ride','avail-driver','a',0,0,'b',0,0,'searching')"
    )
    cur.execute(
        "INSERT INTO ride_offers (ride_id,driver_id,status,expires_at) "
        "VALUES ('snapshot-ride','avail-driver','pending',clock_timestamp() + interval '1 minute')"
    )
    snapshot = _snapshot(cur)
    assert snapshot["protocol_enabled"] is True
    assert snapshot["pending_offer"]["ride_id"] == "snapshot-ride"

    cur.execute("UPDATE ride_offers SET expires_at=clock_timestamp() - interval '1 second' WHERE ride_id='snapshot-ride'")
    snapshot = _snapshot(cur)
    assert snapshot["pending_offer"] is None
    assert snapshot["offer_reconciliation_required"] is True


def test_dark_by_default_and_rejects_client_execute(availability_db):
    cur = availability_db
    result = _transition(cur, 0, "go_online", "dark-request")
    assert result["code"] == "AVAILABILITY_V2_DISABLED"
    cur.execute("SELECT is_online, state_version FROM drivers WHERE id='avail-driver'")
    assert cur.fetchone() == (True, 0)
    cur.execute("SELECT has_function_privilege('authenticated', 'transition_driver_availability(text,bigint,text,text,text)', 'EXECUTE')")
    assert cur.fetchone()[0] is False
    cur.execute("SELECT has_function_privilege('service_role', 'transition_driver_availability(text,bigint,text,text,text)', 'EXECUTE')")
    assert cur.fetchone()[0] is True


def test_stale_epoch_cannot_override_newer_transition(availability_db):
    cur = availability_db
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    first = _transition(cur, 0, "stop_requests", "stop-1")
    stale = _transition(cur, 0, "pause_unreachable", "pause-old")
    assert first["online_epoch"] == "1"
    assert first["accepting_requests"] is False
    assert stale["code"] == "ONLINE_EPOCH_STALE"
    assert stale["online_epoch"] == "1"


def test_idempotency_and_authenticated_controller(availability_db):
    cur = availability_db
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    first = _transition(cur, 0, "stop_requests", "same-id")
    duplicate = _transition(cur, 0, "stop_requests", "same-id")
    assert duplicate == first
    conflict = _transition(cur, 0, "go_online", "same-id")
    assert conflict["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    unauthorized = _transition(cur, 1, "go_online", "bad-session", session="old-session")
    assert unauthorized["code"] == "UNAUTHORIZED_SESSION"
    cur.execute("SELECT token_version,current_session_id FROM users WHERE id='avail-user'")
    assert cur.fetchone() == (0, "sess-A")


def test_request_id_is_scoped_to_driver(availability_db):
    cur = availability_db
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    cur.execute("INSERT INTO users (id,phone,current_session_id) VALUES ('other-user','+13060000998','sess-B')")
    cur.execute(
        "INSERT INTO drivers (id,user_id,name,phone,is_online,is_available,is_verified,status) "
        "VALUES ('other-driver','other-user','Other Driver','+13060000998',false,false,true,'active')"
    )
    first = _transition(cur, 0, "go_online", "shared-request")
    second = _transition(cur, 0, "go_online", "shared-request", session="sess-B", driver="other-driver")
    assert first["code"] == second["code"] == "OK"
    cur.execute("SELECT count(*) FROM driver_availability_requests WHERE request_id='shared-request'")
    assert cur.fetchone()[0] == 2


def test_active_trip_stop_keeps_online_and_period(availability_db):
    cur = availability_db
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    cur.execute(
        "INSERT INTO rides (id,driver_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES ('active-ride','avail-driver','a',0,0,'b',0,0,'in_progress')"
    )
    cur.execute("INSERT INTO driver_insurance_periods (driver_id,period,ride_id) VALUES ('avail-driver',3,'active-ride')")
    result = _transition(cur, 0, "stop_requests", "stop-trip")
    assert result["is_online"] is True
    assert result["accepting_requests"] is False
    cur.execute("SELECT period,ride_id,ended_at FROM driver_insurance_periods WHERE driver_id='avail-driver'")
    assert cur.fetchone() == (3, "active-ride", None)


def test_go_offline_blocked_by_obligation(availability_db):
    cur = availability_db
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    cur.execute(
        "INSERT INTO rides (id,driver_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES ('assigned-ride','avail-driver','a',0,0,'b',0,0,'driver_assigned')"
    )
    result = _transition(cur, 0, "go_offline", "offline-obligation")
    assert result["code"] == "OBLIGATION_ACTIVE"
    assert result["online_epoch"] == "0"


def test_go_online_binds_existing_session_without_changing_generation(availability_db):
    cur = availability_db
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    cur.execute("UPDATE drivers SET is_online=false,is_available=false WHERE id='avail-driver'")
    result = _transition(cur, 0, "go_online", "go-1")
    assert result["code"] == "OK"
    assert result["online_epoch"] == "1"
    assert result["controller_session_id"] == "sess-A"
    assert result["accepting_requests"] is True
    assert result["ready_until"] is not None
    cur.execute("SELECT token_version,current_session_id FROM users WHERE id='avail-user'")
    assert cur.fetchone() == (0, "sess-A")
    cur.execute("SELECT went_online_at,last_status_changed_at,updated_at FROM drivers WHERE id='avail-driver'")
    went_online_at, changed_at, updated_at = cur.fetchone()
    assert went_online_at is not None and changed_at is not None and updated_at is not None


def test_failed_insurance_result_rolls_back_availability_transition(availability_db):
    import psycopg2

    cur = availability_db
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    cur.execute("INSERT INTO driver_insurance_periods (driver_id,period) VALUES ('avail-driver',0)")
    cur.execute(
        """CREATE OR REPLACE FUNCTION public.record_insurance_period_transition(
               p_driver_id text,p_new_period smallint,p_ride_id text DEFAULT NULL)
           RETURNS jsonb LANGUAGE sql AS $$ SELECT jsonb_build_object('status','race') $$"""
    )
    try:
        with pytest.raises(psycopg2.Error, match="availability Period-1 transition failed"):
            _transition(cur, 0, "go_online", "period-race")
        cur.execute("SELECT is_online,is_available,accepting_requests,online_epoch,state_version FROM drivers WHERE id='avail-driver'")
        assert cur.fetchone() == (True, True, False, 0, 0)
        cur.execute("SELECT period,ended_at FROM driver_insurance_periods WHERE driver_id='avail-driver'")
        assert cur.fetchone() == (0, None)
    finally:
        migrations = Path(__file__).resolve().parents[2] / "migrations"
        from scripts.run_migrations import _split_sql_statements

        for statement in _split_sql_statements(
            (migrations / "421_insurance_period_ride_identity.sql").read_text(encoding="utf-8")
        ):
            cur.execute(statement)
