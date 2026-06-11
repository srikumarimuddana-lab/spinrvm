"""Migration 141 — AI chat retention (Step J) regression checks.

CI has no Postgres, so this pins the SQL contract textually: the re-forked
purge_pii_retention keeps every pre-existing step (A–I) and counter from
migration 129 verbatim-in-spirit, adds Step J for ai_messages /
ai_conversations at the 90d chat window, reports the new counters in the
JSONB result, and keeps the SECURITY DEFINER + grant lockdown intact.
"""

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_141 = (MIGRATIONS / "141_retention_purge_ai_messages.sql").read_text()
SQL_129 = (MIGRATIONS / "129_ride_routes_pickup_road_polyline.sql").read_text()


class TestStepJ:
    def test_deletes_ai_messages_at_chat_age(self):
        assert "DELETE FROM ai_messages" in SQL_141
        assert SQL_141.count("c_chat_age") >= 4  # Step D pair + Step J pairs

    def test_deletes_only_empty_old_conversations(self):
        assert "DELETE FROM ai_conversations" in SQL_141
        assert "NOT EXISTS" in SQL_141

    def test_counters_reported_in_result(self):
        assert "'ai_messages_deleted',        v_ai_msgs_deleted" in SQL_141
        assert "'ai_conversations_deleted',   v_ai_convs_deleted" in SQL_141

    def test_dry_run_branch_counts_without_mutating(self):
        step_j = SQL_141.split("Step J")[1]
        assert "ELSE" in step_j
        assert "SELECT COUNT(*) INTO v_ai_msgs_deleted" in step_j


class TestReForkIntegrity:
    def test_every_129_step_survives(self):
        for step in ["Step A", "Step B", "Step C", "Step D", "Step E", "Step F", "Step G", "Step H", "Step I"]:
            assert step in SQL_141, f"{step} dropped in re-fork"

    def test_every_129_result_key_survives(self):
        for key in re.findall(r"'(\w+)',\s+v_\w+", SQL_129):
            assert f"'{key}'" in SQL_141, f"result key {key} dropped in re-fork"

    def test_security_definer_and_grants_intact(self):
        assert "SECURITY DEFINER" in SQL_141
        assert "SET search_path = public, pg_temp" in SQL_141
        assert "REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM PUBLIC, anon, authenticated" in SQL_141
        assert "GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role" in SQL_141

    def test_rollback_plan_documented(self):
        header = SQL_141.split("CREATE OR REPLACE")[0]
        assert "Rollback" in header
        assert "129" in header

    def test_audit_insert_preserved(self):
        assert "'pii_retention_purge'" in SQL_141
        assert "'system:retention_purge'" in SQL_141
