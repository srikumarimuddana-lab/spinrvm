"""SQL contract for trip-location route-integrity schema migration."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "235_trip_location_route_integrity.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def test_adds_idempotent_location_identity_and_capture_order_index() -> None:
    sql = _sql()

    assert "ADD COLUMN IF NOT EXISTS captured_at timestamptz" in sql
    assert "ADD COLUMN IF NOT EXISTS recording_session_id uuid" in sql
    assert "ADD COLUMN IF NOT EXISTS sequence_number bigint" in sql
    assert "uq_dlh_ride_driver_session_sequence" in sql
    assert "ride_id, driver_id, recording_session_id, sequence_number" in sql
    assert "idx_dlh_ride_captured" in sql
    assert "ride_id, captured_at, recording_session_id, sequence_number" in sql


def test_adds_versioned_segmented_route_finalizer_state() -> None:
    sql = _sql()

    for column in (
        "route_schema_version",
        "route_revision",
        "processing_status",
        "observed_segments",
        "road_matched_segments",
        "completion_point",
        "snapshot_revision",
        "processing_claimed_at",
        "next_retry_at",
        "retry_count",
        "finalized_at",
    ):
        assert column in sql

    assert "idx_ride_routes_processing" in sql
    assert "processing_status, next_retry_at, computed_at" in sql
    assert "jsonb_typeof(observed_segments) = 'array'" in sql
    assert "jsonb_typeof(road_matched_segments) = 'array'" in sql


def test_adds_shadow_mode_settings_with_safe_constraints() -> None:
    sql = _sql()

    assert "route_integrity_v2_mode" in sql
    assert "route_finalize_grace_seconds" in sql
    assert "route_location_gap_alert_seconds" in sql
    assert "'off', 'shadow', 'on'" in sql
    assert "DEFAULT 'shadow'" in sql


def test_is_append_only_and_has_a_rollback_plan() -> None:
    sql = _sql()

    assert "Rollback plan" in sql
    assert "DROP COLUMN" in sql
    assert "NOTIFY pgrst, 'reload schema'" in sql
