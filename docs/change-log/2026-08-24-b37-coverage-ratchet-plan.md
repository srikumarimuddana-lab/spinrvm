# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude (agent session), on behalf of vikas@ngitservices.com |
| Surface(s) | rider-app, driver-app, admin-dashboard (config only — no app code touched) |
| Domain (Sentry tag) | n/a (CI/test-tooling change, not a runtime code path) |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | ACTION_ITEMS.md B37 (closing the entry's last open sub-item) |

## 1. Issue / gap identified

`ACTION_ITEMS.md` B37 was mostly closed 2026-08-22 (coverage directories
widened across all three frontend surfaces; admin-dashboard's CI step
wired to actually run `--coverage`), but its own "Recommended fix step 4"
— a milestone ratchet plan raising each surface's CI-enforced coverage
threshold as real test coverage improves, toward the user's stated 100%
target — was explicitly left open. Separately, re-measuring for this task
found rider-app's and driver-app's numeric gates had drifted 30-50
percentage points below actual measured coverage since 2026-08-22, because
an enormous amount of screen-by-screen test-authoring landed under B37
without the threshold ever being re-tightened to track it.

## 2. Root cause

The 2026-08-22 B37 work correctly tightened each threshold to just below
its *then-current* measured coverage, but there was no follow-up mechanism
to re-tighten it as further test-authoring (also logged under B37, in
several follow-on PRs #4460/#4465/#4467 and later sessions) kept raising
real coverage. Each individual test-authoring PR reasonably didn't touch
the threshold config (out of scope for that PR), so the gate quietly
became a rubber stamp: `rider-app` measured 76.42% lines against a
`lines:20` gate; `driver-app` measured 67.37% lines against a `lines:33`
gate.

## 3. Fix / remediation

1. Wrote `docs/testing/coverage-ratchet-plan.md` — states current real
   coverage per surface (freshly measured, not estimated), current gate,
   the ratchet mechanism (tighten to ~2-3pts below actual now; revisit
   quarterly or after any >5pt coverage jump), and an explicit honesty
   note that literal 100% is not the working target (platform-conditional
   branches, defensive catches, thin re-exports, native bootstrap files
   may never realistically hit 100%).
2. Applied the plan's first ratchet step: re-measured all three frontend
   surfaces and tightened `rider-app/jest.config.js` and
   `driver-app/jest.config.js`'s `coverageThreshold` blocks to sit ~2-3pts
   below today's actual numbers. `admin-dashboard/vitest.config.ts`'s
   `coverage.thresholds` block was re-measured but left unchanged — it
   hadn't drifted (still within ~1-2pts of actual, already at the plan's
   target gap).
3. Updated `ACTION_ITEMS.md`'s B37 entry: checkbox now `[x]`, status note
   summarizing the closure and the exact before/after threshold numbers,
   and the "Acceptance" line's "Still open" note replaced with a closure
   note.

No application code, test file, or CI workflow YAML was touched — this is
config-only (three coverage-threshold blocks) plus two docs.

## 4. Risk & impact on existing functionality

- **What reads these config values:** `.github/workflows/ci.yml` invokes
  `yarn test --ci --coverage --forceExit --reporters=default` for both the
  legacy driver-app job (line ~170) and the canonical `driver-app-test`
  (line ~215) and `rider-app-test` (line ~260) jobs, and
  `npm run test:coverage` for admin-dashboard (line ~307) — all four read
  the `coverageThreshold`/`thresholds` block in the corresponding config
  file directly. No other file references these threshold numbers (grepped
  `.github/workflows/ci.yml` for `test:coverage`/`jest.config`/
  `vitest.config`/`yarn test`/`npm test` — only the four call sites above).
- **Blast radius: isolated to CI's coverage-gate pass/fail on these three
  jobs.** Raising a threshold makes the gate *stricter*, not looser — the
  only way this change breaks anything is if a future PR's real coverage
  regresses below the new, tighter floor, which is the explicit intended
  behavior (a working regression tripwire instead of a stale rubber
  stamp). No runtime code path, no ride state machine, no money/wallet
  delta, no background loop is touched.
- **Nothing else reads `jest.config.js`/`vitest.config.ts`'s threshold
  values** — they are not imported by application code, only consumed by
  the Jest/Vitest coverage CLI at test-run time.

## 5. User-experience effect

None. This is a CI/test-tooling change only — no rider, driver, corporate
admin, or internal admin sees any different behavior. No copy, UI, or API
change of any kind.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/jest.config.js` | `coverageThreshold.global`: `lines:20→73, functions:16→69, branches:15→63` | Gate had drifted ~53-56pts below actual measured coverage (76.42%/71.77%/65.68%); re-tightened to ~2-3pts below today's real ceiling per the new ratchet plan |
| `driver-app/jest.config.js` | `coverageThreshold.global`: `lines:33→65, functions:26→60, statements:32→63` | Same drift pattern — gate ~30-34pts below actual (67.37%/63.25%/65.73%); re-tightened |
| `admin-dashboard/vitest.config.ts` | Comment updated with fresh 2026-08-24 measurement; threshold values (`branches:11, functions:10, lines:19, statements:18`) left unchanged | Re-measured 19.11%/13.05%/11.71%/20.75% — essentially unchanged from the 2026-08-22 baseline this threshold was already set against; no test-authoring pass has touched this surface yet, so no bump was warranted |
| `docs/testing/coverage-ratchet-plan.md` (new) | Milestone ratchet plan: current-state table, ratchet mechanism, 100%-is-aspirational note | Closes the one piece of B37's Recommended-fix step 4 that was still open |
| `ACTION_ITEMS.md` | B37 entry: checkbox `[ ]→[x]`, status note, Acceptance line | Marks the entry closed with a dated summary and doc link |
| `docs/change-log/2026-08-24-b37-coverage-ratchet-plan.md` (new, this file) | Change Impact Log for this change | Required per `CLAUDE.md` for any commit closing a gap |

## 7. Before / after

```js
// rider-app/jest.config.js — before
coverageThreshold: {
  global: {
    lines: 20,
    functions: 16,
    branches: 15,
  },
},
```

```js
// rider-app/jest.config.js — after
coverageThreshold: {
  global: {
    lines: 73,
    functions: 69,
    branches: 63,
  },
},
```

```js
// driver-app/jest.config.js — before
coverageThreshold: {
  global: {
    lines: 33,
    functions: 26,
    statements: 32,
  },
},
```

```js
// driver-app/jest.config.js — after
coverageThreshold: {
  global: {
    lines: 65,
    functions: 60,
    statements: 63,
  },
},
```

admin-dashboard's `thresholds` block values are unchanged (comment-only
update), so no before/after snippet is included for it per the template's
"only required for behavior-changing diffs" rule.

## 8. Rollback plan

No feature flag applies (this is a CI gate, not a runtime feature). To
revert: restore the three threshold blocks to their prior numeric values
via `git revert` on this commit — since nothing here touches live data,
Stripe charges, wallet deltas, or ride state, a plain code revert is a
complete and sufficient rollback (no data-level remediation needed). If
the new, tighter gate starts blocking legitimate in-flight PRs whose
coverage genuinely can't clear it yet, the faster fix is to lower just the
specific metric that's blocking back toward (but still below) actual
measured coverage, rather than a full revert.

## 9. Verification performed

- [x] Automated tests run — exact commands and real output, all from this
  session, all in the repo's cloned worktree (not staging, not CI):
  - `cd rider-app && npx jest --coverage` — first run: 121 passed / 1 failed
    (`__tests__/homeScreen.test.tsx`, "redirects to /driver-arriving for a
    searching active ride", `Exceeded timeout of 15000 ms`). Re-run
    immediately after (no code change): **122/122 suites, 1241/1241 tests,
    all green** — confirmed a pre-existing flake under coverage-
    instrumentation CPU contention (matches the documented pattern in this
    same config file's `testTimeout` comment for `homeScreen`/
    `rideOptions`/`driverProfileScreen`), not a regression from this
    change, per CLAUDE.md's "re-run once to confirm a flake" guidance.
    Measured: **74.73% statements / 65.68% branches / 71.77% functions /
    76.42% lines.** Re-ran a third time after applying the new thresholds:
    122/122 suites, 1241/1241 tests, clean, no threshold failure in
    output.
  - `cd driver-app && npx jest --coverage` — **115/115 suites, 1243/1243
    tests, all green** on the first run, no flake observed this session.
    Measured: **65.73% statements / 57.45% branches / 63.25% functions /
    67.37% lines.** Re-ran after applying the new thresholds: same
    115/115, 1243/1243, clean.
  - `cd admin-dashboard && npx vitest run --coverage` — **36/36 test
    files, 351/351 tests, all green.** Measured: **19.11% statements
    (3153/16498) / 13.05% branches (1916/14673) / 11.71% functions
    (541/4618) / 20.75% lines (2949/14212).** Re-ran after the (unchanged)
    threshold edit to confirm the comment-only diff didn't break parsing:
    same result.
  - Backend was **not** re-run for this task — used the existing,
    documented ratchet in `backend/pytest.ini` (`--cov-fail-under=60`,
    ratchet-history comment 6%→40%→50%→60%, target ceiling 80) and
    CLAUDE.md's Testing Conventions per-module minimums as the source of
    truth, since backend already has a working ratchet mechanism this
    task didn't need to touch or duplicate.
- [x] Manual repro steps followed — n/a beyond the above; this is a config
  value change with no user-facing flow to click through.
- [x] Blast-radius grep performed — `grep -n "test:coverage\|jest.config\|
  vitest.config" .github/workflows/ci.yml` and `grep -n "yarn jest\|jest
  --coverage\|npm test\|npm run test"` confirmed exactly four CI call
  sites read these threshold blocks (listed in section 4); no other file
  in the repo references the threshold values.
- [x] Reviewed against relevant CLAUDE.md convention(s) — Testing
  Conventions (coverage minimums per domain, ratchet philosophy matching
  `pytest.ini`'s existing pattern) and the Change Impact & Risk Log
  requirement itself.
- [ ] Feature-flagged if user-visible and non-trivial — n/a, not
  user-visible (internal CI gate only).
- **Production build:** not applicable/not run. This change touches only
  `jest.config.js`/`vitest.config.ts` coverage-threshold numbers and
  markdown docs — no application source, so `npm run build` for
  admin-dashboard/rider-app/driver-app was not run and would not exercise
  this diff. If reviewers want it run anyway as a sanity check, none of
  the three surfaces' production builds were attempted in this session.

## 10. What was NOT verified

- Backend's actual current `pytest --cov` percentage was **not** re-run
  this session — relied on `backend/pytest.ini`'s own ratchet-history
  comment and CLAUDE.md's documented per-module minimums instead, since
  the task's scope (per B37's own open item) was the three frontend
  surfaces' milestone-ratchet gap, and backend already has its own
  functioning ratchet this task didn't need to touch.
- Did not run any surface's production build (`npm run build` /
  `expo export` / equivalent) — this diff has no application-code
  component for a build to exercise, only test-tool config and docs, so a
  build run would not have added verification signal for this specific
  change (as distinct from CLAUDE.md's general rule for app-code PRs,
  which this is not).
- Did not investigate the `homeScreen.test.tsx` flake beyond confirming it
  is pre-existing and reproduces the same documented shape already noted
  in `rider-app/jest.config.js`'s `testTimeout` comment — no new issue
  filed since it's already a known, tracked pattern, not a new discovery.
- No visual/snapshot regression tooling exists for any of the three
  surfaces touched here in an active state (per CLAUDE.md's standing
  note), but this change has zero visual surface (coverage-threshold
  numbers in config files, plus markdown) so that gap doesn't apply to
  this diff specifically.
- Did not attempt to raise admin-dashboard's threshold at all this round,
  even nominally — chose to leave it exactly as-is rather than force a
  cosmetic 0.something-point bump, since its measured coverage genuinely
  hadn't moved since the last time it was set. This is a judgment call
  documented in the plan doc and this log rather than a verified fact
  about what the "right" gap should be beyond CLAUDE.md's general
  "regression tripwire, not stale rubber stamp" goal.

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data
  remediation needed)
- [x] Blast radius is stated, not assumed (grepped CI workflow for every
  consumer of these config values)
- [x] No silent behavior change to an already-shipped flow — this is a CI
  gate only, no shipped user flow is affected
