from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "238_trip_route_integrity_retention.sql"
RETENTION_LOOP = ROOT / "utils" / "retention_purge.py"


def test_route_geometry_retention_scrubs_v2_coordinate_surfaces_at_three_years() -> None:
    sql = MIGRATION.read_text()

    assert "CREATE OR REPLACE FUNCTION purge_trip_route_geometry" in sql
    assert "INTERVAL '3 years'" in sql
    assert "observed_segments = '[]'::jsonb" in sql
    assert "road_matched_segments = '[]'::jsonb" in sql
    assert "completion_point = NULL" in sql
    assert "snapshot_url = NULL" in sql
    assert "route_geometry_anonymized_at" in sql


def test_retention_loop_invokes_the_trip_route_geometry_purge() -> None:
    source = RETENTION_LOOP.read_text()

    assert 'rpc("purge_trip_route_geometry", {"p_dry_run": dry_run})' in source
    assert "trip_route_geometry_purge" in source
