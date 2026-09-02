"""C50 Phase 2 (T14) — real-Postgres tests for the ``dispatch_claim_batch``
RPC (migration 401), run against the actual throwaway ``postgres:15``
container/harness Phase 1's T11 built (``backend/tests/direct_pool/
conftest.py``).

Companion to ``backend/tests/test_dispatch_claim_parity.py`` (mocked,
Python-side parity). This file is the one that actually calls the SQL
function and inspects real rows -- it is the ground truth for:

  * the claim/skip/release logic (migration 401's translation of
    driver_repo.py's claim_driver_atomic + matching.py's revalidation),
  * the ride_offers rows it inserts,
  * the driver_insurance_periods rows record_insurance_period_transition
    writes when called from inside this RPC's transaction,
  * genuine concurrent-claim behavior (two real transactions racing the
    same driver row), which cannot be simulated with mocks.

Run with (same harness as T11/the fixture smoke test):

    export TEST_DATABASE_URL="<your connection string>"
    cd backend
    pytest tests/direct_pool -c /dev/null --confcutdir=tests/direct_pool
"""

from __future__ import annotations

import concurrent.futures
import os
from datetime import datetime, timedelta, timezone

import pytest

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover - self-skip handled by conftest
    psycopg2 = None


_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
_EXPIRES = _NOW + timedelta(seconds=15)


def _insert_user(cur, uid: str):
    cur.execute("INSERT INTO users (id, phone) VALUES (%s, %s)", (uid, f"+1306555{uid[-4:]}"))


def _insert_driver(cur, did: str, uid: str, **overrides):
    cols = {
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
    }
    cols.update(overrides)
    cur.execute(
        "INSERT INTO drivers (id, user_id, name, phone, is_online, is_available, is_verified, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            did,
            uid,
            f"Driver {did}",
            f"+1306555{did[-4:]}",
            cols["is_online"],
            cols["is_available"],
            cols["is_verified"],
            cols["status"],
        ),
    )


def _insert_ride(cur, rid: str, rider_id: str):
    cur.execute(
        """
        INSERT INTO rides (id, rider_id, pickup_address, pickup_lat, pickup_lng,
                            dropoff_address, dropoff_lat, dropoff_lng)
        VALUES (%s, %s, 'A', 52.13, -106.67, 'B', 52.15, -106.60)
        """,
        (rid, rider_id),
    )


def _call_claim_batch(cur, ride_id, driver_ids, eta_seconds, max_offers, offered_at=_NOW, expires_at=_EXPIRES):
    cur.execute(
        "SELECT driver_id, claimed, driver_row, ride_offer_id FROM dispatch_claim_batch(%s, %s, %s, %s, %s, %s)",
        (ride_id, driver_ids, eta_seconds, max_offers, offered_at, expires_at),
    )
    return cur.fetchall()


# ── Basic claim / offer / insurance semantics ──────────────────────────


def test_claims_in_order_and_stops_at_max_offers(pg_cur):
    _insert_user(pg_cur, "u1")
    _insert_user(pg_cur, "u2")
    _insert_user(pg_cur, "u3")
    _insert_driver(pg_cur, "d1", "u1")
    _insert_driver(pg_cur, "d2", "u2")
    _insert_driver(pg_cur, "d3", "u3")
    _insert_user(pg_cur, "rider1")
    _insert_ride(pg_cur, "r1", "rider1")

    rows = _call_claim_batch(pg_cur, "r1", ["d1", "d2", "d3"], [100, 200, 300], max_offers=2)

    # max_offers=2 -> only d1 and d2 attempted at all; d3 never reached
    # (mirrors matching.py's `if len(claimed_drivers) >= max_offers: break`
    # check running BEFORE the next candidate is attempted).
    assert [r[0] for r in rows] == ["d1", "d2"]
    assert all(r[1] is True for r in rows)  # claimed=True for both


def test_ride_offers_rows_match_expected_columns(pg_cur):
    _insert_user(pg_cur, "u1")
    _insert_driver(pg_cur, "d1", "u1")
    _insert_user(pg_cur, "rider1")
    _insert_ride(pg_cur, "r1", "rider1")

    rows = _call_claim_batch(pg_cur, "r1", ["d1"], [123], max_offers=1)
    assert len(rows) == 1
    ride_offer_id = rows[0][3]
    assert ride_offer_id is not None

    pg_cur.execute(
        "SELECT ride_id, driver_id, status, eta_seconds, offered_at, expires_at FROM ride_offers WHERE id = %s",
        (ride_offer_id,),
    )
    offer_row = pg_cur.fetchone()
    assert offer_row == ("r1", "d1", "pending", 123, _NOW, _EXPIRES)


def test_driver_row_reflects_post_claim_state(pg_cur):
    _insert_user(pg_cur, "u1")
    _insert_driver(pg_cur, "d1", "u1")
    _insert_user(pg_cur, "rider1")
    _insert_ride(pg_cur, "r1", "rider1")

    rows = _call_claim_batch(pg_cur, "r1", ["d1"], [123], max_offers=1)
    driver_row = rows[0][2]
    assert driver_row["is_available"] is False
    assert driver_row["availability_claimed_at"] is not None

    pg_cur.execute("SELECT is_available, availability_claimed_at FROM drivers WHERE id = 'd1'")
    is_available, claimed_at = pg_cur.fetchone()
    assert is_available is False
    assert claimed_at is not None


def test_insurance_period_2_written_in_same_call(pg_cur):
    """The RPC's own transaction must have written the Period-2 row --
    confirms record_insurance_period_transition(driver_id, 2, ride_id) was
    actually invoked from inside dispatch_claim_batch, not skipped."""
    _insert_user(pg_cur, "u1")
    _insert_driver(pg_cur, "d1", "u1")
    _insert_user(pg_cur, "rider1")
    _insert_ride(pg_cur, "r1", "rider1")

    _call_claim_batch(pg_cur, "r1", ["d1"], [123], max_offers=1)

    pg_cur.execute(
        "SELECT period, ride_id, ended_at FROM driver_insurance_periods "
        "WHERE driver_id = 'd1' ORDER BY started_at DESC LIMIT 1"
    )
    period, ride_id, ended_at = pg_cur.fetchone()
    assert period == 2
    assert ride_id == "r1"
    assert ended_at is None  # open row -- append-only, never mutated except ended_at


def test_unavailable_driver_is_skipped_not_offered(pg_cur):
    """A driver with is_available=false at claim time (already claimed by
    something else, or offline) gets a claimed=False row and no
    ride_offers/insurance write -- not silently dropped from the result."""
    _insert_user(pg_cur, "u1")
    _insert_driver(pg_cur, "d1", "u1", is_available=False)
    _insert_user(pg_cur, "rider1")
    _insert_ride(pg_cur, "r1", "rider1")

    rows = _call_claim_batch(pg_cur, "r1", ["d1"], [123], max_offers=1)
    assert len(rows) == 1
    driver_id, claimed, driver_row, ride_offer_id = rows[0]
    assert driver_id == "d1"
    assert claimed is False
    assert driver_row is None
    assert ride_offer_id is None

    pg_cur.execute("SELECT count(*) FROM ride_offers WHERE driver_id = 'd1'")
    assert pg_cur.fetchone()[0] == 0
    pg_cur.execute("SELECT count(*) FROM driver_insurance_periods WHERE driver_id = 'd1'")
    assert pg_cur.fetchone()[0] == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_online": False},
        {"is_verified": False},
        {"status": "suspended"},
    ],
    ids=["offline", "unverified", "suspended"],
)
def test_failed_revalidation_releases_driver_and_skips_offer(pg_cur, overrides):
    """Mirrors matching.py:875's revalidation: a driver whose
    is_available=true passes the claim UPDATE but fails is_online/
    is_verified/status='active' must be released (is_available reset to
    true, availability_claimed_at cleared) and NOT offered."""
    _insert_user(pg_cur, "u1")
    _insert_driver(pg_cur, "d1", "u1", **overrides)
    _insert_user(pg_cur, "rider1")
    _insert_ride(pg_cur, "r1", "rider1")

    rows = _call_claim_batch(pg_cur, "r1", ["d1"], [123], max_offers=1)
    assert len(rows) == 1
    assert rows[0][1] is False  # claimed=False

    pg_cur.execute("SELECT is_available, availability_claimed_at FROM drivers WHERE id = 'd1'")
    is_available, claimed_at = pg_cur.fetchone()
    assert is_available is True  # released back
    assert claimed_at is None  # claim stamp cleared -- orphan reaper (migration 157) won't misfire

    pg_cur.execute("SELECT count(*) FROM ride_offers WHERE driver_id = 'd1'")
    assert pg_cur.fetchone()[0] == 0
    pg_cur.execute("SELECT count(*) FROM driver_insurance_periods WHERE driver_id = 'd1'")
    assert pg_cur.fetchone()[0] == 0


def test_mismatched_array_lengths_raises(pg_cur):
    _insert_user(pg_cur, "u1")
    _insert_driver(pg_cur, "d1", "u1")
    _insert_user(pg_cur, "rider1")
    _insert_ride(pg_cur, "r1", "rider1")

    with pytest.raises(psycopg2.errors.RaiseException, match="length mismatch"):
        _call_claim_batch(pg_cur, "r1", ["d1"], [1, 2], max_offers=1)
    pg_cur.connection.rollback()


def test_empty_driver_list_returns_no_rows(pg_cur):
    _insert_user(pg_cur, "rider1")
    _insert_ride(pg_cur, "r1", "rider1")

    rows = _call_claim_batch(pg_cur, "r1", [], [], max_offers=5)
    assert rows == []


# ── Real concurrency: two transactions racing the same driver ─────────


def _dsn_for_direct_connection(pg_conn) -> str:
    """Build a fresh DSN pointed at the SAME throwaway database pg_conn
    (the session fixture) is already connected to -- needed because a real
    concurrency test requires two SEPARATE connections/transactions, not
    two cursors sharing one connection (psycopg2 serializes all statements
    on one connection; there is no way to interleave two transactions on
    it)."""
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    info = pg_conn.get_dsn_parameters()
    import urllib.parse as _u

    parts = _u.urlsplit(dsn)
    return _u.urlunsplit((parts.scheme, parts.netloc, f"/{info['dbname']}", parts.query, parts.fragment))


def test_concurrent_claim_batch_calls_same_driver_only_one_wins(pg_conn):
    """The core race test T14 requires: two concurrent dispatch_claim_batch
    calls both attempting to claim the SAME driver for DIFFERENT rides.
    Exactly one must succeed (claimed=True); the other must get
    claimed=False for that driver -- never both, never neither.

    Uses two real, separate psycopg2 connections (two real Postgres
    backends/transactions) run on two threads via a barrier so both
    UPDATE statements are issued as close to simultaneously as possible --
    a mocked test cannot exercise this; it needs Postgres's real row-lock
    serialization to prove the RPC's single-statement
    `UPDATE ... WHERE is_available = true` is actually the sole
    concurrency guard it claims to be.
    """
    dsn = _dsn_for_direct_connection(pg_conn)

    setup_cur = pg_conn.cursor()
    _insert_user(setup_cur, "race-u1")
    _insert_driver(setup_cur, "race-d1", "race-u1")
    _insert_user(setup_cur, "race-rider-a")
    _insert_user(setup_cur, "race-rider-b")
    _insert_ride(setup_cur, "race-ride-a", "race-rider-a")
    _insert_ride(setup_cur, "race-ride-b", "race-rider-b")

    import threading

    barrier = threading.Barrier(2)
    results = {}
    errors = {}

    def _attempt(label, ride_id):
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            barrier.wait(timeout=10)  # line both threads up to maximize overlap
            cur.execute(
                "SELECT driver_id, claimed FROM dispatch_claim_batch(%s, %s, %s, %s, %s, %s)",
                (ride_id, ["race-d1"], [100], 1, _NOW, _EXPIRES),
            )
            results[label] = cur.fetchall()
        except Exception as exc:  # pragma: no cover - surfaced via the assertion below
            errors[label] = exc
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_attempt, "a", "race-ride-a"),
            pool.submit(_attempt, "b", "race-ride-b"),
        ]
        for f in futures:
            f.result(timeout=15)

    assert not errors, f"unexpected errors during concurrent claim: {errors}"
    assert set(results.keys()) == {"a", "b"}

    claimed_flags = [results["a"][0]["claimed"], results["b"][0]["claimed"]]
    # Exactly one True, one False -- no double-claim, no double-miss.
    assert sorted(claimed_flags) == [False, True], f"expected exactly one winner: a={results['a']}, b={results['b']}"

    # The database's own state agrees with whichever side won: exactly one
    # ride_offers row exists for race-d1, and the driver is left
    # is_available=false (still claimed by the winner -- not double-released).
    check_cur = pg_conn.cursor()
    check_cur.execute("SELECT count(*) FROM ride_offers WHERE driver_id = 'race-d1'")
    assert check_cur.fetchone()[0] == 1

    check_cur.execute("SELECT is_available FROM drivers WHERE id = 'race-d1'")
    assert check_cur.fetchone()[0] is False

    check_cur.execute("SELECT count(*) FROM driver_insurance_periods WHERE driver_id = 'race-d1' AND ended_at IS NULL")
    assert check_cur.fetchone()[0] == 1, "exactly one open Period-2 row, from the single winning claim"


def test_driver_flipped_unavailable_between_read_and_claim_is_skipped(pg_conn):
    """Simulates the scenario T14 explicitly calls out: a driver whose
    is_available flips to false BETWEEN the (Python-side) candidate read
    and this RPC's claim attempt (e.g. a concurrent claim on a different
    ride, or an admin action) must be correctly skipped/released here, not
    offered. Driven via a second real connection that flips the row before
    the RPC call on the primary connection, proving the RPC's own read
    (inside its UPDATE) sees the CURRENT database state, not a stale one.
    """
    dsn = _dsn_for_direct_connection(pg_conn)

    setup_cur = pg_conn.cursor()
    _insert_user(setup_cur, "flip-u1")
    _insert_driver(setup_cur, "flip-d1", "flip-u1")
    _insert_user(setup_cur, "flip-rider")
    _insert_ride(setup_cur, "flip-ride", "flip-rider")

    # A second, separate connection flips is_available -> false and commits,
    # simulating "the driver got claimed by something else" happening in the
    # real gap between the Python candidate-read and this RPC call.
    other_conn = psycopg2.connect(dsn)
    other_conn.autocommit = True
    try:
        other_cur = other_conn.cursor()
        other_cur.execute("UPDATE drivers SET is_available = false WHERE id = 'flip-d1'")
    finally:
        other_conn.close()

    rows = _call_claim_batch(pg_conn.cursor(), "flip-ride", ["flip-d1"], [100], max_offers=1)
    assert len(rows) == 1
    driver_id, claimed, driver_row, ride_offer_id = rows[0]
    assert claimed is False
    assert ride_offer_id is None

    check_cur = pg_conn.cursor()
    check_cur.execute("SELECT count(*) FROM ride_offers WHERE driver_id = 'flip-d1'")
    assert check_cur.fetchone()[0] == 0
