"""
Real-Postgres coverage for the transactional outbox (migration 399).

These tests exercise the paid-ride trigger, lease RPCs, concurrent claims,
and role denial against an actual Postgres — a mocked supabase-py client
cannot prove SKIP LOCKED or trigger atomicity. They self-skip when
TEST_DATABASE_URL / DATABASE_URL is unset (see tests/rls/conftest.py).
"""

from __future__ import annotations

import json
import uuid

import pytest

try:
    import psycopg2
    import psycopg2.errors
except ImportError:  # pragma: no cover - guarded by conftest's skipif
    psycopg2 = None

from conftest import _DSN, _dsn_with_dbname, as_role

pytestmark = [
    pytest.mark.rls,
    pytest.mark.skipif(
        psycopg2 is None or not _DSN,
        reason=(
            "RLS role-level tests require psycopg2 and a real Postgres reachable via "
            "TEST_DATABASE_URL (or DATABASE_URL) -- see backend/tests/rls/conftest.py."
        ),
    ),
]


def _uuid() -> str:
    return str(uuid.uuid4())


def _seed_user(cur, user_id: str) -> None:
    cur.execute(
        "INSERT INTO users (id, phone, role) VALUES (%s, %s, %s)",
        (user_id, f"+1306555{user_id[-4:]}", "rider"),
    )


def _seed_ride(cur, ride_id: str, rider_id: str, *, status: str = "searching", payment_status: str = "pending") -> None:
    cur.execute(
        """
        INSERT INTO rides
            (id, rider_id, driver_id, pickup_address, pickup_lat, pickup_lng,
             dropoff_address, dropoff_lat, dropoff_lng, status, payment_status)
        VALUES (%s, %s, NULL, 'A', 50.4, -104.6, 'B', 50.5, -104.7, %s, %s)
        """,
        (ride_id, rider_id, status, payment_status),
    )


def _enable_producer(cur) -> None:
    cur.execute(
        "INSERT INTO settings (id, outbox_receipts_enabled) VALUES ('app_settings', TRUE) "
        "ON CONFLICT (id) DO UPDATE SET outbox_receipts_enabled = TRUE"
    )


def _disable_producer(cur) -> None:
    cur.execute(
        "INSERT INTO settings (id, outbox_receipts_enabled) VALUES ('app_settings', FALSE) "
        "ON CONFLICT (id) DO UPDATE SET outbox_receipts_enabled = FALSE"
    )


def _outbox_rows(cur, ride_id: str):
    cur.execute(
        "SELECT topic, dedupe_key, payload, status FROM outbox_messages WHERE dedupe_key = %s",
        (f"auto:{ride_id}",),
    )
    return cur.fetchall()


def _claim(cur, worker_id: str, batch_size: int = 1, lease_seconds: int = 300):
    cur.execute(
        "SELECT id, topic, payload, status, attempt_count, lease_token, leased_by "
        "FROM public.outbox_claim_batch(%s, %s, %s)",
        (worker_id, batch_size, lease_seconds),
    )
    return cur.fetchall()


@pytest.fixture()
def extra_conn(pg_test_dbname):
    """A second connection to the same scratch database for concurrency tests."""
    conn = psycopg2.connect(_dsn_with_dbname(_DSN, pg_test_dbname))
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_producer_flag_defaults_false(pg_cur):
    pg_cur.execute("SELECT outbox_receipts_enabled FROM settings WHERE id = 'app_settings'")
    row = pg_cur.fetchone()
    assert row is not None
    assert row[0] is False


def test_paid_then_completed_does_not_enqueue_when_flag_off(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _disable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider)
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    pg_cur.execute("UPDATE rides SET status = 'completed' WHERE id = %s", (ride_id,))
    assert _outbox_rows(pg_cur, ride_id) == []


def test_completed_then_paid_enqueues_when_flag_on(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider)
    pg_cur.execute("UPDATE rides SET status = 'completed' WHERE id = %s", (ride_id,))
    assert _outbox_rows(pg_cur, ride_id) == []
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    rows = _outbox_rows(pg_cur, ride_id)
    assert len(rows) == 1
    topic, dedupe_key, payload, status = rows[0]
    assert topic == "ride_receipt.v1"
    assert dedupe_key == f"auto:{ride_id}"
    assert payload == {"ride_id": ride_id}
    assert list(payload.keys()) == ["ride_id"]
    assert status == "pending"


def test_paid_then_completed_enqueues_when_flag_on(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider)
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    assert _outbox_rows(pg_cur, ride_id) == []
    pg_cur.execute("UPDATE rides SET status = 'completed' WHERE id = %s", (ride_id,))
    rows = _outbox_rows(pg_cur, ride_id)
    assert len(rows) == 1
    assert rows[0][2] == {"ride_id": ride_id}


def test_duplicate_paid_update_does_not_duplicate_outbox_row(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider, status="completed", payment_status="pending")
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    assert len(_outbox_rows(pg_cur, ride_id)) == 1


def test_insert_of_already_paid_completed_ride_does_not_fire_trigger(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider, status="completed", payment_status="paid")
    assert _outbox_rows(pg_cur, ride_id) == []


def test_payload_contains_only_ride_id(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider, status="completed")
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    pg_cur.execute("SELECT payload FROM outbox_messages WHERE dedupe_key = %s", (f"auto:{ride_id}",))
    payload = pg_cur.fetchone()[0]
    assert payload == {"ride_id": ride_id}
    dumped = str(payload).lower()
    assert "email" not in dumped
    assert "phone" not in dumped
    assert "lat" not in dumped
    assert "lng" not in dumped


def test_trigger_insert_rolls_back_with_enclosing_transaction(pg_test_dbname):
    conn = psycopg2.connect(_dsn_with_dbname(_DSN, pg_test_dbname))
    conn.autocommit = False
    cur = conn.cursor()
    try:
        rider, ride_id = _uuid(), _uuid()
        _seed_user(cur, rider)
        _enable_producer(cur)
        _seed_ride(cur, ride_id, rider, status="completed")
        cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
        cur.execute("SELECT count(*) FROM outbox_messages WHERE dedupe_key = %s", (f"auto:{ride_id}",))
        assert cur.fetchone()[0] == 1
        conn.rollback()
        cur.execute("SELECT count(*) FROM outbox_messages WHERE dedupe_key = %s", (f"auto:{ride_id}",))
        assert cur.fetchone()[0] == 0
    finally:
        conn.close()


def test_claim_returns_pending_row_and_increments_attempts(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider, status="completed")
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    claimed = _claim(pg_cur, "worker-a")
    assert len(claimed) == 1
    _id, topic, payload, status, attempt_count, lease_token, leased_by = claimed[0]
    assert topic == "ride_receipt.v1"
    assert payload == {"ride_id": ride_id}
    assert status == "processing"
    assert attempt_count == 1
    assert lease_token
    assert leased_by == "worker-a"


def test_ack_cas_requires_matching_lease_token(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider, status="completed")
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    claimed = _claim(pg_cur, "worker-a")
    msg_id, *_, lease_token, _leased_by = claimed[0]

    pg_cur.execute("SELECT ok FROM public.outbox_ack(%s, %s)", (msg_id, "not-the-token"))
    assert pg_cur.fetchone()[0] is False
    pg_cur.execute("SELECT status FROM outbox_messages WHERE id = %s", (msg_id,))
    assert pg_cur.fetchone()[0] == "processing"

    pg_cur.execute("SELECT ok FROM public.outbox_ack(%s, %s)", (msg_id, lease_token))
    assert pg_cur.fetchone()[0] is True
    pg_cur.execute("SELECT status, lease_token FROM outbox_messages WHERE id = %s", (msg_id,))
    status, token = pg_cur.fetchone()
    assert status == "published"
    assert token is None


def test_fail_reschedules_then_dead_letters_at_max_attempts(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider, status="completed")
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))

    last_token = None
    msg_id = None
    for i in range(8):
        claimed = _claim(pg_cur, f"worker-{i}")
        assert len(claimed) == 1, f"expected a claim on attempt {i + 1}"
        msg_id, *_, last_token, _ = claimed[0]
        pg_cur.execute(
            "SELECT ok FROM public.outbox_fail(%s, %s, %s)",
            (msg_id, last_token, "provider_unavailable"),
        )
        assert pg_cur.fetchone()[0] is True
        # Fail owns the next-attempt delay in the database (15s × 2^(n-1)).
        # Tests that walk every attempt must make the row due again.
        pg_cur.execute(
            "UPDATE outbox_messages SET available_at = now() WHERE id = %s AND status = 'pending'",
            (msg_id,),
        )

    pg_cur.execute(
        "SELECT status, attempt_count, last_error_code FROM outbox_messages WHERE id = %s",
        (msg_id,),
    )
    status, attempt_count, error_code = pg_cur.fetchone()
    assert status == "dead_lettered"
    assert attempt_count == 8
    assert error_code == "provider_unavailable"


def test_expired_max_attempt_processing_row_is_dead_lettered_on_claim(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider, status="completed")
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    claimed = _claim(pg_cur, "worker-a")
    msg_id = claimed[0][0]
    pg_cur.execute(
        """
        UPDATE outbox_messages
           SET attempt_count = max_attempts,
               leased_until = now() - interval '1 second'
         WHERE id = %s
        """,
        (msg_id,),
    )
    claimed_again = _claim(pg_cur, "worker-b")
    statuses = [row[3] for row in claimed_again]
    assert statuses == ["dead_lettered"]
    assert "processing" not in statuses
    pg_cur.execute("SELECT status, last_error_code FROM outbox_messages WHERE id = %s", (msg_id,))
    status, error_code = pg_cur.fetchone()
    assert status == "dead_lettered"
    assert error_code == "max_attempts_exceeded"


def test_expired_lease_is_reclaimable_before_max_attempts(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider, status="completed")
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    first = _claim(pg_cur, "worker-a")
    msg_id, *_, old_token, _ = first[0]
    pg_cur.execute(
        "UPDATE outbox_messages SET leased_until = now() - interval '1 second' WHERE id = %s",
        (msg_id,),
    )
    second = _claim(pg_cur, "worker-b")
    assert len(second) == 1
    new_id, *_, attempt_count, new_token, leased_by = second[0]
    assert new_id == msg_id
    assert attempt_count == 2
    assert leased_by == "worker-b"
    assert new_token != old_token


def test_stale_token_fail_is_a_noop(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider, status="completed")
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    claimed = _claim(pg_cur, "worker-a")
    msg_id = claimed[0][0]
    pg_cur.execute("SELECT ok FROM public.outbox_fail(%s, %s, %s)", (msg_id, "stale", "provider_unavailable"))
    assert pg_cur.fetchone()[0] is False
    pg_cur.execute("SELECT status, attempt_count FROM outbox_messages WHERE id = %s", (msg_id,))
    status, attempt_count = pg_cur.fetchone()
    assert status == "processing"
    assert attempt_count == 1


def test_redrive_only_accepts_dead_lettered_and_writes_audit(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider, status="completed")
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))
    claimed = _claim(pg_cur, "worker-a")
    msg_id, *_, token, _ = claimed[0]
    pg_cur.execute("SELECT ok FROM public.outbox_redrive(%s, %s)", (msg_id, "ops-1"))
    assert pg_cur.fetchone()[0] is False

    pg_cur.execute(
        """
        UPDATE outbox_messages
           SET status = 'dead_lettered',
               dead_lettered_at = now(),
               lease_token = NULL,
               last_error_code = 'provider_unavailable'
         WHERE id = %s
        """,
        (msg_id,),
    )
    pg_cur.execute("SELECT ok FROM public.outbox_redrive(%s, %s)", (msg_id, "ops-1"))
    assert pg_cur.fetchone()[0] is True
    pg_cur.execute(
        "SELECT status, attempt_count, redrive_count, lease_token FROM outbox_messages WHERE id = %s",
        (msg_id,),
    )
    status, attempt_count, redrive_count, lease_token = pg_cur.fetchone()
    assert status == "pending"
    assert attempt_count == 0
    assert redrive_count == 1
    assert lease_token is None
    pg_cur.execute(
        "SELECT action, entity_type, entity_id, actor_id, details FROM audit_logs WHERE entity_id = %s",
        (msg_id,),
    )
    action, entity_type, entity_id, actor_id, details = pg_cur.fetchone()
    assert action == "outbox_redrive"
    assert entity_type == "outbox_messages"
    assert entity_id == msg_id
    assert actor_id == "ops-1"
    blob = details if isinstance(details, str) else json.dumps(details or {})
    assert "ops-1" in blob
    assert "@" not in blob


def test_concurrent_claims_are_disjoint(pg_cur, extra_conn, pg_test_dbname):
    as_role(pg_cur, None)
    rider = _uuid()
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    ride_ids = [_uuid(), _uuid()]
    for ride_id in ride_ids:
        _seed_ride(pg_cur, ride_id, rider, status="completed")
        pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))

    conn_a = extra_conn
    cur_a = conn_a.cursor()
    conn_b = psycopg2.connect(_dsn_with_dbname(_DSN, pg_test_dbname))
    conn_b.autocommit = False
    cur_b = conn_b.cursor()
    try:
        cur_a.execute(
            "SELECT id FROM public.outbox_claim_batch(%s, %s, %s)",
            ("worker-a", 1, 300),
        )
        ids_a = {row[0] for row in cur_a.fetchall()}
        cur_b.execute(
            "SELECT id FROM public.outbox_claim_batch(%s, %s, %s)",
            ("worker-b", 1, 300),
        )
        ids_b = {row[0] for row in cur_b.fetchall()}
        conn_a.commit()
        conn_b.commit()
    finally:
        conn_b.close()

    assert len(ids_a) == 1
    assert len(ids_b) == 1
    assert ids_a.isdisjoint(ids_b)


def test_anon_and_authenticated_cannot_read_or_execute_outbox(pg_cur):
    rider, ride_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _enable_producer(pg_cur)
    _seed_ride(pg_cur, ride_id, rider, status="completed")
    pg_cur.execute("UPDATE rides SET payment_status = 'paid' WHERE id = %s", (ride_id,))

    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("SELECT id FROM outbox_messages")

    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("SELECT id FROM outbox_messages")
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("SELECT * FROM public.outbox_claim_batch(%s, %s, %s)", ("attacker", 1, 300))


def test_outbox_messages_has_deny_all_rls_policy(pg_cur):
    as_role(pg_cur, None)
    pg_cur.execute("SELECT polname FROM pg_policy WHERE polrelid = 'public.outbox_messages'::regclass")
    names = {row[0] for row in pg_cur.fetchall()}
    assert "outbox_messages_service_only" in names


def test_claim_is_security_invoker_trigger_is_security_definer(pg_cur):
    as_role(pg_cur, None)
    pg_cur.execute(
        """
        SELECT p.proname, p.prosecdef
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.proname IN (
             'outbox_claim_batch', 'outbox_ack', 'outbox_fail',
             'outbox_enqueue_ride_receipt'
           )
         ORDER BY p.proname
        """
    )
    flags = {name: prosecdef for name, prosecdef in pg_cur.fetchall()}
    assert flags["outbox_claim_batch"] is False
    assert flags["outbox_ack"] is False
    assert flags["outbox_fail"] is False
    assert flags["outbox_enqueue_ride_receipt"] is True


def test_claim_rejects_out_of_range_batch_and_lease(pg_cur):
    as_role(pg_cur, None)
    with pytest.raises(psycopg2.Error):
        pg_cur.execute("SELECT * FROM public.outbox_claim_batch(%s, %s, %s)", ("w", 0, 300))
    with pytest.raises(psycopg2.Error):
        pg_cur.execute("SELECT * FROM public.outbox_claim_batch(%s, %s, %s)", ("w", 1, 10))
