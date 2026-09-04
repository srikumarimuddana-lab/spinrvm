# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (session 01Sspqro7zzjKdTbUh6D61wQ), root-causing CR #4934 |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | drivers, rides (mobile app build/CI health) |
| PR / commit link | branch `claude/fix-sentry-replay-resolution` |
| Related issue or gap ID | [CR #4934](https://github.com/srikumarimuddana-lab/spinrvm/issues/4934) |

## 1. Issue / gap identified

Every mobile CI check that bundles either app (`driver-app`/`rider-app — expo export (android + ios)`, `Driver app E2E tests (Playwright)`, `Rider app E2E tests (Playwright)`) has been red on `main` itself, unconditionally, since before 2026-09-04 — confirmed on `main`'s own `CI/CD Pipeline` run at commit `b7b7e08` (a prior fix attempt's own merge commit) with zero PR diff involved. Full history and evidence trail: CR #4934.

## 2. Root cause

Both apps' checked-in `yarn.lock` has a `"@sentry/browser@10.71.0":` entry with **no `dependencies:` block at all** — just `version`/`resolved`/`integrity`. Confirmed against the real published npm package metadata for this exact version (matching shasum/integrity) that `@sentry/browser@10.71.0` actually depends on `@sentry/core`, `@sentry/replay`, `@sentry/feedback`, `@sentry/conventions`, `@sentry/browser-utils`, and `@sentry/replay-canvas`.

Yarn v1's install algorithm only materializes a package into `node_modules` when something in the lockfile's own recorded dependency graph asks for it — a `--frozen-lockfile` install does not re-derive dependencies from each package's real npm metadata. With `@sentry/browser`'s entry missing its `dependencies:` edge, four of those six packages (`@sentry/replay`, `@sentry/replay-canvas`, `@sentry/feedback`, `@sentry/browser-utils`) were pure orphans in the lockfile: resolvable blocks with zero incoming reference from anywhere, so `yarn install` silently skipped installing them. (`@sentry/core` and `@sentry/conventions` were unaffected because other packages in the tree reference those two directly, giving yarn a real edge to them.)

`@sentry/browser`'s own published code unconditionally `require`/`import`s all four missing packages regardless of target platform (reached transitively via `@sentry/react-native` → `shared/services/errorReporting.ts`, imported from both apps' root/index files), so every bundler — Metro for native Android/iOS export, Metro for web export (what the E2E Playwright tests build against) — failed identically at `Unable to resolve module @sentry/replay`.

**How this happened**: per PR #4921's own investigation (referenced in CR #4934), PR #4914's bot auto-commit ("sync yarn.lock to package.json") bumped `@sentry/browser` to `10.71.0` and updated some but not all of the resulting dependency structure — this specific gap (the missing `dependencies:` block on `@sentry/browser`'s own entry) survived that bump and PR #4921's later, ineffective re-sync attempt.

## 3. Fix / remediation

Added the correct `dependencies:` block to `@sentry/browser@10.71.0`'s lockfile entry in both `rider-app/yarn.lock` and `driver-app/yarn.lock`, matching its real published `package.json` exactly:

```yaml
dependencies:
  "@sentry/browser-utils" "10.71.0"
  "@sentry/conventions" "^0.16.0"
  "@sentry/core" "10.71.0"
  "@sentry/feedback" "10.71.0"
  "@sentry/replay" "10.71.0"
  "@sentry/replay-canvas" "10.71.0"
```

No version bump, no `resolutions`/`overrides` workaround, no `package.json` change — just the one missing edge the lockfile should have recorded when `@sentry/browser` was bumped to `10.71.0`.

## 4. Risk & impact on existing functionality

- Purely a dependency-materialization fix — no application code changed, no new package added to either `package.json`. The four packages that now install were already being loaded at runtime in production (via `@sentry/browser`'s own code) whenever the bundler happened to have them cached from an earlier, different lockfile state — this fix makes that dependable and reproducible instead of accidental.
- Blast radius: isolated to `yarn.lock` in both apps. No other file changed.
- No ride state, dispatch, fare, auth, or PII-handling code touched.

## 5. User-experience effect

None directly — this is a build/CI-health fix. Indirectly, it restores mobile CI as a real, trustworthy signal for every future rider-app/driver-app/shared PR (previously masked behind a mandatory "pre-existing, not mine" comment on 4 separate checks).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/yarn.lock` | Added `dependencies:` block to `@sentry/browser@10.71.0`'s entry (7 lines) | Missing edge caused 4 transitive packages to never install |
| `rider-app/yarn.lock` | Same | Same |

## 7. Before / after

```yaml
# before (both apps)
"@sentry/browser@10.71.0":
  version "10.71.0"
  resolved "https://registry.yarnpkg.com/@sentry/browser/-/browser-10.71.0.tgz#c290bfd245fc2501f9f615db6da287149d5594a5"
  integrity sha512-fTE9tUDoggJSFv8cQ+h1UA9onovOoUNJWu8Var1MXmUVuVG/W3WqzJFVyKArZMj95lizZkq8aiS63SURvrBsFQ==

# after
"@sentry/browser@10.71.0":
  version "10.71.0"
  resolved "https://registry.yarnpkg.com/@sentry/browser/-/browser-10.71.0.tgz#c290bfd245fc2501f9f615db6da287149d5594a5"
  integrity sha512-fTE9tUDoggJSFv8cQ+h1UA9onovOoUNJWu8Var1MXmUVuVG/W3WqzJFVyKArZMj95lizZkq8aiS63SURvrBsFQ==
  dependencies:
    "@sentry/browser-utils" "10.71.0"
    "@sentry/conventions" "^0.16.0"
    "@sentry/core" "10.71.0"
    "@sentry/feedback" "10.71.0"
    "@sentry/replay" "10.71.0"
    "@sentry/replay-canvas" "10.71.0"
```

## 8. Rollback plan

`git revert` — pure lockfile addition, no data touched, no migration, no coordinated deploy. Reverting returns to the known-broken (but not newly-broken) prior state.

## 9. Verification performed

- [x] `yarn install --frozen-lockfile` (the exact command CI runs) confirmed to materialize `node_modules/@sentry/{replay,replay-canvas,feedback,browser-utils}` in both apps — checked directly, not assumed.
- [x] **Full end-to-end reproduction of the actual failure and its fix**: ran `npx expo export --platform {web,android,ios}` in both apps (6 runs total) — all 6 completed successfully with zero `@sentry/replay` resolution errors, producing real bundles. This directly exercises the same Metro bundling path every failing CI job runs (`mobile-bundle-smoke.yml`'s android+ios export; the E2E Playwright tests' web export step).
- [x] `npx tsc --noEmit` clean (exit 0) in both apps.
- [x] Full automated suites, both green: driver-app `jest` (127 suites / 1437 tests), rider-app `jest` (140 suites / 1949 tests) — unaffected, as expected for a dependency-only fix.
- [x] Diff verified minimal: `git diff --stat` shows exactly 7 lines added per file, nothing else touched (no incidental lockfile drift from the `yarn install` runs used to verify).
- [x] Root cause confirmed against real npm registry metadata for `@sentry/browser@10.71.0` (matching shasum/integrity to the checked-in lockfile entry) rather than assumed.

## 10. What was NOT verified

- Did not verify against GitHub Actions' own runners directly (this fix was developed and fully verified in this session's sandbox) — the sandbox's `yarn install --frozen-lockfile` + real `expo export` reproduction is the closest available proxy, and CI will be the final confirmation once this PR's own checks run.
- Did not investigate why PR #4914's original bot auto-commit produced this specific malformed lockfile shape (missing one package's `dependencies:` block while getting others right) — out of scope for fixing the resulting gap; the fix here is data-only (correcting the lockfile), not a change to whatever tooling generated it.
- Did not check whether other `yarn.lock` entries elsewhere in either file have a similar missing-`dependencies:`-block gap — this fix is scoped to the specific, confirmed, currently-broken `@sentry/browser` entry only.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data involved)
- [x] Blast radius is stated, not assumed (2 files, 7 lines each, confirmed via `git diff --stat`)
- [x] No silent behavior change to an already-shipped flow — this fix makes previously-accidental/uncached runtime behavior (the 4 packages loading from a stale cache) into dependable, reproducible behavior; nothing user-visible changes
