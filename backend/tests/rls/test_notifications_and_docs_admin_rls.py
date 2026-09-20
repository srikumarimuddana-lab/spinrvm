"""
DB-role-level RLS coverage for `cloud_messages`, `push_tokens` (migration 06)
and `document_requirements` (migration 02) -- ACTION_ITEMS.md C123 phase 1 /
migration 432. None of these three tables had any RLS test coverage before
this change; the harness itself didn't even build them (see conftest.py's
own comment on why, including a real, separate finding: document_requirements'
original "Admin full access for requirements" policy compares `users.id`
(text) to `auth.uid()` (uuid) with no cast, which cannot be replayed against
a fresh schema -- only migration 432's fixed replacement is built here).

cloud_messages: admin-broadcast table (marketing/notification pushes), no
owner-row policy at all -- only "Admin full access" (FOR ALL, the broken
policy 432 replaces) and the service-role bypass. Unlike audit_logs, no
migration ever REVOKEd anon/authenticated's baseline grant on this table, so
an unauthenticated/non-admin read is RLS-filtered to empty, not a grant-layer
error -- different posture from audit_logs' migration-51 hardening, and out
of scope for this change (C123 is about the broken role-check pattern, not a
grant-narrowing pass).

push_tokens: "Users manage own push tokens" (FOR ALL, own-row, untouched by
432) is the real, working access path; "Admin read push_tokens" (FOR SELECT)
is the broken policy 432 replaces.

document_requirements: "Public read access for requirements" (SELECT, TO
authenticated + anon, USING (true)) is intentional -- driver app reads the
doc-requirements list without auth -- and untouched by 432. "Admin full
access for requirements" (FOR ALL, the older role='admin'-only form, no
super_admin) is the broken policy 432 replaces.
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


# --------------------------------------------------------------------------
# cloud_messages
# --------------------------------------------------------------------------


def _seed_cloud_message(cur, message_id: str) -> None:
    cur.execute(
        "INSERT INTO cloud_messages (id, title, description) VALUES (%s, %s, %s)",
        (message_id, "Test broadcast", "Test body"),
    )


def test_admin_cannot_select_cloud_messages(pg_cur):
    admin = _uuid()
    message_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    as_role(pg_cur, "service_role", None)
    _seed_cloud_message(pg_cur, message_id)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM cloud_messages WHERE id = %s", (message_id,))
    assert pg_cur.fetchall() == []


def test_admin_cannot_insert_cloud_messages(pg_cur):
    """migration 432's USING (false) on a FOR ALL policy also denies
    INSERT/UPDATE/DELETE -- Postgres uses the USING expression as the
    implicit WITH CHECK when none is specified. Closes the same
    missing-WITH-CHECK write gap migration 416 fixed on corporate_accounts,
    not just the SELECT path."""
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_cloud_message(pg_cur, _uuid())


def test_rider_cannot_select_cloud_messages(pg_cur):
    rider = _uuid()
    message_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    as_role(pg_cur, "service_role", None)
    _seed_cloud_message(pg_cur, message_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM cloud_messages WHERE id = %s", (message_id,))
    assert pg_cur.fetchall() == []


def test_service_role_bypasses_cloud_messages(pg_cur):
    message_id = _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_cloud_message(pg_cur, message_id)
    pg_cur.execute("SELECT id FROM cloud_messages WHERE id = %s", (message_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [message_id]


# --------------------------------------------------------------------------
# push_tokens
# --------------------------------------------------------------------------


def _seed_push_token(cur, token_id: str, user_id: str) -> None:
    cur.execute(
        "INSERT INTO push_tokens (id, user_id, token) VALUES (%s, %s, %s)",
        (token_id, user_id, f"token-{token_id}"),
    )


def test_user_can_manage_own_push_token(pg_cur):
    """ "Users manage own push tokens" (FOR ALL, untouched by 432) is the
    real, working access path -- unaffected by this change."""
    rider = _uuid()
    token_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    _seed_push_token(pg_cur, token_id, rider)
    pg_cur.execute("SELECT id FROM push_tokens WHERE id = %s", (token_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [token_id]


def test_user_cannot_select_others_push_token(pg_cur):
    owner, stranger = _uuid(), _uuid()
    token_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, owner, role="rider")
    _seed_user(pg_cur, stranger, role="rider")
    as_role(pg_cur, "service_role", None)
    _seed_push_token(pg_cur, token_id, owner)
    as_role(pg_cur, "authenticated", {"sub": stranger, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM push_tokens WHERE id = %s", (token_id,))
    assert pg_cur.fetchall() == []


def test_admin_cannot_select_any_push_token(pg_cur):
    """migration 432: "Admin read push_tokens" is replaced with an explicit
    USING (false) -- an admin can no longer see another user's token."""
    owner, admin = _uuid(), _uuid()
    token_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, owner, role="rider")
    _seed_user(pg_cur, admin, role="admin")
    as_role(pg_cur, "service_role", None)
    _seed_push_token(pg_cur, token_id, owner)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM push_tokens WHERE id = %s", (token_id,))
    assert pg_cur.fetchall() == []


def test_service_role_bypasses_push_tokens(pg_cur):
    token_id = _uuid()
    owner = _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_push_token(pg_cur, token_id, owner)
    pg_cur.execute("SELECT id FROM push_tokens WHERE id = %s", (token_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [token_id]


# --------------------------------------------------------------------------
# document_requirements
# --------------------------------------------------------------------------


def _seed_document_requirement(cur, req_id: str) -> None:
    cur.execute(
        "INSERT INTO document_requirements (id, name) VALUES (%s, %s)",
        (req_id, "Driving License"),
    )


def test_anon_can_select_document_requirements(pg_cur):
    """ "Public read access for requirements" is intentional -- the driver
    app reads this list before/without authentication -- and untouched by
    432."""
    req_id = _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_document_requirement(pg_cur, req_id)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM document_requirements WHERE id = %s", (req_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [req_id]


def test_authenticated_can_select_document_requirements(pg_cur):
    rider = _uuid()
    req_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    as_role(pg_cur, "service_role", None)
    _seed_document_requirement(pg_cur, req_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM document_requirements WHERE id = %s", (req_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [req_id]


def test_admin_cannot_insert_document_requirements(pg_cur):
    """migration 432: "Admin full access for requirements" (the older
    role='admin'-only form, no super_admin) is replaced with an explicit
    USING (false) FOR ALL deny -- an admin authenticated JWT can no longer
    write via PostgREST, closing the same missing-WITH-CHECK gap as
    cloud_messages above."""
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_document_requirement(pg_cur, _uuid())


def test_service_role_can_write_document_requirements(pg_cur):
    req_id = _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_document_requirement(pg_cur, req_id)
    pg_cur.execute("SELECT id FROM document_requirements WHERE id = %s", (req_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [req_id]
