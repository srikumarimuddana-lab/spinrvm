#!/usr/bin/env python3
"""Per-module coverage floor for the 5 files CLAUDE.md names by path in its
"Coverage minimums (per domain)" testing-conventions section:

  >= 90%: routes/payments.py, services/fare_service.py, utils/crypto.py
  >= 80%: routes/rides.py (now the routes/rides/ package -- see below),
          services/dispatch_service.py

`corporate-coverage-floor-gate` (check_corporate_coverage_floor.py) already
enforces the same pattern for corporate_*.py. This is the higher-priority
gap CLAUDE.md itself calls out: "Not yet enforced by a --cov-fail-under
gate on this module specifically" for these 5 files too -- ranked blocker
#30 in docs/audit/2026-08-18-full-fleet-whole-app-audit.md. Same rationale
as the corporate gate: `coverage-regression-gate` is whole-codebase-
aggregate and advisory (continue-on-error: true), so a payments.py
regression can hide inside an otherwise-healthy aggregate number forever.

Shared checking logic (manifest lookup, coverage-json parsing, PR-diff
scoping, directory-aggregate support) lives in `_coverage_floor_lib.py`.

`routes/rides.py` no longer exists as a single file -- it was split into
the `routes/rides/` package (see ACTION_ITEMS.md A1's own acceptance
criteria: "CI fails if ... `routes/rides/*` ... below 80%", i.e. the
package is already treated as the tracked unit in practice, not the single
file CLAUDE.md's prose still names). This gate tracks it as the
directory-aggregate key "routes/rides/" -- sum(covered_lines) /
sum(num_statements) across every file pytest-cov's JSON report has under
that prefix. See `_coverage_floor_lib.coverage_key_for` /
`_coverage_floor_lib._measure` for how a directory-prefix key is resolved
and aggregated, and this gate's own PASS/FAIL touched-file mapping below --
a change to ANY file under `routes/rides/` counts as touching the
"routes/rides/" tracked unit, matching how CLAUDE.md's own historical A1
acceptance criteria already treated the package as one unit.

FLOOR MANIFEST -- provenance and the CLAUDE.md-target-vs-safer-floor call
--------------------------------------------------------------------------
The 2026-08-19 baseline measurement below (not the gate's own CI
invocation, which runs the full suite -- see `ci-guardrails.yml`'s
`money-path-coverage-floor-gate` job) was taken via targeted `-k`-scoped
runs for local speed (same pattern as `check_corporate_coverage_floor.py`'s
own `-k corporate` invocation) -- one run selecting payment/fare/crypto/otp
tests for the first 3 rows, one selecting ride/dispatch/matching/booking/
cancellation/lifecycle tests for the last 2 -- rather than a single
full-suite run, purely for measurement wall-clock cost (a full run took
>10 min locally; both scoped runs together took under 8). See
docs/change-log/2026-08-19-money-path-coverage-floor-gate-fix.md for the
exact commands and full numbers.

Every floor below follows the SAME "measured - 5, rounded down to the
nearest 5" convention `check_corporate_coverage_floor.py` already uses --
this is simpler than a target-vs-measured branch and, critically, is
already safe in both cases: rounding measured-5 down to the nearest 5
always lands strictly below the real measured value, whether or not that
value happens to clear the CLAUDE.md target.

`routes/payments.py` measured BELOW its 90% CLAUDE.md target (86.1%, not
90%+) -- this is a real, pre-existing gap, not something this gate
manufactured, and it is flagged explicitly in docs/change-log/2026-08-19-
money-path-coverage-floor-gate-fix.md as a follow-up decision needed (write
tests to close the gap, or revise the documented target) per CLAUDE.md's
own "escalate, don't silently ship" release gate #9 -- this gate's floor
for that file (80%) only stops further erosion, it does not silently
declare the gap closed. The other 4 rows are already at/above their
documented target. This mirrors exactly how `check_corporate_coverage_
floor.py` was introduced only once corporate_*.py was already compliant,
and how its 4 no-recorded-measurement rows shipped with an explicitly
conservative, clearly-labeled starting floor rather than a guess.

Do not lower a floor to make a failing PR pass. If a floor is wrong
(measured incorrectly, or the module was legitimately refactored down in
scope), fix the floor in its own commit with a citation, same as any other
documented convention change -- never as part of the PR that would
otherwise fail it.
"""

from __future__ import annotations

import sys

try:
    from ._coverage_floor_lib import FloorManifest, main_for
except ImportError:
    from _coverage_floor_lib import FloorManifest, main_for

# path (relative to `backend/`) -> (floor_percent, provenance)
#
# CLAUDE.md targets: payments.py / fare_service.py / crypto.py >= 90%,
# routes/rides/ (package) / dispatch_service.py >= 80%.
#
# See this file's module docstring for how each floor below was derived:
# measured - 5, rounded down to the nearest 5 (same convention as
# check_corporate_coverage_floor.py). docs/change-log/2026-08-19-money-
# path-coverage-floor-gate-fix.md has the full run output each row cites.
FLOOR_MANIFEST: FloorManifest = {
    "routes/payments.py": (
        80.0,
        "measured 86.1% (2026-08-19, -k payment/fare/crypto/otp run); "
        "BELOW 90% target -- see change-log follow-up, floor = measured - 5 rounded to nearest 5",
    ),
    "services/fare_service.py": (
        90.0,
        "measured 99.4% (2026-08-19, -k payment/fare/crypto/otp run); target 90% met",
    ),
    "utils/crypto.py": (
        95.0,
        "measured 100.0% (2026-08-19, -k payment/fare/crypto/otp run); target 90% met",
    ),
    "routes/rides/": (
        80.0,
        "measured 85.2% aggregate (2026-08-19, -k ride/dispatch/matching/booking/cancellation/lifecycle run); "
        "target 80% met",
    ),
    "services/dispatch_service.py": (
        85.0,
        "measured 92.4% (2026-08-19, -k ride/dispatch/matching/booking/cancellation/lifecycle run); target 80% met",
    ),
}


if __name__ == "__main__":
    sys.exit(main_for(FLOOR_MANIFEST, "money-path", __doc__))
