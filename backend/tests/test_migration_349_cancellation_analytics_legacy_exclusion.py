"""Migration 349 regression checks: four admin analytics functions
(admin_cancellation_breakdown, admin_driver_acceptance_rates,
admin_analytics_overview, admin_earnings_overview_agg) gain a
legacy-imported-ride exclusion, in response to the same session's
cancelled/failed legacy-booking import (booking_import_service.py) writing
`rides.status='cancelled'` rows for the first time — previously only
`status='completed'` legacy rows existed, which is exactly the assumption
migration 341's own test (test_migration_341_admin_money_legacy_exclusion.py)
pins: "No legacy row can be 'cancelled' (completed-only importer)". This
migration is what invalidates that assumption.

CI has no Postgres, so these checks pin the SQL contract textually — same
convention as 341's own test file and test_step_h_driver_rides_guard_migration.py
(321).
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_349 = (MIGRATIONS / "349_exclude_legacy_cancelled_from_cancellation_analytics.sql").read_text()

EXCLUSION_PREDICATE = "legacy_import_metadata = '{}'::jsonb"
# The narrower, cancelled-only predicate cohort gets instead of the blanket one.
COHORT_CANCELLED_ONLY_PREDICATE = "AND NOT (status = 'cancelled' AND legacy_import_metadata != '{}'::jsonb)"

ACCEPTANCE_RATES = SQL_349.split("CREATE OR REPLACE FUNCTION public.admin_driver_acceptance_rates")[1].split(
    "CREATE OR REPLACE FUNCTION public.admin_cancellation_breakdown"
)[0]
CANCELLATION_BREAKDOWN = SQL_349.split("CREATE OR REPLACE FUNCTION public.admin_cancellation_breakdown")[1].split(
    "CREATE OR REPLACE FUNCTION public.admin_analytics_overview"
)[0]
ANALYTICS_OVERVIEW = SQL_349.split("CREATE OR REPLACE FUNCTION public.admin_analytics_overview")[1].split(
    "CREATE OR REPLACE FUNCTION public.admin_earnings_overview_agg"
)[0]
EARNINGS_OVERVIEW_AGG = SQL_349.split("CREATE OR REPLACE FUNCTION public.admin_earnings_overview_agg")[1]

COMPLETED_CTE = EARNINGS_OVERVIEW_AGG.split("completed AS (")[1].split("cohort AS (")[0]
COHORT_CTE = EARNINGS_OVERVIEW_AGG.split("cohort AS (")[1].split("cancelled_src AS (")[0]
CANCELLED_SRC_CTE = EARNINGS_OVERVIEW_AGG.split("cancelled_src AS (")[1].split("cancelled AS (")[0]


class TestAdminDriverAcceptanceRatesExcludesLegacy:
    def test_carries_the_exclusion(self):
        assert EXCLUSION_PREDICATE in ACCEPTANCE_RATES

    def test_still_computes_all_three_counts(self):
        for key in ("total_rides", "completed", "cancelled_by_driver"):
            assert key in ACCEPTANCE_RATES

    def test_cancelled_by_driver_keys_off_reason_text(self):
        # Unlike admin_cancellation_breakdown / the earnings-agg `cancelled`
        # CTE, this function has no separate no-driver-found branch — it's a
        # single FILTER on `cancellation_reason ILIKE '%driver%'`. That means
        # this session's own synthetic fallback text ("No driver found
        # (legacy import)") would itself match here (contains "driver"),
        # which is exactly why a matched-driver legacy row must be excluded
        # unconditionally rather than relying on reason-text disambiguation.
        assert "lower(r.cancellation_reason) LIKE '%driver%'" in ACCEPTANCE_RATES


class TestAdminCancellationBreakdownExcludesLegacy:
    def test_carries_the_exclusion(self):
        assert EXCLUSION_PREDICATE in CANCELLATION_BREAKDOWN

    def test_still_filters_cancelled_status_and_window(self):
        assert "status = 'cancelled'" in CANCELLATION_BREAKDOWN
        assert "created_at >= p_start" in CANCELLATION_BREAKDOWN

    def test_still_returns_reason_party_hourly_buckets(self):
        for key in ("'reasons'", "'by_party'", "'hourly'"):
            assert key in CANCELLATION_BREAKDOWN


class TestAdminAnalyticsOverviewExcludesLegacy:
    def test_carries_the_exclusion(self):
        assert EXCLUSION_PREDICATE in ANALYTICS_OVERVIEW

    def test_excluded_at_the_source_cte_not_per_key(self):
        # One predicate on the shared `p` CTE covers every derived key
        # (total/completed/cancelled/in_progress/searching/scheduled/
        # revenue/daily/hourly) rather than needing it repeated per branch.
        assert ANALYTICS_OVERVIEW.count(EXCLUSION_PREDICATE) == 1

    def test_still_computes_every_status_bucket(self):
        for key in ("'total'", "'completed'", "'cancelled'", "'in_progress'", "'searching'", "'scheduled'"):
            assert key in ANALYTICS_OVERVIEW


class TestAdminEarningsOverviewAggCancellationFix:
    """The core fix: 341 shipped believing no legacy row could ever be
    'cancelled'. This session's importer makes that false. 349 must exclude
    legacy rows from cancellation counting WITHOUT retroactively changing
    the pre-existing, separate question of legacy-imported COMPLETED rides
    in the funnel (271 such rows have counted here since migration 227,
    predating this session)."""

    def test_completed_cte_unchanged_still_carries_its_own_exclusion(self):
        assert EXCLUSION_PREDICATE in COMPLETED_CTE
        assert "status = 'completed'" in COMPLETED_CTE

    def test_cohort_cte_does_not_get_the_blanket_predicate(self):
        # Blanket-excluding cohort would also drop legacy-imported COMPLETED
        # rides from fn_requested/fn_reached_searching/fn_completed —
        # retroactively changing already-live numbers for a separate,
        # pre-existing question this migration does not decide.
        assert EXCLUSION_PREDICATE not in COHORT_CTE

    def test_cohort_cte_gets_the_narrower_cancelled_only_predicate(self):
        # Instead, cohort excludes ONLY legacy-imported rows that are also
        # status='cancelled' — the genuinely new gap this session's importer
        # introduces (no legacy row could be 'cancelled' before it).
        assert COHORT_CANCELLED_ONLY_PREDICATE in COHORT_CTE

    def test_cohort_cte_still_feeds_the_funnel_keys(self):
        assert "fn_requested" in EARNINGS_OVERVIEW_AGG
        assert "fn_reached_searching" in EARNINGS_OVERVIEW_AGG
        assert "fn_completed" in EARNINGS_OVERVIEW_AGG

    def test_cancelled_src_cte_carries_the_blanket_exclusion(self):
        # Unlike cohort, cancelled_src ONLY feeds cancellation-counting keys
        # (cx_count/cx_revenue/cx_rider_cancels/cx_driver_cancels/
        # fn_cancelled_after_start) — no pre-existing legacy-completed
        # question applies here, so the blanket predicate is correct.
        assert EXCLUSION_PREDICATE in CANCELLED_SRC_CTE

    def test_cancelled_cte_reads_from_cancelled_src_not_cohort(self):
        cancelled_cte = EARNINGS_OVERVIEW_AGG.split("cancelled AS (")[1].split("tax AS (")[0]
        assert "FROM cancelled_src" in cancelled_cte
        assert "FROM cohort" not in cancelled_cte

    def test_cancellation_and_funnel_keys_still_present(self):
        for key in ("cx_count", "cx_revenue", "cx_rider_cancels", "cx_driver_cancels", "fn_cancelled_after_start"):
            assert key in EARNINGS_OVERVIEW_AGG


class TestSecurityPropertiesUnchanged:
    """Adding an exclusion predicate must never loosen an analytics
    function's access grants — same discipline migration 341's own test
    pins for the money-aggregate functions."""

    def test_all_four_functions_still_security_definer_stable(self):
        for fn_sql in (ACCEPTANCE_RATES, CANCELLATION_BREAKDOWN, ANALYTICS_OVERVIEW, EARNINGS_OVERVIEW_AGG):
            assert "STABLE" in fn_sql
            assert "SECURITY DEFINER" in fn_sql
            assert "SET search_path = public, pg_catalog" in fn_sql

    def test_execute_still_revoked_from_anon_and_authenticated(self):
        assert "REVOKE EXECUTE ON FUNCTION public.admin_driver_acceptance_rates" in SQL_349
        assert "REVOKE EXECUTE ON FUNCTION public.admin_cancellation_breakdown" in SQL_349
        assert "REVOKE EXECUTE ON FUNCTION public.admin_analytics_overview" in SQL_349
        assert "REVOKE EXECUTE ON FUNCTION public.admin_earnings_overview_agg" in SQL_349
        assert SQL_349.count("FROM anon, authenticated") == 4

    def test_earnings_overview_agg_still_grants_service_role(self):
        assert "GRANT  EXECUTE ON FUNCTION public.admin_earnings_overview_agg" in SQL_349
        assert "TO service_role" in SQL_349


class TestMigrationHeader:
    def test_carries_the_override_annotation(self):
        assert "migration-override-ok:" in SQL_349

    def test_notifies_postgrest_schema_reload(self):
        assert "NOTIFY pgrst, 'reload schema';" in SQL_349
