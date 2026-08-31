"""
DB-role-level RLS coverage for `saved_addresses` (ACTION_ITEMS.md B40).

Migration 378 added SELECT/INSERT/DELETE policies scoped to
`auth.uid()::text = user_id` -- previously RLS was enabled with zero
policies (fail-closed: anon/authenticated denied everything). These tests
assert the new owner-only policies actually behave as intended from a real
Postgres `anon`/`authenticated` role, and that `service_role` (what
`routes/addresses.py` uses in production) still bypasses RLS entirely.
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


def _seed_user(cur, user_id: str) -> None:
    cur.execute(
        "INSERT INTO users (id, phone, role) VALUES (%s, %s, 'rider')",
        (user_id, f"+1306555{user_id[-4:]}"),
    )


def _seed_address(cur, address_id: str, user_id: str) -> None:
    cur.execute(
        """
        INSERT INTO saved_addresses (id, user_id, name, address, lat, lng)
        VALUES (%s, %s, 'Home', '1 A St', 50.4, -104.6)
        """,
        (address_id, user_id),
    )


def test_owner_can_select_own_address(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    addr_id = _uuid()
    _seed_address(pg_cur, addr_id, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM saved_addresses WHERE id = %s", (addr_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [addr_id]


def test_authenticated_cannot_select_another_users_address(pg_cur):
    """The consequential case: rider A must never read rider B's home/work
    address via a direct PostgREST/anon-key query."""
    me, other = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, other)
    addr_id = _uuid()
    _seed_address(pg_cur, addr_id, other)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM saved_addresses WHERE id = %s", (addr_id,))
    # RLS silently filters the row out rather than erroring -- zero rows.
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_any_address(pg_cur):
    other = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, other)
    addr_id = _uuid()
    _seed_address(pg_cur, addr_id, other)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM saved_addresses WHERE id = %s", (addr_id,))
    assert pg_cur.fetchall() == []


def test_owner_can_insert_own_address(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    _seed_address(pg_cur, _uuid(), me)
    assert pg_cur.rowcount == 1


def test_authenticated_cannot_insert_address_for_another_user(pg_cur):
    """saved_addresses_owner_insert's WITH CHECK requires auth.uid()::text =
    user_id -- an authenticated user must not be able to forge an address
    row attributed to someone else. A WITH CHECK failure on INSERT raises,
    unlike SELECT/DELETE denial (which just filters/no-ops)."""
    me, victim = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, victim)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_address(pg_cur, _uuid(), victim)  # user_id = victim, not me


def test_anon_cannot_insert_address(pg_cur):
    """anon has no `sub` claim at all, so auth.uid() is NULL and can never
    equal any user_id."""
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_address(pg_cur, _uuid(), rider)


def test_owner_can_delete_own_address(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    addr_id = _uuid()
    _seed_address(pg_cur, addr_id, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("DELETE FROM saved_addresses WHERE id = %s", (addr_id,))
    assert pg_cur.rowcount == 1


def test_authenticated_cannot_delete_another_users_address(pg_cur):
    me, other = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, other)
    addr_id = _uuid()
    _seed_address(pg_cur, addr_id, other)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("DELETE FROM saved_addresses WHERE id = %s", (addr_id,))
    # RLS hides the row rather than erroring -- 0 rows affected, not denied.
    assert pg_cur.rowcount == 0
    as_role(pg_cur, None)
    pg_cur.execute("SELECT id FROM saved_addresses WHERE id = %s", (addr_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [addr_id]


def test_service_role_bypasses_rls_on_saved_addresses(pg_cur):
    """Confirms routes/addresses.py's actual production access path
    (service-role key) is unaffected by migration 378 -- it bypasses RLS
    regardless of which/how many policies exist."""
    other = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, other)
    addr_id = _uuid()
    _seed_address(pg_cur, addr_id, other)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT id FROM saved_addresses WHERE id = %s", (addr_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [addr_id]
