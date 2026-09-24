"""Driver readiness policy (migration 462 part B): list, reconcile, prompt claim."""

import psycopg2
from test_driver_availability_readiness import _offer_db, readiness_db  # noqa: F401 - pytest fixtures
from test_offer_decision_atomicity_resolve import _dsn, _offer, _race


def _enforce(cur):
    cur.execute("UPDATE settings SET driver_readiness_policy_enabled=true WHERE id='app_settings'")


def _list(cur, limit=50):
    cur.execute("SELECT public.list_driver_availability_reconcile_candidates(%s)", (limit,))
    return cur.fetchone()[0]


def _reconcile(cur, kind, epoch=5, driver="d1", request_id=None):
    cur.execute(
        "SELECT public.reconcile_driver_readiness(%s,%s,%s,%s)",
        (driver, epoch, kind, request_id or f"{kind}:{driver}:{epoch}"),
    )
    return cur.fetchone()[0]


def _ids(rows):
    return sorted(r["driver_id"] for r in rows)


def test_list_candidates_by_kind(readiness_db):  # noqa: F811
    cur = readiness_db
    cur.execute("UPDATE drivers SET last_contact_at=clock_timestamp() - interval '6 minutes' WHERE id='d2'")
    cur.execute("UPDATE drivers SET ready_until=clock_timestamp() - interval '1 second' WHERE id='d1'")
    cur.execute("UPDATE drivers SET ready_until=clock_timestamp() + interval '1 minute' WHERE id='d3'")
    listed = _list(cur)
    assert _ids(listed["contact_gap"]) == ["d2"]
    assert listed["readiness_due"] == [] and listed["prompt_due"] == []
    _enforce(cur)
    listed = _list(cur)
    assert _ids(listed["readiness_due"]) == ["d1"]
    assert _ids(listed["prompt_due"]) == ["d3"]
    row = listed["readiness_due"][0]
    assert set(row) == {"driver_id", "user_id", "online_epoch", "ready_until"}
    assert row["user_id"] == "u1" and row["online_epoch"] == "5"
    assert len(_list(cur, 1)["readiness_due"]) == 1


def test_reconcile_readiness_pauses_once(readiness_db):  # noqa: F811
    cur = readiness_db
    _enforce(cur)
    assert _reconcile(cur, "readiness")["code"] == "NOT_DUE"
    cur.execute("UPDATE drivers SET ready_until=clock_timestamp() - interval '1 second' WHERE id='d1'")
    first = _reconcile(cur, "readiness")
    assert first["code"] == "OK"
    assert first["availability_reason"] == "pause_idle"
    assert first["user_id"] == "u1"
    replay = _reconcile(cur, "readiness")
    assert replay["code"] == "ONLINE_EPOCH_STALE"


def test_stale_worker_after_newer_go_gets_epoch_stale(readiness_db):  # noqa: F811
    cur = readiness_db
    cur.execute("UPDATE drivers SET last_contact_at=clock_timestamp() - interval '6 minutes' WHERE id='d1'")
    cur.execute("UPDATE drivers SET online_epoch=6 WHERE id='d1'")
    assert _reconcile(cur, "contact_gap", epoch=5)["code"] == "ONLINE_EPOCH_STALE"
    result = _reconcile(cur, "contact_gap", epoch=6)
    assert result["code"] == "OK"
    assert result["availability_reason"] == "pause_unreachable"


def test_active_trip_across_deadline_is_not_paused(readiness_db):  # noqa: F811
    cur = readiness_db
    _enforce(cur)
    o1, c1 = _offer(cur, "d1")
    cur.execute("UPDATE drivers SET ready_until=clock_timestamp() - interval '1 second' WHERE id='d1'")
    # Claimed for an offer: is_available is false, so the idle predicate fails.
    assert _ids(_list(cur)["readiness_due"]) == []
    assert _reconcile(cur, "readiness")["code"] == "NOT_DUE"
    cur.execute("SELECT is_online, accepting_requests FROM drivers WHERE id='d1'")
    assert cur.fetchone() == (True, True)


def test_contact_gap_during_trip_keeps_trip_online(readiness_db):  # noqa: F811
    cur = readiness_db
    cur.execute(
        "INSERT INTO rides (id,rider_id,driver_id,pickup_address,pickup_lat,pickup_lng,dropoff_address,"
        "dropoff_lat,dropoff_lng,status) VALUES ('trip','rider','d1','a',0,0,'b',0,0,'in_progress')"
    )
    cur.execute(
        "UPDATE drivers SET is_available=false, last_contact_at=clock_timestamp() - interval '6 minutes' WHERE id='d1'"
    )
    result = _reconcile(cur, "contact_gap")
    assert result["code"] == "OK"
    assert result["is_online"] is True
    assert result["accepting_requests"] is False


def test_prompt_claimed_once(readiness_db):  # noqa: F811
    cur = readiness_db
    cur.execute("SELECT ready_until FROM drivers WHERE id='d1'")
    ready_until = cur.fetchone()[0]
    cur.execute("SELECT public.claim_readiness_prompt('d1',5,%s)", (ready_until,))
    assert cur.fetchone()[0] == "u1"
    cur.execute("SELECT public.claim_readiness_prompt('d1',5,%s)", (ready_until,))
    assert cur.fetchone()[0] is None
    cur.execute("SELECT public.claim_readiness_prompt('d1',4,%s)", (ready_until,))
    assert cur.fetchone()[0] is None


def test_duplicate_workers_skip_locked(readiness_db):  # noqa: F811
    cur = readiness_db
    _enforce(cur)
    cur.execute("UPDATE drivers SET ready_until=clock_timestamp() - interval '1 second' WHERE id='d1'")
    first, second = _race(
        cur,
        lambda c: _reconcile(c, "readiness", request_id="w1"),
        lambda c: _reconcile(c, "readiness", request_id="w2"),
    )
    assert first["code"] == "OK"
    assert second["code"] == "BUSY"
    conn = psycopg2.connect(_dsn(cur))
    try:
        c = conn.cursor()
        c.execute("SELECT count(*) FROM driver_availability_requests WHERE driver_id='d1' AND action='pause_idle'")
        assert c.fetchone()[0] == 1
    finally:
        conn.close()


def test_partial_indexes_exist_and_are_used(readiness_db):  # noqa: F811
    cur = readiness_db
    cur.execute(
        "SELECT indexname FROM pg_indexes WHERE tablename='drivers' AND indexname IN "
        "('drivers_availability_contact_due_idx','drivers_availability_ready_due_idx') ORDER BY 1"
    )
    assert [r[0] for r in cur.fetchall()] == [
        "drivers_availability_contact_due_idx",
        "drivers_availability_ready_due_idx",
    ]
    cur.execute("SET enable_seqscan = off")
    try:
        cur.execute(
            "EXPLAIN SELECT id FROM drivers WHERE is_online AND accepting_requests AND is_available "
            "AND ready_until <= now() ORDER BY ready_until LIMIT 10"
        )
        plan = "\n".join(r[0] for r in cur.fetchall())
        assert "drivers_availability_ready_due_idx" in plan
        cur.execute(
            "EXPLAIN SELECT id FROM drivers WHERE is_online AND controller_session_id IS NOT NULL "
            "AND last_contact_at < now() - interval '5 minutes' ORDER BY last_contact_at LIMIT 10"
        )
        plan = "\n".join(r[0] for r in cur.fetchall())
        assert "drivers_availability_contact_due_idx" in plan
    finally:
        cur.execute("RESET enable_seqscan")
