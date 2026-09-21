"""
DB-role-level RLS coverage for the corporate billing/wallet tables --
ACTION_ITEMS.md C49, picked as the highest-value remaining gap per
CLAUDE.md's own priority signal (corporate wallet/billing tables move real
money via `corporate_wallet_apply_delta` and carry corporate-member PII).

`corporate_accounts` (migrations 05 create, 17 FK-guard + RLS, 416 admin-policy
fix, 430 admin-unreachable fix): migration 416 replaced migration 17's original
`FOR ALL TO authenticated` (`users.role = 'admin'` exactly) with a SELECT-only
`admin` or `super_admin` policy, mirroring migration 142's fix on the nine
sibling tables below, plus a table-level REVOKE of INSERT/UPDATE/DELETE/TRUNCATE
from `authenticated` and REVOKE ALL from `anon` — so, like the nine sibling
tables, every one of anon/non-admin-authenticated/admin-authenticated's write
attempts now raises a grant-level `InsufficientPrivilege`, not a
silently-filtered zero-row RLS denial. Reads are anon/non-admin-denied by RLS;
admin/super_admin reads are *also* denied as of migration 430 (see below) --
production data cleanup means no `users` row can hold either value anymore,
but the policy itself is now `USING (false)` regardless.

`corporate_wallets` / `corporate_wallet_transactions` / `corporate_members`
/ `corporate_member_allowances` / `corporate_allowance_requests` (migration
27 create, migration 142 lockdown): migration 27 originally shipped a `FOR
ALL TO authenticated` admin policy on all nine corporate tables it created
(role = 'admin' only, no WITH CHECK -- any authenticated JWT could otherwise
forge a wallet balance). Migration 142 replaced that on these nine tables
with a SELECT-only "admin or super_admin" policy plus a table-level REVOKE
of INSERT/UPDATE/DELETE/TRUNCATE from authenticated and REVOKE ALL from
anon -- so unlike `corporate_accounts`, anon has no table-level privilege
left at all here (SELECT/INSERT/UPDATE/DELETE all raise, not just RLS-deny).
Three of the nine (`corporate_members`, `corporate_member_allowances`,
`corporate_allowance_requests`) also get a "member read own" SELECT policy
so a company member can see their own membership/allowance/request rows
without being an admin. This file covers those three plus
`corporate_wallets`/`corporate_wallet_transactions` (the two direct
money-mutation tables 142's own header comment calls out by name), plus --
picked up in a later round -- the remaining four of the nine:
`corporate_policies`, `corporate_allowed_domains`, `corporate_policy_evaluations`
(added to `_ADMIN_ONLY_MONEY_TABLES` below; confirmed by reading migration
142's actual `DO $$ ... FOREACH t IN ARRAY [...]` loop that all nine tables,
these three included, get the identical DROP-FOR-ALL/CREATE-SELECT-ADMIN-READ/
REVOKE-ALL-anon/REVOKE-write-authenticated treatment, with no member-read-own
wrinkle for any of the three) and `ride_payment_sources` (same policy shape,
but its own dedicated test block below rather than the shared parametrize --
its primary key column is `ride_id`, not `id`, so the generic
`SELECT/UPDATE/DELETE ... WHERE id = %s` the parametrized tests use doesn't
apply verbatim).

Gap found writing this file, fixed before merge: `corporate_accounts`'s own
admin policy (migration 17) was never included in migration 142's
admin-check fix -- it still checked `users.role = 'admin'` exactly, excluding
`super_admin`. Migration 416 (PR #5307) closed this the same day, applying
migration 142's exact fix pattern to this one table.

ACTION_ITEMS.md C107 / migration 430: a 2026-09-13 production data cleanup plus
migration 256's `chk_users_role_not_admin` CHECK constraint already closed the
original C107 finding by ensuring `users.role` can no longer hold
'admin'/'super_admin' at all -- that fix is a data/schema guarantee, not a
policy rewrite, and stays exactly as it was (migration 256 is deliberately
*not* applied in this file's shared fixture; see conftest.py's comment on why).
Migration 430 is additional, layered hardening on top of that: it replaces the
"Admin read <table>" policies themselves with an explicit `USING (false)` on
all nine tables here plus `corporate_accounts`, so the denial no longer
depends on the CHECK constraint holding -- even if a future migration ever
relaxed it, or a role value slipped in some other way, these policies would
still deny. `test_admin_can_select`/`test_super_admin_can_select` are rewritten
below as `test_admin_cannot_select`/`test_super_admin_cannot_select`: they
still seed `role="admin"`/`"super_admin"` (that seed step itself succeeds --
this fixture doesn't enforce migration 256) and assert the SELECT now returns
zero rows, pinning migration 430's actual behavior directly rather than only
inferring it from `test_non_admin_stranger_cannot_select`.
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

_ADMIN_ONLY_MONEY_TABLES = (
    "corporate_wallets",
    "corporate_wallet_transactions",
    "corporate_members",
    "corporate_member_allowances",
    "corporate_allowance_requests",
    "corporate_policies",
    "corporate_allowed_domains",
    "corporate_policy_evaluations",
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _seed_user(cur, user_id: str, role: str = "rider") -> None:
    cur.execute(
        "INSERT INTO users (id, phone, role) VALUES (%s, %s, %s)",
        (user_id, f"+1306555{user_id[-4:]}", role),
    )


def _seed_company(cur, company_id: str, name: str = "Acme Co") -> None:
    cur.execute("INSERT INTO corporate_accounts (id, name) VALUES (%s, %s)", (company_id, name))


def _seed_wallet(cur, wallet_id: str, company_id: str) -> None:
    cur.execute(
        "INSERT INTO corporate_wallets (id, company_id, balance) VALUES (%s, %s, 100.00)",
        (wallet_id, company_id),
    )


def _seed_wallet_txn(cur, txn_id: str, wallet_id: str) -> None:
    cur.execute(
        """
        INSERT INTO corporate_wallet_transactions (id, wallet_id, scope, type, amount, balance_after)
        VALUES (%s, %s, 'master', 'topup', 10.00, 110.00)
        """,
        (txn_id, wallet_id),
    )


def _seed_member(cur, member_id: str, company_id: str, user_id: str | None = None) -> None:
    cur.execute(
        "INSERT INTO corporate_members (id, company_id, user_id, status) VALUES (%s, %s, %s, 'active')",
        (member_id, company_id, user_id),
    )


def _seed_allowance(cur, allowance_id: str, member_id: str) -> None:
    cur.execute(
        "INSERT INTO corporate_member_allowances (id, member_id, type, amount) VALUES (%s, %s, 'fixed_recurring', 50.00)",
        (allowance_id, member_id),
    )


def _seed_allowance_request(cur, request_id: str, member_id: str) -> None:
    cur.execute(
        "INSERT INTO corporate_allowance_requests (id, member_id, amount, reason) VALUES (%s, %s, 20.00, 'need more rides')",
        (request_id, member_id),
    )


def _seed_policy(cur, policy_id: str, company_id: str) -> None:
    cur.execute(
        "INSERT INTO corporate_policies (id, company_id) VALUES (%s, %s)",
        (policy_id, company_id),
    )


def _seed_allowed_domain(cur, domain_id: str, company_id: str) -> None:
    cur.execute(
        "INSERT INTO corporate_allowed_domains (id, company_id, domain) VALUES (%s, %s, %s)",
        (domain_id, company_id, f"{domain_id[:8]}.example.com"),
    )


def _seed_policy_evaluation(cur, eval_id: str, company_id: str) -> None:
    cur.execute(
        "INSERT INTO corporate_policy_evaluations (id, ride_id, company_id, result, phase) "
        "VALUES (%s, %s, %s, 'pass', 'booking')",
        (eval_id, _uuid(), company_id),
    )


def _seed_ride_payment_source(cur, ride_id: str, company_id: str) -> None:
    cur.execute(
        "INSERT INTO ride_payment_sources (ride_id, source_type, company_id, policy_check_result) "
        "VALUES (%s, 'company_allowance', %s, 'pass')",
        (ride_id, company_id),
    )


def _seed_chain(cur, member_user_id: str | None = None) -> dict:
    """Seeds one company -> wallet -> (wallet txn) and one company -> member
    -> (allowance, allowance request), plus one row each in the three
    remaining admin-only-shaped tables (policy, allowed domain, policy
    evaluation), returning the FK ids plus a table -> its-own-seeded-row-id
    map for the parametrized tests below. `ride_payment_sources` is seeded
    separately (see `_seed_ride_payment_source`/its own dedicated test
    block) since its primary key is `ride_id`, not `id`."""
    company_id, wallet_id, txn_id = _uuid(), _uuid(), _uuid()
    member_id, allowance_id, request_id = _uuid(), _uuid(), _uuid()
    policy_id, domain_id, eval_id = _uuid(), _uuid(), _uuid()
    _seed_company(cur, company_id)
    _seed_wallet(cur, wallet_id, company_id)
    _seed_wallet_txn(cur, txn_id, wallet_id)
    _seed_member(cur, member_id, company_id, member_user_id)
    _seed_allowance(cur, allowance_id, member_id)
    _seed_allowance_request(cur, request_id, member_id)
    _seed_policy(cur, policy_id, company_id)
    _seed_allowed_domain(cur, domain_id, company_id)
    _seed_policy_evaluation(cur, eval_id, company_id)
    return {
        "company_id": company_id,
        "wallet_id": wallet_id,
        "member_id": member_id,
        "row_id": {
            "corporate_wallets": wallet_id,
            "corporate_wallet_transactions": txn_id,
            "corporate_members": member_id,
            "corporate_member_allowances": allowance_id,
            "corporate_allowance_requests": request_id,
            "corporate_policies": policy_id,
            "corporate_allowed_domains": domain_id,
            "corporate_policy_evaluations": eval_id,
        },
    }


def _insert_new_corporate_policy(cur, ids: dict) -> None:
    """corporate_policies.company_id is UNIQUE, and _seed_chain already used
    ids["company_id"] for its own policy row -- reusing it here would hit
    that constraint the moment INSERT privilege was ever (re-)granted,
    turning a future RLS regression into a confusing UniqueViolation
    failure instead of the clear InsufficientPrivilege every other table's
    insert-denial test produces. Seed a fresh company instead so this
    table's failure mode stays unambiguous too."""
    fresh_company_id = _uuid()
    _seed_company(cur, fresh_company_id)
    _seed_policy(cur, _uuid(), fresh_company_id)


_INSERT_FN = {
    "corporate_wallets": lambda cur, ids: _seed_wallet(cur, _uuid(), ids["company_id"]),
    "corporate_wallet_transactions": lambda cur, ids: _seed_wallet_txn(cur, _uuid(), ids["wallet_id"]),
    "corporate_members": lambda cur, ids: _seed_member(cur, _uuid(), ids["company_id"]),
    "corporate_member_allowances": lambda cur, ids: _seed_allowance(cur, _uuid(), ids["member_id"]),
    "corporate_allowance_requests": lambda cur, ids: _seed_allowance_request(cur, _uuid(), ids["member_id"]),
    "corporate_policies": _insert_new_corporate_policy,
    "corporate_allowed_domains": lambda cur, ids: _seed_allowed_domain(cur, _uuid(), ids["company_id"]),
    "corporate_policy_evaluations": lambda cur, ids: _seed_policy_evaluation(cur, _uuid(), ids["company_id"]),
}


# ── corporate_wallets / corporate_wallet_transactions / corporate_members /
#    corporate_member_allowances / corporate_allowance_requests ───────────


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_admin_cannot_select(pg_cur, table):
    """migration 430 (ACTION_ITEMS.md C107): the old "Admin read <table>"
    policy is replaced with an explicit USING (false), so an admin JWT is
    denied exactly like any other non-owning authenticated user now --
    production has no users.role='admin' row left to exploit this with, but
    the policy itself no longer depends on that being true."""
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute(f"SELECT id FROM {table} WHERE id = %s", (ids["row_id"][table],))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_super_admin_cannot_select(pg_cur, table):
    """Same as test_admin_cannot_select, for the super_admin role value --
    migration 430's USING (false) policy makes no distinction between the
    two, unlike the admin-vs-super_admin parity gap migration 142/416 fixed
    for the old, now-replaced policy."""
    super_admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, super_admin, role="super_admin")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": super_admin, "role": "authenticated"})
    pg_cur.execute(f"SELECT id FROM {table} WHERE id = %s", (ids["row_id"][table],))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_non_admin_stranger_cannot_select(pg_cur, table):
    """A non-admin authenticated user who is also not the owning member (the
    default _seed_chain member has no user_id) is denied by both the
    admin-read and the member-read-own policy."""
    stranger = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, stranger, role="rider")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": stranger, "role": "authenticated"})
    pg_cur.execute(f"SELECT id FROM {table} WHERE id = %s", (ids["row_id"][table],))
    assert pg_cur.fetchall() == []


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_anon_cannot_select(pg_cur, table):
    """migration 142's REVOKE ALL FROM anon means this raises -- not a
    silent empty result -- unlike the non-admin-authenticated case above,
    which still holds the table-level SELECT grant and is denied by RLS
    alone."""
    as_role(pg_cur, None)
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute(f"SELECT id FROM {table} WHERE id = %s", (ids["row_id"][table],))


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_authenticated_cannot_insert(pg_cur, table):
    """No authenticated role can insert, admin or not -- 142 replaced the
    FOR ALL policy with SELECT-only, and separately revoked the INSERT grant
    itself at the table-privilege level, independent of role value."""
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _INSERT_FN[table](pg_cur, ids)


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_anon_cannot_insert(pg_cur, table):
    as_role(pg_cur, None)
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _INSERT_FN[table](pg_cur, ids)


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_authenticated_cannot_update(pg_cur, table):
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute(f"UPDATE {table} SET id = id WHERE id = %s", (ids["row_id"][table],))


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_authenticated_cannot_delete(pg_cur, table):
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute(f"DELETE FROM {table} WHERE id = %s", (ids["row_id"][table],))


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_service_role_bypasses(pg_cur, table):
    """Confirms the backend's real access path (service_role, via
    corporate_wallet_apply_delta and the corporate services layer) is
    unaffected by any of the above."""
    as_role(pg_cur, None)
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute(f"SELECT id FROM {table} WHERE id = %s", (ids["row_id"][table],))
    assert [r[0] for r in pg_cur.fetchall()] == [ids["row_id"][table]]


# ── ride_payment_sources (same admin-read/no-write shape as the tables above,
#    but keyed by ride_id rather than id -- migration 27's own primary key
#    choice -- so it can't share the generic id-based parametrized tests) ──


def test_admin_cannot_select_ride_payment_source(pg_cur):
    """migration 430 (ACTION_ITEMS.md C107): ride_payment_sources is one of
    the 11 tables whose admin-read policy is replaced with USING (false)."""
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    ids = _seed_chain(pg_cur)
    ride_id = _uuid()
    _seed_ride_payment_source(pg_cur, ride_id, ids["company_id"])
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT ride_id FROM ride_payment_sources WHERE ride_id = %s", (ride_id,))
    assert pg_cur.fetchall() == []


def test_super_admin_cannot_select_ride_payment_source(pg_cur):
    super_admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, super_admin, role="super_admin")
    ids = _seed_chain(pg_cur)
    ride_id = _uuid()
    _seed_ride_payment_source(pg_cur, ride_id, ids["company_id"])
    as_role(pg_cur, "authenticated", {"sub": super_admin, "role": "authenticated"})
    pg_cur.execute("SELECT ride_id FROM ride_payment_sources WHERE ride_id = %s", (ride_id,))
    assert pg_cur.fetchall() == []


def test_non_admin_stranger_cannot_select_ride_payment_source(pg_cur):
    stranger = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, stranger, role="rider")
    ids = _seed_chain(pg_cur)
    ride_id = _uuid()
    _seed_ride_payment_source(pg_cur, ride_id, ids["company_id"])
    as_role(pg_cur, "authenticated", {"sub": stranger, "role": "authenticated"})
    pg_cur.execute("SELECT ride_id FROM ride_payment_sources WHERE ride_id = %s", (ride_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_ride_payment_source(pg_cur):
    as_role(pg_cur, None)
    ids = _seed_chain(pg_cur)
    ride_id = _uuid()
    _seed_ride_payment_source(pg_cur, ride_id, ids["company_id"])
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("SELECT ride_id FROM ride_payment_sources WHERE ride_id = %s", (ride_id,))


def test_authenticated_cannot_insert_ride_payment_source(pg_cur):
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_ride_payment_source(pg_cur, _uuid(), ids["company_id"])


def test_anon_cannot_insert_ride_payment_source(pg_cur):
    as_role(pg_cur, None)
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_ride_payment_source(pg_cur, _uuid(), ids["company_id"])


def test_authenticated_cannot_update_ride_payment_source(pg_cur):
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    ids = _seed_chain(pg_cur)
    ride_id = _uuid()
    _seed_ride_payment_source(pg_cur, ride_id, ids["company_id"])
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("UPDATE ride_payment_sources SET ride_id = ride_id WHERE ride_id = %s", (ride_id,))


def test_authenticated_cannot_delete_ride_payment_source(pg_cur):
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    ids = _seed_chain(pg_cur)
    ride_id = _uuid()
    _seed_ride_payment_source(pg_cur, ride_id, ids["company_id"])
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("DELETE FROM ride_payment_sources WHERE ride_id = %s", (ride_id,))


def test_service_role_bypasses_ride_payment_source(pg_cur):
    as_role(pg_cur, None)
    ids = _seed_chain(pg_cur)
    ride_id = _uuid()
    _seed_ride_payment_source(pg_cur, ride_id, ids["company_id"])
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT ride_id FROM ride_payment_sources WHERE ride_id = %s", (ride_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [ride_id]


# ── member-read-own (corporate_members / _member_allowances / _allowance_requests) ─


def test_member_can_select_own_membership_row(pg_cur):
    member_user = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, member_user, role="rider")
    ids = _seed_chain(pg_cur, member_user_id=member_user)
    as_role(pg_cur, "authenticated", {"sub": member_user, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM corporate_members WHERE id = %s", (ids["member_id"],))
    assert [r[0] for r in pg_cur.fetchall()] == [ids["member_id"]]


def test_member_can_select_own_allowance_row(pg_cur):
    member_user = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, member_user, role="rider")
    ids = _seed_chain(pg_cur, member_user_id=member_user)
    as_role(pg_cur, "authenticated", {"sub": member_user, "role": "authenticated"})
    pg_cur.execute(
        "SELECT id FROM corporate_member_allowances WHERE id = %s",
        (ids["row_id"]["corporate_member_allowances"],),
    )
    assert [r[0] for r in pg_cur.fetchall()] == [ids["row_id"]["corporate_member_allowances"]]


def test_member_can_select_own_allowance_request_row(pg_cur):
    member_user = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, member_user, role="rider")
    ids = _seed_chain(pg_cur, member_user_id=member_user)
    as_role(pg_cur, "authenticated", {"sub": member_user, "role": "authenticated"})
    pg_cur.execute(
        "SELECT id FROM corporate_allowance_requests WHERE id = %s",
        (ids["row_id"]["corporate_allowance_requests"],),
    )
    assert [r[0] for r in pg_cur.fetchall()] == [ids["row_id"]["corporate_allowance_requests"]]


# ── corporate_accounts ──────────────────────────────────────────────────


def test_admin_cannot_select_corporate_account(pg_cur):
    """migration 430 (ACTION_ITEMS.md C107): corporate_accounts's own
    admin-read policy (migration 416) is replaced with USING (false) too."""
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert pg_cur.fetchall() == []


def test_super_admin_cannot_select_corporate_account(pg_cur):
    super_admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, super_admin, role="super_admin")
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": super_admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert pg_cur.fetchall() == []


def test_non_admin_authenticated_cannot_select_corporate_account(pg_cur):
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_corporate_account(pg_cur):
    """Migration 416 REVOKEd ALL on corporate_accounts from anon (matching
    the nine sibling tables), so anon has no table-level SELECT grant left
    at all -- this is a privilege error now, not a silently-filtered RLS
    deny."""
    as_role(pg_cur, None)
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))


def test_admin_cannot_write_corporate_account(pg_cur):
    """Migration 17's original FOR ALL (no explicit WITH CHECK) reused the
    USING clause for INSERT/UPDATE too, so any admin's write passed
    uniformly for any row -- migration 416 (PR #5307) narrowed the policy to
    SELECT-only and REVOKEd INSERT/UPDATE/DELETE/TRUNCATE from authenticated
    entirely, matching migration 142's nine sibling tables: no role gets RLS
    write access to these tables anymore, only the backend's service-role
    connection (which bypasses RLS/grants) writes them."""
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_company(pg_cur, _uuid())
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("UPDATE corporate_accounts SET name = 'Renamed Co' WHERE id = %s", (account_id,))
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("DELETE FROM corporate_accounts WHERE id = %s", (account_id,))


def test_non_admin_authenticated_cannot_write_corporate_account(pg_cur):
    """Migration 416 REVOKEd INSERT/UPDATE/DELETE/TRUNCATE from authenticated
    entirely, so every write attempt now raises a grant-level privilege
    error immediately, rather than being silently filtered to zero rows by
    RLS."""
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_company(pg_cur, _uuid())
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("UPDATE corporate_accounts SET name = 'Hacked' WHERE id = %s", (account_id,))
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("DELETE FROM corporate_accounts WHERE id = %s", (account_id,))


def test_anon_cannot_write_corporate_account(pg_cur):
    """Migration 416 REVOKEd ALL on corporate_accounts from anon, so every
    write attempt now raises a grant-level privilege error immediately."""
    as_role(pg_cur, None)
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_company(pg_cur, _uuid())
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("UPDATE corporate_accounts SET name = 'Hacked' WHERE id = %s", (account_id,))
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute("DELETE FROM corporate_accounts WHERE id = %s", (account_id,))


def test_service_role_bypasses_corporate_accounts(pg_cur):
    as_role(pg_cur, None)
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [account_id]
