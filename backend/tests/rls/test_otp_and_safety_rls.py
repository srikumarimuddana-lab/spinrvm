"""
DB-role-level RLS coverage for five auth/safety-sensitive tables --
ACTION_ITEMS.md C49 remaining scope.

`otp_records` (supabase_schema.sql + supabase_rls.sql's `otp_deny_all`):
service-role-only, `FOR ALL USING (false)` with no `TO` clause -- applies to
every role, anon and authenticated alike.

`rider_email_verification_otp` (migration 362, the corrected replacement for
migration 299 -- see conftest.py's comment on why 299 itself is never
applied): `FOR ALL TO authenticated USING (false)`, and no policy at all for
anon (RLS's default-deny covers that role without one).

`emergency_contacts` (migration 120): owner-only SELECT/INSERT/DELETE via
`auth.uid()::text = user_id`. Deliberately no UPDATE policy and no admin
override -- migration 378 (saved_addresses) explicitly cites this table as
the precedent for that same no-admin-override shape.

`safety_incidents` (migration 94): service-role bypass; reporter-only SELECT
of their own submitted reports; no INSERT or DELETE policy for anyone but
service_role (the comment on the migration is explicit: "Insert is
service-role only -- admins escalate via the backend API, not by writing
directly to the table"). The original admin/super_admin SELECT+UPDATE
policies (via a `users.role` subquery) are unreachable -- migration 256 make
that role value permanently impossible to hold -- and ACTION_ITEMS.md C123
phase 2 / migration 433 replaces both with an explicit `USING (false)` deny,
same pattern as migration 430/432.

`safety_incident_photos` (migration 340): service-role bypass, zero policy
for anon/authenticated by design -- evidence photos can name or depict a
third party, so a client must never enumerate this table directly.
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


# ── otp_records ─────────────────────────────────────────────────────────


def _seed_otp_record(cur, record_id: str) -> None:
    cur.execute("INSERT INTO otp_records (id) VALUES (%s)", (record_id,))


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_select_otp_records(pg_cur, role):
    as_role(pg_cur, None)
    record_id = _uuid()
    _seed_otp_record(pg_cur, record_id)
    as_role(pg_cur, role, {"sub": _uuid(), "role": role} if role == "authenticated" else None)
    pg_cur.execute("SELECT id FROM otp_records WHERE id = %s", (record_id,))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_insert_otp_records(pg_cur, role):
    as_role(pg_cur, role, {"sub": _uuid(), "role": role} if role == "authenticated" else None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_otp_record(pg_cur, _uuid())


def test_service_role_bypasses_otp_records(pg_cur):
    as_role(pg_cur, None)
    record_id = _uuid()
    _seed_otp_record(pg_cur, record_id)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT id FROM otp_records WHERE id = %s", (record_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [record_id]


# ── rider_email_verification_otp ───────────────────────────────────────


def _seed_email_otp(cur, user_id: str, email: str) -> None:
    cur.execute(
        """
        INSERT INTO rider_email_verification_otp (user_id, email, code_hash, expires_at)
        VALUES (%s, %s, 'hash', now() + interval '15 minutes')
        """,
        (user_id, email),
    )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_select_email_otp(pg_cur, role):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    email = f"{_uuid()}@example.com"
    _seed_email_otp(pg_cur, me, email)
    as_role(pg_cur, role, {"sub": me, "role": role} if role == "authenticated" else None)
    pg_cur.execute("SELECT email FROM rider_email_verification_otp WHERE email = %s", (email,))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_insert_email_otp(pg_cur, role):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    as_role(pg_cur, role, {"sub": me, "role": role} if role == "authenticated" else None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_email_otp(pg_cur, me, f"{_uuid()}@example.com")


def test_service_role_bypasses_email_otp(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    email = f"{_uuid()}@example.com"
    as_role(pg_cur, "service_role", None)
    _seed_email_otp(pg_cur, me, email)
    pg_cur.execute("SELECT email FROM rider_email_verification_otp WHERE email = %s", (email,))
    assert [r[0] for r in pg_cur.fetchall()] == [email]


# ── emergency_contacts ──────────────────────────────────────────────────


def _seed_contact(cur, contact_id: str, user_id: str) -> None:
    cur.execute(
        "INSERT INTO emergency_contacts (id, user_id, name, phone) VALUES (%s, %s, 'Contact', '+13065551234')",
        (contact_id, user_id),
    )


def test_owner_can_select_own_contact(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    contact_id = _uuid()
    _seed_contact(pg_cur, contact_id, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM emergency_contacts WHERE id = %s", (contact_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [contact_id]


def test_authenticated_cannot_select_another_users_contact(pg_cur):
    me, other = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, other)
    contact_id = _uuid()
    _seed_contact(pg_cur, contact_id, other)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM emergency_contacts WHERE id = %s", (contact_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_any_contact(pg_cur):
    other = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, other)
    contact_id = _uuid()
    _seed_contact(pg_cur, contact_id, other)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM emergency_contacts WHERE id = %s", (contact_id,))
    assert pg_cur.fetchall() == []


def test_owner_can_insert_own_contact(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    _seed_contact(pg_cur, _uuid(), me)
    assert pg_cur.rowcount == 1


def test_authenticated_cannot_insert_contact_for_another_user(pg_cur):
    """emergency_contacts_owner_insert's WITH CHECK requires auth.uid()::text
    = user_id -- an authenticated user must not be able to attach a forged
    emergency contact to someone else's account."""
    me, victim = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, victim)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_contact(pg_cur, _uuid(), victim)


def test_anon_cannot_insert_contact(pg_cur):
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_contact(pg_cur, _uuid(), rider)


def test_owner_cannot_update_own_contact(pg_cur):
    """No UPDATE policy exists on this table at all -- corrections go
    through delete + re-insert, per the app's own client flow."""
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    contact_id = _uuid()
    _seed_contact(pg_cur, contact_id, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("UPDATE emergency_contacts SET name = 'Changed' WHERE id = %s", (contact_id,))
    assert pg_cur.rowcount == 0


def test_owner_can_delete_own_contact(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    contact_id = _uuid()
    _seed_contact(pg_cur, contact_id, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("DELETE FROM emergency_contacts WHERE id = %s", (contact_id,))
    assert pg_cur.rowcount == 1


def test_authenticated_cannot_delete_another_users_contact(pg_cur):
    me, other = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, other)
    contact_id = _uuid()
    _seed_contact(pg_cur, contact_id, other)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("DELETE FROM emergency_contacts WHERE id = %s", (contact_id,))
    assert pg_cur.rowcount == 0


def test_service_role_bypasses_emergency_contacts(pg_cur):
    other = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, other)
    contact_id = _uuid()
    _seed_contact(pg_cur, contact_id, other)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT id FROM emergency_contacts WHERE id = %s", (contact_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [contact_id]


# ── safety_incidents ────────────────────────────────────────────────────


def _seed_incident(cur, incident_id: str, reporter_id: str | None) -> None:
    cur.execute(
        "INSERT INTO safety_incidents (id, reported_by_user_id, category, description) "
        "VALUES (%s, %s, 'abuse', 'test report')",
        (incident_id, reporter_id),
    )


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_admin_roles_cannot_select_any_incident(pg_cur, role):
    """ACTION_ITEMS.md C123 phase 2 / migration 433: the "Admin read/update
    safety_incidents" policy is replaced with an explicit USING (false) --
    admin and super_admin are denied identically, same as any other
    non-reporting authenticated user."""
    reporter, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    _seed_user(pg_cur, admin, role=role)
    incident_id = _uuid()
    _seed_incident(pg_cur, incident_id, reporter)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM safety_incidents WHERE id = %s", (incident_id,))
    assert pg_cur.fetchall() == []


def test_reporter_can_select_own_incident(pg_cur):
    reporter = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    incident_id = _uuid()
    _seed_incident(pg_cur, incident_id, reporter)
    as_role(pg_cur, "authenticated", {"sub": reporter, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM safety_incidents WHERE id = %s", (incident_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [incident_id]


def test_non_admin_non_reporter_cannot_select_incident(pg_cur):
    reporter, stranger = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    _seed_user(pg_cur, stranger)
    incident_id = _uuid()
    _seed_incident(pg_cur, incident_id, reporter)
    as_role(pg_cur, "authenticated", {"sub": stranger, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM safety_incidents WHERE id = %s", (incident_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_incident(pg_cur):
    reporter = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    incident_id = _uuid()
    _seed_incident(pg_cur, incident_id, reporter)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM safety_incidents WHERE id = %s", (incident_id,))
    assert pg_cur.fetchall() == []


def test_admin_cannot_update_incident_status(pg_cur):
    """ACTION_ITEMS.md C123 phase 2 / migration 433: the "Admin update
    safety_incidents" policy is replaced with an explicit USING (false) --
    same RLS default-deny-as-empty-set behavior as
    test_reporter_cannot_update_own_incident below (no exception, 0 rows
    affected)."""
    reporter, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    _seed_user(pg_cur, admin, role="admin")
    incident_id = _uuid()
    _seed_incident(pg_cur, incident_id, reporter)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("UPDATE safety_incidents SET status = 'resolved' WHERE id = %s", (incident_id,))
    assert pg_cur.rowcount == 0


def test_reporter_cannot_update_own_incident(pg_cur):
    """Reporter has read-only access to their own report -- once filed, the
    record is admin-owned (migration 94's own comment)."""
    reporter = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    incident_id = _uuid()
    _seed_incident(pg_cur, incident_id, reporter)
    as_role(pg_cur, "authenticated", {"sub": reporter, "role": "authenticated"})
    pg_cur.execute("UPDATE safety_incidents SET status = 'resolved' WHERE id = %s", (incident_id,))
    assert pg_cur.rowcount == 0


def test_authenticated_cannot_insert_incident(pg_cur):
    """No INSERT policy exists for authenticated at all, admin included --
    reports are filed through the backend API (service_role), never a
    direct client insert."""
    reporter = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    as_role(pg_cur, "authenticated", {"sub": reporter, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_incident(pg_cur, _uuid(), reporter)


def test_anon_cannot_insert_incident(pg_cur):
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_incident(pg_cur, _uuid(), None)


def test_admin_cannot_delete_incident(pg_cur):
    """No DELETE policy exists for anyone but service_role -- this is a
    regulated audit record under the SK Transportation Act; nothing but the
    backend may remove a row."""
    reporter, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    _seed_user(pg_cur, admin, role="admin")
    incident_id = _uuid()
    _seed_incident(pg_cur, incident_id, reporter)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("DELETE FROM safety_incidents WHERE id = %s", (incident_id,))
    assert pg_cur.rowcount == 0


def test_service_role_bypasses_safety_incidents(pg_cur):
    as_role(pg_cur, "service_role", None)
    incident_id = _uuid()
    _seed_incident(pg_cur, incident_id, None)
    pg_cur.execute("SELECT id FROM safety_incidents WHERE id = %s", (incident_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [incident_id]


# ── safety_incident_photos ──────────────────────────────────────────────


def _seed_photo(cur, incident_id: str) -> None:
    cur.execute(
        "INSERT INTO safety_incident_photos (incident_id, storage_key) VALUES (%s, %s)",
        (incident_id, f"safety-evidence/{_uuid()}.jpg"),
    )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_select_incident_photos(pg_cur, role):
    reporter = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    incident_id = _uuid()
    _seed_incident(pg_cur, incident_id, reporter)
    _seed_photo(pg_cur, incident_id)
    as_role(pg_cur, role, {"sub": reporter, "role": role} if role == "authenticated" else None)
    pg_cur.execute("SELECT incident_id FROM safety_incident_photos WHERE incident_id = %s", (incident_id,))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_insert_incident_photos(pg_cur, role):
    reporter = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    incident_id = _uuid()
    _seed_incident(pg_cur, incident_id, reporter)
    as_role(pg_cur, role, {"sub": reporter, "role": role} if role == "authenticated" else None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_photo(pg_cur, incident_id)


def test_service_role_bypasses_safety_incident_photos(pg_cur):
    as_role(pg_cur, None)
    incident_id = _uuid()
    _seed_incident(pg_cur, incident_id, None)
    as_role(pg_cur, "service_role", None)
    _seed_photo(pg_cur, incident_id)
    pg_cur.execute("SELECT incident_id FROM safety_incident_photos WHERE incident_id = %s", (incident_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [incident_id]
