"""Schema contract for revisioned ride-route snapshots."""

from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "236_ride_route_snapshot_revision.sql"


def test_snapshot_url_is_stored_with_its_route_revision() -> None:
    sql = MIGRATION.read_text()

    assert "ADD COLUMN IF NOT EXISTS snapshot_url text" in sql
    assert "snapshot_revision" in sql
    assert "Rollback plan" in sql
    assert "NOTIFY pgrst, 'reload schema'" in sql
