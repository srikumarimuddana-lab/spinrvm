#!/usr/bin/env python3
"""Shared logic for per-module coverage-floor CI gates.

Extracted from `check_corporate_coverage_floor.py` (the first gate of this
shape, introduced 2026-08-02 for `corporate_*.py`) so the same
"only assert against a module THIS PR actually touched, floor is a
ratcheting measured-minus-buffer value, never a demand for an instant jump
to the target" pattern can be reused for other coverage-floor manifests
(e.g. `check_money_path_coverage_floor.py` for
payments/fare_service/crypto/rides/dispatch_service) without copy-pasting
the whole file.

A manifest entry's key is either:
  - an exact path relative to `backend/` matching a `files` key in
    pytest-cov's JSON report (e.g. "routes/payments.py"), or
  - a directory-prefix key ending in "/" (e.g. "routes/rides/"), meaning
    "aggregate every file the coverage report has under this prefix" --
    used for `routes/rides.py`, which CLAUDE.md still names as a single
    file but which was split into the `routes/rides/` package (see
    ACTION_ITEMS.md A1's acceptance criteria: "CI fails if ... `routes/
    rides/*` ... below 80%" -- the package *is* the tracked unit now).
    Aggregate percent = sum(covered_lines) / sum(num_statements) across
    every file under the prefix that appears in the coverage report.

Do not lower a floor to make a failing PR pass. If a floor is wrong
(measured incorrectly, or the module was legitimately refactored down in
scope), fix the floor in its own commit with a citation, same as any other
documented convention change -- never as part of the PR that would
otherwise fail it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FloorManifest = dict[str, tuple[float, str]]


def load_changed_files(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def coverage_key_for(changed_path: str, manifest: FloorManifest) -> str | None:
    """Map a repo-root-relative changed path (e.g.
    'backend/routes/corporate_accounts.py') to a manifest key (e.g.
    'routes/corporate_accounts.py' or a directory-prefix key like
    'routes/rides/'), or None if it isn't a tracked module."""
    prefix = "backend/"
    if not changed_path.startswith(prefix):
        return None
    rel = changed_path[len(prefix) :]
    if rel in manifest:
        return rel
    for key in manifest:
        if key.endswith("/") and rel.startswith(key):
            return key
    return None


def _measure(key: str, files: dict) -> tuple[float, bool]:
    """Returns (percent_covered, present). For a directory-prefix key,
    aggregates every file in the coverage report under that prefix."""
    if key.endswith("/"):
        matched = {path: entry for path, entry in files.items() if path.startswith(key)}
        if not matched:
            return 0.0, False
        covered = sum(e.get("summary", {}).get("covered_lines", 0) for e in matched.values())
        total = sum(e.get("summary", {}).get("num_statements", 0) for e in matched.values())
        pct = (covered / total * 100.0) if total else 0.0
        return pct, True

    entry = files.get(key)
    if entry is None:
        return 0.0, False
    return entry.get("summary", {}).get("percent_covered", 0.0), True


def run_gate(
    *,
    manifest: FloorManifest,
    coverage_json: Path,
    changed_files_file: Path,
    gate_label: str,
) -> int:
    changed = load_changed_files(changed_files_file)
    touched_modules = sorted({key for f in changed if (key := coverage_key_for(f, manifest))})

    if not touched_modules:
        print(f"No tracked {gate_label} module in this PR's diff -- gate not applicable, PASS.")
        return 0

    if not coverage_json.exists():
        print(f"FAIL: expected coverage report at {coverage_json} but it does not exist.")
        print("The pytest --cov run that should have produced it must have failed outright --")
        print("that is a real failure, not something this gate should silently pass through.")
        return 1

    try:
        report = json.loads(coverage_json.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"FAIL: could not read/parse {coverage_json}: {exc}")
        return 1

    files = report.get("files", {})
    failures: list[str] = []
    print(f"{gate_label} coverage floor check -- {len(touched_modules)} tracked module(s) touched by this PR:\n")

    for key in touched_modules:
        floor, provenance = manifest[key]
        pct, present = _measure(key, files)
        if not present:
            # Present in the manifest but absent from the coverage report --
            # e.g. the module was deleted, or the targeted test selection
            # never imported it. Must surface loudly, not be read as "0
            # findings = fine" (CLAUDE.md: do not silently swallow errors).
            print(f"  FAIL  {key:<55} not present in coverage report (floor {floor:.0f}%, {provenance})")
            failures.append(key)
            continue
        status = "PASS" if pct >= floor else "FAIL"
        print(f"  {status}  {key:<55} {pct:5.1f}%  (floor {floor:.0f}%, {provenance})")
        if pct < floor:
            failures.append(key)

    print()
    if failures:
        print(f"FAIL: {len(failures)} module(s) below their coverage floor: {', '.join(failures)}")
        print("Add tests to cover the code this PR added/changed in that module, or open a")
        print("Change Request (.github/ISSUE_TEMPLATE/ci_change_request.yml) if the drop is justified.")
        return 1

    print(f"PASS: all touched {gate_label} modules meet their coverage floor.")
    return 0


def build_arg_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--coverage-json", required=True, type=Path, help="pytest-cov JSON report")
    parser.add_argument(
        "--changed-files-file",
        required=True,
        type=Path,
        help="newline-delimited file of repo-root-relative changed paths for this PR",
    )
    return parser


def main_for(manifest: FloorManifest, gate_label: str, description: str) -> int:
    args = build_arg_parser(description).parse_args()
    return run_gate(
        manifest=manifest,
        coverage_json=args.coverage_json,
        changed_files_file=args.changed_files_file,
        gate_label=gate_label,
    )


if __name__ == "__main__":
    print("This module is a library for check_*_coverage_floor.py scripts -- not runnable directly.")
    sys.exit(2)
