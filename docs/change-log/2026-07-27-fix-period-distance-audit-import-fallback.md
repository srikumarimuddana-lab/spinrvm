# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-27 |
| Author | Claude Code (A4 backend test debt pass) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers (insurance-period audit trail; adjacent to safety) |
| PR / commit link | srikumarimuddana-lab/spinrvm#2421, commit ca0f0f4 |
| Related issue or gap ID | A4 (156 failing backend tests) — surfaced by `test_dual_import_parity.py` |

## 1. Issue / gap identified

`routes/drivers/ride_complete.py`'s import of `record_ride_period_distances`
used `except ImportError: pass` instead of the repo's standard dual-import
fallback (`except ImportError: from utils.period_distance_audit import
record_ride_period_distances`). Whenever this module loads in top-level
import mode (as opposed to `python -m backend.server`), the name is left
undefined, and `complete_ride` throws `NameError` when it calls it.

## 2. Root cause

Someone added the try/except scaffolding for the dual-import convention but
left the except branch as a bare `pass` instead of the required fallback
absolute import — likely a copy/paste of the pattern without filling in the
fallback. `test_dual_import_parity.py` is a regression test purpose-built to
catch exactly this class of bug (a name bound in the try branch but missing
from the except branch); it already contained the assertion, it just wasn't
passing yet.

## 3. Fix / remediation

Added the missing fallback import, mirroring the pattern already used one
function above it in the same file for `compute_trip_distances`/
`load_ride_breadcrumbs`.

## 4. Risk & impact on existing functionality

- The call site (`routes/drivers/ride_complete.py:661`, inside
  `complete_ride`) is wrapped in its own try/except that logs and continues
  ("period-distance audit failed... settlement unaffected") — so this bug
  never blocked ride completion or fare settlement. Its only effect was a
  silently-skipped write to the period-distance audit record whenever the
  module loaded in top-level import mode.
- Blast radius: isolated to this one call site and the
  `record_ride_period_distances` function it calls
  (`utils/period_distance_audit.py`). No other caller of
  `record_ride_period_distances` exists (grepped the repo).
- No interaction with the ride state machine, money/wallet deltas, or the
  16 background loops — this is a post-completion audit write only.
- In the deployed configuration (`python -m backend.server`, per
  `CLAUDE.md`), the relative import already succeeded and this bug was not
  reachable — it only manifested for tooling/tests that import the module
  top-level. This fix is a correctness/defense-in-depth fix for the dual
  -import convention, not a fix for an observed production outage.

## 5. User-experience effect

None. Backend-only; no rider/driver/admin-visible behavior changes. The fix
restores writes to a regulatory audit table
(`driver_insurance_periods`-adjacent period-distance record) that is not
read by any user-facing surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_complete.py` | `except ImportError: pass` → `except ImportError: from utils.period_distance_audit import record_ride_period_distances` | Restore the dual-import fallback so the name resolves in both import modes |
| `backend/tests/test_dual_import_parity.py` | Removed stale `routes/safety.py: {"notify_safety_team"}` baseline entry | That fallback branch was already fixed; the ratchet test enforces removing entries once fixed |

## 7. Before / after

```python
# Before
try:
    from ...utils.period_distance_audit import record_ride_period_distances
except ImportError:
    pass  # type: ignore
```

```python
# After
try:
    from ...utils.period_distance_audit import record_ride_period_distances
except ImportError:
    from utils.period_distance_audit import record_ride_period_distances  # type: ignore
```

## 8. Rollback plan

`git-revert-safe` — this is a pure import-fallback fix with no data migration,
no config, and no feature flag involved. A `git revert` fully undoes it. No
live data was mutated by this change (it only affects whether a downstream
audit write is *attempted*, not any wallet/ride/payment state).

## 9. Verification performed

- [x] Automated tests run: `pytest -q --no-cov tests/test_dual_import_parity.py` (3/3 pass), `pytest -q --no-cov tests/test_drivers_extended.py` (81/81 pass, full file)
- [ ] Manual repro steps followed in staging — not done; this is a backend-only import-fallback fix verified via the existing dual-import regression test
- [x] Blast-radius grep performed: grepped for all callers of `record_ride_period_distances` (single call site) and confirmed no other module imports the broken symbol
- [x] Reviewed against relevant `CLAUDE.md` convention: dual-import pattern ("Dual import pattern" under Critical Conventions)
- [ ] Feature-flagged — not applicable; this restores an already-shipped, non-user-visible audit write, not new behavior

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (isolated, single call site)
- [x] No silent behavior change to an already-shipped user-facing flow (backend-only audit-trail fix, "User experience effect" field filled in above)
