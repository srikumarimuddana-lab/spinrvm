"""
DB-role-level RLS coverage for the financial-ledger extension --
`financial_event_entries` (migration 286), `reconciliation_discrepancies`
(migration 59), and `subscription_payments` (migration 151) --
ACTION_ITEMS.md C49.

Picked as this round's themed slice deliberately: all three extend or sit
directly alongside the already-covered `financial_events` money ledger
(migrations 58/70/290), and CLAUDE.md's coverage-minimum tier for money
paths makes this the highest-stakes group left in C49's remaining-table
list as of this round.

`financial_event_entries`: the double-entry leg table. RLS enabled,
`WITH CHECK (true)` INSERT (no `TO` clause -- applies to PUBLIC) narrowed
by its own REVOKE/GRANT lockdown (shipped in the same migration, unlike
`financial_events` itself, which needed a separate migration 290 to
retrofit this), admin-only SELECT (`users.role = 'admin'`, not
`super_admin` -- narrower than most admin checks in this schema), no
UPDATE/DELETE policy at all. On top of RLS, a real trigger
(`financial_event_entries_no_update`) blocks UPDATE unconditionally, even
for `service_role` (RLS bypass is not trigger bypass) -- DELETE is
deliberately NOT trigger-blocked, since the row's own designed lifecycle
is `ON DELETE CASCADE` from its parent `financial_events` row.

`reconciliation_discrepancies`: a single `FOR ALL USING (role = 'admin')`
policy with **no accompanying REVOKE** -- distinct from every other money
table in this fixture. Postgres uses a `FOR ALL` policy's `USING` clause as
its implicit `WITH CHECK` when none is given, so the admin-only condition
gates every operation (SELECT/INSERT/UPDATE/DELETE) through RLS alone, with
the default Supabase table-level grant left untouched. Verified empirically
below, not assumed from the SQL.

`subscription_payments`: driver-owned SELECT (own rows only, via
`drivers.user_id`) + REVOKE/GRANT write lockdown -- and, per the migration's
own comment, deliberately **no admin SELECT policy at all** ("the admin
stats endpoint reads via the service role... a direct authenticated-role
admin read would see zero rows by design"). This is a real, testable
contrast with its two siblings above, both of which do carry an admin read
path.

Two findings, both fixed 2026-09-23 by migration 456 (ACTION_ITEMS.md C129,
closed) -- tests below pin the fixed behavior, not the original gap:

1. **Unreachable admin-role RLS, a pair missed by the C107/C123 sweeps.**
   `financial_event_entries_select` (286) and `reconciliation_discrepancies`'
   `recon_admin_only` (59) both gated on
   `(SELECT role FROM users WHERE id = auth.uid()::text) = 'admin'` -- the
   exact pattern migrations 430/432/433 found permanently unreachable in
   production (migration 256's `chk_users_role_not_admin` CHECK means
   `users.role` can never actually equal `'admin'`; real admin identity
   lives in `admin_staff`) and systematically replaced with an explicit
   `USING (false)` deny across every other table carrying it. A repo-wide
   grep (`grep -rl financial_event_entries\\|reconciliation_discrepancies
   backend/migrations/430_*.sql backend/migrations/432_*.sql
   backend/migrations/433_*.sql`) had confirmed neither table appeared in
   any of the three fixes. Migration 456 extends the same `USING (false)`
   pattern to both.
2. **`subscription_payments` claimed "Append-only ledger" in its own
   `COMMENT ON TABLE` but had zero DB-level enforcement of that claim** --
   no trigger of any kind, unlike its sibling `financial_event_entries`
   (real UPDATE-blocking trigger) and the established pattern for audit
   tables making the same claim (`audit_logs`, `compliance_export_events`).
   Since `service_role` bypasses RLS entirely, only a trigger can constrain
   it. No production code path ever issued an UPDATE/DELETE here (confirmed
   by grepping `routes/`, `services/`, `utils/`), so this was a defense-in-
   depth gap, not a live incident -- migration 456 adds
   `subscription_payments_no_mutate`, blocking BOTH UPDATE and DELETE
   (unlike `financial_event_entries_no_update`, which is UPDATE-only to
   avoid breaking its parent's `ON DELETE CASCADE` -- `subscription_payments`
   has no FK/cascade relationship to any parent row, confirmed by grepping
   every migration touching it for a `REFERENCES`/`FOREIGN KEY` on
   `driver_id`: none exists).
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
        "INSERT INTO drivers (id, user_id, name, phone) VALUES (%s, %s, 'Test Driver', '+13065559999')",
        (driver_id, user_id),
    )


def _seed_financial_event(cur, user_id: str) -> str:
    cur.execute(
        "INSERT INTO financial_events (event_type, user_id, delta_cents) VALUES ('fare_settle', %s, 1000) RETURNING id",
        (user_id,),
    )
    return cur.fetchone()[0]


def _seed_discrepancy(cur, disc_id: str, date_val: str = "2026-01-01") -> None:
    cur.execute(
        "INSERT INTO reconciliation_discrepancies "
        "(id, date, stripe_total_cents, db_total_cents, discrepancy_cents) "
        "VALUES (%s, %s, 1000, 900, 100)",
        (disc_id, date_val),
    )


def _seed_subscription_payment(cur, payment_id: str, driver_id: str) -> None:
    cur.execute(
        "INSERT INTO subscription_payments (id, driver_id, amount, billing_reason) VALUES (%s, %s, 19.99, 'one_off')",
        (payment_id, driver_id),
    )


# --------------------------------------------------------------------------
# financial_event_entries
# --------------------------------------------------------------------------


def test_service_role_bypasses_select(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    cur = as_role(pg_cur, "service_role")
    cur.execute(
        "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
        "VALUES (%s, 'driver_payable', 'credit', 1000)",
        (event_id,),
    )
    cur.execute("SELECT event_id FROM financial_event_entries WHERE event_id = %s", (event_id,))
    assert cur.fetchall() == [(event_id,)]


def test_admin_authenticated_cannot_select(pg_cur):
    """ACTION_ITEMS.md C129 / migration 456: financial_event_entries_select's
    users.role = 'admin' check is unreachable -- migration 256's CHECK
    constraint makes that value impossible to hold, same root cause as
    migration 430 (C107). 456 replaces it with an explicit USING (false)
    deny; an admin-shaped authenticated session is denied identically to
    any other non-admin user."""
    user_id, admin_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    _seed_user(pg_cur, admin_id, role="admin")
    event_id = _seed_financial_event(pg_cur, user_id)
    as_role(pg_cur, "service_role").execute(
        "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
        "VALUES (%s, 'platform_revenue', 'credit', 1000)",
        (event_id,),
    )
    cur = as_role(pg_cur, "authenticated", {"sub": admin_id, "role": "authenticated"})
    cur.execute("SELECT event_id FROM financial_event_entries WHERE event_id = %s", (event_id,))
    assert cur.fetchall() == []


def test_non_admin_authenticated_cannot_select(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    as_role(pg_cur, "service_role").execute(
        "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
        "VALUES (%s, 'platform_revenue', 'credit', 1000)",
        (event_id,),
    )
    cur = as_role(pg_cur, "authenticated", {"sub": user_id, "role": "authenticated"})
    cur.execute("SELECT event_id FROM financial_event_entries WHERE event_id = %s", (event_id,))
    assert cur.fetchall() == []


def test_anon_cannot_select(pg_cur):
    """`REVOKE ALL ... FROM anon` (286) blocks anon at the grant layer --
    unlike authenticated, which keeps a SELECT grant and is filtered to
    zero rows by RLS instead (see test_non_admin_authenticated_cannot_select
    above). Both deny access; only the layer differs."""
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    as_role(pg_cur, "service_role").execute(
        "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
        "VALUES (%s, 'platform_revenue', 'credit', 1000)",
        (event_id,),
    )
    cur = as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute("SELECT event_id FROM financial_event_entries WHERE event_id = %s", (event_id,))


def test_authenticated_cannot_insert_grant_revoked(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    cur = as_role(pg_cur, "authenticated", {"sub": user_id, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute(
            "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
            "VALUES (%s, 'driver_payable', 'credit', 1000)",
            (event_id,),
        )


def test_anon_cannot_insert_grant_revoked(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    cur = as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute(
            "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
            "VALUES (%s, 'driver_payable', 'credit', 1000)",
            (event_id,),
        )


def test_authenticated_cannot_delete(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    as_role(pg_cur, "service_role").execute(
        "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
        "VALUES (%s, 'driver_payable', 'credit', 1000)",
        (event_id,),
    )
    cur = as_role(pg_cur, "authenticated", {"sub": user_id, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute("DELETE FROM financial_event_entries WHERE event_id = %s", (event_id,))


def test_update_trigger_blocks_even_service_role(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    cur = as_role(pg_cur, "service_role")
    cur.execute(
        "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
        "VALUES (%s, 'driver_payable', 'credit', 1000)",
        (event_id,),
    )
    with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
        cur.execute(
            "UPDATE financial_event_entries SET amount_cents = 2000 WHERE event_id = %s",
            (event_id,),
        )


def test_cascade_delete_from_parent_event_removes_entries(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    cur = as_role(pg_cur, "service_role")
    cur.execute(
        "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
        "VALUES (%s, 'driver_payable', 'credit', 1000)",
        (event_id,),
    )
    cur.execute("SELECT set_config('spinr.financial_events.allow_delete', 'true', false)")
    cur.execute("DELETE FROM financial_events WHERE id = %s", (event_id,))
    cur.execute("SELECT set_config('spinr.financial_events.allow_delete', 'false', false)")
    cur.execute("SELECT event_id FROM financial_event_entries WHERE event_id = %s", (event_id,))
    assert cur.fetchall() == []


def test_account_check_constraint_rejects_invalid_value(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    cur = as_role(pg_cur, "service_role")
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
            "VALUES (%s, 'not_a_real_account', 'credit', 1000)",
            (event_id,),
        )


def test_side_check_constraint_rejects_invalid_value(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    cur = as_role(pg_cur, "service_role")
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
            "VALUES (%s, 'driver_payable', 'sideways', 1000)",
            (event_id,),
        )


def test_amount_cents_must_be_positive(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    cur = as_role(pg_cur, "service_role")
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
            "VALUES (%s, 'driver_payable', 'credit', -1)",
            (event_id,),
        )


def test_unbalanced_view_flags_imbalanced_event(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    event_id = _seed_financial_event(pg_cur, user_id)
    cur = as_role(pg_cur, "service_role")
    cur.execute(
        "INSERT INTO financial_event_entries (event_id, account, side, amount_cents) "
        "VALUES (%s, 'driver_payable', 'debit', 1000)",
        (event_id,),
    )
    cur.execute(
        "SELECT imbalance_cents FROM financial_event_entries_unbalanced WHERE event_id = %s",
        (event_id,),
    )
    assert cur.fetchall() == [(1000,)]


def test_unbalanced_view_denied_to_authenticated(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    cur = as_role(pg_cur, "authenticated", {"sub": user_id, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute("SELECT * FROM financial_event_entries_unbalanced")


# --------------------------------------------------------------------------
# reconciliation_discrepancies
# --------------------------------------------------------------------------


def test_recon_service_role_bypasses_all(pg_cur):
    disc_id = _uuid()
    cur = as_role(pg_cur, "service_role")
    _seed_discrepancy(cur, disc_id)
    cur.execute("SELECT id FROM reconciliation_discrepancies WHERE id = %s", (disc_id,))
    assert cur.fetchall() == [(disc_id,)]


def test_recon_admin_authenticated_cannot_select_or_update(pg_cur):
    """ACTION_ITEMS.md C129 / migration 456: recon_admin_only's
    users.role = 'admin' check is the same unreachable pattern as
    financial_event_entries_select above. 456 replaces the FOR ALL policy
    with USING (false) WITH CHECK (false) -- an admin-shaped session can
    neither see nor update a row. The UPDATE affects zero rows rather than
    raising (RLS filters the target row away before the write; there is no
    grant-layer REVOKE on this table to raise InsufficientPrivilege
    instead)."""
    admin_id, disc_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin_id, role="admin")
    as_role(pg_cur, "service_role").execute(
        "INSERT INTO reconciliation_discrepancies "
        "(id, date, stripe_total_cents, db_total_cents, discrepancy_cents) "
        "VALUES (%s, '2026-01-01', 1000, 900, 100)",
        (disc_id,),
    )
    cur = as_role(pg_cur, "authenticated", {"sub": admin_id, "role": "authenticated"})
    cur.execute("SELECT id FROM reconciliation_discrepancies WHERE id = %s", (disc_id,))
    assert cur.fetchall() == []
    cur.execute(
        "UPDATE reconciliation_discrepancies SET status = 'resolved', resolved_by = %s WHERE id = %s",
        (admin_id, disc_id),
    )
    assert cur.rowcount == 0
    as_role(pg_cur, "service_role").execute("SELECT status FROM reconciliation_discrepancies WHERE id = %s", (disc_id,))
    assert pg_cur.fetchall() == [("open",)]


def test_recon_non_admin_authenticated_cannot_select(pg_cur):
    user_id, disc_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    as_role(pg_cur, "service_role").execute(
        "INSERT INTO reconciliation_discrepancies "
        "(id, date, stripe_total_cents, db_total_cents, discrepancy_cents) "
        "VALUES (%s, '2026-01-01', 1000, 900, 100)",
        (disc_id,),
    )
    cur = as_role(pg_cur, "authenticated", {"sub": user_id, "role": "authenticated"})
    cur.execute("SELECT id FROM reconciliation_discrepancies WHERE id = %s", (disc_id,))
    assert cur.fetchall() == []


def test_recon_anon_cannot_select_despite_no_revoke(pg_cur):
    """No REVOKE exists for this table (unlike financial_event_entries/
    subscription_payments) -- confirms RLS alone, via the FOR ALL policy's
    implicit WITH CHECK, is a sufficient gate on its own."""
    disc_id = _uuid()
    as_role(pg_cur, "service_role").execute(
        "INSERT INTO reconciliation_discrepancies "
        "(id, date, stripe_total_cents, db_total_cents, discrepancy_cents) "
        "VALUES (%s, '2026-01-01', 1000, 900, 100)",
        (disc_id,),
    )
    cur = as_role(pg_cur, "anon", None)
    cur.execute("SELECT id FROM reconciliation_discrepancies WHERE id = %s", (disc_id,))
    assert cur.fetchall() == []


def test_recon_non_admin_authenticated_cannot_insert(pg_cur):
    user_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id)
    cur = as_role(pg_cur, "authenticated", {"sub": user_id, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute(
            "INSERT INTO reconciliation_discrepancies "
            "(id, date, stripe_total_cents, db_total_cents, discrepancy_cents) "
            "VALUES (%s, '2026-02-02', 1000, 900, 100)",
            (_uuid(),),
        )


def test_recon_status_check_constraint_rejects_invalid_value(pg_cur):
    cur = as_role(pg_cur, "service_role")
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "INSERT INTO reconciliation_discrepancies "
            "(id, date, stripe_total_cents, db_total_cents, discrepancy_cents, status) "
            "VALUES (%s, '2026-01-01', 1000, 900, 100, 'not_a_real_status')",
            (_uuid(),),
        )


def test_recon_unique_date_constraint_blocks_duplicate(pg_cur):
    cur = as_role(pg_cur, "service_role")
    _seed_discrepancy(cur, _uuid(), date_val="2026-03-03")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        _seed_discrepancy(cur, _uuid(), date_val="2026-03-03")


def test_recon_resolved_by_references_real_user(pg_cur):
    disc_id = _uuid()
    cur = as_role(pg_cur, "service_role")
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cur.execute(
            "INSERT INTO reconciliation_discrepancies "
            "(id, date, stripe_total_cents, db_total_cents, discrepancy_cents, resolved_by) "
            "VALUES (%s, '2026-01-01', 1000, 900, 100, %s)",
            (disc_id, _uuid()),
        )


# --------------------------------------------------------------------------
# subscription_payments
# --------------------------------------------------------------------------


def test_sub_service_role_bypasses_all(pg_cur):
    user_id, driver_id, payment_id = _uuid(), _uuid(), _uuid()
    cur = as_role(pg_cur, "service_role")
    _seed_user(cur, user_id, role="driver")
    _seed_driver(cur, driver_id, user_id)
    _seed_subscription_payment(cur, payment_id, driver_id)
    cur.execute("SELECT id FROM subscription_payments WHERE id = %s", (payment_id,))
    assert cur.fetchall() == [(payment_id,)]


def test_sub_driver_can_select_own_payments(pg_cur):
    user_id, driver_id, payment_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id, role="driver")
    _seed_driver(pg_cur, driver_id, user_id)
    as_role(pg_cur, "service_role").execute(
        "INSERT INTO subscription_payments (id, driver_id, amount, billing_reason) VALUES (%s, %s, 19.99, 'one_off')",
        (payment_id, driver_id),
    )
    cur = as_role(pg_cur, "authenticated", {"sub": user_id, "role": "authenticated"})
    cur.execute("SELECT id FROM subscription_payments WHERE id = %s", (payment_id,))
    assert cur.fetchall() == [(payment_id,)]


def test_sub_driver_cannot_select_other_drivers_payments(pg_cur):
    owner_user, owner_driver = _uuid(), _uuid()
    other_user, other_driver = _uuid(), _uuid()
    payment_id = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, owner_user, role="driver")
    _seed_driver(pg_cur, owner_driver, owner_user)
    _seed_user(pg_cur, other_user, role="driver")
    _seed_driver(pg_cur, other_driver, other_user)
    as_role(pg_cur, "service_role").execute(
        "INSERT INTO subscription_payments (id, driver_id, amount, billing_reason) VALUES (%s, %s, 19.99, 'one_off')",
        (payment_id, owner_driver),
    )
    cur = as_role(pg_cur, "authenticated", {"sub": other_user, "role": "authenticated"})
    cur.execute("SELECT id FROM subscription_payments WHERE id = %s", (payment_id,))
    assert cur.fetchall() == []


def test_sub_admin_has_no_special_select_access(pg_cur):
    """Distinct from financial_event_entries/reconciliation_discrepancies:
    this table's own migration comment says an admin read via the
    authenticated role sees zero rows BY DESIGN -- reproduced directly,
    not inferred, matching the same standard C118 used last round."""
    driver_user, driver_id, admin_id, payment_id = _uuid(), _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, driver_user, role="driver")
    _seed_driver(pg_cur, driver_id, driver_user)
    _seed_user(pg_cur, admin_id, role="admin")
    as_role(pg_cur, "service_role").execute(
        "INSERT INTO subscription_payments (id, driver_id, amount, billing_reason) VALUES (%s, %s, 19.99, 'one_off')",
        (payment_id, driver_id),
    )
    cur = as_role(pg_cur, "authenticated", {"sub": admin_id, "role": "authenticated"})
    cur.execute("SELECT id FROM subscription_payments WHERE id = %s", (payment_id,))
    assert cur.fetchall() == []


def test_sub_anon_cannot_select(pg_cur):
    """`REVOKE ALL ... FROM anon` (151) blocks anon at the grant layer --
    same distinction from the RLS-filtered authenticated-role denials above
    as financial_event_entries' test_anon_cannot_select."""
    user_id, driver_id, payment_id = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id, role="driver")
    _seed_driver(pg_cur, driver_id, user_id)
    as_role(pg_cur, "service_role").execute(
        "INSERT INTO subscription_payments (id, driver_id, amount, billing_reason) VALUES (%s, %s, 19.99, 'one_off')",
        (payment_id, driver_id),
    )
    cur = as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute("SELECT id FROM subscription_payments WHERE id = %s", (payment_id,))


def test_sub_authenticated_cannot_insert_grant_revoked(pg_cur):
    user_id, driver_id = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, user_id, role="driver")
    _seed_driver(pg_cur, driver_id, user_id)
    cur = as_role(pg_cur, "authenticated", {"sub": user_id, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute(
            "INSERT INTO subscription_payments (id, driver_id, amount, billing_reason) "
            "VALUES (%s, %s, 19.99, 'one_off')",
            (_uuid(), driver_id),
        )


def test_sub_anon_cannot_insert_grant_revoked(pg_cur):
    cur = as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute(
            "INSERT INTO subscription_payments (id, driver_id, amount, billing_reason) "
            "VALUES (%s, %s, 19.99, 'one_off')",
            (_uuid(), _uuid()),
        )


def test_sub_unique_stripe_invoice_id_dedupes(pg_cur):
    user_id, driver_id = _uuid(), _uuid()
    cur = as_role(pg_cur, "service_role")
    _seed_user(cur, user_id, role="driver")
    _seed_driver(cur, driver_id, user_id)
    cur.execute(
        "INSERT INTO subscription_payments (id, driver_id, amount, billing_reason, stripe_invoice_id) "
        "VALUES (%s, %s, 19.99, 'subscription_cycle', 'in_dup_1')",
        (_uuid(), driver_id),
    )
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute(
            "INSERT INTO subscription_payments (id, driver_id, amount, billing_reason, stripe_invoice_id) "
            "VALUES (%s, %s, 19.99, 'subscription_cycle', 'in_dup_1')",
            (_uuid(), driver_id),
        )


def test_sub_immutability_trigger_blocks_service_role_update(pg_cur):
    """ACTION_ITEMS.md C129 / migration 456: subscription_payments_no_mutate
    now enforces the table's own "Append-only ledger" COMMENT ON TABLE claim
    at the trigger level -- RLS bypass (service_role) is not trigger bypass,
    matching financial_event_entries_no_update's precedent."""
    user_id, driver_id, payment_id = _uuid(), _uuid(), _uuid()
    cur = as_role(pg_cur, "service_role")
    _seed_user(cur, user_id, role="driver")
    _seed_driver(cur, driver_id, user_id)
    _seed_subscription_payment(cur, payment_id, driver_id)
    with pytest.raises(psycopg2.errors.RaiseException):
        cur.execute("UPDATE subscription_payments SET amount = 999.99 WHERE id = %s", (payment_id,))


def test_sub_immutability_trigger_blocks_service_role_delete(pg_cur):
    """Unlike financial_event_entries_no_update (UPDATE-only, to avoid
    breaking its parent's ON DELETE CASCADE), subscription_payments has no
    FK/cascade relationship to any parent row -- so this trigger blocks
    DELETE too, making the table genuinely append-only end to end."""
    user_id, driver_id, payment_id = _uuid(), _uuid(), _uuid()
    cur = as_role(pg_cur, "service_role")
    _seed_user(cur, user_id, role="driver")
    _seed_driver(cur, driver_id, user_id)
    _seed_subscription_payment(cur, payment_id, driver_id)
    with pytest.raises(psycopg2.errors.RaiseException):
        cur.execute("DELETE FROM subscription_payments WHERE id = %s", (payment_id,))
