"""Migration 51 (B-P1-7) shape tests — audit_logs lockdown + 7y retention.

The full SQL is exercised in CI integration runs against a throwaway
schema. Here we validate the migration's structural invariants from
text, so a future edit that silently weakens the lockdown (drops the
trigger, restores FOR ALL, forgets the INSERT fix) fails locally.
"""

from __future__ import annotations

from pathlib import Path

import pytest

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "51_audit_logs_lockdown.sql"


@pytest.fixture(scope="module")
def sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_drops_for_all_admin_policy(sql: str):
    assert 'DROP POLICY IF EXISTS "Admin full access audit_logs"' in sql


def test_replaces_with_select_only_admin_read_policy(sql: str):
    assert 'CREATE POLICY "Admin read audit_logs"' in sql
    assert "FOR SELECT" in sql
    assert "TO authenticated" in sql
    assert "users.role IN ('admin', 'super_admin')" in sql


def test_blocks_update_via_trigger(sql: str):
    assert "CREATE OR REPLACE FUNCTION audit_logs_block_update()" in sql
    assert "BEFORE UPDATE ON audit_logs" in sql
    assert "RAISE EXCEPTION" in sql
    assert "append-only" in sql.lower()


def test_revokes_postgrest_surface(sql: str):
    assert "REVOKE ALL ON audit_logs FROM anon" in sql
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON audit_logs FROM authenticated" in sql
    assert "GRANT  SELECT ON audit_logs TO authenticated" in sql or "GRANT SELECT ON audit_logs TO authenticated" in sql


def test_purge_function_uses_production_schema_columns(sql: str):
    """Migration 50's INSERT used migration 08's never-applied columns
    (actor_id, actor_role, resource). Migration 51 must fix this to use
    the actual production schema (action, entity_type, entity_id,
    user_email, details TEXT) — see migration 06 lines 39-47."""
    assert "INSERT INTO audit_logs (id, action, entity_type, entity_id, user_email, details, created_at)" in sql
    # The fixed INSERT must NOT use the broken column names.
    insert_block_start = sql.find("INSERT INTO audit_logs")
    insert_block_end = sql.find(";", insert_block_start)
    insert_block = sql[insert_block_start:insert_block_end]
    assert "actor_id" not in insert_block
    assert "actor_role" not in insert_block
    assert "resource" not in insert_block


def test_adds_audit_logs_7y_purge_step(sql: str):
    assert "c_audit_log_age" in sql
    assert "INTERVAL '7 years'" in sql
    assert "DELETE FROM audit_logs" in sql
    assert "audit_logs_deleted" in sql


def test_purge_function_locked_down(sql: str):
    """SECURITY DEFINER + pinned search_path + service_role-only EXECUTE
    — same posture as migration 50; must not regress on CREATE OR REPLACE."""
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = public, pg_temp" in sql
    assert "REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM PUBLIC, anon, authenticated" in sql
    assert "GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role" in sql


def test_table_comment_records_append_only_contract(sql: str):
    assert "COMMENT ON TABLE audit_logs" in sql
    assert "append-only" in sql.lower()
