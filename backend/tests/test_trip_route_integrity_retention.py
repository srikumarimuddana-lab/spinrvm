from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "238_trip_route_integrity_retention.sql"
SNAPSHOT_LEDGER_MIGRATION = ROOT / "migrations" / "240_ride_route_snapshot_object_ledger.sql"
RETENTION_LOOP = ROOT / "utils" / "retention_purge.py"


def test_route_geometry_retention_scrubs_v2_coordinate_surfaces_at_three_years() -> None:
    sql = MIGRATION.read_text()

    assert "CREATE OR REPLACE FUNCTION purge_trip_route_geometry" in sql
    assert "INTERVAL '3 years'" in sql
    assert "observed_segments = '[]'::jsonb" in sql
    assert "road_matched_segments = '[]'::jsonb" in sql
    assert "completion_point = NULL" in sql
    assert "snapshot_url = NULL" in sql
    assert "snapshot_purge_pending_at" in sql
    assert "snapshot_object_path = NULL" in sql
    assert "route_geometry_anonymized_at" in sql


def test_retention_loop_invokes_the_trip_route_geometry_purge() -> None:
    ledger_sql = SNAPSHOT_LEDGER_MIGRATION.read_text()
    source = RETENTION_LOOP.read_text()

    assert 'rpc("purge_trip_route_geometry", {"p_dry_run": dry_run})' in source
    assert "trip_route_geometry_purge" in source
    assert "_delete_expired_route_snapshot_objects" in source
    assert "ride-route-snapshots" in source
    assert ".remove(paths)" in source
    assert "ride_route_snapshot_objects" in source
    assert "CREATE TABLE IF NOT EXISTS public.ride_route_snapshot_objects" in ledger_sql
    assert "retention_due_at" in ledger_sql
    assert "ON CONFLICT (storage_bucket, object_path) DO NOTHING" in ledger_sql
