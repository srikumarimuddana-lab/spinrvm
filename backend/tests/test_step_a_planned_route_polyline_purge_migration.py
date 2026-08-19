"""Regression checks for migration 335: purge_pii_retention()'s Step A
anonymizes ride GPS at 3 years (pickup/dropoff lat/lng, route_polyline,
phase_polylines, route_snapshot_url) but never touched
rides.planned_route_polyline (migration 100) -- the Google Directions
polyline captured at booking time. A ride past the 3-year GPS anonymization
window still had its full planned turn-by-turn route sitting live in that
column even after gps_anonymized_at was stamped.

Found 2026-08-19 via A40 (docs/audit/2026-08-18-full-fleet-whole-app-audit.md,
ranked blocker #4/#11).

CI has no Postgres, so these checks pin the SQL contract textually -- same
convention as test_step_f_stripe_events_column_fix_migration.py (324),
test_step_d_ride_messages_column_fix_migration.py (323),
test_step_h_driver_rides_guard_migration.py (321).
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_335 = (MIGRATIONS / "335_purge_pii_retention_step_a_planned_route_polyline.sql").read_text()

PURGE_FN = SQL_335.split("CREATE OR REPLACE FUNCTION purge_pii_retention")[1]
STEP_A = PURGE_FN.split("-- Step A")[1].split("-- Step B")[0]


class TestStepAAnonymizesPlannedRoutePolyline:
    def test_live_branch_clears_planned_route_polyline(self):
        # Step A's live-write branch is the UPDATE ... SET block, before the
        # dry-run ELSE (which only SELECTs a COUNT and touches no columns).
        live_branch = STEP_A.split("ELSE")[0]
        assert "planned_route_polyline" in live_branch
        assert "planned_route_polyline = '[]'::jsonb" in live_branch

    def test_still_clears_the_other_gps_columns(self):
        # Guard against the re-fork accidentally dropping an existing field
        # while adding the new one.
        live_branch = STEP_A.split("ELSE")[0]
        for column in (
            "pickup_lat",
            "pickup_lng",
            "dropoff_lat",
            "dropoff_lng",
            "route_polyline",
            "phase_polylines",
            "route_snapshot_url",
        ):
            assert column in live_branch, f"Step A must still clear {column}"

    def test_dry_run_branch_is_unchanged_count_only(self):
        # The dry-run COUNT(*) branch never mutates columns -- it should not
        # need to reference planned_route_polyline at all, only the WHERE
        # clause's existing filter columns.
        dry_branch = STEP_A.split("ELSE")[1]
        assert "SELECT COUNT(*) INTO v_rides_anonymized" in dry_branch
        assert "gps_anonymized_at IS NULL" in dry_branch

    def test_gps_anonymized_at_guard_still_present(self):
        # The idempotency guard (only touch rows not yet anonymized) must
        # survive the re-fork unchanged, both to avoid re-touching already
        # anonymized rows and to keep the function safe to re-run.
        assert "gps_anonymized_at      = v_started_at" in STEP_A.split("ELSE")[0]
        assert "AND gps_anonymized_at IS NULL" in STEP_A


class TestStepFFixCarriedForwardFromMigration324:
    """335 re-forks the function from 324 -- Step F's stripe_events.received_at
    fix must not regress."""

    def test_step_f_still_uses_received_at(self):
        step_f = PURGE_FN.split("-- Step F")[1].split("-- Step G")[0]
        assert "received_at < v_started_at - c_stripe_event_age" in step_f
        assert "created_at < v_started_at - c_stripe_event_age" not in step_f


class TestStepDFixCarriedForwardFromMigration323:
    """335 re-forks the function from 324 (which re-forked from 323) --
    Step D's ride_messages.timestamp fix must not regress."""

    def test_step_d_still_uses_timestamp(self):
        step_d = PURGE_FN.split("-- Step D")[1].split("-- Step E")[0]
        assert '"timestamp" < v_started_at - c_chat_age' in step_d
        assert "created_at < v_started_at - c_chat_age" not in step_d


class TestStepHFixCarriedForwardFromMigration321:
    """335 re-forks the function from 324 (which re-forked from 323/321) --
    A38's Step H driver-ride guard must not regress."""

    def test_step_h_still_guards_on_driver_rides(self):
        step_h = PURGE_FN.split("-- Step H")[1].split("-- Step I")[0]
        assert "EXISTS (SELECT 1 FROM rides r2        WHERE r2.driver_id = d.id)" in step_h
