"""Schema contract for revisioned ride-route snapshots."""

from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "236_ride_route_snapshot_revision.sql"


def test_snapshot_url_is_stored_with_its_route_revision() -> None:
    sql = MIGRATION.read_text()
    writer = (Path(__file__).resolve().parents[1] / "routes" / "drivers" / "_shared.py").read_text()

    assert "ADD COLUMN IF NOT EXISTS snapshot_url text" in sql
    assert "ADD COLUMN IF NOT EXISTS snapshot_object_path text" in sql
    assert "INSERT INTO storage.buckets" in sql
    assert "'ride-route-snapshots', 'ride-route-snapshots', false" in sql
    assert "SET public = false" in sql
    assert "snapshot_revision" in sql
    assert 'bucket = "ride-route-snapshots" if revision > 0 else "ride-snapshots"' in writer
    assert "snapshot_object_path" in writer
    assert "if revision > 0:" in writer
    assert '"snapshot_url": None' in writer
    assert "Rollback plan" in sql
    assert "NOTIFY pgrst, 'reload schema'" in sql
