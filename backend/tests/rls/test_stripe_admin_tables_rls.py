"""
DB-role-level RLS coverage for `stripe_disputes` (migration 88) and
`stripe_orphan_refunds` (migration 254) -- ACTION_ITEMS.md C49, picked
alongside the corporate billing tables (test_corporate_billing_rls.py) as
the other CLAUDE.md-flagged priority: Stripe-related payment tables.

Both tables ship exactly one policy each -- an admin-only `FOR SELECT` --
and no INSERT/UPDATE/DELETE policy at all, so writes are entirely
service-role-only regardless of any JWT claim (the migrations' own header
comments say as much: "admin-only table; service role bypasses"). Neither
carries a table-level REVOKE, so anon/authenticated keep their harness
baseline grant; RLS alone is what denies them.

Mechanism note (why every test below passes a real, non-empty claims dict
even for the `anon` Postgres role): unlike every other admin-role-check
already covered in this suite (`financial_events`, and this round's
`corporate_*` tables), these two policies read
`current_setting('request.jwt.claims', true)::json->>'role'` directly,
without the `nullif(current_setting(...), '')` guard the `auth.uid()`/
`auth.role()` shim functions use (see conftest.py's `_AUTH_SHIM_SQL`). This
suite's `as_role(cur, role, None)` convention sets that GUC to an empty
string (not NULL) to represent "no JWT" -- and `''::json` is a genuine
Postgres syntax error, not NULL. Every *other* anon/no-claims test in this
suite is safe from this because auth.uid()/auth.role() guard against it;
these two policies don't. This is a real, narrow gap found while writing
this file (a truly anonymous PostgREST request in production most likely
never sets this GUC to `''` at all -- PostgREST leaves it unset for a
request with no Authorization header, which reads back as NULL, not '',
and NULL::json is NULL, not an error -- so this is unconfirmed against real
PostgREST behavior, not asserted as a production bug). Rather than encode
an assertion this harness cannot verify by running against a real Postgres
in this sandbox, every SELECT-path test below uses a well-formed non-admin
claims dict (e.g. `{"role": "rider"}`) for the denied path instead of the
suite's usual bare `None` for anon, sidestepping the cast entirely. INSERT
denial is unaffected either way (see below) and uses `None` freely.
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


def _seed_dispute(cur, dispute_id: str) -> None:
    cur.execute(
        "INSERT INTO stripe_disputes (id, stripe_dispute_id) VALUES (%s, %s)",
        (dispute_id, f"dp_{dispute_id[:8]}"),
    )


def _seed_orphan_refund(cur, refund_id: str) -> None:
    cur.execute(
        "INSERT INTO stripe_orphan_refunds (id, stripe_charge_id) VALUES (%s, %s)",
        (refund_id, f"ch_{refund_id[:8]}"),
    )


# ── stripe_disputes ─────────────────────────────────────────────────────


def test_admin_role_claim_can_select_dispute(pg_cur):
    as_role(pg_cur, None)
    dispute_id = _uuid()
    _seed_dispute(pg_cur, dispute_id)
    as_role(pg_cur, "authenticated", {"role": "admin"})
    pg_cur.execute("SELECT id FROM stripe_disputes WHERE id = %s", (dispute_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [dispute_id]


def test_non_admin_role_claim_cannot_select_dispute(pg_cur):
    as_role(pg_cur, None)
    dispute_id = _uuid()
    _seed_dispute(pg_cur, dispute_id)
    as_role(pg_cur, "authenticated", {"role": "rider"})
    pg_cur.execute("SELECT id FROM stripe_disputes WHERE id = %s", (dispute_id,))
    assert pg_cur.fetchall() == []


def test_anon_with_non_admin_role_claim_cannot_select_dispute(pg_cur):
    """Uses a well-formed non-admin claims dict rather than the suite's
    usual bare anon/None -- see module docstring's mechanism note."""
    as_role(pg_cur, None)
    dispute_id = _uuid()
    _seed_dispute(pg_cur, dispute_id)
    as_role(pg_cur, "anon", {"role": "rider"})
    pg_cur.execute("SELECT id FROM stripe_disputes WHERE id = %s", (dispute_id,))
    assert pg_cur.fetchall() == []


def test_admin_role_claim_still_cannot_insert_dispute(pg_cur):
    """No INSERT policy exists at all -- even a claimed admin role cannot
    write through PostgREST; this table is genuinely service-role-only, as
    the migration's own header comment says."""
    as_role(pg_cur, "authenticated", {"role": "admin"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_dispute(pg_cur, _uuid())


def test_anon_cannot_insert_dispute(pg_cur):
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_dispute(pg_cur, _uuid())


def test_admin_role_claim_cannot_update_or_delete_dispute(pg_cur):
    """No UPDATE/DELETE policy exists either -- unlike a missing INSERT
    policy (which raises), a missing UPDATE/DELETE policy just means zero
    rows are visible to the operation: it silently affects nothing."""
    as_role(pg_cur, None)
    dispute_id = _uuid()
    _seed_dispute(pg_cur, dispute_id)
    as_role(pg_cur, "authenticated", {"role": "admin"})
    pg_cur.execute("UPDATE stripe_disputes SET status = 'won' WHERE id = %s", (dispute_id,))
    assert pg_cur.rowcount == 0
    pg_cur.execute("DELETE FROM stripe_disputes WHERE id = %s", (dispute_id,))
    assert pg_cur.rowcount == 0


def test_service_role_bypasses_stripe_disputes(pg_cur):
    """The real production write path (Stripe webhook handler writing
    disputes via the service-role client) is unaffected."""
    as_role(pg_cur, "service_role", None)
    dispute_id = _uuid()
    _seed_dispute(pg_cur, dispute_id)
    assert pg_cur.rowcount == 1
    pg_cur.execute("SELECT id FROM stripe_disputes WHERE id = %s", (dispute_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [dispute_id]
    pg_cur.execute("UPDATE stripe_disputes SET status = 'won' WHERE id = %s", (dispute_id,))
    assert pg_cur.rowcount == 1
    pg_cur.execute("DELETE FROM stripe_disputes WHERE id = %s", (dispute_id,))
    assert pg_cur.rowcount == 1


# ── stripe_orphan_refunds ───────────────────────────────────────────────


@pytest.mark.parametrize("role_claim", ["admin", "super_admin", "finance"])
def test_admin_finance_role_claims_can_select_orphan_refund(pg_cur, role_claim):
    as_role(pg_cur, None)
    refund_id = _uuid()
    _seed_orphan_refund(pg_cur, refund_id)
    as_role(pg_cur, "authenticated", {"role": role_claim})
    pg_cur.execute("SELECT id FROM stripe_orphan_refunds WHERE id = %s", (refund_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [refund_id]


def test_non_admin_role_claim_cannot_select_orphan_refund(pg_cur):
    as_role(pg_cur, None)
    refund_id = _uuid()
    _seed_orphan_refund(pg_cur, refund_id)
    as_role(pg_cur, "authenticated", {"role": "rider"})
    pg_cur.execute("SELECT id FROM stripe_orphan_refunds WHERE id = %s", (refund_id,))
    assert pg_cur.fetchall() == []


def test_anon_with_non_admin_role_claim_cannot_select_orphan_refund(pg_cur):
    as_role(pg_cur, None)
    refund_id = _uuid()
    _seed_orphan_refund(pg_cur, refund_id)
    as_role(pg_cur, "anon", {"role": "rider"})
    pg_cur.execute("SELECT id FROM stripe_orphan_refunds WHERE id = %s", (refund_id,))
    assert pg_cur.fetchall() == []


def test_finance_role_claim_still_cannot_insert_orphan_refund(pg_cur):
    as_role(pg_cur, "authenticated", {"role": "finance"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_orphan_refund(pg_cur, _uuid())


def test_anon_cannot_insert_orphan_refund(pg_cur):
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_orphan_refund(pg_cur, _uuid())


def test_finance_role_claim_cannot_update_or_delete_orphan_refund(pg_cur):
    as_role(pg_cur, None)
    refund_id = _uuid()
    _seed_orphan_refund(pg_cur, refund_id)
    as_role(pg_cur, "authenticated", {"role": "finance"})
    pg_cur.execute("UPDATE stripe_orphan_refunds SET reason = 'resolved' WHERE id = %s", (refund_id,))
    assert pg_cur.rowcount == 0
    pg_cur.execute("DELETE FROM stripe_orphan_refunds WHERE id = %s", (refund_id,))
    assert pg_cur.rowcount == 0


def test_service_role_bypasses_stripe_orphan_refunds(pg_cur):
    as_role(pg_cur, "service_role", None)
    refund_id = _uuid()
    _seed_orphan_refund(pg_cur, refund_id)
    assert pg_cur.rowcount == 1
    pg_cur.execute("SELECT id FROM stripe_orphan_refunds WHERE id = %s", (refund_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [refund_id]
    pg_cur.execute("UPDATE stripe_orphan_refunds SET reason = 'resolved' WHERE id = %s", (refund_id,))
    assert pg_cur.rowcount == 1
    pg_cur.execute("DELETE FROM stripe_orphan_refunds WHERE id = %s", (refund_id,))
    assert pg_cur.rowcount == 1
