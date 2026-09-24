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
    migration = migrations / "457_driver_availability_epoch.sql"
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

    cur.execute(
        "UPDATE ride_offers SET expires_at=clock_timestamp() - interval '1 second' WHERE ride_id='snapshot-ride'"
    )
    snapshot = _snapshot(cur)
    assert snapshot["pending_offer"] is None
    assert snapshot["offer_reconciliation_required"] is True


def test_dark_by_default_and_rejects_client_execute(availability_db):
    cur = availability_db
    result = _transition(cur, 0, "go_online", "dark-request")
    assert result["code"] == "AVAILABILITY_V2_DISABLED"
    cur.execute("SELECT is_online, state_version FROM drivers WHERE id='avail-driver'")
    assert cur.fetchone() == (True, 0)
    cur.execute(
        "SELECT has_function_privilege('authenticated', 'transition_driver_availability(text,bigint,text,text,text)', 'EXECUTE')"
    )
    assert cur.fetchone()[0] is False
    cur.execute(
        "SELECT has_function_privilege('service_role', 'transition_driver_availability(text,bigint,text,text,text)', 'EXECUTE')"
    )
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
    # A replay returns the saved result, marked so callers skip side effects.
    assert "replayed" not in first
    assert duplicate == {**first, "replayed": True}
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
    cur.execute(
        "INSERT INTO driver_insurance_periods (driver_id,period,ride_id) VALUES ('avail-driver',3,'active-ride')"
    )
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


def test_trusted_policy_pause_preserves_active_obligation_and_insurance(availability_db):
    cur = availability_db
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    cur.execute("UPDATE drivers SET status='suspended' WHERE id='avail-driver'")
    cur.execute("UPDATE drivers SET controller_session_id='replaced-session' WHERE id='avail-driver'")
    cur.execute(
        "INSERT INTO rides (id,driver_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES ('policy-ride','avail-driver','a',0,0,'b',0,0,'in_progress')"
    )
    cur.execute(
        "INSERT INTO driver_insurance_periods (driver_id,period,ride_id) VALUES ('avail-driver',3,'policy-ride')"
    )

    raced_go = _transition(cur, 0, "go_online", "go-after-suspension")
    assert raced_go["code"] == "ELIGIBILITY_BLOCKED"
    assert raced_go["reason_code"] == "ACCOUNT_INELIGIBLE"

    result = _transition(cur, 0, "pause_policy", "policy-pause")

    assert result["code"] == "OK"
    assert result["is_online"] is True
    assert result["accepting_requests"] is False
    assert result["availability_reason"] == "pause_policy"
    assert result["controller_session_id"] == "replaced-session"
    cur.execute("SELECT period,ride_id,ended_at FROM driver_insurance_periods WHERE driver_id='avail-driver'")
    assert cur.fetchone() == (3, "policy-ride", None)


def test_policy_pause_rechecks_terminal_status_under_driver_lock(availability_db):
    cur = availability_db
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    cur.execute(
        "SELECT is_online,accepting_requests,online_epoch,state_version,availability_reason,controller_session_id "
        "FROM drivers WHERE id='avail-driver'"
    )
    before = cur.fetchone()
    result = _transition(cur, 0, "pause_policy", "policy-no-longer-blocked")
    assert result["code"] == "POLICY_STATE_CHANGED"
    cur.execute(
        "SELECT is_online,accepting_requests,online_epoch,state_version,availability_reason,controller_session_id "
        "FROM drivers WHERE id='avail-driver'"
    )
    assert cur.fetchone() == before


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
        cur.execute(
            "SELECT is_online,is_available,accepting_requests,online_epoch,state_version FROM drivers WHERE id='avail-driver'"
        )
        assert cur.fetchone() == (True, True, False, 0, 0)
        cur.execute("SELECT period,ended_at FROM driver_insurance_periods WHERE driver_id='avail-driver'")
        assert cur.fetchone() == (0, None)
    finally:
        migrations = Path(__file__).resolve().parents[2] / "migrations"
        try:
            from backend.scripts.run_migrations import _split_sql_statements
        except ImportError:  # pragma: no cover - import style varies by entrypoint
            import sys as _sys

            _sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
            from backend.scripts.run_migrations import _split_sql_statements

        for statement in _split_sql_statements(
            (migrations / "421_insurance_period_ride_identity.sql").read_text(encoding="utf-8")
        ):
            cur.execute(statement)


def _enable_v2(cur):
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")


@pytest.mark.parametrize("current_session", [None, "sess-new"], ids=["no-current-session", "current-not-controller"])
def test_system_actor_pauses_without_current_session_or_controller(availability_db, current_session):
    cur = availability_db
    _enable_v2(cur)
    cur.execute("UPDATE users SET current_session_id=%s WHERE id='avail-user'", (current_session,))
    cur.execute(
        "UPDATE drivers SET status='suspended',controller_session_id='sess-old',accepting_requests=true "
        "WHERE id='avail-driver'"
    )

    result = _transition(cur, 0, "pause_policy", "policy-pause", session="system:policy")

    assert result["code"] == "OK"
    assert (result["is_online"], result["accepting_requests"], result["online_epoch"]) == (False, False, "1")
    assert result["controller_session_id"] == "sess-old"
    replay = _transition(cur, 0, "pause_policy", "policy-pause", session="system:policy")
    assert replay == {**result, "replayed": True}


@pytest.mark.parametrize(
    "session,action",
    [
        ("system:policy", "go_online"),
        ("system:logout", "go_offline"),
        ("system:finalize", "displace_controller"),
        ("system:bogus", "stop_requests"),
    ],
)
def test_system_actor_is_limited_to_known_sources_and_pause_actions(availability_db, session, action):
    import psycopg2

    cur = availability_db
    _enable_v2(cur)
    with pytest.raises(psycopg2.Error) as error:
        _transition(cur, 0, action, "system-misuse", session=session)
    assert error.value.pgcode == "22023"
    cur.execute("SELECT online_epoch,controller_session_id FROM drivers WHERE id='avail-driver'")
    assert cur.fetchone() == (0, None)


def test_system_stop_is_not_device_contact(availability_db):
    cur = availability_db
    _enable_v2(cur)
    cur.execute(
        "UPDATE drivers SET controller_session_id='sess-A',accepting_requests=true,"
        "last_contact_at=clock_timestamp() - interval '10 minutes' WHERE id='avail-driver'"
    )
    cur.execute("SELECT last_contact_at FROM drivers WHERE id='avail-driver'")
    before = cur.fetchone()[0]

    assert _transition(cur, 0, "stop_requests", "logout-stop", session="system:logout")["code"] == "OK"
    cur.execute("SELECT last_contact_at FROM drivers WHERE id='avail-driver'")
    assert cur.fetchone()[0] == before

    assert _transition(cur, 1, "stop_requests", "device-stop")["code"] == "OK"
    cur.execute("SELECT last_contact_at FROM drivers WHERE id='avail-driver'")
    assert cur.fetchone()[0] > before


@pytest.mark.parametrize(
    "is_online,controller,rebound",
    [(True, None, False), (False, "sess-A", False), (False, "sess-old", True), (True, "sess-old", True)],
    ids=["no-controller", "same-controller", "offline-old-controller", "online-old-controller"],
)
def test_go_online_from_current_session_binds_controller(availability_db, is_online, controller, rebound):
    cur = availability_db
    _enable_v2(cur)
    cur.execute(
        "UPDATE drivers SET is_online=%s,is_available=%s,accepting_requests=%s,controller_session_id=%s "
        "WHERE id='avail-driver'",
        (is_online, is_online, is_online, controller),
    )

    result = _transition(cur, 0, "go_online", "rebind-go")

    assert result["code"] == "OK"
    assert result["controller_session_id"] == "sess-A"
    assert result["online_epoch"] == "1"
    assert result["controller_rebound"] is rebound
    assert result["server_time"]
    assert (result["is_online"], result["accepting_requests"]) == (True, True)


def test_relogin_takes_over_an_online_controller_only_without_obligation(availability_db):
    cur = availability_db
    _enable_v2(cur)
    cur.execute("UPDATE drivers SET controller_session_id='sess-A',accepting_requests=true WHERE id='avail-driver'")
    # A session that is not current cannot act, even with the controller online.
    assert _transition(cur, 0, "go_online", "early-takeover", session="sess-B")["code"] == "UNAUTHORIZED_SESSION"

    cur.execute("UPDATE users SET current_session_id='sess-B' WHERE id='avail-user'")
    cur.execute(
        "INSERT INTO rides (id,driver_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES ('relogin-ride','avail-driver','a',0,0,'b',0,0,'driver_assigned')"
    )
    # (b) Assigned work refuses the takeover and leaves the controller alone;
    # other commands from the new session stay fenced to the controller.
    assert _transition(cur, 0, "go_online", "relogin-blocked", session="sess-B")["code"] == "OBLIGATION_ACTIVE"
    assert _transition(cur, 0, "stop_requests", "relogin-stop", session="sess-B")["code"] == (
        "CONTROLLER_SESSION_MISMATCH"
    )
    cur.execute("SELECT controller_session_id,online_epoch FROM drivers WHERE id='avail-driver'")
    assert cur.fetchone() == ("sess-A", 0)

    # (a) Without the obligation the newest login rebinds and bumps the epoch.
    cur.execute("UPDATE rides SET status='completed' WHERE id='relogin-ride'")
    result = _transition(cur, 0, "go_online", "relogin-go", session="sess-B")
    assert result["code"] == "OK"
    assert (result["controller_session_id"], result["online_epoch"], result["controller_rebound"]) == (
        "sess-B",
        "1",
        True,
    )
    # (c) The old session is refused outright.
    assert _transition(cur, 1, "stop_requests", "old-stop", session="sess-A")["code"] == "UNAUTHORIZED_SESSION"


_SEAMS = ("driver_ready_window()", "driver_readiness_prompt_lead()", "driver_readiness_enforced()")


def test_readiness_seams_default_off_and_are_private(availability_db):
    cur = availability_db
    cur.execute(
        "SELECT public.driver_ready_window() = interval '62 minutes', "
        "public.driver_readiness_prompt_lead() = interval '2 minutes', public.driver_readiness_enforced()"
    )
    assert cur.fetchone() == (True, True, False)
    for seam in _SEAMS:
        for role in ("anon", "authenticated", "service_role"):
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, seam))
            assert cur.fetchone()[0] is False, (role, seam)


def test_go_online_ready_window_comes_from_the_seam(availability_db):
    cur = availability_db
    _enable_v2(cur)
    assert _transition(cur, 0, "go_online", "window-default")["code"] == "OK"
    cur.execute(
        "SELECT abs(extract(epoch FROM ready_until - (clock_timestamp() + interval '62 minutes'))) < 5 "
        "FROM drivers WHERE id='avail-driver'"
    )
    assert cur.fetchone()[0] is True

    cur.execute(
        "CREATE OR REPLACE FUNCTION public.driver_ready_window() RETURNS interval LANGUAGE sql AS $$ SELECT interval '10 minutes' $$"
    )
    try:
        assert _transition(cur, 1, "go_online", "window-seam")["code"] == "OK"
        cur.execute(
            "SELECT abs(extract(epoch FROM ready_until - (clock_timestamp() + interval '10 minutes'))) < 5 "
            "FROM drivers WHERE id='avail-driver'"
        )
        assert cur.fetchone()[0] is True
    finally:
        cur.execute(
            "CREATE OR REPLACE FUNCTION public.driver_ready_window() RETURNS interval LANGUAGE sql STABLE "
            "SECURITY INVOKER SET search_path = pg_catalog, public AS $$ SELECT interval '62 minutes' $$"
        )


def test_snapshot_reports_readiness_policy_and_prompt_time(availability_db):
    cur = availability_db
    _enable_v2(cur)
    assert _transition(cur, 0, "go_online", "prompt-go")["code"] == "OK"
    snapshot = _snapshot(cur)
    assert snapshot["readiness_enforced"] is False
    cur.execute(
        "SELECT %s::timestamptz = ready_until - interval '2 minutes' FROM drivers WHERE id='avail-driver'",
        (snapshot["readiness_prompt_at"],),
    )
    assert cur.fetchone()[0] is True

    assert _transition(cur, 1, "go_offline", "prompt-off")["code"] == "OK"
    assert _snapshot(cur)["readiness_prompt_at"] is None
