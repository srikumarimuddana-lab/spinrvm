"""run_migrations.py mid-batch failure reporting.

Before this fix, `main()` applied each pending migration in a bare loop with
no try/except, so a failure partway through a batch (e.g. a syntax error or
a constraint violation on the Nth of M pending files) propagated as an
unhandled exception -- a raw traceback with no summary of which files had
already committed, which one failed, or which were never attempted. Each
file already commits independently (`_apply_one` / `_apply_one_autocommit`
each `conn.commit()` on success), so nothing here changes *what* gets
applied -- only that the operator gets a clean, explicit summary instead of
a stack trace, and the run still exits non-zero.

Run:
    pytest backend/tests/test_run_migrations_batch_failure.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from backend.scripts.run_migrations import _apply_pending


def test_mid_batch_failure_reports_applied_failed_and_remaining(capsys):
    pending = [Path("101_a.sql"), Path("102_b.sql"), Path("103_c.sql")]

    def fake_apply_one(conn, path):
        if path.name == "102_b.sql":
            raise RuntimeError('syntax error at or near "FOO"')
        # 101_a.sql "commits" successfully; 103_c.sql is never reached.

    with patch("backend.scripts.run_migrations._apply_one", side_effect=fake_apply_one):
        rc = _apply_pending(conn=object(), pending=pending)

    assert rc == 1
    err = capsys.readouterr().err
    assert "101_a.sql" in err and "before the failure" in err
    assert "102_b.sql" in err and "syntax error" in err
    assert "103_c.sql" in err
    # The already-applied file must never appear as not-applied.
    not_applied_line = next(line for line in err.splitlines() if line.startswith("Not applied"))
    assert "101_a.sql" not in not_applied_line


def test_all_migrations_apply_cleanly_returns_zero(capsys):
    pending = [Path("101_a.sql"), Path("102_b.sql")]

    with patch("backend.scripts.run_migrations._apply_one") as mock_apply:
        rc = _apply_pending(conn=object(), pending=pending)

    assert rc == 0
    assert mock_apply.call_count == 2
    assert "Applied 2 migration(s)." in capsys.readouterr().out


def test_first_migration_failure_reports_zero_applied(capsys):
    pending = [Path("101_a.sql")]

    with patch("backend.scripts.run_migrations._apply_one", side_effect=RuntimeError("boom")):
        rc = _apply_pending(conn=object(), pending=pending)

    assert rc == 1
    err = capsys.readouterr().err
    assert "Applied 0 migration(s) before the failure: (none)" in err
    assert "Not applied (1 total, including the failed file): 101_a.sql" in err
