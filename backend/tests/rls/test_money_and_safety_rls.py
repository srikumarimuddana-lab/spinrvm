"""
DB-role-level RLS coverage for `financial_events` (7-year CRA money ledger,
migrations 58/70/290) and `driver_insurance_periods` (SGI-regulated safety
audit trail, migration 64). See backend/tests/rls/conftest.py.

`financial_events` is the more interesting case: RLS alone was not the fix
for migration 290's hole -- the INSERT policy from migration 58 is
`WITH CHECK (true)`, permissive by design so the backend's service role can
freely write. What actually blocks anon/authenticated from forging ledger
rows is the table-level GRANT REVOKE migration 290 added. These tests
exercise both layers together, the way a real PostgREST request would hit
them.
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


# --------------------------------------------------------------------------
# financial_events
# --------------------------------------------------------------------------


def test_owner_can_select_own_financial_event(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    event_id = _uuid()
    pg_cur.execute(
        "INSERT INTO financial_events (id, event_type, user_id, delta_cents) VALUES (%s, 'wallet_topup', %s, 1000)",
        (event_id, me),
    )
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM financial_events WHERE id = %s", (event_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [event_id]


def test_other_rider_cannot_select_someone_elses_financial_event(pg_cur):
    victim, attacker = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, victim)
    _seed_user(pg_cur, attacker)
    event_id = _uuid()
    pg_cur.execute(
        "INSERT INTO financial_events (id, event_type, user_id, delta_cents) VALUES (%s, 'stripe_charge', %s, 5000)",
        (event_id, victim),
    )
    as_role(pg_cur, "authenticated", {"sub": attacker, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM financial_events WHERE id = %s", (event_id,))
    assert pg_cur.fetchall() == []


def test_admin_jwt_role_claim_can_select_any_financial_event(pg_cur):
    """migration 70's fix: an admin JWT (role claim, not a subquery) can
    read any row -- this is the O(1) replacement for the migration 58
    correlated-subquery version."""
    someone = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, someone)
    event_id = _uuid()
    pg_cur.execute(
        "INSERT INTO financial_events (id, event_type, user_id, delta_cents) VALUES (%s, 'driver_payout', %s, 2500)",
        (event_id, someone),
    )
    admin = _uuid()
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated", "user_metadata": {"role": "admin"}})
    pg_cur.execute("SELECT id FROM financial_events WHERE id = %s", (event_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [event_id]


def test_anon_cannot_insert_financial_event(pg_cur):
    """migration 290: table-level REVOKE ALL from anon blocks this at the
    grant layer, before RLS is even consulted."""
    victim = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, victim)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute(
            "INSERT INTO financial_events (id, event_type, user_id, delta_cents) "
            "VALUES (%s, 'wallet_topup', %s, 999999)",
            (_uuid(), victim),
        )


def test_authenticated_cannot_insert_financial_event(pg_cur):
    """migration 290's core fix: even though migration 58's RLS INSERT
    policy is `WITH CHECK (true)` (permissive), the table-level GRANT was
    revoked from authenticated too -- an attacker with a valid rider JWT
    still cannot forge a ledger row for themselves or anyone else."""
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute(
            "INSERT INTO financial_events (id, event_type, user_id, delta_cents) "
            "VALUES (%s, 'wallet_topup', %s, 999999)",
            (_uuid(), me),
        )


def test_service_role_can_insert_financial_event(pg_cur):
    """The backend's real write path: service_role bypasses RLS and was
    never revoked at the grant layer."""
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute(
        "INSERT INTO financial_events (id, event_type, user_id, delta_cents) VALUES (%s, 'fare_settle', %s, 4200)",
        (_uuid(), me),
    )
    assert pg_cur.rowcount == 1


def test_no_role_can_update_financial_event(pg_cur):
    """Append-only ledger: the immutability trigger blocks UPDATE for every
    role, including service_role (RLS bypass does not bypass triggers)."""
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    event_id = _uuid()
    pg_cur.execute(
        "INSERT INTO financial_events (id, event_type, user_id, delta_cents) VALUES (%s, 'wallet_topup', %s, 1000)",
        (event_id, me),
    )
    as_role(pg_cur, "service_role", None)
    with pytest.raises(psycopg2.errors.RaiseException):
        pg_cur.execute("UPDATE financial_events SET delta_cents = 999999 WHERE id = %s", (event_id,))


# --------------------------------------------------------------------------
# driver_insurance_periods
# --------------------------------------------------------------------------


def test_driver_can_select_own_insurance_periods(pg_cur):
    driver_user, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (id, driver_id, period) VALUES (%s, %s, 1)",
        (period_id, driver_id),
    )
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM driver_insurance_periods WHERE id = %s", (period_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [period_id]


def test_rider_cannot_select_any_insurance_periods(pg_cur):
    """CLAUDE.md: 'riders never see this table' -- confirm at the DB level,
    not just by omission in the API surface."""
    driver_user, driver_id = _uuid(), _uuid()
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, rider)
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (id, driver_id, period) VALUES (%s, %s, 2)",
        (period_id, driver_id),
    )
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM driver_insurance_periods WHERE id = %s", (period_id,))
    assert pg_cur.fetchall() == []


def test_another_driver_cannot_select_insurance_periods(pg_cur):
    driver_user, driver_id = _uuid(), _uuid()
    other_driver_user = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, other_driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (id, driver_id, period) VALUES (%s, %s, 0)",
        (period_id, driver_id),
    )
    as_role(pg_cur, "authenticated", {"sub": other_driver_user, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM driver_insurance_periods WHERE id = %s", (period_id,))
    assert pg_cur.fetchall() == []


def test_admin_can_select_any_insurance_period(pg_cur):
    driver_user, driver_id = _uuid(), _uuid()
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, admin, role="admin")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (id, driver_id, period) VALUES (%s, %s, 2)",
        (period_id, driver_id),
    )
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM driver_insurance_periods WHERE id = %s", (period_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [period_id]


def test_authenticated_cannot_insert_insurance_period(pg_cur):
    """migration 64 deliberately ships no INSERT policy for
    anon/authenticated -- Postgres RLS default-denies any command with no
    applicable policy, even though the baseline table-level GRANT (mirroring
    Supabase's default) would otherwise permit the INSERT. This is the
    'defence-in-depth without an explicit REVOKE' pattern the migration's
    own comment describes -- worth asserting directly since it's easy to
    get backwards."""
    driver_user, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute(
            "INSERT INTO driver_insurance_periods (id, driver_id, period) VALUES (%s, %s, 1)",
            (_uuid(), driver_id),
        )


def test_service_role_can_insert_insurance_period(pg_cur):
    driver_user, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (id, driver_id, period) VALUES (%s, %s, 1)",
        (_uuid(), driver_id),
    )
    assert pg_cur.rowcount == 1


def test_no_role_can_delete_insurance_period(pg_cur):
    """Append-only regulatory audit trail: the immutability trigger blocks
    DELETE for every role, including service_role."""
    driver_user, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (id, driver_id, period) VALUES (%s, %s, 1)",
        (period_id, driver_id),
    )
    as_role(pg_cur, "service_role", None)
    with pytest.raises(psycopg2.errors.RaiseException):
        pg_cur.execute("DELETE FROM driver_insurance_periods WHERE id = %s", (period_id,))
