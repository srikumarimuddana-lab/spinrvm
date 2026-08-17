"""Regression checks for migration 323: purge_pii_retention() Step D used
ride_messages.created_at, but that column has never existed -- migration 98
created ride_messages with `timestamp` instead. Postgres does not validate
column references inside a plpgsql function body until execution, so the
daily retention-purge loop failed at Step D every tick, aborting Steps E-N
too (found live 2026-08-17 via a purge_pii_retention(true) dry-run call).

CI has no Postgres, so these checks pin the SQL contract textually -- same
convention as test_step_h_driver_rides_guard_migration.py (321),
test_pipeda_30day_profile_scrub_migration.py (296), and
test_deletion_hard_delete_migration.py (216).
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_323 = (MIGRATIONS / "323_purge_pii_retention_step_d_ride_messages_column_fix.sql").read_text()

PURGE_FN = SQL_323.split("CREATE OR REPLACE FUNCTION purge_pii_retention")[1]
STEP_D = PURGE_FN.split("-- Step D")[1].split("-- Step E")[0]

# Step D appears once with both the live-delete branch and the dry-run COUNT
# branch inside the same IF/ELSE block -- both must reference the correct
# column, since the dry-run path exists specifically so an operator can
# preview what the live path would do.
LIVE_BRANCH, DRY_BRANCH = STEP_D.split("ELSE", 1)


class TestStepDUsesTimestampNotCreatedAt:
    def test_live_branch_filters_on_timestamp(self):
        assert '"timestamp" < v_started_at - c_chat_age' in LIVE_BRANCH

    def test_dry_run_branch_filters_on_timestamp(self):
        assert '"timestamp" < v_started_at - c_chat_age' in DRY_BRANCH

    def test_neither_branch_references_the_nonexistent_column(self):
        # Check the actual filter clause, not comment prose -- both branches'
        # own header comments legitimately mention "created_at" by name to
        # explain what was wrong.
        for branch in (LIVE_BRANCH, DRY_BRANCH):
            assert "ride_messages" in branch
            assert "created_at < v_started_at" not in branch

    def test_index_added_for_the_new_filter_column(self):
        assert "CREATE INDEX IF NOT EXISTS idx_ride_messages_timestamp_purge" in SQL_323
        assert 'ON ride_messages ("timestamp")' in SQL_323
