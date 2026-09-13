"""
DB-role-level RLS regression pin for migration 416 (corporate_accounts admin
policy fix). See backend/tests/rls/conftest.py for the harness this depends
on -- these tests need a real Postgres reachable via TEST_DATABASE_URL (or
DATABASE_URL) and self-skip otherwise (see conftest.py's `pytestmark`).

Kept in its own module rather than added to a shared corporate-tables test
file so it does not collide with PR #5305's in-flight `test_corporate_billing_
rls.py` (different branch, same class of bug on the 9 sibling tables).

corporate_accounts is not part of conftest.py's base schema (only users/
drivers/rides/financial_events/driver_insurance_periods/saved_addresses are
built there), so this file builds it itself: migration 05 (create table),
migration 17 (the original -- buggy -- admin policy), then migration 416
(the fix under test), applied verbatim and in order, same "apply the
schema's evolution in migration order" approach conftest.py already uses for
financial_events and lost_and_found. The blanket
`GRANT ... TO anon, authenticated, service_role` between 17 and 416 mirrors
Supabase's own default table-level grants (conftest.py's own convention for
every table it creates outside the initial schema extraction) -- without it,
this harness would under-state what a real Supabase project's anon/
authenticated roles could attempt against this table before migration 416
narrows it.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

try:
    import psycopg2
except ImportError:  # pragma: no cover - guarded by conftest's skipif
    psycopg2 = None

from conftest import as_role

pytestmark = pytest.mark.rls

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _uuid() -> str:
    return str(uuid.uuid4())


def _seed_user(cur, user_id: str, role: str = "rider") -> None:
    cur.execute(
        "INSERT INTO users (id, phone, role) VALUES (%s, %s, %s)",
        (user_id, f"+1306555{user_id[-4:]}", role),
    )


def _seed_account(cur, account_id: str, name: str = "Test Co") -> None:
    cur.execute("INSERT INTO corporate_accounts (id, name) VALUES (%s, %s)", (account_id, name))


@pytest.fixture(scope="module", autouse=True)
def _corporate_accounts_schema(pg_conn):
    """One-time (per test session) schema build for this module: create
    corporate_accounts and apply its RLS policy history verbatim, in order."""
    cur = pg_conn.cursor()
    cur.execute("RESET ROLE")
    cur.execute((_MIGRATIONS_DIR / "05_corporate_accounts.sql").read_text())
    cur.execute((_MIGRATIONS_DIR / "17_corporate_accounts_fk.sql").read_text())
    cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON corporate_accounts TO anon, authenticated, service_role")
    cur.execute((_MIGRATIONS_DIR / "416_corporate_accounts_rls_super_admin_fix.sql").read_text())
    cur.close()
    yield


def test_super_admin_can_select_any_corporate_account(pg_cur):
    """Migration 416 regression pin: this is the bug. Before 416,
    corporate_accounts' policy (migration 17) checked `role = 'admin'` only
    -- a super_admin-role authenticated JWT was denied entirely, unlike the
    9 sibling corporate tables migration 142 already fixed to check
    `role IN ('admin', 'super_admin')`."""
    account_id = _uuid()
    super_admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, super_admin, role="super_admin")
    _seed_account(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": super_admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [account_id]


def test_admin_can_still_select_any_corporate_account(pg_cur):
    """No regression on the pre-existing, already-working admin path."""
    account_id = _uuid()
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    _seed_account(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [account_id]


def test_rider_cannot_select_corporate_accounts(pg_cur):
    account_id = _uuid()
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    _seed_account(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_corporate_accounts(pg_cur):
    """migration 416's table-level REVOKE ALL FROM anon blocks this at the
    grant layer, before RLS is even consulted -- same layered pattern
    migration 142 established for the 9 siblings."""
    account_id = _uuid()
    as_role(pg_cur, None)
    _seed_account(pg_cur, account_id)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))


def test_super_admin_cannot_insert_corporate_account(pg_cur):
    """migration 416's other half of the fix: the original migration 17
    policy was FOR ALL with no WITH CHECK, letting any admin-role
    authenticated JWT write via PostgREST. 416 replaces it with a SELECT-only
    policy and revokes the write grant, matching the 9 siblings' now-correct
    shape -- a super_admin (or admin) authenticated JWT can no longer insert
    at all, regardless of role."""
    super_admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, super_admin, role="super_admin")
    as_role(pg_cur, "authenticated", {"sub": super_admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("INSERT INTO corporate_accounts (id, name) VALUES (%s, 'Forged Co')", (_uuid(),))


def test_service_role_can_insert_corporate_account(pg_cur):
    """The backend's real write path (backend/supabase_client.py, always
    service_role) was never revoked and still bypasses RLS entirely."""
    account_id = _uuid()
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("INSERT INTO corporate_accounts (id, name) VALUES (%s, 'Service Co')", (account_id,))
    assert pg_cur.rowcount == 1
