"""
DB-role-level RLS coverage for `referral_payouts` (migration 171) and
`auto_payout_batches` (migration 314) -- ACTION_ITEMS.md C49 remaining
scope.

`referral_payouts`: clients may read only their own rows (as referrer OR
referee); every client write (insert/update/delete) is explicitly denied by
`USING (false)`/`WITH CHECK (false)` policies -- only the backend's
service_role (which bypasses RLS) writes this ledger.

`auto_payout_batches`: fully service-role-scoped -- all four policies
(select/insert/update/delete) are `TO service_role` only, so anon/
authenticated have no applicable policy for any action and RLS default-
denies everything, the same "service-role-only" shape as migration 26's
deny-all tables.
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


def _seed_referral_payout(cur, payout_id: str, referrer_id: str | None, referee_id: str | None) -> None:
    cur.execute(
        """
        INSERT INTO referral_payouts (id, referrer_user_id, referee_user_id, kind, referrer_reward, referee_reward)
        VALUES (%s, %s, %s, 'rider', 5.00, 5.00)
        """,
        (payout_id, referrer_id, referee_id),
    )


# ── referral_payouts ────────────────────────────────────────────────────


def test_referrer_can_select_own_payout(pg_cur):
    referrer, referee = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, referrer)
    _seed_user(pg_cur, referee)
    payout_id = _uuid()
    _seed_referral_payout(pg_cur, payout_id, referrer, referee)
    as_role(pg_cur, "authenticated", {"sub": referrer, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM referral_payouts WHERE id = %s", (payout_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [payout_id]


def test_referee_can_select_own_payout(pg_cur):
    """The other half of the select_own policy's OR clause."""
    referrer, referee = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, referrer)
    _seed_user(pg_cur, referee)
    payout_id = _uuid()
    _seed_referral_payout(pg_cur, payout_id, referrer, referee)
    as_role(pg_cur, "authenticated", {"sub": referee, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM referral_payouts WHERE id = %s", (payout_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [payout_id]


def test_unrelated_user_cannot_select_someone_elses_payout(pg_cur):
    referrer, referee, stranger = _uuid(), _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, referrer)
    _seed_user(pg_cur, referee)
    _seed_user(pg_cur, stranger)
    payout_id = _uuid()
    _seed_referral_payout(pg_cur, payout_id, referrer, referee)
    as_role(pg_cur, "authenticated", {"sub": stranger, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM referral_payouts WHERE id = %s", (payout_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_any_payout(pg_cur):
    referrer, referee = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, referrer)
    _seed_user(pg_cur, referee)
    payout_id = _uuid()
    _seed_referral_payout(pg_cur, payout_id, referrer, referee)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM referral_payouts WHERE id = %s", (payout_id,))
    assert pg_cur.fetchall() == []


def test_authenticated_cannot_insert_payout(pg_cur):
    """referral_payouts_no_insert: WITH CHECK (false), unconditional --
    even the referrer/referee themselves cannot self-insert a payout row."""
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_referral_payout(pg_cur, _uuid(), me, me)


def test_anon_cannot_insert_payout(pg_cur):
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_referral_payout(pg_cur, _uuid(), None, None)


def test_authenticated_cannot_update_payout(pg_cur):
    referrer, referee = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, referrer)
    _seed_user(pg_cur, referee)
    payout_id = _uuid()
    _seed_referral_payout(pg_cur, payout_id, referrer, referee)
    as_role(pg_cur, "authenticated", {"sub": referrer, "role": "authenticated"})
    pg_cur.execute("UPDATE referral_payouts SET status = 'paid' WHERE id = %s", (payout_id,))
    assert pg_cur.rowcount == 0


def test_authenticated_cannot_delete_payout(pg_cur):
    referrer, referee = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, referrer)
    _seed_user(pg_cur, referee)
    payout_id = _uuid()
    _seed_referral_payout(pg_cur, payout_id, referrer, referee)
    as_role(pg_cur, "authenticated", {"sub": referrer, "role": "authenticated"})
    pg_cur.execute("DELETE FROM referral_payouts WHERE id = %s", (payout_id,))
    assert pg_cur.rowcount == 0


def test_service_role_bypasses_rls_on_referral_payouts(pg_cur):
    """Confirms the payout background loop's actual production access path
    (service-role key, core/lifespan.py) is unaffected."""
    referrer, referee = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, referrer)
    _seed_user(pg_cur, referee)
    as_role(pg_cur, "service_role", None)
    payout_id = _uuid()
    _seed_referral_payout(pg_cur, payout_id, referrer, referee)
    assert pg_cur.rowcount == 1
    pg_cur.execute("SELECT id FROM referral_payouts WHERE id = %s", (payout_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [payout_id]


# ── auto_payout_batches ─────────────────────────────────────────────────


def _seed_batch(cur, batch_id: str, week_key: str) -> None:
    cur.execute(
        "INSERT INTO auto_payout_batches (id, week_key) VALUES (%s, %s)",
        (batch_id, week_key),
    )


def test_authenticated_cannot_select_any_batch(pg_cur):
    """No policy targets authenticated at all -- all four policies are
    `TO service_role` only, so RLS default-denies every action."""
    as_role(pg_cur, "service_role", None)
    batch_id = _uuid()
    _seed_batch(pg_cur, batch_id, "2026-W37")
    as_role(pg_cur, "authenticated", {"sub": _uuid(), "role": "authenticated"})
    pg_cur.execute("SELECT id FROM auto_payout_batches WHERE id = %s", (batch_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_any_batch(pg_cur):
    as_role(pg_cur, "service_role", None)
    batch_id = _uuid()
    _seed_batch(pg_cur, batch_id, "2026-W37")
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM auto_payout_batches WHERE id = %s", (batch_id,))
    assert pg_cur.fetchall() == []


def test_authenticated_cannot_insert_batch(pg_cur):
    as_role(pg_cur, "authenticated", {"sub": _uuid(), "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_batch(pg_cur, _uuid(), "2026-W38")


def test_anon_cannot_insert_batch(pg_cur):
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_batch(pg_cur, _uuid(), "2026-W38")


def test_service_role_can_select_insert_update_delete_batch(pg_cur):
    """Confirms the auto-payout loop's actual production access path
    (service-role key) can exercise all four actions this table needs."""
    as_role(pg_cur, "service_role", None)
    batch_id = _uuid()
    _seed_batch(pg_cur, batch_id, "2026-W39")
    assert pg_cur.rowcount == 1

    pg_cur.execute("SELECT id FROM auto_payout_batches WHERE id = %s", (batch_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [batch_id]

    pg_cur.execute("UPDATE auto_payout_batches SET status = 'completed' WHERE id = %s", (batch_id,))
    assert pg_cur.rowcount == 1

    pg_cur.execute("DELETE FROM auto_payout_batches WHERE id = %s", (batch_id,))
    assert pg_cur.rowcount == 1
