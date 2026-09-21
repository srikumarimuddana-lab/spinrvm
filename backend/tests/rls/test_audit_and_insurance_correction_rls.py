"""
DB-role-level RLS coverage for `audit_logs` (security/admin-action audit
trail, migrations 06/51/56/57) and its two insurance-period audit siblings
`driver_insurance_period_corrections` (355) and `driver_period_distances`
(249) -- ACTION_ITEMS.md C49.

`audit_logs`: two access layers stacked, exercised together the way a real
PostgREST request would hit them (same technique as
test_money_and_safety_rls.py's financial_events tests):
  * grant layer -- migration 51 REVOKEs ALL from anon and
    INSERT/UPDATE/DELETE/TRUNCATE from authenticated, leaving authenticated
    with SELECT only.
  * RLS layer -- that surviving SELECT is further filtered to
    admin/super_admin by the "Admin read audit_logs" policy, so a non-admin
    authenticated user reaches the grant check but gets zero rows back, not
    a grant-layer error.
  * append-only, via THREE triggers, fixed by migration 434 (ACTION_ITEMS.md
    C112) to compose safely: 51's `audit_logs_no_update` (BEFORE UPDATE,
    SQLSTATE check_violation), 56's `audit_logs_no_delete` (BEFORE DELETE,
    flag-gated on the session GUC `spinr.audit_logs.allow_delete` so
    `purge_pii_retention()`'s 7y retention step can still delete old rows),
    and 57's `audit_logs_no_mutate` -- originally BEFORE UPDATE OR DELETE,
    unconditional, no flag awareness at all; migration 434 narrows it to
    UPDATE only. Postgres fires same-event BEFORE ROW triggers in
    alphabetical order by name ("audit_logs_no_delete" <
    "audit_logs_no_mutate" < "audit_logs_no_update"), confirmed by running
    this file against a real Postgres rather than assumed from the
    migrations' text alone:
      - UPDATE: `audit_logs_no_mutate` (57) still fires first and aborts
        unconditionally -- `audit_logs_no_update` (51) never gets a turn.
        Unchanged by 434, which only narrows 57's DELETE side.
      - DELETE (pre-434): `audit_logs_no_delete` (56) fired first and, when
        the flag was set, ALLOWED the delete to proceed to the next
        trigger -- but `audit_logs_no_mutate` (57) then fired anyway and
        aborted it unconditionally regardless of the flag. This broke
        `purge_pii_retention()`'s Step G (the Saskatchewan Transportation
        Act's 7-year `audit_logs` retention ceiling) in any environment
        where migration 57 had been applied on top of 56.
      - DELETE (post-434): `audit_logs_no_mutate` (57) no longer fires on
        DELETE at all, so `audit_logs_no_delete` (56) alone governs it --
        a flagged delete now succeeds, an unflagged one is still fully
        denied. See `test_flag_gated_delete_now_succeeds_after_migration_434_trigger_fix`
        and `test_no_role_can_delete_audit_logs_even_service_role` below,
        and ACTION_ITEMS.md C112 for the full writeup (including why the
        live `purge_pii_retention()` body also needed its Step G
        exception-handler restored in the same migration, not just the
        trigger fix alone).

ACTION_ITEMS.md C123 phase 1 / migration 432: the "Admin read audit_logs"
policy above (migration 51) checks users.role IN ('admin', 'super_admin'),
the same unreachable pattern migration 430 (C107) fixed on 11 other tables --
migration 256's CHECK constraint makes that role value permanently
impossible to hold. Migration 432 replaces it with an explicit USING (false)
deny; `test_admin_authenticated_cannot_select_audit_logs` below was rewritten
from a "can select" assertion to pin that denial rather than removed.

`driver_insurance_period_corrections` / `driver_period_distances`: both
mirror `driver_insurance_periods`' own shape (migration 64, already covered
in test_money_and_safety_rls.py) -- owner-or-admin SELECT (resolved through
`driver_insurance_periods`/`drivers`, no direct driver_id column on the
corrections table), no INSERT/UPDATE/DELETE policy for anon/authenticated
(service-role-only writes, RLS default-denies the rest), an unconditional
append-only trigger. `driver_period_distances` additionally blocks UPDATE
in that same unconditional trigger (unlike `driver_insurance_periods`,
which permits exactly one UPDATE to close an open period) -- both directions
exercised here.

ACTION_ITEMS.md C123 phase 2 / migration 433: both tables' single SELECT
policy carried the identical entangled-OR pattern as `driver_insurance_periods`
itself -- the owner-driver's legitimate self-read access and a broken
`users.role IN ('admin', 'super_admin')` check (unreachable since migration
256) combined in one USING clause. Found while writing 433 (not part of
C123's original named scope, which only listed `driver_insurance_periods`),
since 355's own header comment says it deliberately mirrors 64's pattern.
Fixed the same way: drop only the broken admin disjunct, keep the owner
access. `test_admin_can_select_any_correction` / `test_admin_can_select_any_distance`
below were rewritten to `_cannot_select_`, pinning that denial; every other
test in both sections is unaffected.
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


def _seed_audit_log(cur, log_id: str, action: str = "admin_login") -> None:
    cur.execute(
        "INSERT INTO audit_logs (id, action, entity_type, entity_id) VALUES (%s, %s, 'system', %s)",
        (log_id, action, log_id),
    )


def _seed_insurance_period(cur, period_id: str, driver_id: str, period: int = 2) -> None:
    cur.execute(
        "INSERT INTO driver_insurance_periods (id, driver_id, period) VALUES (%s, %s, %s)",
        (period_id, driver_id, period),
    )


def _seed_correction(cur, correction_id: str, original_period_id: str, corrected_by: str) -> None:
    cur.execute(
        "INSERT INTO driver_insurance_period_corrections "
        "(id, original_period_id, corrected_started_at, reason, corrected_by) "
        "VALUES (%s, %s, now(), 'reconstructed from GPS breadcrumbs', %s)",
        (correction_id, original_period_id, corrected_by),
    )


def _seed_distance(cur, distance_id: str, driver_id: str, period: int = 1) -> None:
    cur.execute(
        "INSERT INTO driver_period_distances (id, driver_id, period, distance_km) VALUES (%s, %s, %s, 4.200)",
        (distance_id, driver_id, period),
    )


# --------------------------------------------------------------------------
# audit_logs
# --------------------------------------------------------------------------


def test_anon_cannot_select_audit_logs(pg_cur):
    """migration 51: REVOKE ALL FROM anon -- the grant layer denies the
    SELECT itself, before RLS is even consulted."""
    log_id = _uuid()
    as_role(pg_cur, None)
    _seed_audit_log(pg_cur, log_id)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("SELECT id FROM audit_logs WHERE id = %s", (log_id,))


def test_non_admin_authenticated_selects_no_audit_logs(pg_cur):
    """GRANT SELECT to authenticated survives migration 51's REVOKE, but the
    RLS policy restricts rows to admin/super_admin -- a non-admin rider gets
    an empty result, not a grant-layer error."""
    rider = _uuid()
    log_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    _seed_audit_log(pg_cur, log_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM audit_logs WHERE id = %s", (log_id,))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_admin_authenticated_cannot_select_audit_logs(pg_cur, role):
    """ACTION_ITEMS.md C123 phase 1 / migration 432: the "Admin read
    audit_logs" policy migration 51 created is unreachable -- migration 256's
    CHECK constraint makes users.role IN ('admin', 'super_admin') permanently
    impossible to hold, same root cause as migration 430 (C107). 432 replaces
    it with an explicit USING (false) deny; admin and super_admin are denied
    identically, same as every non-admin authenticated user."""
    admin = _uuid()
    log_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role=role)
    _seed_audit_log(pg_cur, log_id)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM audit_logs WHERE id = %s", (log_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_insert_audit_logs(pg_cur):
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_audit_log(pg_cur, _uuid())


def test_authenticated_cannot_insert_audit_logs(pg_cur):
    """migration 51's core fix: INSERT was revoked from authenticated too --
    even an admin JWT cannot forge an audit row directly; only service_role
    (the backend's log_audit() helper) writes here."""
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_audit_log(pg_cur, _uuid())


def test_service_role_can_insert_audit_logs(pg_cur):
    as_role(pg_cur, "service_role", None)
    _seed_audit_log(pg_cur, _uuid())
    assert pg_cur.rowcount == 1


def test_authenticated_cannot_update_audit_logs(pg_cur):
    """Grant-layer REVOKE UPDATE FROM authenticated -- an admin authenticated
    session is denied before it ever reaches either trigger."""
    admin = _uuid()
    log_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    _seed_audit_log(pg_cur, log_id)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("UPDATE audit_logs SET action = 'tampered' WHERE id = %s", (log_id,))


def test_no_role_can_update_audit_logs_even_service_role(pg_cur):
    """Append-only: with two independent tamper-evidence triggers stacked
    (51, 57 -- 434 narrows 57 to UPDATE-only but does not remove it), UPDATE
    is blocked for every role including service_role (RLS/grant bypass does
    not bypass triggers)."""
    log_id = _uuid()
    as_role(pg_cur, None)
    _seed_audit_log(pg_cur, log_id)
    as_role(pg_cur, "service_role", None)
    with pytest.raises(psycopg2.errors.RaiseException):
        pg_cur.execute("UPDATE audit_logs SET action = 'tampered' WHERE id = %s", (log_id,))


def test_no_role_can_delete_audit_logs_even_service_role(pg_cur):
    """migration 56's audit_logs_no_delete trigger is now (post-434) the
    sole DELETE guard on this table -- an un-flagged DELETE is still fully
    blocked, confirmed by the actual error class raised here,
    psycopg2.errors.CheckViolation (56's ERRCODE)."""
    log_id = _uuid()
    as_role(pg_cur, None)
    _seed_audit_log(pg_cur, log_id)
    as_role(pg_cur, "service_role", None)
    with pytest.raises(psycopg2.errors.CheckViolation):
        pg_cur.execute("DELETE FROM audit_logs WHERE id = %s", (log_id,))


def test_flag_gated_delete_now_succeeds_after_migration_434_trigger_fix(pg_cur):
    """Proves the fix for a real production bug (ACTION_ITEMS.md C112):
    migration 56 added `audit_logs_no_delete`, a BEFORE DELETE trigger that
    allows the delete through when the session-local GUC
    `spinr.audit_logs.allow_delete` is 'true' -- exactly what
    `purge_pii_retention()`'s Step G sets immediately before its 7-year
    `audit_logs` retention DELETE. But migration 57 (applied after 56 in
    filename-sort order) separately added `audit_logs_no_mutate`, an
    unconditional BEFORE UPDATE OR DELETE trigger with no knowledge of that
    flag at all -- Postgres fires every applicable BEFORE ROW trigger for
    one DELETE, not just the first to match, so even with the flag set
    exactly the way the retention job sets it, the delete used to abort
    anyway (RaiseException from 57, once 56's own check had let it
    through).

    Migration 434 fixes this by narrowing 57's audit_logs_no_mutate trigger
    to UPDATE only, leaving 56 as the sole DELETE guard. With the flag set,
    the delete must now actually succeed -- both that no exception is
    raised AND that the row is genuinely gone (proving 56's own check
    passed, not that some other trigger silently no-op'd).

    set_config's third argument is `false` (session-scoped), not `true`
    (transaction-local) as the real purge_pii_retention() uses -- this
    fixture's connection runs autocommit, so each pg_cur.execute() is its
    own implicit transaction and a transaction-local flag from one
    statement would already be gone before the next. Session-scoped is
    sufficient to prove which trigger(s) fire; it doesn't change which
    triggers fire or in what order."""
    log_id = _uuid()
    as_role(pg_cur, None)
    _seed_audit_log(pg_cur, log_id)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT set_config('spinr.audit_logs.allow_delete', 'true', false)")
    pg_cur.execute("DELETE FROM audit_logs WHERE id = %s", (log_id,))
    assert pg_cur.rowcount == 1
    pg_cur.execute("SELECT set_config('spinr.audit_logs.allow_delete', 'false', false)")
    pg_cur.execute("SELECT id FROM audit_logs WHERE id = %s", (log_id,))
    assert pg_cur.fetchall() == []


# --------------------------------------------------------------------------
# driver_insurance_period_corrections
# --------------------------------------------------------------------------


def test_driver_can_select_own_correction(pg_cur):
    driver_user, driver_id, admin = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, admin, role="admin")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    _seed_insurance_period(pg_cur, period_id, driver_id)
    correction_id = _uuid()
    _seed_correction(pg_cur, correction_id, period_id, admin)
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM driver_insurance_period_corrections WHERE id = %s", (correction_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [correction_id]


def test_other_driver_cannot_select_correction(pg_cur):
    driver_user, driver_id, other_driver_user, admin = _uuid(), _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, other_driver_user, role="driver")
    _seed_user(pg_cur, admin, role="admin")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    _seed_insurance_period(pg_cur, period_id, driver_id)
    correction_id = _uuid()
    _seed_correction(pg_cur, correction_id, period_id, admin)
    as_role(pg_cur, "authenticated", {"sub": other_driver_user, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM driver_insurance_period_corrections WHERE id = %s", (correction_id,))
    assert pg_cur.fetchall() == []


def test_admin_cannot_select_any_correction(pg_cur):
    """ACTION_ITEMS.md C123 phase 2 / migration 433: this table's single
    SELECT policy carried the identical entangled-OR pattern as
    driver_insurance_periods itself (found while writing 433, not part of
    C123's original named scope) -- rewritten to drop only the broken `OR
    <admin check>` clause. An admin JWT is now denied exactly like any other
    non-owning driver; the owning driver's own access (tested above) is
    unaffected."""
    driver_user, driver_id, admin = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, admin, role="admin")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    _seed_insurance_period(pg_cur, period_id, driver_id)
    correction_id = _uuid()
    _seed_correction(pg_cur, correction_id, period_id, admin)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM driver_insurance_period_corrections WHERE id = %s", (correction_id,))
    assert pg_cur.fetchall() == []


def test_authenticated_cannot_insert_correction(pg_cur):
    """migration 355 ships no INSERT policy for anon/authenticated, same
    'RLS default-denies with no applicable policy' pattern as
    driver_insurance_periods itself (migration 64)."""
    driver_user, driver_id, admin = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, admin, role="admin")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    _seed_insurance_period(pg_cur, period_id, driver_id)
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_correction(pg_cur, _uuid(), period_id, admin)


def test_authenticated_cannot_update_correction(pg_cur):
    """No UPDATE policy exists for authenticated (or anon) -- unlike INSERT's
    WITH CHECK (which raises InsufficientPrivilege on violation), Postgres
    RLS's UPDATE-side USING filter just excludes the row from the update
    set silently: 0 rows affected, no exception, even for the driver who
    owns the underlying period. Confirmed empirically -- an initial version
    of this test wrongly expected an exception here."""
    driver_user, driver_id, admin = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, admin, role="admin")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    _seed_insurance_period(pg_cur, period_id, driver_id)
    correction_id = _uuid()
    _seed_correction(pg_cur, correction_id, period_id, admin)
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    pg_cur.execute(
        "UPDATE driver_insurance_period_corrections SET reason = 'edited' WHERE id = %s",
        (correction_id,),
    )
    assert pg_cur.rowcount == 0


def test_authenticated_cannot_delete_correction(pg_cur):
    """Same RLS default-deny-as-empty-set behavior as UPDATE above, for
    DELETE's USING filter."""
    driver_user, driver_id, admin = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, admin, role="admin")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    _seed_insurance_period(pg_cur, period_id, driver_id)
    correction_id = _uuid()
    _seed_correction(pg_cur, correction_id, period_id, admin)
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    pg_cur.execute("DELETE FROM driver_insurance_period_corrections WHERE id = %s", (correction_id,))
    assert pg_cur.rowcount == 0


def test_service_role_can_insert_correction(pg_cur):
    driver_user, driver_id, admin = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, admin, role="admin")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    _seed_insurance_period(pg_cur, period_id, driver_id)
    as_role(pg_cur, "service_role", None)
    _seed_correction(pg_cur, _uuid(), period_id, admin)
    assert pg_cur.rowcount == 1


def test_no_role_can_update_correction_even_service_role(pg_cur):
    driver_user, driver_id, admin = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, admin, role="admin")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    _seed_insurance_period(pg_cur, period_id, driver_id)
    correction_id = _uuid()
    _seed_correction(pg_cur, correction_id, period_id, admin)
    as_role(pg_cur, "service_role", None)
    with pytest.raises(psycopg2.errors.RaiseException):
        pg_cur.execute(
            "UPDATE driver_insurance_period_corrections SET reason = 'edited' WHERE id = %s",
            (correction_id,),
        )


def test_no_role_can_delete_correction_even_service_role(pg_cur):
    driver_user, driver_id, admin = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, admin, role="admin")
    _seed_driver(pg_cur, driver_id, driver_user)
    period_id = _uuid()
    _seed_insurance_period(pg_cur, period_id, driver_id)
    correction_id = _uuid()
    _seed_correction(pg_cur, correction_id, period_id, admin)
    as_role(pg_cur, "service_role", None)
    with pytest.raises(psycopg2.errors.RaiseException):
        pg_cur.execute("DELETE FROM driver_insurance_period_corrections WHERE id = %s", (correction_id,))


# --------------------------------------------------------------------------
# driver_period_distances
# --------------------------------------------------------------------------


def test_driver_can_select_own_distance(pg_cur):
    driver_user, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    distance_id = _uuid()
    _seed_distance(pg_cur, distance_id, driver_id)
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM driver_period_distances WHERE id = %s", (distance_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [distance_id]


def test_other_driver_cannot_select_distance(pg_cur):
    driver_user, driver_id, other_driver_user = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, other_driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    distance_id = _uuid()
    _seed_distance(pg_cur, distance_id, driver_id)
    as_role(pg_cur, "authenticated", {"sub": other_driver_user, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM driver_period_distances WHERE id = %s", (distance_id,))
    assert pg_cur.fetchall() == []


def test_admin_cannot_select_any_distance(pg_cur):
    """ACTION_ITEMS.md C123 phase 2 / migration 433: same entangled-OR
    pattern as driver_insurance_periods and the corrections table above,
    fixed the same way -- an admin JWT is now denied, the owning driver's
    own access (tested above) is unaffected."""
    driver_user, driver_id, admin = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_user(pg_cur, admin, role="admin")
    _seed_driver(pg_cur, driver_id, driver_user)
    distance_id = _uuid()
    _seed_distance(pg_cur, distance_id, driver_id)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM driver_period_distances WHERE id = %s", (distance_id,))
    assert pg_cur.fetchall() == []


def test_authenticated_cannot_insert_distance(pg_cur):
    driver_user, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_distance(pg_cur, _uuid(), driver_id)


def test_authenticated_cannot_update_distance(pg_cur):
    """No UPDATE policy exists for authenticated (or anon) -- unlike INSERT's
    WITH CHECK (which raises InsufficientPrivilege on violation), Postgres
    RLS's UPDATE-side USING filter just excludes the row silently: 0 rows
    affected, no exception, even for the driver who owns the row. Confirmed
    empirically -- an initial version of this test wrongly expected an
    exception here."""
    driver_user, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    distance_id = _uuid()
    _seed_distance(pg_cur, distance_id, driver_id)
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    pg_cur.execute("UPDATE driver_period_distances SET distance_km = 1.000 WHERE id = %s", (distance_id,))
    assert pg_cur.rowcount == 0


def test_authenticated_cannot_delete_distance(pg_cur):
    """Same RLS default-deny-as-empty-set behavior as UPDATE above, for
    DELETE's USING filter."""
    driver_user, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    distance_id = _uuid()
    _seed_distance(pg_cur, distance_id, driver_id)
    as_role(pg_cur, "authenticated", {"sub": driver_user, "role": "authenticated"})
    pg_cur.execute("DELETE FROM driver_period_distances WHERE id = %s", (distance_id,))
    assert pg_cur.rowcount == 0


def test_service_role_can_insert_distance(pg_cur):
    driver_user, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    as_role(pg_cur, "service_role", None)
    _seed_distance(pg_cur, _uuid(), driver_id)
    assert pg_cur.rowcount == 1


def test_no_role_can_update_distance_even_service_role(pg_cur):
    """Unlike driver_insurance_periods (which permits one UPDATE to close an
    open period), driver_period_distances' trigger blocks UPDATE
    unconditionally -- a distance row is computed once and final."""
    driver_user, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    distance_id = _uuid()
    _seed_distance(pg_cur, distance_id, driver_id)
    as_role(pg_cur, "service_role", None)
    with pytest.raises(psycopg2.errors.RaiseException):
        pg_cur.execute("UPDATE driver_period_distances SET distance_km = 99.999 WHERE id = %s", (distance_id,))


def test_no_role_can_delete_distance_even_service_role(pg_cur):
    driver_user, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    distance_id = _uuid()
    _seed_distance(pg_cur, distance_id, driver_id)
    as_role(pg_cur, "service_role", None)
    with pytest.raises(psycopg2.errors.RaiseException):
        pg_cur.execute("DELETE FROM driver_period_distances WHERE id = %s", (distance_id,))
