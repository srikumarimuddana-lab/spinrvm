"""A release audit must not create schema or migration provenance."""

from unittest.mock import MagicMock

import pytest

from backend.scripts import run_migrations as runner


@pytest.mark.parametrize("mode", ["--status", "--dry-run"])
@pytest.mark.parametrize("tracking_exists", [False, True])
def test_audit_modes_never_bootstrap_database(monkeypatch, tmp_path, mode, tracking_exists):
    tracking = tmp_path / runner.TRACKING_TABLE_MIGRATION
    tracking.write_text("CREATE TABLE schema_migrations (filename text, checksum text);")
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (1,) if tracking_exists else None
    cursor.fetchall.return_value = []
    monkeypatch.setattr(runner, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(runner, "_connect", lambda: conn)
    monkeypatch.setattr("sys.argv", ["run_migrations", mode])

    assert runner.main() == 0

    statements = [call.args[0].strip().upper() for call in cursor.execute.call_args_list]
    assert statements and all(sql.startswith("SELECT") for sql in statements)
    if not tracking_exists:
        assert not any("FROM SCHEMA_MIGRATIONS" in sql for sql in statements)
    conn.commit.assert_not_called()
    conn.close.assert_called_once()


def test_apply_still_bootstraps_missing_tracking_table(monkeypatch, tmp_path):
    tracking = tmp_path / runner.TRACKING_TABLE_MIGRATION
    tracking.write_text("CREATE TABLE schema_migrations (filename text, checksum text);")
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = None
    monkeypatch.setattr(runner, "MIGRATIONS_DIR", tmp_path)

    runner._ensure_tracking_table(conn)

    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("CREATE TABLE" in sql for sql in statements)
    assert any("INSERT INTO schema_migrations" in sql for sql in statements)
    conn.commit.assert_called_once()
