"""
DB-role-level RLS regression pin for migration 416 (corporate_accounts admin
policy fix). See backend/tests/rls/conftest.py for the harness this depends
on -- these tests need a real Postgres reachable via TEST_DATABASE_URL (or
DATABASE_URL) and self-skip otherwise (see conftest.py's `pytestmark`).

Kept in its own module rather than added to `test_corporate_billing_rls.py`
(PR #5305) to avoid a merge conflict between the two independent, parallel
PRs -- both landed the same day. **Correction (2026-09-13, at merge time):**
this file originally built its own copy of the corporate_accounts schema
(migrations 05/17/416) via a module-scoped fixture, written against the
assumption -- true when this file was first written, before #5305 merged --
that "corporate_accounts is not part of conftest.py's base schema." By merge
time #5305's own version of conftest.py had already extended the shared
fixture to build corporate_accounts (migrations 05/17/27) plus the 9 sibling
corporate tables; migration 416 has now been added to that same shared
fixture (right after its 142 extraction, for the same grant-then-narrow
sequencing reason) so `test_corporate_billing_rls.py` and this file see the
same, single, correct schema. This file's own duplicate fixture was removed
rather than left to double-apply migration 416 in the same session.

ACTION_ITEMS.md C107 / migration 430: a 2026-09-13 production data cleanup
plus migration 256's `chk_users_role_not_admin` CHECK constraint already
closed the original C107 finding (no `users` row can hold 'admin'/
'super_admin' anymore) without touching any policy. migration 430 layers
additional hardening on top: it replaces the "Admin read <table>" policy this
file's `test_super_admin_can_select_any_corporate_account` /
`test_admin_can_still_select_any_corporate_account` originally pinned with an
explicit `USING (false)`, so both are rewritten below to assert denial
instead of removed -- migration 416's admin/super_admin parity fix is still
real (both role values reach the same, now-`false`, policy; neither is
special-cased over the other), it's just that the policy denies everyone now.
`test_super_admin_cannot_insert_corporate_account` is untouched by 430 (write
access was already revoked at the grant layer by 416) and is unaffected by
any of this.

Migration 431 (found 2026-09-20 while rolling out 430 to production): a
second, out-of-band "Admin full access for corporate accounts" policy that
no migration file in this repo's history ever created -- see
`test_stray_admin_policy_removed_by_431` below for the regression test and
`backend/migrations/431_drop_stray_corporate_accounts_admin_policy.sql` for
the full root-cause writeup.
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


def _seed_account(cur, account_id: str, name: str = "Test Co") -> None:
    cur.execute("INSERT INTO corporate_accounts (id, name) VALUES (%s, %s)", (account_id, name))


def test_super_admin_cannot_select_any_corporate_account(pg_cur):
    """migration 430 (ACTION_ITEMS.md C107): the admin-read policy migration
    416 fixed for super_admin parity is now USING (false) -- super_admin is
    denied exactly like admin, not specially permitted."""
    account_id = _uuid()
    super_admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, super_admin, role="super_admin")
    _seed_account(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": super_admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert pg_cur.fetchall() == []


def test_admin_cannot_select_any_corporate_account(pg_cur):
    """Same denial applies to the plain admin role value -- migration 430
    makes no distinction between the two."""
    account_id = _uuid()
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    _seed_account(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert pg_cur.fetchall() == []


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


# ── migration 431: out-of-band "Admin full access for corporate accounts" ──


def test_stray_admin_policy_removed_by_431(pg_cur):
    """migration 431 (found 2026-09-20 while rolling out 430): production
    carried a policy named "Admin full access for corporate accounts" that no
    migration file in this repo's history ever created (confirmed via `git
    log --all -S` across every branch) -- pure out-of-band drift, invisible
    to this harness since it only ever builds schema by replaying migration
    files. conftest.py's global setup already applies 431, so by the time any
    test runs the drifted policy is long gone -- there is nothing left here
    to demonstrate a fix against. This test manufactures the exact drifted
    state directly (recreating the stray policy verbatim), proves it really
    was a live access hole (an admin-role JWT gains SELECT through it despite
    migration 430's own USING (false) policy on this table -- RLS ORs
    permissive SELECT policies together), then re-applies 431's DROP and
    proves the hole closes. Ends by restoring the post-431 state the rest of
    the suite expects, so this test doesn't leak side effects to others."""
    as_role(pg_cur, None)
    pg_cur.execute(
        """
        CREATE POLICY "Admin full access for corporate accounts"
            ON corporate_accounts FOR ALL TO authenticated
            USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text
                             AND users.role = 'admin'))
        """
    )
    try:
        account_id = _uuid()
        admin = _uuid()
        _seed_user(pg_cur, admin, role="admin")
        _seed_account(pg_cur, account_id)

        as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
        pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
        assert [r[0] for r in pg_cur.fetchall()] == [account_id], (
            "expected the drifted stray policy to actually grant access here -- "
            "if this fails, the stray-policy scenario isn't reproduced correctly"
        )

        as_role(pg_cur, None)
        pg_cur.execute('DROP POLICY IF EXISTS "Admin full access for corporate accounts" ON corporate_accounts')

        as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
        pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
        assert pg_cur.fetchall() == []
    finally:
        as_role(pg_cur, None)
        pg_cur.execute('DROP POLICY IF EXISTS "Admin full access for corporate accounts" ON corporate_accounts')
