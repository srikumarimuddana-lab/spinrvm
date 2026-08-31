"""Unit tests for backend/scripts/audit_migration_drift.py's diff logic.

Pure unit tests — no live DB dependency. `build_report()` is deliberately
side-effect-free (see its docstring), so these tests hand it a temp
directory of fake migration files and a hand-built `tracked` dict rather
than touching a real database or importing run_migrations' DB helpers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from audit_migration_drift import build_report, checksum_of  # noqa: E402

pytestmark = pytest.mark.unit


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


def test_three_bucket_categorization(tmp_path):
    matched = _write(tmp_path, "01_matched.sql", "CREATE TABLE foo();")
    mismatched = _write(tmp_path, "02_mismatched.sql", "CREATE TABLE bar();")
    untracked = _write(tmp_path, "03_untracked.sql", "CREATE TABLE baz();")

    tracked = {
        "01_matched.sql": checksum_of(matched),
        "02_mismatched.sql": "0" * 64,  # deliberately wrong checksum
        # 03_untracked.sql has no row at all
    }

    report = build_report([matched, mismatched, untracked], tracked)

    assert report.tracked_match == ["01_matched.sql"]
    assert report.untracked == ["03_untracked.sql"]
    assert len(report.tracked_mismatch) == 1
    assert report.tracked_mismatch[0]["filename"] == "02_mismatched.sql"
    assert report.tracked_mismatch[0]["recorded"] == "0" * 64
    assert report.tracked_mismatch[0]["current"] == checksum_of(mismatched)
    assert report.total == 3


def test_all_tracked_and_matching(tmp_path):
    a = _write(tmp_path, "10_a.sql", "SELECT 1;")
    b = _write(tmp_path, "11_b.sql", "SELECT 2;")
    tracked = {"10_a.sql": checksum_of(a), "11_b.sql": checksum_of(b)}

    report = build_report([a, b], tracked)

    assert report.tracked_match == ["10_a.sql", "11_b.sql"]
    assert report.tracked_mismatch == []
    assert report.untracked == []


def test_all_untracked(tmp_path):
    a = _write(tmp_path, "20_a.sql", "SELECT 1;")
    b = _write(tmp_path, "21_b.sql", "SELECT 2;")

    report = build_report([a, b], {})

    assert report.tracked_match == []
    assert report.tracked_mismatch == []
    assert report.untracked == ["20_a.sql", "21_b.sql"]


def test_to_dict_shape_matches_counts(tmp_path):
    a = _write(tmp_path, "30_a.sql", "SELECT 1;")
    b = _write(tmp_path, "31_b.sql", "SELECT 2;")
    tracked = {"30_a.sql": checksum_of(a)}

    report = build_report([a, b], tracked)
    d = report.to_dict()

    assert d["total_repo_files"] == 2
    assert d["tracked_match_count"] == 1
    assert d["tracked_mismatch_count"] == 0
    assert d["untracked_count"] == 1
    assert d["untracked"] == ["31_b.sql"]
