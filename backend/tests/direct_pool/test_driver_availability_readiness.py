"""Driver readiness policy (migration 462 part A): seams and confirm_driver_ready."""

import pytest
from test_offer_decision_atomicity import _apply
from test_offer_decision_atomicity import offer_db as _offer_db  # noqa: F401 - pytest fixture

_CONFIRM_SIG = "confirm_driver_ready(text,bigint,text,text,text)"


@pytest.fixture()
def readiness_db(_offer_db):  # noqa: F811
    cur = _offer_db
    _apply(cur, "461_offer_delivery_receipts.sql")
    _apply(cur, "462_driver_readiness_policy.sql")
    _apply(cur, "462_driver_readiness_policy.sql")
    # settings persists across tests on the module-scoped connection.
    cur.execute(
        "UPDATE settings SET driver_readiness_policy_enabled=false, driver_readiness_idle_minutes=60, "
        "driver_readiness_prompt_minutes=2, driver_availability_v2_enabled=true WHERE id='app_settings'"
    )
    cur.execute("UPDATE drivers SET ready_until = clock_timestamp() + interval '30 minutes' WHERE id='d1'")
    return cur


def _enforce(cur, on=True):
    cur.execute("UPDATE settings SET driver_readiness_policy_enabled=%s WHERE id='app_settings'", (on,))


def _confirm(cur, epoch=5, session="sess-1", request_id="c-1", reason="still_ready", driver="d1"):
    cur.execute("SELECT public.confirm_driver_ready(%s,%s,%s,%s,%s)", (driver, epoch, session, request_id, reason))
    return cur.fetchone()[0]


def _row(cur, driver="d1"):
    cur.execute(
        "SELECT online_epoch, state_version, ready_until, readiness_prompt_sent_for, is_online, "
        "accepting_requests, availability_reason FROM drivers WHERE id=%s",
        (driver,),
    )
    return cur.fetchone()


def test_seams_follow_settings_and_stay_private(readiness_db):
    cur = readiness_db
    cur.execute("SELECT driver_ready_window(), driver_readiness_prompt_lead(), driver_readiness_enforced()")
    window, lead, enforced = cur.fetchone()
    assert window.total_seconds() == 62 * 60
    assert lead.total_seconds() == 120
    assert enforced is False
    cur.execute(
        "UPDATE settings SET driver_readiness_idle_minutes=30, driver_readiness_prompt_minutes=5, "
        "driver_readiness_policy_enabled=true WHERE id='app_settings'"
    )
    cur.execute("SELECT driver_ready_window(), driver_readiness_prompt_lead(), driver_readiness_enforced()")
    window, lead, enforced = cur.fetchone()
    assert window.total_seconds() == 35 * 60
    assert lead.total_seconds() == 300
    assert enforced is True
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=false WHERE id='app_settings'")
    cur.execute("SELECT driver_readiness_enforced()")
    assert cur.fetchone()[0] is False
    for fn in ("driver_ready_window()", "driver_readiness_prompt_lead()", "driver_readiness_enforced()"):
        cur.execute(f"SELECT has_function_privilege('service_role', '{fn}', 'EXECUTE')")
        assert cur.fetchone()[0] is False
    cur.execute(f"SELECT has_function_privilege('authenticated', '{_CONFIRM_SIG}', 'EXECUTE')")
    assert cur.fetchone()[0] is False
    cur.execute(f"SELECT has_function_privilege('service_role', '{_CONFIRM_SIG}', 'EXECUTE')")
    assert cur.fetchone()[0] is True


def test_settings_checks_reject_out_of_range(readiness_db):
    cur = readiness_db
    for column, value in (("driver_readiness_idle_minutes", 5), ("driver_readiness_prompt_minutes", 11)):
        with pytest.raises(Exception, match="check"):
            cur.execute(f"UPDATE settings SET {column}=%s WHERE id='app_settings'", (value,))


def test_confirm_refreshes_without_epoch_bump_and_replays(readiness_db):
    cur = readiness_db
    _enforce(cur)
    cur.execute("UPDATE drivers SET readiness_prompt_sent_for=ready_until WHERE id='d1'")
    epoch, version, before, _prompted, *_ = _row(cur)
    result = _confirm(cur)
    assert result["code"] == "OK"
    assert result["online_epoch"] == "5"
    after = _row(cur)
    assert after[0] == epoch
    assert after[1] == version + 1
    assert after[2] > before
    assert after[3] is None
    replay = _confirm(cur)
    assert replay == {**result, "replayed": True}
    assert _confirm(cur, request_id="c-1", reason="trip_completed", epoch=None)["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_confirm_check_order(readiness_db):
    cur = readiness_db
    assert _confirm(cur, session="system:readiness", request_id="x0")["code"] == "SESSION_SUPERSEDED"
    assert _confirm(cur, session="old", request_id="x1")["code"] == "SESSION_SUPERSEDED"
    cur.execute("UPDATE drivers SET controller_session_id='sess-old' WHERE id='d1'")
    mismatch = _confirm(cur, request_id="x2")
    assert mismatch["code"] == "REQUESTS_PAUSED"
    assert mismatch["reason_code"] == "CONTROLLER_SESSION_MISMATCH"
    cur.execute("UPDATE drivers SET controller_session_id='sess-1' WHERE id='d1'")
    assert _confirm(cur, epoch=4, request_id="x3")["code"] == "ONLINE_EPOCH_STALE"
    cur.execute("UPDATE drivers SET accepting_requests=false, is_available=false WHERE id='d1'")
    assert _confirm(cur, request_id="x4")["code"] == "REQUESTS_PAUSED"
    cur.execute("UPDATE drivers SET is_online=false WHERE id='d1'")
    assert _confirm(cur, request_id="x5")["code"] == "DRIVER_OFFLINE"
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=false WHERE id='app_settings'")
    assert _confirm(cur, request_id="x6")["code"] == "AVAILABILITY_V2_DISABLED"


def test_late_confirm_pauses_idle_driver(readiness_db):
    cur = readiness_db
    _enforce(cur)
    cur.execute("UPDATE drivers SET ready_until=clock_timestamp() - interval '1 second' WHERE id='d1'")
    result = _confirm(cur)
    assert result["code"] == "READINESS_EXPIRED"
    assert result["online_epoch"] == "6"
    epoch, _v, ready_until, _p, is_online, accepting, reason = _row(cur)
    assert (epoch, is_online, accepting, reason) == (6, False, False, "pause_idle")
    assert ready_until is None


def test_late_confirm_refreshes_when_not_enforced(readiness_db):
    cur = readiness_db
    cur.execute("UPDATE drivers SET ready_until=clock_timestamp() - interval '1 second' WHERE id='d1'")
    assert _confirm(cur)["code"] == "OK"


def test_trip_completed_needs_no_epoch_and_refreshes(readiness_db):
    cur = readiness_db
    _enforce(cur)
    cur.execute("UPDATE drivers SET ready_until=clock_timestamp() - interval '1 second' WHERE id='d1'")
    result = _confirm(cur, epoch=None, request_id="trip-completed:r1", reason="trip_completed")
    assert result["code"] == "OK"
    assert _row(cur)[0] == 5
    with pytest.raises(Exception, match="required"):
        _confirm(cur, epoch=None, request_id="c-9")


def test_heartbeats_never_extend_readiness(readiness_db):
    cur = readiness_db
    before = _row(cur)[2]
    cur.execute("UPDATE drivers SET last_contact_at=clock_timestamp() - interval '40 seconds' WHERE id='d1'")
    cur.execute("SELECT public.renew_driver_presence('d1','sess-1',5,NULL)")
    assert cur.fetchone()[0]["status"] == "renewed"
    assert _row(cur)[2] == before


def test_go_online_uses_configured_readiness_window(readiness_db):
    cur = readiness_db
    cur.execute(
        "UPDATE settings SET driver_readiness_idle_minutes=15, driver_readiness_prompt_minutes=2 "
        "WHERE id='app_settings'"
    )
    cur.execute(
        "UPDATE drivers SET is_online=false, is_available=false, accepting_requests=false, "
        "controller_session_id=NULL WHERE id='d1'"
    )
    cur.execute("SELECT clock_timestamp()")
    before = cur.fetchone()[0]
    cur.execute(
        "SELECT public.transition_driver_availability('d1',5,'sess-1','go_online','go-window')"
    )
    result = cur.fetchone()[0]
    assert result["code"] == "OK"
    ready_until = _row(cur)[2]
    assert 17 * 60 <= (ready_until - before).total_seconds() <= 17 * 60 + 5


def test_system_actor_allowlist_skips_user_session_and_contact_mutation(readiness_db):
    cur = readiness_db
    cur.execute("UPDATE users SET current_session_id=NULL WHERE id='u1'")
    cur.execute(
        "UPDATE drivers SET controller_session_id='replaced-session', "
        "last_contact_at=clock_timestamp() - interval '10 minutes' WHERE id='d1'"
    )
    cur.execute("SELECT last_contact_at FROM drivers WHERE id='d1'")
    contact_before = cur.fetchone()[0]

    cur.execute(
        "SELECT public.transition_driver_availability('d1',5,'system:finalize','pause_idle','system-finalize')"
    )
    result = cur.fetchone()[0]
    assert result["code"] == "OK"
    assert result["is_online"] is False
    cur.execute("SELECT last_contact_at FROM drivers WHERE id='d1'")
    assert cur.fetchone()[0] == contact_before

    cur.execute(
        "SELECT public.transition_driver_availability('d1',6,'system:logout','pause_idle','bad-pair')"
    )
    assert cur.fetchone()[0]["code"] == "UNAUTHORIZED_SESSION"
    cur.execute("SELECT is_online,online_epoch FROM drivers WHERE id='d1'")
    assert cur.fetchone() == (False, 6)


def test_logout_system_actor_preserves_active_obligation(readiness_db):
    cur = readiness_db
    cur.execute("UPDATE users SET current_session_id=NULL WHERE id='u1'")
    cur.execute("UPDATE drivers SET controller_session_id='replaced-session' WHERE id='d1'")
    cur.execute(
        "INSERT INTO rides (id,driver_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES ('system-logout-ride','d1','a',0,0,'b',0,0,'in_progress')"
    )
    cur.execute(
        "INSERT INTO driver_insurance_periods (driver_id,period,ride_id) "
        "VALUES ('d1',3,'system-logout-ride')"
    )
    cur.execute("SELECT last_contact_at FROM drivers WHERE id='d1'")
    contact_before = cur.fetchone()[0]

    cur.execute(
        "SELECT public.transition_driver_availability('d1',5,'system:logout','stop_requests','logout-active-ride')"
    )
    result = cur.fetchone()[0]
    assert result["code"] == "OK"
    assert result["is_online"] is True
    assert result["accepting_requests"] is False
    cur.execute("SELECT last_contact_at FROM drivers WHERE id='d1'")
    assert cur.fetchone()[0] == contact_before
    cur.execute("SELECT period,ride_id,ended_at FROM driver_insurance_periods WHERE driver_id='d1'")
    assert cur.fetchone() == (3, "system-logout-ride", None)
