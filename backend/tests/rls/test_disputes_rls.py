"""
DB-role-level RLS coverage for `disputes` -- ACTION_ITEMS.md C107 / migration
430. `disputes` (migration 10 create, migration 142 admin-policy lockdown)
shares the exact same broken admin-RLS pattern migration 142 fixed on its 9
corporate-table siblings (`EXISTS (... users.role IN ('admin', 'super_admin')
...)`), in the same migration file, just never counted in C107's own "10
tables" tally. migration 430 fixes it identically: the unreachable
"Admin read disputes" policy is replaced with an explicit USING (false)
policy. This is this table's first RLS test coverage of any kind -- the
harness never built `disputes` at all before this change (see conftest.py's
own note on why).
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


def _seed_dispute(cur, dispute_id: str, user_id: str | None = None) -> None:
    cur.execute(
        "INSERT INTO disputes (id, user_id) VALUES (%s, %s)",
        (dispute_id, user_id),
    )


def test_rider_can_select_own_dispute(pg_cur):
    rider = _uuid()
    dispute_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    _seed_dispute(pg_cur, dispute_id, user_id=rider)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM disputes WHERE id = %s", (dispute_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [dispute_id]


def test_stranger_cannot_select_others_dispute(pg_cur):
    owner, stranger = _uuid(), _uuid()
    dispute_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, owner, role="rider")
    _seed_user(pg_cur, stranger, role="rider")
    _seed_dispute(pg_cur, dispute_id, user_id=owner)
    as_role(pg_cur, "authenticated", {"sub": stranger, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM disputes WHERE id = %s", (dispute_id,))
    assert pg_cur.fetchall() == []


def test_admin_cannot_select_dispute(pg_cur):
    """migration 430: the old "Admin read disputes" policy is replaced with
    an explicit USING (false) -- an admin JWT is denied exactly like any
    other non-owning authenticated user now."""
    owner, admin = _uuid(), _uuid()
    dispute_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, owner, role="rider")
    _seed_user(pg_cur, admin, role="admin")
    _seed_dispute(pg_cur, dispute_id, user_id=owner)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM disputes WHERE id = %s", (dispute_id,))
    assert pg_cur.fetchall() == []


def test_super_admin_cannot_select_dispute(pg_cur):
    owner, super_admin = _uuid(), _uuid()
    dispute_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, owner, role="rider")
    _seed_user(pg_cur, super_admin, role="super_admin")
    _seed_dispute(pg_cur, dispute_id, user_id=owner)
    as_role(pg_cur, "authenticated", {"sub": super_admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM disputes WHERE id = %s", (dispute_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_dispute(pg_cur):
    """Migration 142's REVOKE ALL FROM anon means this raises -- a
    grant-level privilege error, not a silently-filtered RLS denial."""
    dispute_id = _uuid()
    as_role(pg_cur, None)
    _seed_dispute(pg_cur, dispute_id)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("SELECT id FROM disputes WHERE id = %s", (dispute_id,))


def test_authenticated_cannot_insert_dispute(pg_cur):
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_dispute(pg_cur, _uuid(), user_id=rider)


def test_service_role_bypasses_dispute(pg_cur):
    dispute_id = _uuid()
    as_role(pg_cur, None)
    _seed_dispute(pg_cur, dispute_id)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT id FROM disputes WHERE id = %s", (dispute_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [dispute_id]
