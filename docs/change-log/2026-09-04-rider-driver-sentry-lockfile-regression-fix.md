# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude (session) |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | rides (CI/build infra, not runtime) |
| PR / commit link | (this PR) |
| Related issue or gap ID | Regression introduced by the merge of PR #4914 (`d9a6c61`) |

## 1. Issue / gap identified

`main`'s `Rider app E2E tests (Playwright)` and `Driver app E2E tests
(Playwright)` jobs both fail at the `Export Expo web build` step (before
even reaching the webServer-start step PR #4914 fixed), with:

```
Error: Unable to resolve module @sentry/replay from
.../node_modules/@sentry/browser/build/npm/esm/prod/index.js:
@sentry/replay could not be found within the project or in these directories
```

## 2. Root cause

PR #4914's GitHub-Actions bot auto-commit ("chore(mobile): sync yarn.lock
to package.json", squashed into the merge commit `d9a6c61`) regenerated
both `rider-app/yarn.lock` and `driver-app/yarn.lock` incorrectly: it
bumped `@sentry/browser` (a transitive dep of `@sentry/react-native`) to
`10.71.0` and correctly updated that entry's own `dependencies:` block to
point at `@sentry/browser-utils@10.71.0`, `@sentry/feedback@10.71.0`,
`@sentry/replay@10.71.0`, and `@sentry/replay-canvas@10.71.0` — but it
never added the corresponding **resolved package entries** for those four
sub-packages at the new version. The old `10.69.0`/`10.71.0`-mismatched
entries were deleted with nothing replacing them.

Net effect: `yarn install` no longer materializes
`node_modules/@sentry/replay` (or the other three), even though
`@sentry/browser`'s own code unconditionally imports it — a lockfile
integrity gap that only surfaces at Metro bundle time (web export), not at
`yarn install` time, which is why CI didn't fail until the export step.

Confirmed via `git diff` between the merge's parent commit and `d9a6c61`:
exactly those 4 package entries were removed from each app's `yarn.lock`
with no replacement, while `@sentry/browser`'s `dependencies:` block still
references them at `10.71.0`.

## 3. Fix / remediation

Ran `yarn install` fresh (no manual edits, no `--force`) in both
`rider-app/` and `driver-app/` from a clean checkout of `main` — this is
the same regeneration PR #4914 itself intended, just done correctly
(the bot's own auto-commit is what introduced the bug, not the `serve`
devDependency addition itself). Confirmed the missing 4 entries are
restored in both lockfiles with entries at `10.71.0`, and confirmed no
`package.json` in either app changed (diff is lockfile-only).

## 4. Risk & impact on existing functionality

- Lockfile-only change in both apps; `package.json` untouched, so no
  declared dependency range changed.
- `@sentry/browser`/`@sentry/replay` are part of `@sentry/react-native`'s
  own dependency tree (error reporting/crash reporting) — restoring the
  correct resolution only affects whether the *web* build (used only for
  Playwright E2E in CI) can resolve them; this does not change the native
  iOS/Android build path at all (native builds use a different bundler
  entry point that isn't affected by this Metro-web-specific resolution
  gap).
- Blast radius: isolated to `rider-app` and `driver-app`'s web-export
  path. No other package's pinned version changed beyond the intended
  `@sentry/*` subtree — confirmed via `git diff --stat` showing only the
  two `yarn.lock` files touched.

## 5. User-experience effect

None — CI/build infrastructure only. Native mobile builds (what riders
and drivers actually run) don't use the web bundler path this fixes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/yarn.lock` | Restored `@sentry/browser-utils@10.71.0`, `@sentry/feedback@10.71.0`, `@sentry/replay@10.71.0`, `@sentry/replay-canvas@10.71.0` resolved entries | Fresh `yarn install` fixes the incomplete resolution left by PR #4914's bot auto-commit |
| `driver-app/yarn.lock` | Same 4 entries restored | Same |

## 7. Before / after

```
# Before (main HEAD d9a6c61, CI log)
Error: Unable to resolve module @sentry/replay from
.../node_modules/@sentry/browser/build/npm/esm/prod/index.js:
@sentry/replay could not be found within the project or in these directories
```

```
# After (local reproduction)
$ npx expo export --platform web
...
› web bundles (3):
_expo/static/js/web/entry-....js (7MB)
...
Exported: dist
```

## 8. Rollback plan

`git revert` — lockfile-only change, no data or live-behavior impact.
Reverting restores the currently-broken state (would need re-fixing).

## 9. Verification performed

- [x] `yarn install` in both `rider-app/` and `driver-app/` from a clean
  checkout of `main` — confirmed the 4 missing `@sentry/*` entries are
  restored in both lockfiles.
- [x] `npx expo export --platform web` in **both** apps — both now
  succeed and produce `dist/` (previously failed with the `@sentry/replay`
  resolution error in CI).
- [x] Confirmed `package.json` unchanged in both apps (`git diff --stat`
  shows only the two `yarn.lock` files modified).
- [x] Blast-radius check: `git diff --stat` confirms only `rider-app/yarn.lock`
  and `driver-app/yarn.lock` changed; no other lockfile or package.json
  touched.
- Not a production build change in the sense of `npm run build` for
  admin-dashboard — this is rider-app/driver-app Expo web export, which
  is CI/E2E-test infrastructure only, not the shipped native app build.

## What was NOT verified

- Did not run the full Playwright E2E test suite end-to-end in this
  sandbox (no browser-automation environment beyond the export step
  itself) — the fix targets exactly the failing step (`Export Expo web
  build`), which is directly reproduced and confirmed fixed above.
- Did not investigate why the GitHub-Actions "sync yarn.lock to
  package.json" bot step produced an incomplete lockfile in the first
  place (a tooling/version-mismatch question in that bot's own
  implementation, outside this fix's scope) — flagging as a possible
  follow-up if this recurs on a future PR that also touches
  `@sentry/react-native`'s dependency tree.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data impact)
- [x] Blast radius is stated: isolated to rider-app/driver-app web-export
  lockfile resolution (CI/E2E-test infra only, not the native build)
- [x] No silent behavior change to any shipped flow (lockfile-only fix,
  restores intended resolution, no `package.json` range change)
