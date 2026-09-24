"""resolve_driver_offer (migration 460 part B): decisions, idempotency and races."""

import concurrent.futures
import os
import threading
import time
import urllib.parse

import psycopg2
from test_offer_decision_atomicity import _open_period, offer_db  # noqa: F401 - pytest fixture


def _ride(cur, ride_id="r1"):
    cur.execute(
        "INSERT INTO rides (id,rider_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,dropoff_lat,"
        "dropoff_lng,status) VALUES (%s,'rider','a',0,0,'b',0,0,'searching') ON CONFLICT (id) DO NOTHING",
        (ride_id,),
    )


def _offer(cur, driver="d1", ride_id="r1", expires_in="30 seconds"):
    """Mirror a v3 claim: claim the driver, insert the pending v2 offer, open Period 2."""
    _ride(cur, ride_id)
    cur.execute(
        "UPDATE drivers SET is_available=false, availability_claim_id=gen_random_uuid(), "
        "availability_claimed_at=clock_timestamp() WHERE id=%s RETURNING availability_claim_id",
        (driver,),
    )
    claim_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO ride_offers (ride_id,driver_id,status,offered_at,expires_at,claim_id,online_epoch,"
        "controller_session_id) SELECT %s,%s,'pending',clock_timestamp(),clock_timestamp() + %s::interval,"
        "%s,online_epoch,controller_session_id FROM drivers WHERE id=%s RETURNING id",
        (ride_id, driver, expires_in, claim_id, driver),
    )
    offer_id = cur.fetchone()[0]
    cur.execute("SELECT record_insurance_period_transition(%s, 2::smallint, %s)", (driver, ride_id))
    return str(offer_id), str(claim_id)


def _resolve(cur, offer_id, claim_id, action, request_id, epoch=None, session=None, threshold=3):
    if action in ("accept", "decline"):
        epoch = 5 if epoch is None else epoch
        session = session or "sess-1"
    cur.execute(
        "SELECT public.resolve_driver_offer(%s::uuid,%s::uuid,%s,%s,%s,%s,%s)",
        (offer_id, claim_id, epoch, session, action, request_id, threshold),
    )
    return cur.fetchone()[0]


def _expire_now(cur, offer_id):
    cur.execute("UPDATE ride_offers SET expires_at=clock_timestamp() - interval '1 second' WHERE id=%s", (offer_id,))


def _driver_state(cur, driver):
    cur.execute(
        "SELECT is_available, availability_claim_id, offer_miss_streak, is_online FROM drivers WHERE id=%s", (driver,)
    )
    return cur.fetchone()


def _period_rows(cur, driver):
    cur.execute("SELECT count(*) FROM driver_insurance_periods WHERE driver_id=%s", (driver,))
    return cur.fetchone()[0]


def test_accept_preempts_losers_without_locking_their_drivers(offer_db):  # noqa: F811
    cur = offer_db
    o1, c1 = _offer(cur, "d1")
    o2, c2 = _offer(cur, "d2")
    result = _resolve(cur, o1, c1, "accept", "acc-1")
    assert result["code"] == "OK"
    assert result["ride_status"] == "driver_accepted"
    assert result["losers"] == [{"offer_id": o2, "driver_id": "d2", "claim_id": c2}]
    assert result["remaining_pending_offers"] == 0
    assert _open_period(cur, "d1") == (2, "r1")
    # The loser is preempted but still claimed until its own release.
    assert _driver_state(cur, "d2")[0:2] == (False, c2)
    released = _resolve(cur, o2, c2, "cancel_unaccepted", f"preempted:{o2}")
    assert released["code"] == "OK"
    assert released["released"] is True
    assert released["outcome"] == "preempted"
    assert _driver_state(cur, "d2")[0:2] == (True, None)
    assert _open_period(cur, "d2") == (1, None)


def test_lost_response_replay_and_idempotency_conflict(offer_db):  # noqa: F811
    cur = offer_db
    o1, c1 = _offer(cur, "d1")
    first = _resolve(cur, o1, c1, "accept", "acc-1")
    replay = _resolve(cur, o1, c1, "accept", "acc-1")
    assert replay == {**first, "replayed": True}
    conflict = _resolve(cur, o1, c1, "decline", "acc-1")
    assert conflict["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    again = _resolve(cur, o1, c1, "accept", "acc-2")
    assert again["code"] == "OK"
    assert again["already_accepted"] is True


def test_accept_at_deadline_is_late_expiry_not_a_miss(offer_db):  # noqa: F811
    cur = offer_db
    o1, c1 = _offer(cur, "d1")
    _expire_now(cur, o1)
    result = _resolve(cur, o1, c1, "accept", "acc-late")
    assert result["code"] == "OFFER_EXPIRED"
    assert result["outcome"] == "expired_late_response"
    assert result["miss_counted"] is False
    assert result["released"] is True
    assert _driver_state(cur, "d1")[0:3] == (True, None, 0)
    # Persisted: the retry replays instead of re-deciding.
    assert _resolve(cur, o1, c1, "accept", "acc-late")["replayed"] is True


def test_expire_counts_one_miss_and_pauses_once(offer_db):  # noqa: F811
    cur = offer_db
    o1, c1 = _offer(cur, "d1")
    assert _resolve(cur, o1, c1, "expire", f"expire:{o1}", threshold=1)["code"] == "OFFER_NOT_EXPIRED"
    _expire_now(cur, o1)
    rows_before = _period_rows(cur, "d1")
    result = _resolve(cur, o1, c1, "expire", f"expire:{o1}", threshold=1)
    assert result["outcome"] == "expired_nonresponse"
    assert result["miss_counted"] is True
    assert result["paused"] is True
    assert result["availability"]["availability_reason"] == "pause_misses"
    assert _driver_state(cur, "d1") == (False, None, 0, False)
    assert _period_rows(cur, "d1") == rows_before + 1
    assert _resolve(cur, o1, c1, "expire", f"expire:{o1}", threshold=1)["replayed"] is True
    other = _resolve(cur, o1, c1, "expire", "reaper-other-id", threshold=1)
    assert other["code"] == "OFFER_ALREADY_RESOLVED"
    assert other["miss_counted"] is False
    cur.execute("SELECT count(*) FROM driver_availability_requests WHERE request_id=%s", (f"miss-pause:{o1}",))
    assert cur.fetchone()[0] == 1


def test_expire_after_epoch_change_is_not_a_miss(offer_db):  # noqa: F811
    cur = offer_db
    o1, c1 = _offer(cur, "d1")
    cur.execute("UPDATE drivers SET online_epoch=online_epoch + 1 WHERE id='d1'")
    _expire_now(cur, o1)
    result = _resolve(cur, o1, c1, "expire", f"expire:{o1}")
    assert result["outcome"] == "expired_availability_changed"
    assert result["miss_counted"] is False


def test_decline_releases_with_exactly_one_period_row(offer_db):  # noqa: F811
    cur = offer_db
    o1, c1 = _offer(cur, "d1")
    rows_before = _period_rows(cur, "d1")
    result = _resolve(cur, o1, c1, "decline", "dec-1")
    assert (result["code"], result["outcome"], result["released"]) == ("OK", "declined", True)
    assert _period_rows(cur, "d1") == rows_before + 1
    assert _open_period(cur, "d1") == (1, None)
    again = _resolve(cur, o1, c1, "decline", "dec-2")
    assert again["code"] == "OFFER_ALREADY_RESOLVED"
    assert again["outcome"] == "declined"


def test_session_rules_for_accept(offer_db):  # noqa: F811
    cur = offer_db
    o1, c1 = _offer(cur, "d1")
    assert _resolve(cur, o1, c1, "accept", "a-old", session="old-sess")["code"] == "SESSION_SUPERSEDED"
    # Newest login is current, but the offer was addressed to the old controller.
    cur.execute("UPDATE users SET current_session_id='sess-new' WHERE id='u1'")
    ended = _resolve(cur, o1, c1, "accept", "a-new", session="sess-new")
    assert (ended["code"], ended["reason_code"]) == ("OFFER_EXPIRED", "OFFER_SESSION_ENDED")
    cur.execute("SELECT status FROM ride_offers WHERE id=%s", (o1,))
    assert cur.fetchone()[0] == "pending"
    cur.execute("SELECT count(*) FROM driver_offer_decisions")
    assert cur.fetchone()[0] == 0


def test_stale_claim_is_not_released_after_newer_claim(offer_db):  # noqa: F811
    cur = offer_db
    o1, c1 = _offer(cur, "d1", "r1")
    cur.execute("UPDATE rides SET status='cancelled', cancelled_at=clock_timestamp() WHERE id='r1'")
    cur.execute("UPDATE ride_offers SET status='cancelled' WHERE id=%s", (o1,))
    cur.execute("UPDATE drivers SET availability_claim_id=gen_random_uuid() WHERE id='d1'")
    result = _resolve(cur, o1, c1, "cancel_unaccepted", f"cancel:{o1}")
    assert result["released"] is False
    assert _driver_state(cur, "d1")[0] is False


def test_rider_cancel_then_release(offer_db):  # noqa: F811
    cur = offer_db
    o1, c1 = _offer(cur, "d1")
    assert _resolve(cur, o1, c1, "cancel_unaccepted", f"cancel:{o1}")["reason_code"] == "RIDE_STILL_SEARCHING"
    cur.execute("UPDATE rides SET status='cancelled', cancelled_at=clock_timestamp() WHERE id='r1'")
    result = _resolve(cur, o1, c1, "cancel_unaccepted", f"cancel:{o1}")
    assert (result["outcome"], result["offer_status"], result["released"]) == ("cancelled", "cancelled", True)
    assert _driver_state(cur, "d1")[0:2] == (True, None)
    accept = _resolve(cur, o1, c1, "accept", "acc-after-cancel")
    assert (accept["code"], accept["reason_code"]) == ("RIDE_STATE_CONFLICT", "RIDE_CANCELLED")


def _dsn(cur):
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    info = cur.connection.get_dsn_parameters()
    parts = urllib.parse.urlsplit(dsn)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"/{info['dbname']}", parts.query, parts.fragment))


def _race(cur, first, second):
    """Run `first` holding its transaction for 1s, `second` after 0.3s; return both results."""
    dsn = _dsn(cur)
    started = threading.Event()
    out = {}

    def _held():
        conn = psycopg2.connect(dsn)
        try:
            c = conn.cursor()
            out["first"] = first(c)
            started.set()
            c.execute("SELECT pg_sleep(1)")
            conn.commit()
        finally:
            conn.close()

    def _waiter():
        started.wait(5)
        time.sleep(0.3)
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        try:
            out["second"] = second(conn.cursor())
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for f in [pool.submit(_held), pool.submit(_waiter)]:
            f.result(timeout=20)
    return out["first"], out["second"]


def test_race_accept_then_expire(offer_db):  # noqa: F811
    cur = offer_db
    o1, c1 = _offer(cur, "d1", expires_in="1 second")
    accept, expire = _race(
        cur,
        lambda c: _resolve(c, o1, c1, "accept", "race-acc"),
        lambda c: _resolve(c, o1, c1, "expire", f"expire:{o1}"),
    )
    assert accept["code"] == "OK"
    assert expire["code"] == "OFFER_ALREADY_RESOLVED"
    assert expire["miss_counted"] is False
    assert _open_period(cur, "d1") == (2, "r1")


def test_race_expire_then_accept(offer_db):  # noqa: F811
    cur = offer_db
    o1, c1 = _offer(cur, "d1")
    _expire_now(cur, o1)
    expire, accept = _race(
        cur,
        lambda c: _resolve(c, o1, c1, "expire", f"expire:{o1}"),
        lambda c: _resolve(c, o1, c1, "accept", "race-acc"),
    )
    assert expire["outcome"] == "expired_nonresponse"
    assert accept["code"] == "OFFER_EXPIRED"
    cur.execute("SELECT count(*) FROM ride_offers WHERE status='accepted'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM driver_insurance_periods WHERE driver_id='d1' AND ended_at IS NULL")
    assert cur.fetchone()[0] == 1


def test_resolve_rpc_grants(offer_db):  # noqa: F811
    cur = offer_db
    sig = "resolve_driver_offer(uuid,uuid,bigint,text,text,text,integer)"
    cur.execute("SELECT has_function_privilege('authenticated', %s, 'EXECUTE')", (sig,))
    assert cur.fetchone()[0] is False
    cur.execute("SELECT has_function_privilege('service_role', %s, 'EXECUTE')", (sig,))
    assert cur.fetchone()[0] is True
