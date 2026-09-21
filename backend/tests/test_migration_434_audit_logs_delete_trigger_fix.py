"""Regression checks for migration 434 (ACTION_ITEMS.md C112): migration 57's
audit_logs_no_mutate trigger (BEFORE UPDATE OR DELETE, unconditional)
conflicted with migration 56's audit_logs_no_delete trigger (BEFORE DELETE,
flag-gated) -- Postgres fires both, so the flag-gated exception migration 56
built for purge_pii_retention()'s Step G never actually worked once 57 was
also applied. Separately, migration 335 (the live purge_pii_retention() body
before this fix) never carried forward the PERFORM set_config(...) call or
exception handler Step G needs, so the DELETE branch was dead code -- a
silent no-op, not a crash, but restoring the flag-set call alone (without
also either narrowing 57's trigger or adding the exception handler) would
have turned that dormant bug into an unhandled-exception rollback of every
other retention step in the same function call.

Migration 434 fixes both together: narrows 57's audit_logs_no_mutate trigger
to UPDATE only, and re-forks purge_pii_retention() from 335 with Step G's
missing set_config/exception-handler restored.

CI has no Postgres for the main suite, so these checks pin the SQL contract
textually -- same convention as test_step_a_planned_route_polyline_purge_migration.py
(335), test_step_f_stripe_events_column_fix_migration.py (324),
test_step_d_ride_messages_column_fix_migration.py (323),
test_step_h_driver_rides_guard_migration.py (321). Live-Postgres behavioral
proof (the trigger actually not firing, the DELETE actually succeeding) is
in backend/tests/rls/test_audit_and_insurance_correction_rls.py, which
self-skips without a real Postgres per CLAUDE.md's RLS tier convention.
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_434 = (MIGRATIONS / "434_fix_audit_logs_delete_trigger_conflict.sql").read_text()

TRIGGER_SECTION = SQL_434.split("-- 1. Narrow migration 57")[1].split("-- 2. Re-fork")[0]
PURGE_FN = SQL_434.split("CREATE OR REPLACE FUNCTION purge_pii_retention")[1]
STEP_G = PURGE_FN.split("-- Step G")[1].split("-- Step H")[0]


class TestTriggerNarrowedToUpdateOnly:
    def test_drops_and_recreates_audit_logs_no_mutate(self):
        assert "DROP TRIGGER IF EXISTS audit_logs_no_mutate ON audit_logs" in TRIGGER_SECTION
        assert "CREATE TRIGGER audit_logs_no_mutate" in TRIGGER_SECTION

    def test_new_trigger_is_update_only_not_update_or_delete(self):
        create_stmt = TRIGGER_SECTION.split("CREATE TRIGGER audit_logs_no_mutate")[1]
        # Must fire on UPDATE...
        assert "BEFORE UPDATE ON audit_logs" in create_stmt
        # ...and must NOT still say "UPDATE OR DELETE" (the pre-fix shape).
        assert "UPDATE OR DELETE" not in create_stmt

    def test_reuses_the_existing_immutability_function_unchanged(self):
        # No new function definition here -- migration 57's _audit_logs_immutable
        # is reused as-is, only the trigger's firing event narrows.
        assert "_audit_logs_immutable" in TRIGGER_SECTION
        assert "CREATE OR REPLACE FUNCTION _audit_logs_immutable" not in TRIGGER_SECTION


class TestStepGRestoresTheMissingFlagAndExceptionHandler:
    def test_live_branch_sets_the_allow_delete_flag_before_deleting(self):
        live_branch = STEP_G.split("ELSE")[0]
        assert "PERFORM set_config('spinr.audit_logs.allow_delete', 'true', true)" in live_branch
        # The set_config call must precede the DELETE, not follow it.
        assert live_branch.index("set_config('spinr.audit_logs.allow_delete', 'true'") < live_branch.index(
            "DELETE FROM audit_logs"
        )

    def test_delete_is_wrapped_in_exception_handler_matching_sibling_steps(self):
        live_branch = STEP_G.split("ELSE")[0]
        assert "EXCEPTION WHEN OTHERS THEN" in live_branch
        assert "RAISE;" in live_branch
        # The flag must be cleared on both the error path and the success path.
        assert live_branch.count("set_config('spinr.audit_logs.allow_delete', 'false', true)") == 2

    def test_dry_run_branch_is_unchanged_count_only(self):
        dry_branch = STEP_G.split("ELSE")[1]
        assert "SELECT COUNT(*) INTO v_audit_deleted" in dry_branch
        assert "set_config" not in dry_branch

    def test_retention_window_and_column_unchanged(self):
        assert "created_at < v_started_at - c_audit_log_age" in STEP_G


class TestOtherStepsCarriedForwardFromMigration335Unregressed:
    """434 re-forks the function from 335 -- every prior step's fix must
    survive verbatim, same guard pattern 335's own test file uses."""

    def test_step_a_still_clears_planned_route_polyline(self):
        step_a = PURGE_FN.split("-- Step A")[1].split("-- Step B")[0]
        assert "planned_route_polyline = '[]'::jsonb" in step_a.split("ELSE")[0]

    def test_step_d_still_uses_timestamp(self):
        step_d = PURGE_FN.split("-- Step D")[1].split("-- Step E")[0]
        assert '"timestamp" < v_started_at - c_chat_age' in step_d
        assert "created_at < v_started_at - c_chat_age" not in step_d

    def test_step_f_still_uses_received_at(self):
        step_f = PURGE_FN.split("-- Step F")[1].split("-- Step G")[0]
        assert "received_at < v_started_at - c_stripe_event_age" in step_f
        assert "created_at < v_started_at - c_stripe_event_age" not in step_f

    def test_step_h_still_guards_on_driver_rides(self):
        step_h = PURGE_FN.split("-- Step H")[1].split("-- Step I")[0]
        assert "EXISTS (SELECT 1 FROM rides r2        WHERE r2.driver_id = d.id)" in step_h

    def test_step_m_compliance_export_events_gate_unregressed(self):
        step_m = PURGE_FN.split("-- Step M")[1].split("-- Step N")[0]
        assert "PERFORM set_config('spinr.compliance_export_events.allow_delete', 'true', true)" in step_m


class TestMigrationOverrideAnnotationPresent:
    def test_override_annotation_present(self):
        # CI's migration-check.yml CREATE-OR-REPLACE conflict check requires
        # this exact annotation for an intentional function re-fork.
        assert "migration-override-ok:" in SQL_434.lower()


class TestGrantsAndCommentCarriedForward:
    def test_execute_grants_unchanged(self):
        assert "REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM PUBLIC, anon, authenticated" in SQL_434
        assert "GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role" in SQL_434

    def test_function_comment_documents_the_434_fix(self):
        assert "434" in SQL_434.split("COMMENT ON FUNCTION purge_pii_retention")[1]
