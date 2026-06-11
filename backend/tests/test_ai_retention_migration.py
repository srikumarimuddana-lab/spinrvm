"""AI chat retention (Step J) regression checks.

History: this branch introduced Step J (90-day ai_messages/ai_conversations
purge) as migration 141; main's 143_retention_purge_surge_pricing.sql was
forked from it (adding Step K) and supersedes it, so 141 was dropped from
this branch and 143 is now the authoritative purge_pii_retention
definition. CI has no Postgres, so these checks pin the SQL contract
textually: Step J survives in 143 with the 90d chat window, dry-run
support, result counters, and the SECURITY DEFINER + grant lockdown.
"""

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_CURRENT = (MIGRATIONS / "143_retention_purge_surge_pricing.sql").read_text()
SQL_129 = (MIGRATIONS / "129_ride_routes_pickup_road_polyline.sql").read_text()


class TestStepJ:
    def test_deletes_ai_messages_at_chat_age(self):
        assert "DELETE FROM ai_messages" in SQL_CURRENT
        # Step D pair (ride_messages) + Step J pairs share the 90d chat window.
        assert SQL_CURRENT.count("c_chat_age") >= 4

    def test_deletes_only_empty_old_conversations(self):
        assert "DELETE FROM ai_conversations" in SQL_CURRENT
        assert "NOT EXISTS" in SQL_CURRENT

    def test_counters_reported_in_result(self):
        assert "'ai_messages_deleted'" in SQL_CURRENT
        assert "'ai_conversations_deleted'" in SQL_CURRENT

    def test_dry_run_branch_counts_without_mutating(self):
        step_j = SQL_CURRENT.split("Step J")[1]
        assert "ELSE" in step_j
        assert "SELECT COUNT(*) INTO v_ai_msgs_deleted" in step_j


class TestReForkIntegrity:
    def test_every_prior_step_survives(self):
        for step in [
            "Step A",
            "Step B",
            "Step C",
            "Step D",
            "Step E",
            "Step F",
            "Step G",
            "Step H",
            "Step I",
            "Step J",
            "Step K",
        ]:
            assert step in SQL_CURRENT, f"{step} missing from the current re-fork"

    def test_every_129_result_key_survives(self):
        for key in re.findall(r"'(\w+)',\s+v_\w+", SQL_129):
            assert f"'{key}'" in SQL_CURRENT, f"result key {key} dropped in re-fork"

    def test_security_definer_and_grants_intact(self):
        assert "SECURITY DEFINER" in SQL_CURRENT
        assert "SET search_path = public, pg_temp" in SQL_CURRENT
        assert "REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM PUBLIC, anon, authenticated" in SQL_CURRENT
        assert "GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role" in SQL_CURRENT

    def test_audit_insert_preserved(self):
        assert "'pii_retention_purge'" in SQL_CURRENT
        assert "'system:retention_purge'" in SQL_CURRENT


class TestTableMigrationPairing:
    def test_ai_tables_migration_present_and_renumbered(self):
        # Step J references ai_messages/ai_conversations — their creating
        # migration must exist on this branch (renumbered past main's 143).
        sql = (MIGRATIONS / "144_ai_assistant_conversations.sql").read_text()
        assert "CREATE TABLE IF NOT EXISTS public.ai_messages" in sql
        assert "ENABLE ROW LEVEL SECURITY" in sql

    def test_admin_actor_migration_renumbered(self):
        sql = (MIGRATIONS / "145_ai_conversations_admin_actor.sql").read_text()
        assert "ADD COLUMN IF NOT EXISTS admin_actor_id" in sql

    def test_superseded_migration_removed(self):
        assert not (MIGRATIONS / "141_retention_purge_ai_messages.sql").exists()
