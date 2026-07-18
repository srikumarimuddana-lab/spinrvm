"""migrate.py --env / --yes / EXPECTED_PROJECT_REF guards (ADR-008).

The runner selects its database purely from env vars, so the promote
pipeline adds two cheap safety nets: an expected-project-ref match and an
interactive confirmation for --env production without --yes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.scripts.migrate import _check_expected_project_ref, _confirm_production


class TestExpectedProjectRef:
    def test_unset_is_noop(self, monkeypatch):
        monkeypatch.delenv("EXPECTED_PROJECT_REF", raising=False)
        _check_expected_project_ref()  # must not raise/exit

    def test_matching_ref_passes(self, monkeypatch):
        monkeypatch.setenv("EXPECTED_PROJECT_REF", "abcd1234")
        monkeypatch.setenv("PG_CONNECTION_STRING", "postgresql://postgres.abcd1234@pooler:5432/postgres")
        _check_expected_project_ref()

    def test_mismatched_ref_exits(self, monkeypatch):
        monkeypatch.setenv("EXPECTED_PROJECT_REF", "prodref99")
        monkeypatch.setenv("PG_CONNECTION_STRING", "postgresql://postgres.stagingref@pooler:5432/postgres")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(SystemExit):
            _check_expected_project_ref()

    def test_falls_back_to_supabase_url(self, monkeypatch):
        monkeypatch.setenv("EXPECTED_PROJECT_REF", "abcd1234")
        monkeypatch.delenv("PG_CONNECTION_STRING", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("SUPABASE_URL", "https://abcd1234.supabase.co")
        _check_expected_project_ref()


class TestProductionConfirmation:
    def test_non_production_needs_no_confirmation(self):
        _confirm_production("staging", assume_yes=False, dry_run=False)

    def test_yes_skips_prompt(self):
        _confirm_production("production", assume_yes=True, dry_run=False)

    def test_dry_run_skips_prompt(self):
        _confirm_production("production", assume_yes=False, dry_run=True)

    def test_non_tty_without_yes_refused(self):
        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            with pytest.raises(SystemExit):
                _confirm_production("production", assume_yes=False, dry_run=False)

    def test_tty_wrong_word_refused(self):
        with patch("sys.stdin") as stdin, patch("builtins.input", return_value="prod"):
            stdin.isatty.return_value = True
            with pytest.raises(SystemExit):
                _confirm_production("production", assume_yes=False, dry_run=False)

    def test_tty_correct_word_passes(self):
        with patch("sys.stdin") as stdin, patch("builtins.input", return_value="production"):
            stdin.isatty.return_value = True
            _confirm_production("production", assume_yes=False, dry_run=False)
