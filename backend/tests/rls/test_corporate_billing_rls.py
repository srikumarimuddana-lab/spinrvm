"""
DB-role-level RLS coverage for the corporate billing/wallet tables --
ACTION_ITEMS.md C49, picked as the highest-value remaining gap per
CLAUDE.md's own priority signal (corporate wallet/billing tables move real
money via `corporate_wallet_apply_delta` and carry corporate-member PII).

`corporate_accounts` (migrations 05 create, 17 FK-guard + RLS): one `FOR
ALL TO authenticated` policy, `users.role = 'admin'` exactly. No REVOKE was
ever applied to this table, so anon/non-admin authenticated are denied
purely by RLS (SELECT/UPDATE/DELETE return zero rows silently; INSERT
raises, since there's no existing row for a zero-policy INSERT to filter).

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
money-mutation tables 142's own header comment calls out by name); the
remaining four of the nine (`corporate_policies`, `corporate_allowed_domains`,
`ride_payment_sources`, `corporate_policy_evaluations`) share the identical
admin-read/no-write shape with no member-read-own wrinkle and are left for a
future round (see conftest.py's coverage-scope note).

Real, unfixed gap found writing this file: `corporate_accounts`'s own admin
policy (migration 17) was never included in migration 142's admin-check fix
-- it still checks `users.role = 'admin'` exactly, excluding `super_admin`,
while the five sibling tables tested here explicitly grant `super_admin` the
same access as `admin`. See test_super_admin_role_cannot_select_corporate_account.
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


def _seed_chain(cur, member_user_id: str | None = None) -> dict:
    """Seeds one company -> wallet -> (wallet txn) and one company -> member
    -> (allowance, allowance request), returning the FK ids plus a
    table -> its-own-seeded-row-id map for the parametrized tests below."""
    company_id, wallet_id, txn_id = _uuid(), _uuid(), _uuid()
    member_id, allowance_id, request_id = _uuid(), _uuid(), _uuid()
    _seed_company(cur, company_id)
    _seed_wallet(cur, wallet_id, company_id)
    _seed_wallet_txn(cur, txn_id, wallet_id)
    _seed_member(cur, member_id, company_id, member_user_id)
    _seed_allowance(cur, allowance_id, member_id)
    _seed_allowance_request(cur, request_id, member_id)
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
        },
    }


_INSERT_FN = {
    "corporate_wallets": lambda cur, ids: _seed_wallet(cur, _uuid(), ids["company_id"]),
    "corporate_wallet_transactions": lambda cur, ids: _seed_wallet_txn(cur, _uuid(), ids["wallet_id"]),
    "corporate_members": lambda cur, ids: _seed_member(cur, _uuid(), ids["company_id"]),
    "corporate_member_allowances": lambda cur, ids: _seed_allowance(cur, _uuid(), ids["member_id"]),
    "corporate_allowance_requests": lambda cur, ids: _seed_allowance_request(cur, _uuid(), ids["member_id"]),
}


# ── corporate_wallets / corporate_wallet_transactions / corporate_members /
#    corporate_member_allowances / corporate_allowance_requests ───────────


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_admin_can_select(pg_cur, table):
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute(f"SELECT id FROM {table} WHERE id = %s", (ids["row_id"][table],))
    assert [r[0] for r in pg_cur.fetchall()] == [ids["row_id"][table]]


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_super_admin_can_select(pg_cur, table):
    """migration 142's fix explicitly includes super_admin for these nine
    tables (unlike corporate_accounts -- see module docstring)."""
    super_admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, super_admin, role="super_admin")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": super_admin, "role": "authenticated"})
    pg_cur.execute(f"SELECT id FROM {table} WHERE id = %s", (ids["row_id"][table],))
    assert [r[0] for r in pg_cur.fetchall()] == [ids["row_id"][table]]


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
    """Even an admin cannot insert -- 142 replaced the FOR ALL policy with
    SELECT-only, and separately revoked the INSERT grant itself."""
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
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
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        pg_cur.execute(f"UPDATE {table} SET id = id WHERE id = %s", (ids["row_id"][table],))


@pytest.mark.parametrize("table", _ADMIN_ONLY_MONEY_TABLES)
def test_authenticated_cannot_delete(pg_cur, table):
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    ids = _seed_chain(pg_cur)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
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


def test_admin_can_select_corporate_account(pg_cur):
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [account_id]


def test_super_admin_role_cannot_select_corporate_account(pg_cur):
    """Real, unfixed gap found writing this file: migration 142 fixed the
    identical 'role = admin only, excludes super_admin' bug (present in
    migration 27's original FOR ALL policies) on the nine corporate_* money
    tables above, but corporate_accounts's own admin policy (migration 17)
    was never included in that fix -- it still checks users.role = 'admin'
    exactly. Confirmed by grepping every migration touching
    corporate_accounts for a later fix: none exists."""
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
    """No REVOKE was ever applied to corporate_accounts (unlike the nine
    tables above) -- anon still holds the table-level SELECT grant, so this
    is a clean RLS-deny (empty result), not a privilege error. The policy is
    also scoped `TO authenticated` only, so anon has zero applicable
    policies regardless of role."""
    as_role(pg_cur, None)
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert pg_cur.fetchall() == []


def test_admin_can_insert_update_delete_corporate_account(pg_cur):
    """FOR ALL with no explicit WITH CHECK reuses the USING clause for
    INSERT/UPDATE too -- the admin check doesn't reference the row being
    written, so it passes uniformly for any row."""
    admin = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, admin, role="admin")
    as_role(pg_cur, "authenticated", {"sub": admin, "role": "authenticated"})
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    assert pg_cur.rowcount == 1
    pg_cur.execute("UPDATE corporate_accounts SET name = 'Renamed Co' WHERE id = %s", (account_id,))
    assert pg_cur.rowcount == 1
    pg_cur.execute("DELETE FROM corporate_accounts WHERE id = %s", (account_id,))
    assert pg_cur.rowcount == 1


def test_non_admin_authenticated_cannot_write_corporate_account(pg_cur):
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider, role="rider")
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "authenticated", {"sub": rider, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_company(pg_cur, _uuid())
    pg_cur.execute("UPDATE corporate_accounts SET name = 'Hacked' WHERE id = %s", (account_id,))
    assert pg_cur.rowcount == 0
    pg_cur.execute("DELETE FROM corporate_accounts WHERE id = %s", (account_id,))
    assert pg_cur.rowcount == 0


def test_anon_cannot_write_corporate_account(pg_cur):
    as_role(pg_cur, None)
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_company(pg_cur, _uuid())
    pg_cur.execute("UPDATE corporate_accounts SET name = 'Hacked' WHERE id = %s", (account_id,))
    assert pg_cur.rowcount == 0
    pg_cur.execute("DELETE FROM corporate_accounts WHERE id = %s", (account_id,))
    assert pg_cur.rowcount == 0


def test_service_role_bypasses_corporate_accounts(pg_cur):
    as_role(pg_cur, None)
    account_id = _uuid()
    _seed_company(pg_cur, account_id)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT id FROM corporate_accounts WHERE id = %s", (account_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [account_id]
