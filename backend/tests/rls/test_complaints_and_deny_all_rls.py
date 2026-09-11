"""
DB-role-level RLS coverage for `complaints` (migration 68) and the
migration-26 deny-all policies on `refresh_tokens`/`stripe_events`/
`schema_migrations` -- ACTION_ITEMS.md C49 remaining scope.

`complaints`: reporters see and insert their own complaints; status changes
(open -> under_review -> resolved/dismissed) are service-role-only (no
UPDATE/DELETE policy for authenticated/anon) -- same shape as
`lost_and_found`.

Note: migration 68's ride_id/reporter_id/reported_id/resolved_by columns
are declared UUID in the merged file, but the current schema's
`users.id`/`rides.id` are TEXT -- the same real drift already documented on
`lost_and_found` (migration 69). See conftest.py's `pg_conn` fixture
comment and the ACTION_ITEMS.md finding; this harness patches the type at
apply time so the policies can be exercised.

`refresh_tokens`/`stripe_events`/`schema_migrations`: all backend-only
tables (migrations 22/24/25 each enable RLS with zero policies; migration
26 adds an explicit `FOR ALL TO anon, authenticated USING (false)` deny-all
as belt-and-suspenders documentation of that intent). Only service_role
(BYPASSRLS) ever reads/writes them in production.
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


def _seed_complaint(cur, complaint_id: str, reporter_id: str) -> None:
    cur.execute(
        "INSERT INTO complaints (id, reporter_id, complaint_type) VALUES (%s, %s, 'safety')",
        (complaint_id, reporter_id),
    )


# ── complaints ──────────────────────────────────────────────────────────


def test_reporter_can_select_own_complaint(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    complaint_id = _uuid()
    _seed_complaint(pg_cur, complaint_id, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM complaints WHERE id = %s", (complaint_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [complaint_id]


def test_authenticated_cannot_select_another_reporters_complaint(pg_cur):
    me, other = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, other)
    complaint_id = _uuid()
    _seed_complaint(pg_cur, complaint_id, other)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM complaints WHERE id = %s", (complaint_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_any_complaint(pg_cur):
    other = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, other)
    complaint_id = _uuid()
    _seed_complaint(pg_cur, complaint_id, other)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM complaints WHERE id = %s", (complaint_id,))
    assert pg_cur.fetchall() == []


def test_reporter_can_insert_own_complaint(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    _seed_complaint(pg_cur, _uuid(), me)
    assert pg_cur.rowcount == 1


def test_authenticated_cannot_insert_complaint_for_another_reporter(pg_cur):
    """complaints_insert's WITH CHECK requires auth.uid()::text =
    reporter_id -- an authenticated user must not be able to forge a
    complaint attributed to someone else."""
    me, victim = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, victim)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_complaint(pg_cur, _uuid(), victim)


def test_anon_cannot_insert_complaint(pg_cur):
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_complaint(pg_cur, _uuid(), rider)


def test_authenticated_cannot_update_own_complaint_status(pg_cur):
    """No UPDATE policy exists -- status transitions
    (open -> under_review -> resolved/dismissed) are service-role-only."""
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    complaint_id = _uuid()
    _seed_complaint(pg_cur, complaint_id, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("UPDATE complaints SET status = 'resolved' WHERE id = %s", (complaint_id,))
    assert pg_cur.rowcount == 0


def test_authenticated_cannot_delete_own_complaint(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    complaint_id = _uuid()
    _seed_complaint(pg_cur, complaint_id, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("DELETE FROM complaints WHERE id = %s", (complaint_id,))
    assert pg_cur.rowcount == 0


def test_service_role_bypasses_rls_on_complaints(pg_cur):
    other = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, other)
    complaint_id = _uuid()
    _seed_complaint(pg_cur, complaint_id, other)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT id FROM complaints WHERE id = %s", (complaint_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [complaint_id]


# ── refresh_tokens / stripe_events / schema_migrations deny-all ─────────


def _seed_refresh_token(cur, token_id: str, user_id: str) -> None:
    cur.execute(
        """
        INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at)
        VALUES (%s, %s, %s, now() + interval '30 days')
        """,
        (token_id, user_id, f"hash-{token_id}"),
    )


def _seed_stripe_event(cur, event_id: str) -> None:
    cur.execute(
        "INSERT INTO stripe_events (event_id, event_type, payload) VALUES (%s, 'payment_intent.succeeded', '{}')",
        (event_id,),
    )


def _seed_schema_migration(cur, filename: str) -> None:
    cur.execute(
        "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
        (filename, "deadbeef"),
    )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_select_refresh_tokens(pg_cur, role):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    token_id = _uuid()
    _seed_refresh_token(pg_cur, token_id, me)
    as_role(pg_cur, role, {"sub": me, "role": role} if role == "authenticated" else None)
    pg_cur.execute("SELECT id FROM refresh_tokens WHERE id = %s", (token_id,))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_select_stripe_events(pg_cur, role):
    as_role(pg_cur, None)
    event_id = _uuid()
    _seed_stripe_event(pg_cur, event_id)
    as_role(pg_cur, role, {"sub": _uuid(), "role": role} if role == "authenticated" else None)
    pg_cur.execute("SELECT event_id FROM stripe_events WHERE event_id = %s", (event_id,))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_select_schema_migrations(pg_cur, role):
    as_role(pg_cur, None)
    filename = f"{_uuid()}.sql"
    _seed_schema_migration(pg_cur, filename)
    as_role(pg_cur, role, {"sub": _uuid(), "role": role} if role == "authenticated" else None)
    pg_cur.execute("SELECT filename FROM schema_migrations WHERE filename = %s", (filename,))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_insert_refresh_tokens(pg_cur, role):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    as_role(pg_cur, role, {"sub": me, "role": role} if role == "authenticated" else None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_refresh_token(pg_cur, _uuid(), me)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_insert_stripe_events(pg_cur, role):
    as_role(pg_cur, role, {"sub": _uuid(), "role": role} if role == "authenticated" else None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_stripe_event(pg_cur, _uuid())


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_insert_schema_migrations(pg_cur, role):
    as_role(pg_cur, role, {"sub": _uuid(), "role": role} if role == "authenticated" else None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_schema_migration(pg_cur, f"{_uuid()}.sql")


def test_service_role_bypasses_rls_on_all_three_deny_all_tables(pg_cur):
    """Confirms the backend's actual production access path (service-role
    key) is unaffected by the deny-all policies on any of the three."""
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    as_role(pg_cur, "service_role", None)

    token_id = _uuid()
    _seed_refresh_token(pg_cur, token_id, me)
    pg_cur.execute("SELECT id FROM refresh_tokens WHERE id = %s", (token_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [token_id]

    event_id = _uuid()
    _seed_stripe_event(pg_cur, event_id)
    pg_cur.execute("SELECT event_id FROM stripe_events WHERE event_id = %s", (event_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [event_id]

    filename = f"{_uuid()}.sql"
    _seed_schema_migration(pg_cur, filename)
    pg_cur.execute("SELECT filename FROM schema_migrations WHERE filename = %s", (filename,))
    assert [r[0] for r in pg_cur.fetchall()] == [filename]
