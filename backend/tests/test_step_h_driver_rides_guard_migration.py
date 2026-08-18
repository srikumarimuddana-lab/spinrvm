"""A38 regression checks (migration 321): Step H's driver-eligibility guard
now checks `rides.driver_id` in addition to `driver_insurance_periods`/
`payouts`/`bank_accounts`. Before this fix, a driver account with completed
ride history but none of those three other rows would pass Step H's guard
and be hard-deleted at the 7-year mark despite having ride history --
`services/test_account_cleanup_service.py`'s `_blocking_reasons()` already
added this exact check as a stricter replacement tool; this migration closes
the gap in Step H itself.

CI has no Postgres, so these checks pin the SQL contract textually -- same
convention as test_pipeda_30day_profile_scrub_migration.py (296) and
test_deletion_hard_delete_migration.py (216).
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_321 = (MIGRATIONS / "321_purge_pii_retention_step_h_driver_rides_guard.sql").read_text()

PURGE_FN = SQL_321.split("CREATE OR REPLACE FUNCTION purge_pii_retention")[1]
STEP_H = PURGE_FN.split("-- Step H")[1].split("-- Step I")[0]

# Step H appears twice: the live-delete loop's WHERE clause, then the
# dry-run COUNT query in the ELSE branch. Both must carry the fix identically
# -- the dry-run path exists specifically so an operator can preview what the
# live path would do; a fix applied to only one would make dry-run lie.
LIVE_BRANCH, DRY_BRANCH = STEP_H.split("ELSE", 1)


class TestStepHNowGuardsOnDriverRides:
    def test_live_branch_checks_rides_as_driver(self):
        assert "EXISTS (SELECT 1 FROM rides r2        WHERE r2.driver_id = d.id)" in LIVE_BRANCH

    def test_dry_run_branch_checks_rides_as_driver(self):
        assert "EXISTS (SELECT 1 FROM rides r2        WHERE r2.driver_id = d.id)" in DRY_BRANCH

    def test_existing_guards_unchanged_in_both_branches(self):
        for branch in (LIVE_BRANCH, DRY_BRANCH):
            assert "driver_insurance_periods dip WHERE dip.driver_id = d.id" in branch
            assert "payouts p       WHERE p.driver_id = d.id" in branch
            assert "bank_accounts b WHERE b.driver_id = d.id" in branch

    def test_rider_side_guard_unchanged(self):
        # A38 is scoped to the driver-side gap only -- the rider-side check
        # (rides.rider_id) already existed and must not be touched here.
        for branch in (LIVE_BRANCH, DRY_BRANCH):
            assert "rides r WHERE r.rider_id = u.id" in branch

    def test_new_guard_uses_distinct_alias_from_rider_check(self):
        # r (rider-side, outer) and r2 (driver-side, nested) must not
        # collide -- both queries reference `rides` at different
        # correlation scopes in the same statement.
        assert "rides r WHERE r.rider_id" in STEP_H
        assert "rides r2        WHERE r2.driver_id" in STEP_H


class TestStepHElseIsStillHardDelete:
    def test_step_h_hard_delete_still_present_unmodified(self):
        assert "DELETE FROM users" in PURGE_FN
        assert "DELETE FROM drivers" in PURGE_FN
        assert "spinr.financial_events.allow_delete" in PURGE_FN

    def test_financial_events_still_purged_not_guarded(self):
        # financial_events is actively DELETEd as part of Step H, not a
        # blocking-existence guard (migration 216's original design) --
        # A38 does not reopen that question.
        assert "DELETE FROM financial_events     WHERE user_id = v_uid" in STEP_H

    def test_toctou_recheck_still_present(self):
        assert "FOR UPDATE" in STEP_H
        assert "IF NOT FOUND THEN" in STEP_H


class TestOtherStepsUntouched:
    def test_step_n_profile_scrub_still_present(self):
        assert "profile_scrubbed_at" in PURGE_FN
        assert "c_profile_scrub_age  INTERVAL := INTERVAL '30 days'" in PURGE_FN

    def test_result_json_unchanged(self):
        assert "'dsar_users_purged'" in PURGE_FN
        assert "'dsar_users_skipped_fk'" in PURGE_FN


class TestPermissionsAndCommentUnchanged:
    def test_revoke_grant_present(self):
        assert "REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM PUBLIC, anon, authenticated" in SQL_321
        assert "GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role" in SQL_321

    def test_comment_mentions_a38(self):
        assert "A38" in SQL_321
