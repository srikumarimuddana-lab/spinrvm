# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (agent session) |
| Surface(s) | rider-app, driver-app, admin-dashboard |
| Domain (Sentry tag) | n/a — dev-tooling/build-time dependency fix, not a runtime app domain |
| PR / commit link | branch `claude/security-gates-js-yaml-sharp-bump` (3 commits, one per app) |
| Related issue or gap ID | Flagged in PR #5149's CI as `G4b · yarn audit (JS deps)` failures; PR #5149's own standing-down comment (2026-09-09) named this as a separate, unrelated follow-up rather than something to fix inside that PR |

## 1. Issue / gap identified

`G4b` (yarn/npm audit, HIGH-severity gate) was failing on all three JS surfaces: `js-yaml` (GHSA-2883-xcg3-v3hh) in all three apps, 8 separate `@xmldom/xmldom` advisories in rider-app and driver-app, and `sharp` (GHSA-rgj7-g3m4-5g8c) in admin-dashboard.

## 2. Root cause

Each package had a patched release available that the repo's pinned resolutions/overrides predated:
- `js-yaml` was pinned to `^4.3.1` (the fix from an earlier, already-closed `ACTION_ITEMS.md` advisory) — `4.3.2` patches a newer CPU-DoS advisory (`maxTotalMergeKeys` doesn't bound CPU use for empty merge sources) published against `4.3.1` itself.
- `@xmldom/xmldom` was pinned to `^0.8.13` — `0.8.15` patches 8 HIGH advisories (element/attribute/PI/doctype injection bypassing `requireWellFormed`, plus several quadratic-time parsing/memory DoS issues).
- `sharp` in admin-dashboard was declared `>=0.35.0` in `overrides`, which already technically permitted the patched `0.35.4`, but a plain `npm install` doesn't re-resolve a dependency whose currently-locked version (`0.35.3`) already satisfies its declared range — so the lockfile stayed on the vulnerable version until the floor was explicitly raised.

None of these findings were introduced by this branch — confirmed by grepping every one of the three apps' own audit output before touching anything.

## 3. Fix / remediation

- rider-app / driver-app: bumped the existing `resolutions` block entries `js-yaml` `^4.3.1`→`^4.3.2` and `@xmldom/xmldom` `^0.8.13`→`^0.8.15`, then regenerated `yarn.lock` via `yarn install`. Both are non-major patch bumps (confirmed via each advisory's own `fixAvailable` metadata before pinning, per CLAUDE.md rule 8).
- admin-dashboard: bumped the `overrides` block's `js-yaml` `^4.3.1`→`^4.3.2`, and tightened `sharp`'s override floor from `>=0.35.0` to `>=0.35.4` (the loose `>=0.35.0` was already permissive enough in principle but wasn't forcing a re-resolve — `>=0.35.4` is also the current npm-registry latest, so this doesn't newly expose the app to an unbounded future major).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to build/dev tooling.** All three packages are transitive dependencies reached through Expo/Metro tooling (rider-app, driver-app) and Next.js image optimization + PDF/XML tooling (admin-dashboard) — none are imported directly by any app's own runtime code (confirmed: `js-yaml`/`xmldom`/`sharp` do not appear as direct imports in any `rider-app/app`, `driver-app/app`, or `admin-dashboard/app` source file, only in `package.json` resolutions/overrides).
- `sharp` specifically backs Next.js's built-in image optimizer in admin-dashboard — the only surface where a broken bump could be user-visible (broken image rendering), which is why a real `npm run build` was run for that app rather than relying on `tsc --noEmit` alone.
- No shared component, hook, backend route, or database table is touched — this PR does not modify `shared/` or `backend/`.
- No interaction with the ride state machine, background loops, or money/wallet deltas.

## 5. User-experience effect

None expected. These are patch-level (non-major) bumps to transitive build/dev dependencies; no application code, copy, or behavior changes. If anything were visibly wrong, admin-dashboard's image optimization is the one plausible surface — ruled out by the real production build succeeding.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/package.json`, `rider-app/yarn.lock` | `js-yaml` `^4.3.1`→`^4.3.2`, `@xmldom/xmldom` `^0.8.13`→`^0.8.15` | Patch 9 HIGH advisories |
| `driver-app/package.json`, `driver-app/yarn.lock` | Same two bumps | Patch the same 9 HIGH advisories |
| `admin-dashboard/package.json`, `admin-dashboard/package-lock.json` | `js-yaml` `^4.3.1`→`^4.3.2`, `sharp` override floor `>=0.35.0`→`>=0.35.4` | Patch 2 HIGH advisories |

## 7. Before / after

```
# Before (rider-app/package.json and driver-app/package.json, "resolutions")
"js-yaml": "^4.3.1",
"@xmldom/xmldom": "^0.8.13",

# After
"js-yaml": "^4.3.2",
"@xmldom/xmldom": "^0.8.15",
```

```
# Before (admin-dashboard/package.json, "overrides")
"sharp": ">=0.35.0",
"js-yaml": "^4.3.1",

# After
"sharp": ">=0.35.4",
"js-yaml": "^4.3.2",
```

## 8. Rollback plan

`git revert` is a complete and sufficient rollback here — no migration, no live data, no feature flag involved. Reverting any of the 3 commits independently restores that app's prior lockfile state (each app's fix is a self-contained commit).

## 9. Verification performed

- [x] Automated tests: rider-app `npx jest --silent` → 145 suites / 2003 tests pass. driver-app `npx jest --silent` → 139 suites / 1566 tests pass (identical count to the just-merged PR #5149, confirming no interaction with that work). admin-dashboard `npx vitest run` → 64 files / 593 tests pass.
- [x] `npx tsc --noEmit` clean on all three apps.
- [x] **Real production build run for admin-dashboard** (`npm run build`) — succeeded, full route manifest printed, not just `tsc --noEmit`/dev server, per this repo's explicit convention for any admin-dashboard/rider-app/driver-app change.
- [x] Post-fix audit re-run on all three: `yarn audit --level high` (rider-app, driver-app) → 0 findings; `npm audit --audit-level=high` (admin-dashboard) → exit 0 (2 pre-existing moderate/low `joi` findings remain, out of scope for the HIGH gate).
- [x] Blast-radius grep performed: confirmed `js-yaml`, `@xmldom/xmldom`, `sharp` are not imported directly by any app's own source, only reached transitively.

## What was NOT verified

- No device/simulator build was run for rider-app or driver-app after this change (matches this repo's existing constraint — this agent session has no EAS credentials or device access). The dependency bumps are dev/build-tooling-only and not expected to affect a native binary, but that expectation has not been confirmed by an actual native build.
- rider-app and driver-app have no visual-regression tooling at all (per CLAUDE.md); admin-dashboard's Playwright visual-regression suite was not run in this pass — a pure dependency-version bump with 0 source-file changes is not expected to move any visual baseline, but that is reasoned, not screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (dev/build tooling only, confirmed via grep)
- [x] No silent behavior change to an already-shipped flow — no source file in any app changed
