"""
DB-role-level RLS coverage for `lost_and_found` and `lost_and_found_messages`
(ACTION_ITEMS.md C49 remaining scope).

`lost_and_found` (migrations 69/69a): reporters see and insert their own
items; status changes are service-role-only (no UPDATE/DELETE policy for
authenticated/anon).

`lost_and_found_messages` (migration 115): the rider or driver on a case can
read/insert messages on it; messages are append-only (no UPDATE/DELETE
policy for anyone but service_role).

Note: migration 69's `id`/`ride_id`/`reporter_id` columns are declared UUID
in the merged file, but the current schema's `users.id`/`rides.id` are TEXT
-- a real drift (see conftest.py's `pg_conn` fixture comment and the new
ACTION_ITEMS.md finding). This harness patches the type at apply time so
the *policies* (which compare via auth.uid()::text regardless of the
underlying column type) can be exercised; the drift itself is unaffected by
anything in this test file.
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


def _seed_lost_and_found(cur, item_id: str, reporter_id: str, driver_id: str | None = None) -> None:
    cur.execute(
        """
        INSERT INTO lost_and_found (id, reporter_id, driver_id, item_description)
        VALUES (%s, %s, %s, 'Blue backpack')
        """,
        (item_id, reporter_id, driver_id),
    )


# ── lost_and_found ──────────────────────────────────────────────────────


def test_reporter_can_select_own_item(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    item_id = _uuid()
    _seed_lost_and_found(pg_cur, item_id, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM lost_and_found WHERE id = %s", (item_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [item_id]


def test_authenticated_cannot_select_another_reporters_item(pg_cur):
    me, other = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, other)
    item_id = _uuid()
    _seed_lost_and_found(pg_cur, item_id, other)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM lost_and_found WHERE id = %s", (item_id,))
    assert pg_cur.fetchall() == []


def test_anon_cannot_select_any_item(pg_cur):
    other = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, other)
    item_id = _uuid()
    _seed_lost_and_found(pg_cur, item_id, other)
    as_role(pg_cur, "anon", None)
    pg_cur.execute("SELECT id FROM lost_and_found WHERE id = %s", (item_id,))
    assert pg_cur.fetchall() == []


def test_reporter_can_insert_own_item(pg_cur):
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    _seed_lost_and_found(pg_cur, _uuid(), me)
    assert pg_cur.rowcount == 1


def test_authenticated_cannot_insert_item_for_another_reporter(pg_cur):
    """lost_and_found_insert's WITH CHECK requires auth.uid()::text =
    reporter_id -- an authenticated user must not be able to forge a report
    attributed to someone else."""
    me, victim = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    _seed_user(pg_cur, victim)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_lost_and_found(pg_cur, _uuid(), victim)


def test_anon_cannot_insert_item(pg_cur):
    rider = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, rider)
    as_role(pg_cur, "anon", None)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_lost_and_found(pg_cur, _uuid(), rider)


def test_authenticated_cannot_update_own_item_status(pg_cur):
    """No UPDATE policy exists at all -- status transitions
    (reported -> found -> returned) are service-role-only by design."""
    me = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, me)
    item_id = _uuid()
    _seed_lost_and_found(pg_cur, item_id, me)
    as_role(pg_cur, "authenticated", {"sub": me, "role": "authenticated"})
    pg_cur.execute("UPDATE lost_and_found SET status = 'found' WHERE id = %s", (item_id,))
    assert pg_cur.rowcount == 0
    as_role(pg_cur, None)
    pg_cur.execute("SELECT status FROM lost_and_found WHERE id = %s", (item_id,))
    assert pg_cur.fetchone()[0] == "reported"


def test_service_role_bypasses_rls_on_lost_and_found(pg_cur):
    other = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, other)
    item_id = _uuid()
    _seed_lost_and_found(pg_cur, item_id, other)
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT id FROM lost_and_found WHERE id = %s", (item_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [item_id]


# ── lost_and_found_messages ─────────────────────────────────────────────


def _seed_message(cur, msg_id: str, case_id: str, sender_id: str | None, sender_role: str) -> None:
    cur.execute(
        """
        INSERT INTO lost_and_found_messages (id, lost_and_found_id, sender_id, sender_role, message)
        VALUES (%s, %s, %s, %s, 'hello')
        """,
        (msg_id, case_id, sender_id, sender_role),
    )


def test_reporter_on_case_can_select_messages(pg_cur):
    reporter = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    case_id = _uuid()
    _seed_lost_and_found(pg_cur, case_id, reporter)
    msg_id = _uuid()
    _seed_message(pg_cur, msg_id, case_id, reporter, "rider")
    as_role(pg_cur, "authenticated", {"sub": reporter, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM lost_and_found_messages WHERE id = %s", (msg_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [msg_id]


def test_driver_on_case_can_select_messages(pg_cur):
    reporter, driver = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    _seed_user(pg_cur, driver)
    case_id = _uuid()
    _seed_lost_and_found(pg_cur, case_id, reporter, driver_id=driver)
    msg_id = _uuid()
    _seed_message(pg_cur, msg_id, case_id, reporter, "rider")
    as_role(pg_cur, "authenticated", {"sub": driver, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM lost_and_found_messages WHERE id = %s", (msg_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [msg_id]


def test_unrelated_user_cannot_select_messages(pg_cur):
    reporter, stranger = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    _seed_user(pg_cur, stranger)
    case_id = _uuid()
    _seed_lost_and_found(pg_cur, case_id, reporter)
    msg_id = _uuid()
    _seed_message(pg_cur, msg_id, case_id, reporter, "rider")
    as_role(pg_cur, "authenticated", {"sub": stranger, "role": "authenticated"})
    pg_cur.execute("SELECT id FROM lost_and_found_messages WHERE id = %s", (msg_id,))
    assert pg_cur.fetchall() == []


def test_reporter_on_case_can_insert_own_message(pg_cur):
    reporter = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    case_id = _uuid()
    _seed_lost_and_found(pg_cur, case_id, reporter)
    as_role(pg_cur, "authenticated", {"sub": reporter, "role": "authenticated"})
    _seed_message(pg_cur, _uuid(), case_id, reporter, "rider")
    assert pg_cur.rowcount == 1


def test_driver_on_case_can_insert_own_message(pg_cur):
    """Migration 412 regression pin: lfm_insert had the identical
    driver-visibility bug as lfm_select (same EXISTS-against-RLS-protected-
    lost_and_found pattern) -- a driver could never insert a message on
    their own case either."""
    reporter, driver = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    _seed_user(pg_cur, driver)
    case_id = _uuid()
    _seed_lost_and_found(pg_cur, case_id, reporter, driver_id=driver)
    as_role(pg_cur, "authenticated", {"sub": driver, "role": "authenticated"})
    _seed_message(pg_cur, _uuid(), case_id, driver, "driver")
    assert pg_cur.rowcount == 1


def test_authenticated_cannot_insert_message_as_another_sender(pg_cur):
    """lfm_insert's WITH CHECK requires auth.uid()::text = sender_id -- a
    user on the case must not be able to forge a message from someone
    else."""
    reporter, impersonated = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    _seed_user(pg_cur, impersonated)
    case_id = _uuid()
    _seed_lost_and_found(pg_cur, case_id, reporter)
    as_role(pg_cur, "authenticated", {"sub": reporter, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_message(pg_cur, _uuid(), case_id, impersonated, "rider")


def test_authenticated_cannot_insert_system_message(pg_cur):
    """lfm_insert explicitly excludes sender_role = 'system' even when
    sender_id matches the caller -- system messages are backend-only."""
    reporter = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    case_id = _uuid()
    _seed_lost_and_found(pg_cur, case_id, reporter)
    as_role(pg_cur, "authenticated", {"sub": reporter, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_message(pg_cur, _uuid(), case_id, reporter, "system")


def test_non_party_cannot_insert_message_even_as_self(pg_cur):
    """The EXISTS subquery requires the sender to be the reporter or driver
    on THIS case -- being a valid, authenticated user isn't enough."""
    reporter, outsider = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    _seed_user(pg_cur, outsider)
    case_id = _uuid()
    _seed_lost_and_found(pg_cur, case_id, reporter)
    as_role(pg_cur, "authenticated", {"sub": outsider, "role": "authenticated"})
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _seed_message(pg_cur, _uuid(), case_id, outsider, "rider")


def test_authenticated_cannot_update_a_message(pg_cur):
    """Messages are append-only -- no UPDATE policy for any client role."""
    reporter = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    case_id = _uuid()
    _seed_lost_and_found(pg_cur, case_id, reporter)
    msg_id = _uuid()
    _seed_message(pg_cur, msg_id, case_id, reporter, "rider")
    as_role(pg_cur, "authenticated", {"sub": reporter, "role": "authenticated"})
    pg_cur.execute("UPDATE lost_and_found_messages SET message = 'edited' WHERE id = %s", (msg_id,))
    assert pg_cur.rowcount == 0


def test_authenticated_cannot_delete_a_message(pg_cur):
    reporter = _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    case_id = _uuid()
    _seed_lost_and_found(pg_cur, case_id, reporter)
    msg_id = _uuid()
    _seed_message(pg_cur, msg_id, case_id, reporter, "rider")
    as_role(pg_cur, "authenticated", {"sub": reporter, "role": "authenticated"})
    pg_cur.execute("DELETE FROM lost_and_found_messages WHERE id = %s", (msg_id,))
    assert pg_cur.rowcount == 0


def test_service_role_bypasses_rls_on_lost_and_found_messages(pg_cur):
    reporter, stranger = _uuid(), _uuid()
    as_role(pg_cur, None)
    _seed_user(pg_cur, reporter)
    _seed_user(pg_cur, stranger)
    case_id = _uuid()
    _seed_lost_and_found(pg_cur, case_id, reporter)
    msg_id = _uuid()
    _seed_message(pg_cur, msg_id, case_id, reporter, "rider")
    as_role(pg_cur, "service_role", None)
    pg_cur.execute("SELECT id FROM lost_and_found_messages WHERE id = %s", (msg_id,))
    assert [r[0] for r in pg_cur.fetchall()] == [msg_id]
