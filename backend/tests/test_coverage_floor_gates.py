"""Tests for the per-module coverage-floor CI gates.

Covers `backend/scripts/_coverage_floor_lib.py` (the shared checking logic)
and both concrete gates that use it: `check_corporate_coverage_floor.py`
(pre-existing, unchanged manifest) and `check_money_path_coverage_floor.py`
(new -- payments/fare_service/crypto/rides/dispatch_service, ranked
blocker #30, docs/audit/2026-08-18-full-fleet-whole-app-audit.md).

No test file previously existed for this gate shape (`check_corporate_
coverage_floor.py` shipped without one) -- these are new, exercising the
shared library directly against synthetic coverage-json/changed-files
input so a bug in the parsing/pass-fail logic can't silently pass every
PR or silently block every PR touching a tracked module.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _coverage_floor_lib as lib  # noqa: E402
from check_corporate_coverage_floor import FLOOR_MANIFEST as CORPORATE_MANIFEST  # noqa: E402
from check_money_path_coverage_floor import FLOOR_MANIFEST as MONEY_PATH_MANIFEST  # noqa: E402


def _write_coverage_json(tmp_path: Path, files: dict) -> Path:
    p = tmp_path / "cov.json"
    p.write_text(json.dumps({"files": files}))
    return p


def _write_changed_files(tmp_path: Path, paths: list[str]) -> Path:
    p = tmp_path / "changed.txt"
    p.write_text("\n".join(paths))
    return p


class TestCoverageKeyFor:
    def test_maps_backend_relative_exact_file(self):
        manifest = {"routes/payments.py": (90.0, "x")}
        assert lib.coverage_key_for("backend/routes/payments.py", manifest) == "routes/payments.py"

    def test_maps_directory_prefix_key(self):
        manifest = {"routes/rides/": (67.0, "x")}
        assert lib.coverage_key_for("backend/routes/rides/booking.py", manifest) == "routes/rides/"
        assert lib.coverage_key_for("backend/routes/rides/matching.py", manifest) == "routes/rides/"

    def test_untracked_file_returns_none(self):
        manifest = {"routes/payments.py": (90.0, "x")}
        assert lib.coverage_key_for("backend/routes/drivers.py", manifest) is None

    def test_non_backend_path_returns_none(self):
        manifest = {"routes/payments.py": (90.0, "x")}
        assert lib.coverage_key_for("rider-app/App.tsx", manifest) is None


class TestRunGatePassFail:
    def test_pr_touching_no_tracked_module_is_a_noop_pass(self, tmp_path):
        manifest = {"routes/payments.py": (90.0, "x")}
        cov = _write_coverage_json(tmp_path, {})
        changed = _write_changed_files(tmp_path, ["backend/routes/drivers.py"])
        rc = lib.run_gate(manifest=manifest, coverage_json=cov, changed_files_file=changed, gate_label="test")
        assert rc == 0

    def test_touched_module_above_floor_passes(self, tmp_path):
        manifest = {"routes/payments.py": (90.0, "x")}
        cov = _write_coverage_json(
            tmp_path,
            {"routes/payments.py": {"summary": {"percent_covered": 95.0}}},
        )
        changed = _write_changed_files(tmp_path, ["backend/routes/payments.py"])
        rc = lib.run_gate(manifest=manifest, coverage_json=cov, changed_files_file=changed, gate_label="test")
        assert rc == 0

    def test_touched_module_below_floor_fails(self, tmp_path):
        manifest = {"routes/payments.py": (90.0, "x")}
        cov = _write_coverage_json(
            tmp_path,
            {"routes/payments.py": {"summary": {"percent_covered": 42.0}}},
        )
        changed = _write_changed_files(tmp_path, ["backend/routes/payments.py"])
        rc = lib.run_gate(manifest=manifest, coverage_json=cov, changed_files_file=changed, gate_label="test")
        assert rc == 1

    def test_touched_module_exactly_at_floor_passes(self, tmp_path):
        manifest = {"routes/payments.py": (90.0, "x")}
        cov = _write_coverage_json(
            tmp_path,
            {"routes/payments.py": {"summary": {"percent_covered": 90.0}}},
        )
        changed = _write_changed_files(tmp_path, ["backend/routes/payments.py"])
        rc = lib.run_gate(manifest=manifest, coverage_json=cov, changed_files_file=changed, gate_label="test")
        assert rc == 0

    def test_touched_module_missing_from_coverage_report_fails_loudly(self, tmp_path):
        # CLAUDE.md: do not silently swallow errors -- absent-from-report
        # must FAIL, not be read as "0 findings = fine".
        manifest = {"routes/payments.py": (90.0, "x")}
        cov = _write_coverage_json(tmp_path, {})
        changed = _write_changed_files(tmp_path, ["backend/routes/payments.py"])
        rc = lib.run_gate(manifest=manifest, coverage_json=cov, changed_files_file=changed, gate_label="test")
        assert rc == 1

    def test_missing_coverage_json_file_fails_loudly(self, tmp_path):
        manifest = {"routes/payments.py": (90.0, "x")}
        changed = _write_changed_files(tmp_path, ["backend/routes/payments.py"])
        rc = lib.run_gate(
            manifest=manifest,
            coverage_json=tmp_path / "does-not-exist.json",
            changed_files_file=changed,
            gate_label="test",
        )
        assert rc == 1

    def test_unparseable_coverage_json_fails_loudly(self, tmp_path):
        manifest = {"routes/payments.py": (90.0, "x")}
        bad = tmp_path / "cov.json"
        bad.write_text("{not valid json")
        changed = _write_changed_files(tmp_path, ["backend/routes/payments.py"])
        rc = lib.run_gate(manifest=manifest, coverage_json=bad, changed_files_file=changed, gate_label="test")
        assert rc == 1

    def test_missing_changed_files_file_is_treated_as_no_changes(self, tmp_path):
        # _load_changed_files returns empty set for a nonexistent path
        # (e.g. `git diff` produced nothing) -- must not crash, must PASS.
        manifest = {"routes/payments.py": (90.0, "x")}
        cov = _write_coverage_json(tmp_path, {})
        rc = lib.run_gate(
            manifest=manifest,
            coverage_json=cov,
            changed_files_file=tmp_path / "does-not-exist.txt",
            gate_label="test",
        )
        assert rc == 0

    def test_multiple_touched_modules_all_must_pass(self, tmp_path):
        manifest = {
            "routes/payments.py": (90.0, "x"),
            "utils/crypto.py": (90.0, "y"),
        }
        cov = _write_coverage_json(
            tmp_path,
            {
                "routes/payments.py": {"summary": {"percent_covered": 95.0}},
                "utils/crypto.py": {"summary": {"percent_covered": 50.0}},
            },
        )
        changed = _write_changed_files(tmp_path, ["backend/routes/payments.py", "backend/utils/crypto.py"])
        rc = lib.run_gate(manifest=manifest, coverage_json=cov, changed_files_file=changed, gate_label="test")
        assert rc == 1  # crypto.py drags the whole gate down even though payments.py passed


class TestDirectoryAggregate:
    def test_aggregates_covered_and_total_lines_across_matched_files(self, tmp_path):
        manifest = {"routes/rides/": (60.0, "x")}
        cov = _write_coverage_json(
            tmp_path,
            {
                "routes/rides/booking.py": {
                    "summary": {"percent_covered": 80.0, "covered_lines": 80, "num_statements": 100}
                },
                "routes/rides/matching.py": {
                    "summary": {"percent_covered": 40.0, "covered_lines": 40, "num_statements": 100}
                },
                # Not under the tracked prefix -- must not be counted.
                "routes/payments.py": {"summary": {"percent_covered": 0.0, "covered_lines": 0, "num_statements": 1000}},
            },
        )
        changed = _write_changed_files(tmp_path, ["backend/routes/rides/booking.py"])
        rc = lib.run_gate(manifest=manifest, coverage_json=cov, changed_files_file=changed, gate_label="test")
        # aggregate = (80+40)/(100+100) = 60.0% -- exactly at the 60% floor -> PASS
        assert rc == 0

    def test_aggregate_below_floor_fails(self, tmp_path):
        manifest = {"routes/rides/": (70.0, "x")}
        cov = _write_coverage_json(
            tmp_path,
            {
                "routes/rides/booking.py": {
                    "summary": {"percent_covered": 80.0, "covered_lines": 80, "num_statements": 100}
                },
                "routes/rides/matching.py": {
                    "summary": {"percent_covered": 40.0, "covered_lines": 40, "num_statements": 100}
                },
            },
        )
        changed = _write_changed_files(tmp_path, ["backend/routes/rides/matching.py"])
        rc = lib.run_gate(manifest=manifest, coverage_json=cov, changed_files_file=changed, gate_label="test")
        assert rc == 1  # 60% aggregate < 70% floor

    def test_no_files_under_prefix_present_fails_loudly(self, tmp_path):
        manifest = {"routes/rides/": (60.0, "x")}
        cov = _write_coverage_json(tmp_path, {})
        changed = _write_changed_files(tmp_path, ["backend/routes/rides/booking.py"])
        rc = lib.run_gate(manifest=manifest, coverage_json=cov, changed_files_file=changed, gate_label="test")
        assert rc == 1


class TestRealManifestsAreWellFormed:
    """Guard against a typo'd floor (e.g. accidentally > 100, or negative)
    slipping into either concrete gate's manifest."""

    @pytest.mark.parametrize("manifest", [CORPORATE_MANIFEST, MONEY_PATH_MANIFEST])
    def test_all_floors_are_sane_percentages(self, manifest):
        for key, (floor, provenance) in manifest.items():
            assert 0.0 <= floor <= 100.0, f"{key} floor {floor} out of range"
            assert provenance, f"{key} has no provenance string"

    def test_money_path_manifest_covers_all_five_claude_md_named_modules(self):
        # routes/rides.py is tracked as the routes/rides/ package -- see
        # check_money_path_coverage_floor.py's module docstring.
        expected_keys = {
            "routes/payments.py",
            "services/fare_service.py",
            "utils/crypto.py",
            "routes/rides/",
            "services/dispatch_service.py",
        }
        assert set(MONEY_PATH_MANIFEST.keys()) == expected_keys

    def test_corporate_manifest_unchanged_key_count(self):
        # Sanity check the refactor to _coverage_floor_lib.py didn't drop
        # or duplicate a row -- 14 corporate_* modules were tracked before
        # this change and still are.
        assert len(CORPORATE_MANIFEST) == 14
