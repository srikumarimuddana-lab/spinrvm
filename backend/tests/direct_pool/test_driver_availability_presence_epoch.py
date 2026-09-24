"""PostgreSQL contract tests for session and epoch fenced presence renewal."""

from datetime import timedelta
from pathlib import Path

import pytest


@pytest.fixture()
def presence_db(pg_cur):
    from conftest import _apply_migration_sql

    migrations = Path(__file__).resolve().parents[2] / "migrations"
    pg_cur.execute("ALTER TABLE drivers ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()")
    for migration_name in ("42_drivers_last_status_changed_at.sql", "97_driver_intent_timestamps.sql"):
        _apply_migration_sql(pg_cur, (migrations / migration_name).read_text(encoding="utf-8"))
    _apply_migration_sql(pg_cur, (migrations / "457_driver_availability_epoch.sql").read_text(encoding="utf-8"))
    pg_cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_session_id text")
    pg_cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 0")
    pg_cur.execute("INSERT INTO users (id, phone, current_session_id) VALUES ('presence-user', '+13060000997', 'sess-A')")
    pg_cur.execute(
        "INSERT INTO drivers (id,user_id,name,phone,is_online,is_available,is_verified,status,online_epoch,"
        "controller_session_id,accepting_requests,last_contact_at,ready_until) "
        "VALUES ('presence-driver','presence-user','Driver','+13060000997',true,true,true,'active',12,"
        "'sess-A',true,clock_timestamp(),clock_timestamp() + interval '62 minutes')"
    )
    pg_cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    return pg_cur


def _renew(cur, session="sess-A", epoch=12, captured_at=None):
    cur.execute(
        "SELECT public.renew_driver_presence(%s,%s,%s,%s)",
        ("presence-driver", session, epoch, captured_at),
    )
    return cur.fetchone()[0]


def test_renew_requires_presented_session_current_controller_and_current_epoch(presence_db):
    cur = presence_db
    assert _renew(cur, session="old-session")["code"] == "UNAUTHORIZED_SESSION"
    assert _renew(cur, session="sess-A", epoch=11)["code"] == "ONLINE_EPOCH_STALE"
    cur.execute("SELECT online_epoch,last_contact_at FROM drivers WHERE id='presence-driver'")
    epoch, contact = cur.fetchone()
    assert epoch == 12
    assert contact is not None


def test_contact_renewal_does_not_refresh_gps_or_readiness(presence_db):
    cur = presence_db
    cur.execute(
        "UPDATE drivers SET last_contact_at=clock_timestamp()-interval '31 seconds', "
        "ready_until=clock_timestamp()+interval '10 minutes' WHERE id='presence-driver'"
    )
    cur.execute("SELECT ready_until,last_contact_at FROM drivers WHERE id='presence-driver'")
    ready_until, before_contact = cur.fetchone()
    result = _renew(cur)
    assert result["code"] == "renewed"
    assert result["contact_valid_until"]
    assert result["location_valid_until"] is None
    cur.execute("SELECT ready_until,last_contact_at FROM drivers WHERE id='presence-driver'")
    persisted_ready, persisted_contact = cur.fetchone()
    assert persisted_ready == ready_until
    assert persisted_contact is not None
    assert persisted_contact > before_contact

    cur.execute("UPDATE drivers SET last_contact_at=clock_timestamp()-interval '29 seconds' WHERE id='presence-driver'")
    cur.execute("SELECT last_contact_at FROM drivers WHERE id='presence-driver'")
    before_coalesced = cur.fetchone()[0]
    assert _renew(cur)["code"] == "renewed"
    cur.execute("SELECT last_contact_at FROM drivers WHERE id='presence-driver'")
    assert cur.fetchone()[0] == before_coalesced


def test_approved_location_deadline_uses_capture_time_and_rejects_old_or_future(presence_db):
    cur = presence_db
    cur.execute("SELECT clock_timestamp()")
    now = cur.fetchone()[0]
    approved = _renew(cur, captured_at=now - timedelta(seconds=59))
    assert approved["code"] == "renewed"
    cur.execute(
        "SELECT (%s::timestamptz - %s::timestamptz) < interval '2 seconds'",
        (approved["location_valid_until"], now),
    )
    assert cur.fetchone()[0] is True
    assert _renew(cur, captured_at=now - timedelta(seconds=61))["code"] == "INVALID_LOCATION_TIME"
    assert _renew(cur, captured_at=now + timedelta(seconds=6))["code"] == "INVALID_LOCATION_TIME"


def test_five_minute_contact_gap_fences_epoch_and_preserves_trip_obligation(presence_db):
    cur = presence_db
    cur.execute("""INSERT INTO rides (id,driver_id,pickup_address,pickup_lat,pickup_lng,
                 dropoff_address,dropoff_lat,dropoff_lng,status)
                 VALUES ('presence-trip','presence-driver','a',0,0,'b',0,0,'in_progress')""")
    cur.execute("INSERT INTO driver_insurance_periods (driver_id,period,ride_id) VALUES ('presence-driver',3,'presence-trip')")
    cur.execute("UPDATE drivers SET last_contact_at=clock_timestamp()-interval '5 minutes 1 second' WHERE id='presence-driver'")
    result = _renew(cur)
    assert result["code"] == "CONTACT_GAP"
    assert result["online_epoch"] == "13"
    cur.execute("SELECT is_online,accepting_requests,is_available,online_epoch,ready_until FROM drivers WHERE id='presence-driver'")
    assert cur.fetchone() == (True, False, False, 13, None)
    cur.execute("SELECT period,ride_id,ended_at FROM driver_insurance_periods WHERE driver_id='presence-driver'")
    assert cur.fetchone() == (3, "presence-trip", None)
    cur.execute("SELECT last_contact_at FROM drivers WHERE id='presence-driver'")
    assert cur.fetchone()[0] is not None
    reconciled = _renew(cur, epoch=13)
    assert reconciled["code"] == "renewed"
    assert reconciled["online_epoch"] == "13"
    cur.execute("SELECT is_online,accepting_requests,is_available,online_epoch FROM drivers WHERE id='presence-driver'")
    assert cur.fetchone() == (True, False, False, 13)
    assert _renew(cur)["code"] == "ONLINE_EPOCH_STALE"


def test_offline_driver_and_dark_flag_cannot_be_revived_by_renewal(presence_db):
    cur = presence_db
    cur.execute("UPDATE drivers SET is_online=false,is_available=false,accepting_requests=false WHERE id='presence-driver'")
    assert _renew(cur)["code"] == "OFFLINE"
    cur.execute("UPDATE drivers SET is_online=true,is_available=true,accepting_requests=true WHERE id='presence-driver'")
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=false WHERE id='app_settings'")
    assert _renew(cur)["code"] == "AVAILABILITY_V2_DISABLED"


def test_presence_rpc_is_service_role_only(presence_db):
    cur = presence_db
    cur.execute("SELECT has_function_privilege('authenticated', 'renew_driver_presence(text,text,bigint,timestamptz)', 'EXECUTE')")
    assert cur.fetchone()[0] is False
    cur.execute("SELECT has_function_privilege('service_role', 'renew_driver_presence(text,text,bigint,timestamptz)', 'EXECUTE')")
    assert cur.fetchone()[0] is True


def test_v2_live_marker_is_fenced_atomically_by_session_and_epoch(presence_db):
    cur = presence_db
    cur.execute(
        """CREATE OR REPLACE FUNCTION public.update_live_driver_marker(
               p_driver_id text,p_captured_at timestamptz,p_values jsonb)
             RETURNS boolean LANGUAGE plpgsql AS $$
             BEGIN
               UPDATE public.drivers SET lat=(p_values->>'lat')::double precision,
                     lng=(p_values->>'lng')::double precision WHERE id=p_driver_id;
               RETURN FOUND;
             END; $$"""
    )

    def fenced(session, epoch):
        cur.execute(
            "SELECT public.update_live_driver_marker_fenced(%s,%s,%s::jsonb,%s,%s)",
            (
                "presence-driver",
                "2026-09-23T12:00:00Z",
                '{"lat":50.45,"lng":-104.6}',
                session,
                epoch,
            ),
        )
        return cur.fetchone()[0]

    cur.execute("SELECT lat,lng FROM drivers WHERE id='presence-driver'")
    original_coordinates = cur.fetchone()
    assert fenced("sess-A", 11) is False
    assert fenced("old-session", 12) is False
    cur.execute("SELECT lat,lng FROM drivers WHERE id='presence-driver'")
    assert cur.fetchone() == original_coordinates
    assert fenced("sess-A", 12) is True
    cur.execute("SELECT lat,lng FROM drivers WHERE id='presence-driver'")
    assert cur.fetchone() == (50.45, -104.6)
    cur.execute(
        "SELECT has_function_privilege('authenticated', "
        "'update_live_driver_marker_fenced(text,timestamptz,jsonb,text,bigint)', 'EXECUTE')"
    )
    assert cur.fetchone()[0] is False
