"""Migration 341 regression checks: three admin money-aggregate functions
(admin_earnings_overview_agg, admin_earnings_daily_series, admin_dashboard_money)
gain the same legacy-imported-ride exclusion migrations 302/303 already
applied to admin_ride_money_rollup/admin_payouts_overview_aggregates.

CI has no Postgres, so these checks pin the SQL contract textually — same
convention as test_step_h_driver_rides_guard_migration.py (321) and
test_pipeda_30day_profile_scrub_migration.py (296).
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_341 = (MIGRATIONS / "341_exclude_legacy_from_remaining_admin_money_aggregates.sql").read_text()

EXCLUSION_PREDICATE = "legacy_import_metadata = '{}'::jsonb"

OVERVIEW_AGG = SQL_341.split("CREATE OR REPLACE FUNCTION public.admin_earnings_overview_agg")[1].split(
    "CREATE OR REPLACE FUNCTION public.admin_earnings_daily_series"
)[0]
DAILY_SERIES = SQL_341.split("CREATE OR REPLACE FUNCTION public.admin_earnings_daily_series")[1].split(
    "CREATE OR REPLACE FUNCTION admin_dashboard_money"
)[0]
DASHBOARD_MONEY = SQL_341.split("CREATE OR REPLACE FUNCTION admin_dashboard_money")[1]

# Within overview_agg, isolate the `completed` CTE (must carry the exclusion)
# from `cohort`/`cancelled` (must NOT — no legacy row is ever 'cancelled',
# the importer is completed-only, so adding it there would be a no-op that
# invites someone to assume cancelled counts need the same treatment).
COMPLETED_CTE = OVERVIEW_AGG.split("completed AS (")[1].split("cohort AS (")[0]
COHORT_AND_CANCELLED_CTES = OVERVIEW_AGG.split("cohort AS (")[1]


class TestAdminEarningsOverviewAggExcludesLegacy:
    def test_completed_cte_carries_the_exclusion(self):
        assert EXCLUSION_PREDICATE in COMPLETED_CTE

    def test_completed_cte_still_filters_status_and_window(self):
        assert "status = 'completed'" in COMPLETED_CTE
        assert "ride_completed_at >= p_start" in COMPLETED_CTE

    def test_cohort_and_cancelled_ctes_unchanged_no_legacy_predicate(self):
        # No legacy row can be 'cancelled' (completed-only importer) — the
        # funnel/cancellation counts never needed this fix, and adding it
        # here would be a silent no-op masking that reasoning.
        assert EXCLUSION_PREDICATE not in COHORT_AND_CANCELLED_CTES

    def test_funnel_and_cancellation_keys_still_present(self):
        for key in ("fn_requested", "fn_completed", "cx_rider_cancels", "cx_driver_cancels"):
            assert key in OVERVIEW_AGG


class TestAdminEarningsDailySeriesExcludesLegacy:
    def test_carries_the_exclusion(self):
        assert EXCLUSION_PREDICATE in DAILY_SERIES

    def test_still_filters_status_and_window(self):
        assert "status = 'completed'" in DAILY_SERIES
        assert "GROUP BY 1" in DAILY_SERIES


class TestAdminDashboardMoneyExcludesLegacy:
    def test_ride_money_subquery_carries_the_exclusion(self):
        ride_subquery = DASHBOARD_MONEY.split("FROM rides r")[1].split(") || (")[0]
        assert EXCLUSION_PREDICATE.replace("legacy_import_metadata", "r.legacy_import_metadata") in ride_subquery

    def test_subscription_payments_subquery_unaffected(self):
        # Spinr Pass revenue is Spinr-native, not ride-shaped — no legacy
        # concept applies; must not gain the predicate.
        sub_subquery = DASHBOARD_MONEY.split(") || (")[1]
        assert "legacy_import_metadata" not in sub_subquery

    def test_still_computes_ride_volume_and_driver_earnings(self):
        assert "ride_volume" in DASHBOARD_MONEY
        assert "driver_earnings" in DASHBOARD_MONEY


class TestSecurityPropertiesUnchanged:
    """The whole point of 302/303's pattern this migration mirrors: adding an
    exclusion predicate must never loosen a money function's access grants."""

    def test_all_three_functions_still_security_definer_stable(self):
        for fn_sql in (OVERVIEW_AGG, DAILY_SERIES, DASHBOARD_MONEY):
            assert "STABLE" in fn_sql
            assert "SECURITY DEFINER" in fn_sql
            assert "SET search_path = public, pg_catalog" in fn_sql

    def test_execute_still_revoked_from_anon_and_authenticated(self):
        assert "REVOKE EXECUTE ON FUNCTION public.admin_earnings_overview_agg" in SQL_341
        assert "FROM anon, authenticated" in SQL_341
        assert "REVOKE EXECUTE ON FUNCTION admin_dashboard_money" in SQL_341
        assert "FROM PUBLIC, anon, authenticated" in SQL_341
