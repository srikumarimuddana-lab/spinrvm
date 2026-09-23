"""Migration ownership must precede bootstrap and pending classification."""
from unittest.mock import MagicMock

import pytest

from backend.scripts import run_migrations as runner


@pytest.mark.parametrize("locked", [False, True])
def test_apply_acquires_database_lock_before_reading_or_writing(monkeypatch, locked):
    events = []
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.execute.side_effect = lambda sql, *args: events.append(sql)
    cur.fetchone.return_value = (locked,)
    conn.commit.side_effect = lambda: events.append("commit")
    monkeypatch.setattr(runner, "_connect", lambda **kwargs: conn)
    monkeypatch.setattr(runner, "_discover_migrations", lambda: [])
    monkeypatch.setattr("sys.argv", ["run_migrations"])
    monkeypatch.setattr(runner, "_ensure_tracking_table", lambda *a, **kw: events.append("bootstrap") or True)
    monkeypatch.setattr(runner, "_fetch_applied", lambda *a: events.append("classify") or {})

    assert runner.main() == (0 if locked else 1)
    assert "pg_try_advisory_lock" in events[0]
    if locked:
        assert events.index("commit") < events.index("bootstrap") < events.index("classify")
    else:
        assert "bootstrap" not in events and "classify" not in events
    conn.close.assert_called_once()


def test_failed_lock_never_bootstraps_and_redacts_exception(monkeypatch, capsys):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError("password=private")
    bootstrap = MagicMock()
    monkeypatch.setattr(runner, "_connect", lambda **kwargs: conn)
    monkeypatch.setattr(runner, "_discover_migrations", lambda: [])
    monkeypatch.setattr(runner, "_ensure_tracking_table", bootstrap)
    monkeypatch.setattr("sys.argv", ["run_migrations"])
    assert runner.main() == 1
    bootstrap.assert_not_called()
    assert "private" not in capsys.readouterr().err
    conn.close.assert_called_once()


@pytest.mark.parametrize("dsn", [
    "postgresql://u:private@aws-0-ca.pooler.supabase.com:5432/db",
    "postgresql://u:private@localhost:6543/db",
    "postgresql://u:private@localhost/db?pgbouncer=true",
    "host=localhost password=private dbname=test",
])
def test_apply_rejects_poolers_and_unverifiable_dsn(dsn):
    with pytest.raises(ValueError, match="direct PostgreSQL URL") as exc:
        runner._validate_apply_dsn(dsn)
    assert "private" not in str(exc.value)


def test_direct_connection_url_is_supported():
    runner._validate_apply_dsn("postgresql://localhost:5432/disposable")
