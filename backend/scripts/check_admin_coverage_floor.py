#!/usr/bin/env python3
"""Per-module coverage floor for backend/routes/admin/ and backend/utils/.

CLAUDE.md's testing-conventions section targets ">= 70%" for "Admin routes,
utilities" -- the one coverage tier named in that table with no enforcing
gate anywhere. `corporate-coverage-floor-gate` and `money-path-coverage-
floor-gate` (see their own scripts, `check_corporate_coverage_floor.py`
and `check_money_path_coverage_floor.py`) already close the equivalent gap
for their own tiers; this closes it for the last one. Found + fixed
2026-08-27 (docs/audit/2026-08-27-cicd-gates-guardrails-audit.md Section 3
/ Phase 3).

Unlike the corporate and money-path gates -- each a short, explicit list of
named files -- "admin routes" and "utilities" are broad, open-ended
directories (48 files under routes/admin/, 147 under utils/ as measured
below). Enumerating each individually the way the other two gates do would
be its own large undertaking with no clear per-file provenance to cite.
This gate instead tracks two directory-aggregate keys, `routes/admin/` and
`utils/` -- the same aggregate mechanism `check_money_path_coverage_floor.py`
already uses for the `routes/rides/` package (see
`_coverage_floor_lib.coverage_key_for` / `_measure`) -- so a change to ANY
file under either directory counts as touching that tracked unit, and the
floor is the aggregate percentage across every file the coverage report
has under that prefix.

This is deliberately coarser than the other two gates: a PR that adds an
untested new admin route can still pass if the *aggregate* stays above
floor, the same way a single low-coverage file doesn't sink
`money-path-coverage-floor-gate`'s directory-aggregate `routes/rides/` key
today. A coarse, real, ratcheting gate closes more of the actual CLAUDE.md
gap than continuing to have no gate at all while a more granular version
is designed -- tighten later if aggregate-level proves too permissive in
practice, same as any other documented convention change.

FLOOR MANIFEST -- provenance
-----------------------------
Measured 2026-08-27 via a real local `pytest --cov=.` run against this
repo's full backend test suite (12965 passed, 5 failed -- the 5 failures
are `test_utils_extended.py`'s `TestEstimateToken` cases, which need real
Postgres connectivity this measurement run didn't have; same environment
shape as `shared-coverage-run` in ci-guardrails.yml, which also has no
Postgres service and already tolerates individual test failures via
`|| true` since its purpose is coverage data, not pass/fail -- unaffected
by this measurement). Full-suite run, not a scoped `-k` subset: unlike
`corporate_*.py`/the 5 money-path files, there is no small keyword filter
that isolates "every admin route or utility test" from the rest of the
suite, so the full-suite aggregate is the honest number -- matching how
this gate's own CI job (`admin-coverage-floor-gate`) measures it too, via
the same `shared-coverage-run` artifact the sibling floor gates consume.

Same "measured - 5, rounded down to the nearest 5" convention as every
other floor gate in this repo.

Do not lower a floor to make a failing PR pass. If a floor is wrong
(measured incorrectly, or the directory was legitimately refactored down
in scope), fix the floor in its own commit with a citation, same as any
other documented convention change -- never as part of the PR that would
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
# CLAUDE.md target: "Admin routes, utilities: >= 70%".
FLOOR_MANIFEST: FloorManifest = {
    "routes/admin/": (
        80.0,
        "measured 89.69% aggregate across 48 files (2026-08-27, full-suite local pytest --cov run); target 70% met",
    ),
    "utils/": (
        85.0,
        "measured 90.48% aggregate across 147 files (2026-08-27, full-suite local pytest --cov run); target 70% met",
    ),
}


if __name__ == "__main__":
    sys.exit(main_for(FLOOR_MANIFEST, "admin/utils", __doc__))
