"""PostgreSQL contract tests for T6 offer delivery receipts (migration 461)."""

import uuid
from pathlib import Path

import pytest

_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
_RECEIPT_SIG = "record_offer_receipt(uuid,uuid,text,text,text,text,text,integer)"


@pytest.fixture()
def receipts_db(pg_cur):
    from conftest import _apply_migration_sql

    pg_cur.execute("ALTER TABLE drivers ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()")
    for name in ("42_drivers_last_status_changed_at.sql", "97_driver_intent_timestamps.sql"):
        _apply_migration_sql(pg_cur, (_MIGRATIONS / name).read_text(encoding="utf-8"))
    pg_cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_session_id text")
    pg_cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 0")
    pg_cur.execute("ALTER TABLE drivers ADD COLUMN IF NOT EXISTS location_captured_at timestamptz")
    for prefix in ("457", "458", "459", "460", "461"):
        for path in sorted(_MIGRATIONS.glob(f"{prefix}_*.sql")):
            _apply_migration_sql(pg_cur, path.read_text(encoding="utf-8"))
    # The second apply proves idempotency.
    for path in sorted(_MIGRATIONS.glob("461_*.sql")):
        _apply_migration_sql(pg_cur, path.read_text(encoding="utf-8"))
    pg_cur.execute("UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'")
    for suffix, phone in (("A", "+13065550101"), ("B", "+13065550102")):
        pg_cur.execute(
            "INSERT INTO users (id, phone, current_session_id) VALUES (%s, %s, %s)",
            (f"rcpt-user-{suffix}", phone, f"sess-{suffix}"),
        )
        pg_cur.execute(
            "INSERT INTO drivers (id,user_id,name,phone,is_online,is_available,is_verified,status,online_epoch,"
            "controller_session_id,accepting_requests,availability_claim_id) "
            "VALUES (%s,%s,'Driver',%s,true,false,true,'active',7,%s,true,%s)",
            (f"rcpt-driver-{suffix}", f"rcpt-user-{suffix}", phone, f"sess-{suffix}", str(uuid.uuid4())),
        )
    return pg_cur


def _offer(cur, driver="rcpt-driver-A", ride="rcpt-ride", session="sess-A", epoch=7, ttl="15 seconds"):
    cur.execute(
        "INSERT INTO rides (id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,dropoff_lng,status) "
        "VALUES (%s,'a',0,0,'b',0,0,'searching') ON CONFLICT (id) DO NOTHING",
        (ride,),
    )
    cur.execute("SELECT availability_claim_id FROM drivers WHERE id=%s", (driver,))
    claim_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO ride_offers (ride_id,driver_id,status,expires_at,claim_id,online_epoch,controller_session_id) "
        "VALUES (%s,%s,'pending',clock_timestamp() + %s::interval,%s,%s,%s) RETURNING id",
        (ride, driver, ttl, claim_id, epoch, session),
    )
    return str(cur.fetchone()[0]), str(claim_id)


def _receipt(
    cur,
    offer_id,
    claim_id,
    *,
    user="rcpt-user-A",
    session="sess-A",
    event="received",
    channel="ws",
    app_state="active",
    remaining_ms=12000,
):
    cur.execute(
        "SELECT public.record_offer_receipt(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s)",
        (offer_id, claim_id, user, session, event, channel, app_state, remaining_ms),
    )
    return cur.fetchone()[0]


def test_receipt_privileges_and_policy(receipts_db):
    cur = receipts_db
    cur.execute("SELECT has_table_privilege('authenticated','public.driver_offer_receipts','INSERT')")
    assert cur.fetchone()[0] is False
    cur.execute("SELECT has_table_privilege('authenticated','public.driver_offer_receipts','SELECT')")
    assert cur.fetchone()[0] is True
    cur.execute("SELECT has_table_privilege('service_role','public.driver_offer_receipts','UPDATE')")
    assert cur.fetchone()[0] is False
    cur.execute(f"SELECT has_function_privilege('authenticated','{_RECEIPT_SIG}','EXECUTE')")
    assert cur.fetchone()[0] is False
    cur.execute(f"SELECT has_function_privilege('service_role','{_RECEIPT_SIG}','EXECUTE')")
    assert cur.fetchone()[0] is True
    cur.execute(
        "SELECT count(*) FROM pg_policies WHERE tablename='driver_offer_receipts' "
        "AND policyname='driver_offer_receipts_driver_select'"
    )
    assert cur.fetchone()[0] == 1


def test_receipt_recorded_once_across_channels(receipts_db):
    cur = receipts_db
    offer_id, claim_id = _offer(cur)
    first = _receipt(cur, offer_id, claim_id, channel="ws")
    assert first["code"] == "OK"
    assert first["recorded"] is True
    assert first["offer_status"] == "pending"
    assert first["late"] is False
    assert first["server_time"] and first["expires_at"]
    dup = _receipt(cur, offer_id, claim_id, channel="push")
    assert dup["code"] == "OK"
    assert dup["recorded"] is False
    presented = _receipt(cur, offer_id, claim_id, event="presented")
    assert presented["recorded"] is True
    cur.execute("SELECT count(*) FROM driver_offer_receipts WHERE offer_id=%s", (offer_id,))
    assert cur.fetchone()[0] == 2


def test_wrong_driver_and_unknown_offer_are_not_found(receipts_db):
    cur = receipts_db
    offer_id, claim_id = _offer(cur)
    assert _receipt(cur, offer_id, claim_id, user="rcpt-user-B", session="sess-B")["code"] == "OFFER_NOT_FOUND"
    assert _receipt(cur, str(uuid.uuid4()), claim_id)["code"] == "OFFER_NOT_FOUND"


def test_claim_and_session_mismatch(receipts_db):
    cur = receipts_db
    offer_id, claim_id = _offer(cur)
    assert _receipt(cur, offer_id, str(uuid.uuid4()))["code"] == "CLAIM_MISMATCH"
    assert _receipt(cur, offer_id, claim_id, session="sess-old")["code"] == "SESSION_SUPERSEDED"
    # A newer login does not own an offer addressed to the old session.
    cur.execute("UPDATE users SET current_session_id='sess-new' WHERE id='rcpt-user-A'")
    result = _receipt(cur, offer_id, claim_id, session="sess-new")
    assert result["code"] == "OFFER_NOT_FOUND"
    assert _receipt(cur, offer_id, claim_id, session="sess-A")["code"] == "SESSION_SUPERSEDED"


def test_invalid_receipts(receipts_db):
    cur = receipts_db
    offer_id, claim_id = _offer(cur)
    assert _receipt(cur, offer_id, claim_id, event="presented", app_state="background")["code"] == "INVALID_RECEIPT"
    assert _receipt(cur, offer_id, claim_id, event="seen")["code"] == "INVALID_RECEIPT"
    assert _receipt(cur, offer_id, claim_id, channel="sms")["code"] == "INVALID_RECEIPT"
    assert _receipt(cur, offer_id, claim_id, app_state="foreground")["code"] == "INVALID_RECEIPT"
    assert _receipt(cur, offer_id, claim_id, remaining_ms=3_600_001)["code"] == "INVALID_RECEIPT"
    cur.execute("SELECT count(*) FROM driver_offer_receipts")
    assert cur.fetchone()[0] == 0


def test_late_receipt_is_recorded_and_flagged(receipts_db):
    cur = receipts_db
    offer_id, claim_id = _offer(cur, ttl="-1 second")
    result = _receipt(cur, offer_id, claim_id, remaining_ms=-1000)
    assert result["code"] == "OK"
    assert result["late"] is True
