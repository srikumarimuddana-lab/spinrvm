"""B18 (partial) regression checks (migration 296): Step N scrubs profile PII
30 days after a DSAR deletion request (regulatory-sk.md Right-to-delete #1),
independent of Step H's 7-year hard delete. Closes the gap where
delete_account_pipeda left name/email/profile_image/saved_addresses fully
live and queryable for the entire 7-year retention window.

CI has no Postgres, so these checks pin the SQL contract textually — same
convention as test_deletion_hard_delete_migration.py (216) and
test_financial_events_ride_id_fk_contract.py (294).
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_296 = (MIGRATIONS / "296_pipeda_30day_profile_scrub.sql").read_text()

PURGE_FN = SQL_296.split("CREATE OR REPLACE FUNCTION purge_pii_retention")[1]
STEP_N = PURGE_FN.split("-- Step N")[1].split("v_result := jsonb_build_object")[0]


class TestNewColumn:
    def test_adds_profile_scrubbed_at_column(self):
        assert "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_scrubbed_at" in SQL_296

    def test_column_is_append_only_by_convention_comment(self):
        assert "never reset to NULL once set" in SQL_296


class TestStepNScrubsProfileFieldsAt30Days:
    def test_scrubs_name_email_and_avatar(self):
        for col in ("first_name", "last_name", "email", "profile_image"):
            assert f"{col}" in STEP_N and "= NULL" in STEP_N.split(f"{col}")[1][:40]

    def test_does_not_touch_phone(self):
        # Nulling phone would break the "sign in again anytime to reactivate"
        # promise in delete_account_pipeda's own response (OTP login is
        # phone-based). regulatory-sk.md's own list excludes phone too.
        assert "phone" not in STEP_N.lower()

    def test_anchored_on_deletion_requested_at_not_scheduled_at(self):
        # deletion_requested_at is the actual DSAR request timestamp;
        # deletion_scheduled_at is request+7y (Step H's field). Step N must
        # use the former for its 30-day window.
        assert "deletion_requested_at < v_started_at - c_profile_scrub_age" in STEP_N
        assert "deletion_scheduled_at" not in STEP_N

    def test_gated_on_pending_deletion_status(self):
        assert "status = 'pending_deletion'" in STEP_N

    def test_idempotent_via_scrubbed_at_marker(self):
        assert "profile_scrubbed_at IS NULL" in STEP_N
        assert "profile_scrubbed_at = v_started_at" in STEP_N

    def test_30_day_constant_matches_promise(self):
        assert "c_profile_scrub_age  INTERVAL := INTERVAL '30 days'" in PURGE_FN

    def test_hard_deletes_saved_addresses_for_scrubbed_accounts(self):
        assert "DELETE FROM saved_addresses" in STEP_N
        assert "WHERE user_id IN (SELECT id FROM scrubbed)" in STEP_N

    def test_dry_run_branch_counts_without_mutating(self):
        dry_branch = STEP_N.split("ELSE")[1]
        assert "UPDATE users" not in dry_branch
        assert "DELETE FROM saved_addresses" not in dry_branch
        assert "SELECT COUNT(*) INTO v_profile_scrubbed" in dry_branch


class TestStepNDoesNotTouchStepHsSevenYearWindow:
    def test_step_h_hard_delete_still_present_unmodified(self):
        assert "DELETE FROM users" in PURGE_FN
        assert "spinr.financial_events.allow_delete" in PURGE_FN

    def test_step_b_relies_on_fk_set_null_not_a_local_step(self):
        # B17's fix (294/295) is schema-level (ON DELETE SET NULL + the
        # trigger allowance that makes it actually fire) -- Step B itself
        # stays a bare DELETE, no explicit unlink step inside this function.
        step_b = PURGE_FN.split("-- Step B:")[1].split("-- Step C:")[0]
        assert "DELETE FROM rides" in step_b
        assert "UPDATE financial_events" not in step_b


class TestResultJsonExposesNewCounters:
    def test_result_includes_profiles_scrubbed(self):
        assert "'profiles_scrubbed'" in PURGE_FN

    def test_result_includes_saved_addresses_deleted_on_scrub(self):
        assert "'saved_addresses_deleted_on_scrub'" in PURGE_FN
