"""Account-deletion retention model regression checks (migration 216).

History: migration 216 switched account deletion to the Uber/Lyft
"attributable retention, NO anonymization" model. It re-forks
purge_pii_retention() VERBATIM from migration 187 (the prior authoritative
definition) and changes ONLY Step H:

  * OLD Step H (187): at the 30-day grace it ANONYMIZED the account —
    phone/email nulled, renamed "Deleted User".
  * NEW Step H (216): NO anonymization. The account is HARD-DELETED only
    once its full regulatory footprint has aged out — riders at 7y; drivers
    only once they have no driver_insurance_periods / payouts / bank_accounts
    (the append-only insurance table is NEVER touched). Runs per-account in a
    subtransaction with an FK-violation handler so a residual RESTRICT
    dependency can never abort the whole daily purge.

CI has no Postgres, so these checks pin the SQL contract textually.
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_216 = (MIGRATIONS / "216_deletion_hard_delete_no_anonymize.sql").read_text()
# Isolate just the new Step H CODE block (not a header-comment mention) so
# assertions target it precisely. These markers appear only in the body.
STEP_H = SQL_216.split("-- Step H (CHANGED")[1].split("-- Step I")[0]


class TestNoAnonymization:
    def test_step_h_does_not_anonymize_the_account(self):
        # None of the old anonymization scrub survives in the new Step H.
        assert "'Deleted'" not in STEP_H
        assert "'User'" not in STEP_H
        assert "first_name" not in STEP_H
        assert "SET phone" not in STEP_H

    def test_module_no_longer_defines_anonymise_helper(self):
        # _anonymise_rides was removed from the deletion handlers entirely.
        users_py = (Path(__file__).resolve().parents[1] / "routes" / "users.py").read_text()
        assert "_anonymise_rides" not in users_py


class TestHardDeleteEligibility:
    def test_step_h_hard_deletes_the_user_row(self):
        assert "DELETE FROM users" in STEP_H

    def test_rider_deletable_dependents_cleared(self):
        # The clearable RESTRICT/owned dependents are removed before the user row.
        for tbl in ("financial_events", "saved_addresses", "support_tickets"):
            assert f"DELETE FROM {tbl}" in STEP_H
        assert "reconciliation_discrepancies" in STEP_H

    def test_driver_footprint_guards_the_delete(self):
        # A driver is only deletable once NONE of these regulatory records remain.
        for tbl in ("driver_insurance_periods", "payouts", "bank_accounts"):
            assert tbl in STEP_H

    def test_never_touches_append_only_insurance_periods(self):
        # Compliance invariant: the insurance-period table is read-only here —
        # only referenced inside EXISTS guards, never deleted or updated.
        assert "DELETE FROM driver_insurance_periods" not in SQL_216
        assert "UPDATE driver_insurance_periods" not in SQL_216

    def test_window_and_no_rides_guard(self):
        assert "deletion_scheduled_at <= v_started_at" in STEP_H
        assert "NOT EXISTS (SELECT 1 FROM rides" in STEP_H


class TestPurgeCannotAbort:
    def test_step_h_is_per_account_subtransaction(self):
        # Per-account BEGIN/EXCEPTION so one bad account can't roll back the purge.
        assert "FOR v_uid IN" in STEP_H
        assert "EXCEPTION WHEN foreign_key_violation THEN" in STEP_H

    def test_toctou_reactivation_recheck(self):
        assert "FOR UPDATE" in STEP_H

    def test_dry_run_branch_counts_without_mutating(self):
        assert "SELECT COUNT(*) INTO v_dsar_purged" in STEP_H

    def test_fk_skip_is_counted_not_silent(self):
        # A caught FK violation increments a counter surfaced in the result,
        # so a blocked account can't sit invisibly (CLAUDE.md: surface loudly).
        assert "v_skipped_fk := v_skipped_fk + 1" in STEP_H
        assert "'dsar_users_skipped_fk'" in SQL_216


class TestReForkIntegrity:
    """216 must preserve every step it inherited from 187 — a re-fork that
    silently drops Step J/K or the 187 received_at fix is a regression."""

    def test_every_prior_step_survives(self):
        for step in [
            "Step A",
            "Step B",
            "Step C",
            "Step D",
            "Step E",
            "Step F",
            "Step G",
            "Step I",
            "Step J",
            "Step K",
        ]:
            assert step in SQL_216, f"{step} dropped by the 216 re-fork"

    def test_187_received_at_fix_preserved(self):
        # Step C must still use received_at (migration 187), not recorded_at.
        assert "received_at < v_started_at - c_loc_history_age" in SQL_216
        assert "recorded_at" not in SQL_216

    def test_steps_j_k_targets_preserved(self):
        assert "DELETE FROM ai_messages" in SQL_216
        assert "DELETE FROM ai_conversations" in SQL_216
        assert "DELETE FROM surge_pricing" in SQL_216

    def test_result_keys_and_grants_preserved(self):
        assert "'dsar_users_purged'" in SQL_216  # key unchanged → no caller change
        assert "'ai_messages_deleted'" in SQL_216
        assert "'surge_pricing_deleted'" in SQL_216
        assert "GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role;" in SQL_216
        assert "SECURITY DEFINER" in SQL_216
