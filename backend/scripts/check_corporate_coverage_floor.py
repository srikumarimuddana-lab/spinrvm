#!/usr/bin/env python3
"""Per-module coverage floor for backend/{routes,services}/corporate_*.py.

CLAUDE.md's testing-conventions section targets >=80% for corporate_*
modules (same tier as rides/dispatch, since it moves real money via
corporate_wallet_apply_delta) but says that target is "not yet enforced by
a --cov-fail-under gate on this module specifically." The existing
`coverage-regression-gate` job in .github/workflows/ci-guardrails.yml does
not close that gap: it is whole-codebase-aggregate and regression-only (a
module can sit at 33% forever as long as it doesn't drop further), and it
is advisory (continue-on-error: true).

This script is the per-module floor. It only ever asserts against a module
that THIS PR actually touched (per CLAUDE.md's "ratchet without blocking
unrelated PRs" framing) -- a PR that doesn't touch corporate_* code is a
no-op pass here, printed and exited 0 immediately.

FLOOR MANIFEST
--------------
Floors below are NOT the 80% target -- they are current-measured-coverage
floors so the gate ratchets up over time instead of demanding an instant
jump to 80% everywhere. Every row as of 2026-08-02 is (measured): a dated,
real `pytest --cov` run is on record for it, either in ACTION_ITEMS.md /
docs/change-log/*.md, or in this gate's own first live CI run (PR #3308,
2026-08-02 -- see the 4 rows citing that run directly). The floor is set a
few points BELOW the recorded number as a flakiness/mock-variance buffer,
matching the 2% tolerance pattern already used by coverage-regression-gate.

If a future corporate_* module is added to this manifest before anyone has
measured it for real, mark it (conservative) with a deliberately low,
clearly-labeled starting floor instead of guessing a number -- see
docs/change-log/2026-07-28-corporate-*-coverage-80.md for the pattern to
follow once a real measurement exists: run `pytest --cov=. --cov-report=json`
against the module's own test file, read the real percent, then set the
floor to real_percent - 5, same as every other row.

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

# path (relative to `backend/`, matching how pytest-cov's JSON report keys
# files when run with `working-directory: backend`) -> (floor_percent, provenance)
FLOOR_MANIFEST: dict[str, tuple[float, str]] = {
    # measured 2026-07-28, see ACTION_ITEMS.md A1 + the cited change-log docs.
    # Floor = measured - 5, rounded down to the nearest 5, as a buffer.
    "routes/corporate_accounts.py": (75.0, "measured 82% (2026-07-28)"),
    "routes/corporate_company.py": (85.0, "measured 93% (2026-07-28)"),
    "routes/corporate_company_bookings.py": (80.0, "measured 87% (2026-07-28)"),
    "routes/corporate_company_kyb.py": (90.0, "measured 98% (2026-07-28)"),
    "routes/corporate_rider.py": (90.0, "measured 97% (2026-07-28)"),
    "routes/corporate_signup.py": (80.0, "measured 89% (2026-07-28)"),
    "services/corporate_allowance_service.py": (90.0, "measured 97% (2026-07-28)"),
    "services/corporate_membership_service.py": (95.0, "measured 100% (2026-07-28)"),
    "services/corporate_policy_service.py": (90.0, "measured 98% (2026-07-28)"),
    "services/corporate_wallet_service.py": (90.0, "measured 97% (2026-07-28)"),
    # These 4 shipped with a conservative 50% placeholder floor when this
    # gate was introduced (no dated measurement was on file at the time).
    # Real numbers below come from this gate's own first live CI run
    # (PR #3308, job https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/30764253064/job/91540074972,
    # 2026-08-02) -- that run executed `pytest tests/ tests/services/ -k
    # corporate --cov=.` for real and printed every corporate module's
    # actual coverage, even though the gate itself didn't need to assert
    # against them that time (that PR touched none of these files). Same
    # measured-minus-5pts-buffer convention as the rest of this manifest.
    "routes/corporate_wallet.py": (80.0, "measured 86% (2026-08-02, PR #3308 CI run)"),
    "services/corporate_member_offboarding_service.py": (75.0, "measured 79% (2026-08-02, PR #3308 CI run)"),
    "services/corporate_suspension_service.py": (75.0, "measured 79% (2026-08-02, PR #3308 CI run)"),
    "services/corporate_wallet_winddown_service.py": (85.0, "measured 91% (2026-08-02, PR #3308 CI run)"),
}


def _load_changed_files(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def _coverage_key_for(changed_path: str) -> str | None:
    """Map a repo-root-relative changed path (e.g.
    'backend/routes/corporate_accounts.py') to a FLOOR_MANIFEST key (e.g.
    'routes/corporate_accounts.py'), or None if it isn't a tracked module."""
    prefix = "backend/"
    if not changed_path.startswith(prefix):
        return None
    rel = changed_path[len(prefix) :]
    return rel if rel in FLOOR_MANIFEST else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-json", required=True, type=Path, help="pytest-cov JSON report")
    parser.add_argument(
        "--changed-files-file",
        required=True,
        type=Path,
        help="newline-delimited file of repo-root-relative changed paths for this PR",
    )
    args = parser.parse_args()

    changed = _load_changed_files(args.changed_files_file)
    touched_modules = sorted({key for f in changed if (key := _coverage_key_for(f))})

    if not touched_modules:
        print("No tracked corporate_* module in this PR's diff -- gate not applicable, PASS.")
        return 0

    if not args.coverage_json.exists():
        print(f"FAIL: expected coverage report at {args.coverage_json} but it does not exist.")
        print("The pytest --cov run that should have produced it must have failed outright --")
        print("that is a real failure, not something this gate should silently pass through.")
        return 1

    try:
        report = json.loads(args.coverage_json.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"FAIL: could not read/parse {args.coverage_json}: {exc}")
        return 1

    files = report.get("files", {})
    failures: list[str] = []
    print(f"Corporate coverage floor check -- {len(touched_modules)} tracked module(s) touched by this PR:\n")

    for key in touched_modules:
        floor, provenance = FLOOR_MANIFEST[key]
        entry = files.get(key)
        if entry is None:
            # Present in the manifest but absent from the coverage report --
            # e.g. the module was deleted, or the targeted test selection
            # below never imported it. Either way this must surface loudly,
            # not be read as "0 findings = fine" (CLAUDE.md: do not silently
            # swallow errors).
            print(f"  FAIL  {key:<55} not present in coverage report (floor {floor:.0f}%, {provenance})")
            failures.append(key)
            continue
        pct = entry.get("summary", {}).get("percent_covered", 0.0)
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

    print("PASS: all touched corporate_* modules meet their coverage floor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
