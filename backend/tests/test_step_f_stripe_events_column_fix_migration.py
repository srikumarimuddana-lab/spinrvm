"""Regression checks for migration 324: purge_pii_retention() Step F used
stripe_events.created_at, but that column has never existed -- migration 22
created stripe_events with `received_at`/`processed_at` instead. Same bug
class as migration 187 (driver_location_history Step C) and migration 323
(ride_messages Step D, applied moments before this one, same session) --
found live 2026-08-17 via a purge_pii_retention(true) dry-run call
immediately after applying 323.

CI has no Postgres, so these checks pin the SQL contract textually -- same
convention as test_step_d_ride_messages_column_fix_migration.py (323),
test_step_h_driver_rides_guard_migration.py (321),
test_pipeda_30day_profile_scrub_migration.py (296), and
test_deletion_hard_delete_migration.py (216).
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_324 = (MIGRATIONS / "324_purge_pii_retention_step_f_stripe_events_column_fix.sql").read_text()

PURGE_FN = SQL_324.split("CREATE OR REPLACE FUNCTION purge_pii_retention")[1]
STEP_F = PURGE_FN.split("-- Step F")[1].split("-- Step G")[0]

# Step F appears once with both the live-delete branch and the dry-run COUNT
# branch inside the same IF/ELSE block -- both must reference the correct
# column, since the dry-run path exists specifically so an operator can
# preview what the live path would do.
LIVE_BRANCH, DRY_BRANCH = STEP_F.split("ELSE", 1)


class TestStepFUsesReceivedAtNotCreatedAt:
    def test_live_branch_filters_on_received_at(self):
        assert "received_at < v_started_at - c_stripe_event_age" in LIVE_BRANCH

    def test_dry_run_branch_filters_on_received_at(self):
        assert "received_at < v_started_at - c_stripe_event_age" in DRY_BRANCH

    def test_neither_branch_references_the_nonexistent_column(self):
        # Check the actual filter clause, not comment prose -- both branches'
        # own header comments legitimately mention "created_at" by name to
        # explain what was wrong.
        for branch in (LIVE_BRANCH, DRY_BRANCH):
            assert "stripe_events" in branch
            assert "created_at < v_started_at - c_stripe_event_age" not in branch


class TestStepDFixCarriedForwardFromMigration323:
    """324 re-forks the function from 323 -- Step D's fix must not regress."""

    def test_step_d_still_uses_timestamp(self):
        step_d = PURGE_FN.split("-- Step D")[1].split("-- Step E")[0]
        assert '"timestamp" < v_started_at - c_chat_age' in step_d
        assert "created_at < v_started_at - c_chat_age" not in step_d


class TestStepHFixCarriedForwardFromMigration321:
    """324 re-forks the function from 323 (which re-forked from 321) --
    A38's Step H driver-ride guard must not regress."""

    def test_step_h_still_guards_on_driver_rides(self):
        step_h = PURGE_FN.split("-- Step H")[1].split("-- Step I")[0]
        assert "EXISTS (SELECT 1 FROM rides r2        WHERE r2.driver_id = d.id)" in step_h
