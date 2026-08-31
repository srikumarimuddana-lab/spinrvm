"""
DB-role-level RLS coverage for `users`, `drivers`, `rides` -- the three core
consumer-facing tables, policies sourced verbatim from `backend/supabase_rls.sql`
(see backend/tests/rls/conftest.py for how these are applied).

Each test asserts what a real Postgres `anon`/`authenticated` role can and
cannot see/write -- not what our Python code happens to call.
"""

from __future__ import annotations

import uuid

import pytest

try:
    import psycopg2
except ImportError:  # pragma: no cover - guarded by conftest's skipif
    psycopg2 = None

from conftest import as_role

pytestmark = pytest.mark.rls


def _uuid() -> str:
    return str(uuid.uuid4())


def _seed_user(cur, user_id: str, role: str = "rider") -> None:
    cur.execute(
        "INSERT INTO users (id, phone, role) VALUES (%s, %s, %s)",
        (user_id, f"+1306555{user_id[-4:]}", role),
    )


def _seed_driver(cur, driver_id: str, user_id: str) -> None:
    cur.execute(
        "INSERT INTO drivers (id, user_id, name, phone) VALUES (%s, %s, %s, %s)",
        (driver_id, user_id, "Test Driver", "+13065550000"),
    )


def _seed_ride(cur, ride_id: str, rider_id: str, driver_id: str | None = None) -> None:
    cur.execute(
        """
        INSERT INTO rides
            (id, rider_id, driver_id, pickup_address, pickup_lat, pickup_lng,
             dropoff_address, dropoff_lat, dropoff_lng)
        VALUES (%s, %s, %s, 'A', 50.4, -104.6, 'B', 50.5, -104.7)
        """,
        (ride_id, rider_id, driver_id),
    )


# --------------------------------------------------------------------------
# users_select_self / users_update_self / users_delete_self
# --------------------------------------------------------------------------


def test_authenticated_can_select_own_user_row(pg_cur):
    me = _uuid()
    _seed_user(pg_cur, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM users WHERE id = %s", (me,))
    assert [r[0] for r in pg_cur.fetchall()] == [me]


def test_authenticated_cannot_select_another_users_row(pg_cur):
    me, other = _uuid(), _uuid()
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, other)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM users WHERE id = %s", (other,))
    # RLS silently filters the row out rather than erroring -- zero rows,
    # not a permission-denied exception, for SELECT.
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_any_user_row(pg_cur):
    other = _uuid()
    _seed_user(pg_cur, other)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM users WHERE id = %s", (other,))
    assert pg_cur.fetchall() == []


def test_authenticated_cannot_update_another_users_row(pg_cur):
    me, other = _uuid(), _uuid()
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, other)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("UPDATE users SET first_name = 'Hacked' WHERE id = %s", (other,))
    assert pg_cur.rowcount == 0
    as_role(pg_cur, None)
    pg_cur.execute("SELECT first_name FROM users WHERE id = %s", (other,))
    assert pg_cur.fetchone()[0] is None


def test_authenticated_can_update_own_user_row(pg_cur):
    me = _uuid()
    _seed_user(pg_cur, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("UPDATE users SET first_name = 'Me' WHERE id = %s", (me,))
    assert pg_cur.rowcount == 1


def test_service_role_bypasses_rls_on_users(pg_cur):
    other = _uuid()
    _seed_user(pg_cur, other)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT id FROM users WHERE id = %s", (other,))
    assert [r[0] for r in pg_cur.fetchall()] == [other]


# --------------------------------------------------------------------------
# drivers_select_public / drivers_update_self
# --------------------------------------------------------------------------


def test_anon_can_select_any_driver_row(pg_cur):
    """drivers_select_public is intentionally `USING (true)` -- riders need
    to see nearby drivers before they've authenticated a ride."""
    driver_user, driver_id = _uuid(), _uuid()
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM drivers WHERE id = %s", (driver_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [driver_id]


def test_authenticated_cannot_update_another_drivers_row(pg_cur):
    driver_user, driver_id = _uuid(), _uuid()
    other_user = _uuid()
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, other_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    as_role(pg_cur, "authenticated", {"sub": other_user, "role": "authenticated"})
    pg_cur.execute("UPDATE drivers SET name = 'Hacked' WHERE id = %s", (driver_id,))
    assert pg_cur.rowcount == 0


def test_authenticated_can_update_own_driver_row(pg_cur):
    driver_user, driver_id = _uuid(), _uuid()
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    pg_cur.execute("UPDATE drivers SET name = 'Updated' WHERE id = %s", (driver_id,))
    assert pg_cur.rowcount == 1


# --------------------------------------------------------------------------
# rides_select_parties / rides_insert_rider / rides_update_parties
# --------------------------------------------------------------------------


def test_rider_can_select_own_ride(pg_cur):
    rider = _uuid()
    _seed_user(pg_cur, rider)
    ride_id = _uuid()
    as_role(pg_cur, None)
    _seed_ride(pg_cur, ride_id, rider)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM rides WHERE id = %s", (ride_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [ride_id]


def test_assigned_driver_can_select_ride(pg_cur):
    rider, driver_user, driver_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    ride_id = _uuid()
    _seed_ride(pg_cur, ride_id, rider, driver_id=driver_user)
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM rides WHERE id = %s", (ride_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [ride_id]


def test_unrelated_rider_cannot_select_someone_elses_ride(pg_cur):
    """The consequential case: rider A must never be able to read rider B's
    ride via a direct PostgREST/anon-key query."""
    rider_a, rider_b = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider_a)
    _seed_user(pg_cur, rider_b)
    ride_id = _uuid()
    _seed_ride(pg_cur, ride_id, rider_b)
    as_role(pg_cur, "authenticated", {"sub": rider_a, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM rides WHERE id = %s", (ride_id,))
    assert pg_cur.fetchall() == []


def test_unassigned_driver_cannot_select_ride(pg_cur):
    """A driver who is NOT assigned to this ride must not see it -- guards
    against a driver browsing other riders' trips."""
    rider, other_driver_user = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_user(pg_cur, other_driver_user, role="driver")
    ride_id = _uuid()
    _seed_ride(pg_cur, ride_id, rider)  # no driver assigned
    as_role(pg_cur, "authenticated", {"sub": other_driver_user, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM rides WHERE id = %s", (ride_id,))
    assert pg_cur.fetchall() == []


def test_rider_can_insert_own_ride(pg_cur):
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    ride_id = _uuid()
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    _seed_ride(pg_cur, ride_id, rider)
    assert pg_cur.rowcount == 1


def test_authenticated_cannot_insert_ride_for_another_rider(pg_cur):
    """rides_insert_rider requires auth.uid() = rider_id -- an authenticated
    user must not be able to forge a ride row attributed to someone else.
    A WITH CHECK failure on INSERT raises, unlike SELECT/UPDATE denial
    (which just filters/no-ops)."""
    me, victim = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, victim)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_ride(pg_cur, _uuid(), victim)  # rider_id = victim, not me


def test_anon_cannot_insert_ride(pg_cur):
    """anon has no `sub` claim at all, so auth.uid() is NULL and can never
    equal any rider_id."""
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_ride(pg_cur, _uuid(), rider)


def test_rider_can_update_own_ride_status(pg_cur):
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    ride_id = _uuid()
    _seed_ride(pg_cur, ride_id, rider)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("UPDATE rides SET status = 'cancelled' WHERE id = %s", (ride_id,))
    assert pg_cur.rowcount == 1


def test_unrelated_rider_cannot_update_someone_elses_ride(pg_cur):
    rider_a, rider_b = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider_a)
    _seed_user(pg_cur, rider_b)
    ride_id = _uuid()
    _seed_ride(pg_cur, ride_id, rider_b)
    as_role(pg_cur, "authenticated", {"sub": rider_a, "role": "authenticated"})
    pg_cur.execute("UPDATE rides SET status = 'cancelled' WHERE id = %s", (ride_id,))
    assert pg_cur.rowcount == 0
