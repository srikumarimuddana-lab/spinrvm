"""Atomic v2 offer decisions (migration 460) against real PostgreSQL."""

from pathlib import Path

import pytest

_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
_DEFERRABLE = ("stop_requests", "pause_policy", "pause_unreachable", "pause_idle", "pause_misses")


def _apply(cur, name):
    from conftest import _apply_migration_sql

    _apply_migration_sql(cur, (_MIGRATIONS / name).read_text(encoding="utf-8"))


@pytest.fixture()
def offer_db(pg_cur):
    cur = pg_cur
    cur.execute("ALTER TABLE drivers ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()")
    for name in ("42_drivers_last_status_changed_at.sql", "97_driver_intent_timestamps.sql"):
        _apply(cur, name)
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_session_id text")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE drivers ADD COLUMN IF NOT EXISTS location_captured_at timestamptz")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS driver_subscriptions "
        "(id text PRIMARY KEY DEFAULT md5(random()::text), driver_id text, status text, expires_at timestamptz)"
    )
    for name in (
        "457_driver_availability_epoch.sql",
        "458_driver_availability_f2_fixes.sql",
        "459_driver_claim_epoch_fence.sql",
        "460_offer_decision_atomicity.sql",
        "464_driver_availability_transition_hardening.sql",
        "486_driver_availability_controller_rebind.sql",
    ):
        _apply(cur, name)
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    for n in (1, 2, 3):
        cur.execute(
            "INSERT INTO users (id, phone, current_session_id) VALUES (%s, %s, %s)",
            (f"u{n}", f"+1306555000{n}", f"sess-{n}"),
        )
        cur.execute(
            "INSERT INTO drivers (id,user_id,name,phone,is_online,is_available,is_verified,status,"
            "online_epoch,controller_session_id,accepting_requests,availability_reason,last_contact_at) "
            "VALUES (%s,%s,'Driver',%s,true,true,true,'active',5,%s,true,'go_online',clock_timestamp())",
            (f"d{n}", f"u{n}", f"+1306555000{n}", f"sess-{n}"),
        )
    cur.execute("INSERT INTO users (id, phone) VALUES ('rider', '+13065559999')")
    return cur


def _finalize(cur, driver="d1", request_id="fin-1"):
    cur.execute("SELECT public.finalize_deferred_driver_availability(%s,%s)", (driver, request_id))
    return cur.fetchone()[0]


def _driver(cur, driver="d1"):
    cur.execute(
        "SELECT is_online, accepting_requests, is_available, online_epoch, availability_reason "
        "FROM drivers WHERE id=%s",
        (driver,),
    )
    return cur.fetchone()


def _open_period(cur, driver="d1"):
    cur.execute(
        "SELECT period, ride_id FROM driver_insurance_periods WHERE driver_id=%s AND ended_at IS NULL",
        (driver,),
    )
    return cur.fetchone()


@pytest.mark.parametrize("reason", _DEFERRABLE)
def test_finalize_completes_each_deferred_reason(offer_db, reason):
    cur = offer_db
    cur.execute(
        "UPDATE drivers SET accepting_requests=false, is_available=false, availability_reason=%s WHERE id='d1'",
        (reason,),
    )
    if reason == "pause_policy":
        # T1 re-checks that the policy state still holds.
        cur.execute("UPDATE drivers SET status='suspended' WHERE id='d1'")
    result = _finalize(cur)
    assert result["code"] == "OK"
    assert result["finalized"] is True
    assert result["availability"]["code"] == "OK"
    is_online, accepting, available, epoch, _ = _driver(cur)
    assert (is_online, accepting, available, epoch) == (False, False, False, 6)
    assert _open_period(cur) == (0, None)


def test_finalize_is_noop_when_accepting_or_other_reason(offer_db):
    cur = offer_db
    assert _finalize(cur)["finalized"] is False
    cur.execute("UPDATE drivers SET accepting_requests=false, availability_reason='go_offline' WHERE id='d1'")
    assert _finalize(cur, request_id="fin-2")["finalized"] is False
    assert _driver(cur)[0] is True


@pytest.mark.parametrize("result", [None, {"status": "race", "opened": False}])
def test_claim_rolls_back_when_period_2_is_not_recorded(offer_db, result):
    cur = offer_db
    cur.execute("UPDATE drivers SET location_captured_at=clock_timestamp() WHERE id='d1'")
    cur.execute(
        "INSERT INTO rides (id,rider_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES ('claim-insurance-failure','rider','a',0,0,'b',0,0,'searching')"
    )
    if result is None:
        failure_body = "BEGIN RAISE EXCEPTION 'forced insurance failure'; END;"
    else:
        failure_body = 'BEGIN RETURN \'{"status":"race","opened":false}\'::jsonb; END;'
    cur.execute(
        "CREATE OR REPLACE FUNCTION public.record_insurance_period_transition("
        "p_driver_id text,p_new_period smallint,p_ride_id text DEFAULT NULL) "
        "RETURNS jsonb LANGUAGE plpgsql AS $$ " + failure_body + " $$"
    )
    try:
        with pytest.raises(Exception, match="insurance"):
            cur.execute(
                "SELECT public.dispatch_claim_offers_v3(%s,"
                "jsonb_build_array(jsonb_build_object("
                "'driver_id','d1','session_id','sess-1','online_epoch','5',"
                "'contact_valid_until',clock_timestamp()+interval '30 seconds',"
                "'location_valid_until',clock_timestamp()+interval '30 seconds')),"
                "1,15,false,'automatic')",
                ("claim-insurance-failure",),
            )
    finally:
        _apply(cur, "421_insurance_period_ride_identity.sql")

    cur.execute("SELECT is_available,availability_claim_id FROM drivers WHERE id='d1'")
    assert cur.fetchone() == (True, None)
    cur.execute("SELECT count(*) FROM ride_offers WHERE ride_id='claim-insurance-failure'")
    assert cur.fetchone()[0] == 0
    cur.execute(
        "SELECT count(*) FROM driver_insurance_periods WHERE driver_id='d1' "
        "AND ride_id='claim-insurance-failure' AND period=2"
    )
    assert cur.fetchone()[0] == 0


def test_finalize_blocked_by_obligation(offer_db):
    cur = offer_db
    cur.execute(
        "INSERT INTO rides (id,rider_id,driver_id,pickup_address,pickup_lat,pickup_lng,"
        "dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES ('trip','rider','d1','a',0,0,'b',0,0,'driver_accepted')"
    )
    cur.execute(
        "UPDATE drivers SET accepting_requests=false, is_available=false, availability_reason='stop_requests' "
        "WHERE id='d1'"
    )
    result = _finalize(cur)
    assert result["finalized"] is False
    assert result["reason"] == "obligation"
    assert _driver(cur)[0] is True


def test_finalize_dark_when_flag_off(offer_db):
    cur = offer_db
    cur.execute("UPDATE settings SET driver_availability_v2_enabled=false WHERE id='app_settings'")
    assert _finalize(cur)["code"] == "AVAILABILITY_V2_DISABLED"


def test_private_helpers_revoked_and_public_granted(offer_db):
    cur = offer_db
    private = (
        "_release_offer_claim_locked(text,ride_offers,timestamptz,text,boolean,integer)",
        "_finalize_deferred_availability_locked(drivers,text)",
        "offer_expiry_counts_as_miss(ride_offers,drivers)",
    )
    for sig in private:
        for role in ("authenticated", "anon", "service_role"):
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig))
            assert cur.fetchone()[0] is False, (role, sig)
    public = "finalize_deferred_driver_availability(text,text)"
    cur.execute("SELECT has_function_privilege('authenticated', %s, 'EXECUTE')", (public,))
    assert cur.fetchone()[0] is False
    cur.execute("SELECT has_function_privilege('service_role', %s, 'EXECUTE')", (public,))
    assert cur.fetchone()[0] is True
    cur.execute("SELECT has_table_privilege('authenticated', 'driver_offer_decisions', 'SELECT')")
    assert cur.fetchone()[0] is False


def test_migration_460_applies_twice(offer_db):
    cur = offer_db
    _apply(cur, "460_offer_decision_atomicity.sql")
    cur.execute("SELECT count(*) FROM pg_constraint WHERE conname='ride_offers_outcome_check'")
    assert cur.fetchone()[0] == 1


@pytest.mark.parametrize("eligible", [True, False])
def test_admin_claim_assigns_only_after_admission(offer_db, eligible):
    cur = offer_db
    cur.execute("UPDATE drivers SET accepting_requests=%s WHERE id='d1'", (eligible,))
    cur.execute(
        "INSERT INTO rides (id,rider_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES ('admin-new','rider','a',0,0,'b',0,0,'searching')"
    )
    cur.execute(
        "SELECT dispatch_claim_offers_v3('admin-new',"
        "jsonb_build_array(jsonb_build_object('driver_id','d1')),1,30,false,'admin_direct')"
    )
    result = cur.fetchone()[0]
    assert result["code"] == "OK"
    assert result["results"][0]["claimed"] is eligible
    cur.execute("SELECT status,driver_id,driver_notified_at FROM rides WHERE id='admin-new'")
    status, driver_id, notified = cur.fetchone()
    assert (status, driver_id) == (("driver_assigned", "d1") if eligible else ("searching", None))
    assert (notified is not None) is eligible
    cur.execute("SELECT is_available,availability_claim_id FROM drivers WHERE id='d1'")
    available, claim_id = cur.fetchone()
    assert available is not eligible
    assert (claim_id is not None) is eligible
    cur.execute("SELECT count(*) FROM driver_insurance_periods WHERE ride_id='admin-new' AND period=2")
    assert cur.fetchone()[0] == int(eligible)


def test_admin_claim_cannot_overlap_existing_batch_offer(offer_db):
    cur = offer_db
    cur.execute(
        "INSERT INTO rides (id,rider_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES ('admin-race','rider','a',0,0,'b',0,0,'searching')"
    )
    cur.execute("INSERT INTO ride_offers(ride_id,driver_id,status) VALUES ('admin-race','d2','pending')")
    cur.execute(
        "SELECT dispatch_claim_offers_v3('admin-race',"
        "jsonb_build_array(jsonb_build_object('driver_id','d1')),1,30,false,'admin_direct')"
    )
    assert cur.fetchone()[0]["code"] == "RIDE_STATE_CONFLICT"
    cur.execute("SELECT status,driver_id FROM rides WHERE id='admin-race'")
    assert cur.fetchone() == ("searching", None)
    cur.execute("SELECT is_available,availability_claim_id FROM drivers WHERE id='d1'")
    assert cur.fetchone() == (True, None)


@pytest.mark.parametrize(
    "assigned_driver,expected", [("d1", "OK"), ("d2", "RIDE_STATE_CONFLICT"), (None, "RIDE_STATE_CONFLICT")]
)
def test_admin_claim_legacy_assignment_must_match_candidate(offer_db, assigned_driver, expected):
    cur = offer_db
    cur.execute(
        "INSERT INTO rides (id,driver_id,rider_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES ('admin-legacy',%s,'rider','a',0,0,'b',0,0,'driver_assigned')",
        (assigned_driver,),
    )
    cur.execute(
        "SELECT dispatch_claim_offers_v3('admin-legacy',"
        "jsonb_build_array(jsonb_build_object('driver_id','d1')),1,30,false,'admin_direct')"
    )
    assert cur.fetchone()[0]["code"] == expected
    if expected != "OK":
        cur.execute("SELECT is_available,availability_claim_id FROM drivers WHERE id='d1'")
        assert cur.fetchone() == (True, None)
