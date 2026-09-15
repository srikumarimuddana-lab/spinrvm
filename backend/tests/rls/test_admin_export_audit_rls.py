"""
DB-role-level RLS coverage for the admin PII-export audit trail --
`data_transfer_export_jobs` (migration 262, + 264's additive `reason`
column), `compliance_export_events` (263, +285's DELETE-gating trigger
redefinition), and `admin_export_approval_requests` (268) -- ACTION_ITEMS.md
C49.

Picked as this round's themed slice deliberately: all three tables back the
same dual-approval/export-audit hardening this session already did at the
app layer (B1 -- gated the SIN/DOB backfill router to super_admin; W2a-c --
added per-handler super_admin rechecks to the data_transfer export/import/
search, data_transfer_jobs, sgi_forms, export_approvals, and
migration_status handlers). This round adds the DB-level backstop under
that same surface.

`data_transfer_export_jobs` / `admin_export_approval_requests`: identical
shape -- RLS enabled, zero policy of any kind for anon/authenticated, four
explicit `TO service_role` policies (SELECT/INSERT/UPDATE/DELETE) covering
the service role. With no applicable policy, Postgres's RLS default is
`USING (false)` for SELECT/UPDATE/DELETE (silently filters to zero
matching rows, no exception -- same "empty set, not an error" behavior
established for `driver_insurance_period_corrections`/
`driver_period_distances` last round) and `WITH CHECK (false)` for INSERT
(raises `psycopg2.errors.InsufficientPrivilege`, SQLSTATE 42501 --
Postgres actually does error on a failed WITH CHECK, unlike a failed
USING).

`compliance_export_events`: SELECT restricted to `admin`/`super_admin` via
a `users.role` subquery (no `TO` clause -- applies to all roles, including
anon, which the subquery filters to zero rows since `auth.uid()` is NULL
with no JWT claims set); no INSERT/UPDATE/DELETE policy exists for anyone
but service_role. On top of RLS, an unconditional-UPDATE / flag-gated-
DELETE trigger (migration 263's original always-block function, replaced
in place by 285's flag-gated version -- same name, same trigger, only the
function body changed, exactly like `audit_logs`' migration 51->56->57
progression last round) makes it append-only even for service_role, which
bypasses RLS but not triggers. Unlike last round's `audit_logs` finding
(ACTION_ITEMS.md C112 -- a *second*, unconditional trigger silently
defeated the first's flag-gated exception), `compliance_export_events` has
only the one trigger, and its flag-gated DELETE path is exercised here
end-to-end and confirmed to actually work -- a real, positive contrast to
C112, not a reproduction of the same bug class recurring.

FK-removal regression tests: migrations 270/274/278 each drop an
admin-identity FK (`requested_by`/`decided_by`/`requested_by_admin_id`/
`admin_user_id` -> `users(id)`) that could never be satisfied by a real
admin caller (admin identity lives in `admin_staff` or an env-var-creds
sentinel like "admin-001", never in `users` -- confirmed live in each
migration's own header: zero rows were ever written to any of these three
tables before its fix). Tested here with an admin id that deliberately
does NOT exist in this harness's `users` table, so a future migration that
silently reintroduces the FK (the exact bug these three migrations exist
to fix) would fail this test immediately rather than only surfacing in
production the next time a real admin used the feature.
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


def _seed_export_job(cur, job_id: str, admin_id: str) -> None:
    cur.execute(
        "INSERT INTO data_transfer_export_jobs (id, requested_by_admin_id, entity_type, entity_ids) "
        "VALUES (%s, %s, 'driver', %s)",
        (job_id, admin_id, '["d-1"]'),
    )


def _seed_export_event(cur, event_id: str, admin_id: str) -> None:
    cur.execute(
        "INSERT INTO compliance_export_events (id, admin_user_id, report_type) VALUES (%s, %s, 'dsar_lookup')",
        (event_id, admin_id),
    )


def _seed_approval_request(cur, request_id: str, requested_by: str, decided_by: str | None = None) -> None:
    cur.execute(
        "INSERT INTO admin_export_approval_requests (id, requested_by, route_key, params, decided_by) "
        "VALUES (%s, %s, 'data_transfer.export', %s, %s)",
        (request_id, requested_by, "{}", decided_by),
    )


# --------------------------------------------------------------------------
# data_transfer_export_jobs
# --------------------------------------------------------------------------


def test_anon_selects_no_export_jobs(pg_cur):
    job_id, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_export_job(pg_cur, job_id, admin)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM data_transfer_export_jobs WHERE id = %s", (job_id,))
    assert pg_cur.fetchall() == []


def test_authenticated_super_admin_selects_no_export_jobs(pg_cur):
    """No policy at all covers authenticated -- not even super_admin can
    read this table directly; only service_role (the backend's own
    routes/admin/data_transfer_export.py) ever queries it."""
    job_id, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_export_job(pg_cur, job_id, admin)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM data_transfer_export_jobs WHERE id = %s", (job_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_insert_export_job(pg_cur):
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_export_job(pg_cur, _uuid(), _uuid())


def test_authenticated_cannot_insert_export_job(pg_cur):
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_export_job(pg_cur, _uuid(), admin)


def test_authenticated_cannot_update_export_job(pg_cur):
    job_id, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_export_job(pg_cur, job_id, admin)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("UPDATE data_transfer_export_jobs SET status = 'failed' WHERE id = %s", (job_id,))
    assert pg_cur.rowcount == 0


def test_authenticated_cannot_delete_export_job(pg_cur):
    job_id, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_export_job(pg_cur, job_id, admin)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("DELETE FROM data_transfer_export_jobs WHERE id = %s", (job_id,))
    assert pg_cur.rowcount == 0


def test_service_role_can_insert_export_job(pg_cur):
    as_role(pg_cur, "service_role", None)
    _seed_export_job(pg_cur, _uuid(), _uuid())
    assert pg_cur.rowcount == 1


def test_service_role_can_select_and_update_export_job(pg_cur):
    job_id, admin = _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_export_job(pg_cur, job_id, admin)
    pg_cur.execute("SELECT id FROM data_transfer_export_jobs WHERE id = %s", (job_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [job_id]
    pg_cur.execute("UPDATE data_transfer_export_jobs SET status = 'completed' WHERE id = %s", (job_id,))
    assert pg_cur.rowcount == 1


def test_export_job_admin_id_needs_no_users_row(pg_cur):
    """Regression guard for migration 274: requested_by_admin_id has no FK
    to users(id) (a real admin's id lives in admin_staff or an env-var-
    creds sentinel, never in users -- see migration 274's own header for
    the confirmed-live production bug this fixed). An admin id that does
    NOT exist in this harness's users table must still insert cleanly; if
    the FK were ever silently reinstated, this INSERT would raise
    ForeignKeyViolation instead of succeeding."""
    nonexistent_admin_id = "admin-001"
    as_role(pg_cur, "service_role", None)
    _seed_export_job(pg_cur, _uuid(), nonexistent_admin_id)
    assert pg_cur.rowcount == 1


# --------------------------------------------------------------------------
# admin_export_approval_requests
# --------------------------------------------------------------------------


def test_anon_selects_no_approval_requests(pg_cur):
    request_id, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_approval_request(pg_cur, request_id, admin)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM admin_export_approval_requests WHERE id = %s", (request_id,))
    assert pg_cur.fetchall() == []


def test_authenticated_super_admin_selects_no_approval_requests(pg_cur):
    """Same posture as data_transfer_export_jobs: service_role-only, so
    even the requesting super_admin's own authenticated session can't read
    their own pending request directly -- only the gated route (via
    service_role) can."""
    request_id, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_approval_request(pg_cur, request_id, admin)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM admin_export_approval_requests WHERE id = %s", (request_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_insert_approval_request(pg_cur):
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_approval_request(pg_cur, _uuid(), _uuid())


def test_authenticated_cannot_insert_approval_request(pg_cur):
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_approval_request(pg_cur, _uuid(), admin)


def test_authenticated_cannot_update_approval_request(pg_cur):
    request_id, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_approval_request(pg_cur, request_id, admin)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("UPDATE admin_export_approval_requests SET status = 'approved' WHERE id = %s", (request_id,))
    assert pg_cur.rowcount == 0


def test_authenticated_cannot_delete_approval_request(pg_cur):
    """Same identical-shape parity as data_transfer_export_jobs' own DELETE
    denial test -- this table's service_delete policy (migration 268)
    otherwise goes unexercised by any negative case."""
    request_id, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_approval_request(pg_cur, request_id, admin)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("DELETE FROM admin_export_approval_requests WHERE id = %s", (request_id,))
    assert pg_cur.rowcount == 0


def test_service_role_can_insert_and_update_approval_request(pg_cur):
    request_id, admin = _uuid(), _uuid()
    as_role(pg_cur, "service_role", None)
    _seed_approval_request(pg_cur, request_id, admin)
    assert pg_cur.rowcount == 1
    pg_cur.execute("UPDATE admin_export_approval_requests SET status = 'approved' WHERE id = %s", (request_id,))
    assert pg_cur.rowcount == 1


def test_self_approval_is_rejected_by_check_constraint(pg_cur):
    """The whole feature this table exists for (ACTION_ITEMS.md B10/B11):
    the requester can never be their own approver. Enforced at the
    application layer too, but the DB CHECK constraint
    (admin_export_approval_requests_no_self_approval) makes the invariant
    hold even against a direct-SQL admin action -- only service_role can
    write here at all, so this is the one role that could otherwise slip
    past an app-layer-only check."""
    admin = _uuid()
    as_role(pg_cur, "service_role", None)
    with pytest.raises(psycopg2.errors.CheckViolation):
        _seed_approval_request(pg_cur, _uuid(), admin, decided_by=admin)


def test_approval_request_ids_need_no_users_row(pg_cur):
    """Regression guard for migration 270: requested_by/decided_by have no
    FK to users(id) (same admin-identity-lives-in-admin_staff reasoning as
    migration 274 above -- see 270's own header for the confirmed-live
    production bug). Both ids deliberately absent from this harness's
    users table; must still insert cleanly."""
    as_role(pg_cur, "service_role", None)
    _seed_approval_request(pg_cur, _uuid(), "admin-001", decided_by="break-glass")
    assert pg_cur.rowcount == 1


# --------------------------------------------------------------------------
# compliance_export_events
# --------------------------------------------------------------------------


def test_anon_selects_no_export_events(pg_cur):
    event_id, admin = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_export_event(pg_cur, event_id, admin)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM compliance_export_events WHERE id = %s", (event_id,))
    assert pg_cur.fetchall() == []


def test_non_admin_authenticated_selects_no_export_events(pg_cur):
    rider, admin, event_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_export_event(pg_cur, event_id, admin)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM compliance_export_events WHERE id = %s", (event_id,))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_admin_authenticated_can_select_export_events(pg_cur, role):
    admin, event_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role=role)
    _seed_export_event(pg_cur, event_id, admin)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM compliance_export_events WHERE id = %s", (event_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [event_id]


def test_anon_cannot_insert_export_event(pg_cur):
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_export_event(pg_cur, _uuid(), _uuid())


def test_admin_authenticated_cannot_insert_export_event(pg_cur):
    """No INSERT policy exists for authenticated at all -- not even an
    admin/super_admin JWT can forge an export-audit row directly; only
    service_role (the backend's _log_compliance_export() helper) writes
    here."""
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_export_event(pg_cur, _uuid(), admin)


def test_admin_authenticated_cannot_update_export_event(pg_cur):
    """No UPDATE policy for authenticated -- the RLS USING filter excludes
    the row silently (0 rows, no exception) before the append-only trigger
    ever gets a matching row to fire on."""
    admin, event_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_export_event(pg_cur, event_id, admin)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("UPDATE compliance_export_events SET report_type = 'tampered' WHERE id = %s", (event_id,))
    assert pg_cur.rowcount == 0


def test_admin_authenticated_cannot_delete_export_event(pg_cur):
    admin, event_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_export_event(pg_cur, event_id, admin)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("DELETE FROM compliance_export_events WHERE id = %s", (event_id,))
    assert pg_cur.rowcount == 0


def test_service_role_can_insert_export_event(pg_cur):
    as_role(pg_cur, "service_role", None)
    _seed_export_event(pg_cur, _uuid(), _uuid())
    assert pg_cur.rowcount == 1


def test_no_role_can_update_export_event_even_service_role(pg_cur):
    """Append-only, unconditionally, for UPDATE -- unlike DELETE below,
    there is no flag that ever permits an UPDATE, for any role."""
    admin, event_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_export_event(pg_cur, event_id, admin)
    as_role(pg_cur, "service_role", None)
    with pytest.raises(psycopg2.errors.RaiseException):
        pg_cur.execute("UPDATE compliance_export_events SET report_type = 'tampered' WHERE id = %s", (event_id,))


def test_service_role_delete_blocked_without_retention_flag(pg_cur):
    """Mirrors migration 263's original always-block DELETE behavior for
    any DELETE that isn't the retention purge itself -- confirmed via the
    actual error class, CheckViolation (285's ERRCODE on the un-flagged
    path)."""
    admin, event_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_export_event(pg_cur, event_id, admin)
    as_role(pg_cur, "service_role", None)
    with pytest.raises(psycopg2.errors.CheckViolation):
        pg_cur.execute("DELETE FROM compliance_export_events WHERE id = %s", (event_id,))


def test_service_role_delete_succeeds_with_retention_flag_set(pg_cur):
    """A genuine positive contrast to last round's C112 finding for
    audit_logs: compliance_export_events has only the ONE trigger (263's
    function body, replaced in place by 285), so the flag
    purge_pii_retention()'s own Step M sets immediately before its DELETE
    (spinr.compliance_export_events.allow_delete) actually lets the delete
    through end-to-end -- there is no second, flag-unaware trigger to
    silently defeat it the way audit_logs' migration-57 trigger does.
    set_config's third argument is `false` (session-scoped, not
    transaction-local) for the same reason as the audit_logs test last
    round: this fixture's connection is autocommit, so each execute() is
    its own implicit transaction."""
    admin, event_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="super_admin")
    _seed_export_event(pg_cur, event_id, admin)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT set_config('spinr.compliance_export_events.allow_delete', 'true', false)")
    pg_cur.execute("DELETE FROM compliance_export_events WHERE id = %s", (event_id,))
    assert pg_cur.rowcount == 1


def test_export_event_admin_id_needs_no_users_row(pg_cur):
    """Regression guard for migration 278: admin_user_id has no FK to
    users(id) (same admin-identity reasoning as migrations 270/274 above --
    see 278's own header for the confirmed-live production bug: zero rows
    were ever written to this table before the fix)."""
    as_role(pg_cur, "service_role", None)
    _seed_export_event(pg_cur, _uuid(), "admin-001")
    assert pg_cur.rowcount == 1
