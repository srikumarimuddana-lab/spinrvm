# Change Impact & Risk — eslint could not start in either mobile app (T5b)

**Date:** 2026-07-30 · **Branch:** `claude/critical-security-pipeda-breach-pn67ww`
**Surface:** rider-app + driver-app (dependencies, CI) · **Risk:** medium — dependency resolution changes for two shipping apps
**Related:** T5 (`3426b89`, `a29cb801`), CLAUDE.md pre-merge gate 8

---

## Issue / gap identified

`expo lint` could not start in **either** mobile app. Neither had ever been linted;
`|| true` in the CI gate reported both as green (removed in T5, which is how this
surfaced). Two unrelated causes:

**rider-app** — `eslint` pinned `^8.57.0`, but `eslint.config.js` opens with
`require('eslint/config')`, an export that only exists in eslint 9. The app could not
load its own config: `ERR_PACKAGE_PATH_NOT_EXPORTED: Package subpath './config' is not
defined by "exports"`.

**driver-app** — `TypeError: expand is not a function` in
`node_modules/minimatch/minimatch.js:271`, reached from `@eslint/config-array`.

## Root cause

The driver-app failure is the interesting one, and it is a textbook instance of the
trap CLAUDE.md gate 8 describes.

Both apps' `package.json` carried:

```json
"brace-expansion": "^5.0.8",
"**/minimatch/brace-expansion": "^1.1.16"
```

The blanket resolution forces **every** consumer onto brace-expansion 5.x. But
`brace-expansion@5` exports an object — `{ EXPANSION_MAX, EXPANSION_MAX_LENGTH, expand }`
— while `minimatch@3.x` does `var expand = require('brace-expansion')` and calls the
module itself. Hence `expand is not a function`.

Whoever added it saw that coming: the second line is a carve-out meant to keep
minimatch on 1.x. **It never materialised.** Under yarn 1 the blanket resolution
rewrites minimatch's own range to `^5.0.8` as well, so there is no version conflict
left to nest for, a single copy hoists to the root, and minimatch resolves upward to
it. Verified on disk: `node_modules/minimatch/node_modules/brace-expansion` did not
exist, and the top-level copy was 5.0.8.

The lockfiles recorded the resulting semver-invalid collapse verbatim, in both apps:

```
brace-expansion@^1.1.7, brace-expansion@^2.0.1, brace-expansion@^2.0.2,
brace-expansion@^5.0.5, brace-expansion@^5.0.8:
  version "5.0.8"
```

`^1.1.7` means `>=1.1.7 <2.0.0`. It cannot be satisfied by 5.0.8, and neither can
`^2.0.1` or `^2.0.2`.

## Fix / remediation

Remove both resolutions and let normal resolution run. Each major then gets its own
patched release, nested where needed — which is what the blanket override prevented:

| Location | Version |
|---|---|
| top level | `brace-expansion@2.1.3` |
| `minimatch/node_modules/brace-expansion` | `1.1.17` |
| `glob/node_modules/brace-expansion` | `5.0.8` |

Plus, for rider-app only, `eslint: ^8.57.0` → `^9.8.0`, matching driver-app and the
version its own flat config requires.

## The security trade-off, stated plainly

Removing the blanket override **reintroduces one audit finding**, and this is a
judgement call rather than a free win:

```
advisory 1124334  severity=high  module=brace-expansion
  vulnerable: <=5.0.7      patched: >=5.0.8
```

The advisory declares 5.0.8 the only patched version, across all majors. So 1.1.17 and
2.1.3 are flagged, and the *only* way to satisfy the audit is the blanket override —
which breaks minimatch 3.x, which breaks eslint. That is the whole trap: the version
bump that "should" fix the finding breaks the tool the finding is about.

**Accepted, with reasons, per CLAUDE.md gate 8** ("File a `[CR]` for a documented
accepted-risk finding rather than leaving a permanently-red gate unexplained"):

1. **Not reachable from shipped code.** `yarn why minimatch` shows every consumer is
   build/lint/test tooling: `eslint`, `eslint-config-expo`, `@eslint/config-array`,
   `@eslint/eslintrc`, `@typescript-eslint/typescript-estree`, `jest`,
   `@jest/reporters`, `babel-plugin-istanbul`, `test-exclude`,
   `babel-plugin-module-resolver`, and build-time paths inside `expo`
   (`babel-preset-expo`, `@react-native/codegen`, `@expo/cli`, `@expo/fingerprint`).
   Metro does not bundle these into the app.
2. **The vulnerability is a ReDoS**, which needs attacker-controlled pattern input.
   The patterns here come from `eslint.config.js`, `jest.config.js` and Metro config —
   files authored by the team. There is no path from a rider or driver to them.
3. **The alternative is worse.** Keeping the override means two apps that cannot be
   linted at all, indefinitely, which is a larger and more open-ended risk than a
   dev-tooling ReDoS.

**A `[CR]` has not been filed** — that means opening a GitHub issue, which is
outward-facing and was not requested. It should be filed against
`.github/ISSUE_TEMPLATE/ci_change_request.yml` referencing this document.

## Risk & impact on existing functionality

**Blast radius: the whole dependency tree of two shipping apps.** That is inherent —
it is a resolution change — so it was verified by running each app's full test suite
and typecheck rather than reasoned about.

- **rider-app's eslint 8→9 bump** is the larger change of the two: it rewrites the
  eslint subtree (199 lockfile lines removed, 70 added). eslint is a devDependency and
  is not part of the app bundle, so it cannot affect runtime behaviour; the risk is to
  CI and local developer workflow, both exercised below.
- **driver-app's diff is minimal** — 2 lines of `package.json`, 12/11 lines of
  lockfile.
- **`brace-expansion` version changes affect glob expansion** in jest test discovery,
  eslint file matching, and Metro/babel config resolution. Previously those ran on a
  semver-invalid pairing (minimatch 3.x against brace-expansion 5.x), i.e. the state
  being fixed *was* the broken one.
- **No application source was modified** in either app. No backend change.

## User experience effect

**None.** No application code changed; both changes are devDependency/resolution only,
and neither package ships in the app bundle. No rider-, driver-, corporate-admin-, or
internal-admin-facing behaviour is affected.

## Files modified

| File | What changed | Why |
|---|---|---|
| `rider-app/package.json` | Removed both `brace-expansion` resolutions; `eslint` `^8.57.0` → `^9.8.0` | Config requires eslint 9; unblock minimatch |
| `rider-app/yarn.lock` | Re-resolved | Consequence of the above |
| `driver-app/package.json` | Removed both `brace-expansion` resolutions | Unblock minimatch 3.x |
| `driver-app/yarn.lock` | Re-resolved | Consequence |
| `.github/workflows/security-gates.yml` | Replaced the "tooling is broken" note with the measured baselines; added a per-app error budget | The gate can now hold a line instead of only reporting |
| `docs/change-log/2026-07-30-mobile-eslint-could-not-start.md` | New — this file | Required by CLAUDE.md |

## Before / after

```diff
 "resolutions": {
-  "brace-expansion": "^5.0.8",
-  "**/minimatch/brace-expansion": "^1.1.16",
 }
 "devDependencies": {
-  "eslint": "^8.57.0",     // rider-app only
+  "eslint": "^9.8.0",
 }
```

```
# driver-app, before:  TypeError: expand is not a function
#                      at Minimatch.braceExpand (node_modules/minimatch/minimatch.js:271)
# driver-app, after:   ✖ 403 problems (178 errors, 225 warnings)

# rider-app, before:   ERR_PACKAGE_PATH_NOT_EXPORTED: './config'  (ESLint 8.57.1)
# rider-app, after:    ✖ 334 problems (28 errors, 306 warnings)   (ESLint 9.39.5)
```

## CI: budget, not a block

Both apps now lint, on an inherited baseline of 28 and 178 errors. Neither extreme is
right: blocking makes the gate permanently red (which decays into the thing `|| true`
was), and waving it through is `|| true` by another name. So the step now parses the
error count and holds it against the measured baseline — **ratchet down as errors are
fixed, never up.** Verified both directions:

| Lint output | Budget | Result |
|---|---:|---|
| 28 errors (rider baseline) | 28 | pass |
| 34 errors | 28 | **block** |
| 178 errors (driver baseline) | 178 | pass |
| 185 errors | 178 | **block** |
| 12 errors | 28 | pass |
| no problems | 28 | pass |

## Rollback plan

`git revert` restores both lockfiles and `package.json` files. Anyone with an existing
`node_modules` must re-run `yarn install` afterwards — the tree differs, so a stale
tree plus a reverted lockfile is the one genuinely broken state.

Reverting **restores the state where neither app can be linted**, and the CI gate will
then correctly fail with "the linter FAILED TO RUN" rather than silently pass. That is
the intended behaviour of the T5 change and should not be read as this revert breaking
CI.

Partial rollback: the two changes are independent. rider-app's eslint bump can be
reverted without touching driver-app, and vice versa.

## Verification performed

Each app was measured **at HEAD first**, then after the change — because "the tests
pass" means nothing without a baseline:

| | rider-app | driver-app |
|---|---|---|
| Tests at HEAD | 51 suites / **434 passed** | 44 suites / **316 passed** |
| Tests after | 51 suites / **434 passed** (twice) | 44 suites / **316 passed** |
| `tsc --noEmit` | exit 0 | exit 0 |
| eslint before | could not start | could not start |
| eslint after | runs — 28 errors / 306 warnings | runs — 178 errors / 225 warnings |

- **The brace-expansion layout was verified on disk**, not inferred from the lockfile:
  2.1.3 top-level, 1.1.17 nested under minimatch, 5.0.8 under glob.
- **`yarn audit` was re-run** after the change specifically to find out whether the
  advisory returned. It did, which is why the trade-off section above exists rather
  than a claim that nothing regressed.
- **Reachability was established with `yarn why minimatch`**, not assumed.
- **The CI budget logic was unit-tested** as a shell function across six inputs, table
  above, including the regression and improvement directions.
- **Workflow YAML parses.**

### A mistake worth recording

My first attempt hand-edited `driver-app/yarn.lock` to delete the offending block and
ran `yarn install --ignore-scripts`. That **dropped `stack-generator` from the
lockfile** and broke all 44 driver-app test suites (0 tests could run:
`Cannot find module 'stack-generator' from node_modules/stacktrace-js/stacktrace.js`).
It also produced 2 spurious rider-app test failures that I initially attributed to
cross-suite teardown flakiness — they were my broken install.

Both problems came from the same two errors: hand-editing a lockfile instead of
letting the package manager write it, and skipping this repo's postinstall
(`[dedupe-shared-nm]`, which prunes nested `@spinr/shared` copies). Redone as
`package.json`-only edits followed by a normal `yarn install`, both apps are at their
full baseline. Recorded because the failure mode — a dependency "fix" that quietly
removes a package — is exactly what gate 8 is about, and I walked into it while fixing
an instance of it.

## What was NOT verified

- **No production build was run for either app.** These are Expo/EAS apps; a real
  build is an EAS job, not something this sandbox can do. `tsc --noEmit` and the full
  jest suite passed for both, but per CLAUDE.md that is **explicitly not equivalent to
  a production build** — and an eslint major bump is exactly the kind of change that
  can perturb a build toolchain. **An EAS build of both apps is the missing check
  before merge.**
- **No device or simulator run.** Nothing was launched.
- **The eslint findings themselves were not triaged.** 28 + 178 errors are now visible
  for the first time; nobody has looked at whether any indicates a real defect. Some
  could be significant — this change only makes them visible and stops the count
  growing. They belong in `ACTION_ITEMS.md` as their own work item.
- **The `[CR]` for the accepted brace-expansion risk has not been filed** (see above).
- **`yarn audit`'s other findings were not addressed** — 6 moderate / 18 high / 12 low
  in driver-app. Pre-existing, out of scope here, and per CLAUDE.md's PR-review note
  not this change's job.
- **CI has not run any of this.** Local `yarn lint`, `jest`, `tsc`, and a shell
  simulation of the budget logic only.
- **Whether `expo lint` in CI resolves the same tree as local `npx eslint`** was not
  confirmed; CI installs from the lockfile, which is the same input, but the CI job
  itself has not executed.
