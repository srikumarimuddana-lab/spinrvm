# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (session, on behalf of vikas@ngitservices.com) |
| Surface(s) | driver-app (dev/test tooling only — no shipped app code) |
| Domain (Sentry tag) | admin (dev-tooling; no runtime domain applies) |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | Filed as suggested task `task_49391bb1` (this PR resolves it), surfaced while verifying #5029 |

## 1. Issue / gap identified

Every driver-app jest test — including files completely unrelated to any
recent change — failed at suite-load time (before any test body runs)
with:

```
TypeError: [BABEL] .../@react-native/jest-preset/jest/react-native-env.js: _lruCache is not a constructor
```

This silently blocked all automated test verification for any driver-app
PR, including the two just-merged icon-fallback fixes (#5028, #5029), both
of which had to disclose "tests added but not executed" as a result.

## 2. Root cause

`node_modules/@babel/plugin-transform-classes/node_modules/@babel/helper-compilation-targets`
was a **redundant nested duplicate** of the top-level
`node_modules/@babel/helper-compilation-targets` — both resolved to the
exact same version (7.29.7; `yarn.lock` has only one merged resolution
range for this package, `"@babel/helper-compilation-targets@^7.28.6",
"@babel/helper-compilation-targets@^7.29.7"`, so there is no genuine
version conflict requiring two copies). This is a yarn v1 linker/hoisting
quirk, not a resolvable version conflict — confirmed by trying two
different `resolutions` field entries (a `**` wildcard nested-path pattern,
and a literal-path exact-version pin), neither of which changed the
physical `node_modules` layout or fixed the failure, even after a full
`yarn install --force`.

The nested duplicate is missing its own nested `lru-cache` dependency
(the top-level copy correctly has one, pinned to the old `^5.1.1`
constructor-style API this Babel version's code uses: `var _lruCache =
require("lru-cache"); ... new _lruCache({...})`). Node's module
resolution from inside the nested duplicate then walks up and finds the
repo's top-level `lru-cache`, which resolves to v10.4.3 — a newer major
version with a different, named-export API (`{ LRUCache }`) that cannot be
used as a bare constructor. Hence "`_lruCache is not a constructor`".

## 3. Fix / remediation

Added `driver-app/scripts/dedupe-babel-helper-compilation-targets.js`,
which deletes exactly that one redundant nested folder if it exists
(no-op otherwise), and wired it into the existing `postinstall` chain
(`patch-package && node scripts/dedupe-shared-nm.js && node
scripts/dedupe-babel-helper-compilation-targets.js`) — mirroring the
**exact same pattern already used in this repo** for an analogous yarn v1
hoisting quirk (`dedupe-shared-nm.js`, which removes a redundant nested
`node_modules/@spinr/shared/node_modules`). After removal, Node's
resolution for `@babel/helper-compilation-targets` from inside
`@babel/plugin-transform-classes` walks up to the top-level copy — the
identical version, with its own correctly-nested `lru-cache@5.1.1`.

Considered and rejected: a `resolutions` field entry (tried two variants,
neither changed the physical layout — see §2); patching `lru-cache` itself
or `@babel/helper-compilation-targets`'s source via `patch-package`
(unnecessarily invasive for what is purely a redundant-directory problem,
and would need re-patching on every dependency bump); switching driver-app
off yarn (far out of scope).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to dev/test tooling.** This changes nothing
  about the shipped app — `postinstall` only runs during `yarn
  install`/CI setup, never in a built app bundle or at runtime. No app
  code, no backend, no schema, no API change.
- Deletes only a node_modules directory that is never committed to git
  (already gitignored) and is provably redundant (identical version to a
  correctly-configured copy elsewhere in the tree) — nothing of value is
  lost; the deleted files exist unchanged at the fallback location.
- If a future dependency bump changes this exact nested path (e.g.
  `@babel/plugin-transform-classes` or `@babel/helper-compilation-targets`
  diverge to different versions, making the nesting legitimate), the
  script simply no-ops (checks `fs.existsSync` first) — it does not
  force-delete a legitimately-needed nested copy blindly, since at that
  point the path/version specifics would differ from what's hardcoded
  here. If the underlying yarn bug recurs at a *different* nested path in
  the future, this script would not catch it (same limitation the existing
  `dedupe-shared-nm.js` already has for its own target).
- Non-fatal failure mode: if `fs.rmSync` fails for some reason (permissions,
  race), the script warns and exits 0 rather than failing the install —
  consistent with `dedupe-shared-nm.js`'s existing behavior. The original
  `_lruCache` error would then still surface at jest-run time, loud and
  diagnosable, rather than being silently masked.

## 5. User-experience effect

None. This is a developer/CI-tooling-only fix — no rider, driver,
corporate-admin, or internal-admin sees any difference. Not applicable to
mid-session visibility.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/scripts/dedupe-babel-helper-compilation-targets.js` | New file — removes the redundant nested `@babel/helper-compilation-targets` copy under `@babel/plugin-transform-classes/node_modules/` if present | Fix the root cause of every driver-app jest test failing to load |
| `driver-app/package.json` | Added `node scripts/dedupe-babel-helper-compilation-targets.js` to the `postinstall` chain | Run the fix automatically on every `yarn install`, matching the existing `dedupe-shared-nm.js` pattern |
| `docs/change-log/2026-09-05-driver-app-jest-lru-cache-fix.md` | New file (this log) | Required disclosure for a fix on a live-tested surface's tooling |

## 7. Before / after

```json
// Before
"postinstall": "patch-package --error-on-warn && node scripts/dedupe-shared-nm.js"
```

```json
// After
"postinstall": "patch-package --error-on-warn && node scripts/dedupe-shared-nm.js && node scripts/dedupe-babel-helper-compilation-targets.js"
```

## 8. Rollback plan

Plain `git revert` — removing the new script and the `postinstall` line
addition restores the previous (broken) state exactly, with zero data or
runtime impact either way (dev-tooling only, nothing persisted).

## 9. Verification performed

- [x] **Reproduced the failure first**, per this repo's CI-fix convention:
      confirmed `yarn jest __tests__/app/becomeDriverScreen.test.tsx`
      (and any other driver-app test) failed at suite-load time with the
      exact `_lruCache is not a constructor` error before any fix.
- [x] Diagnosed the exact broken path via `require.resolve('lru-cache',
      {paths: [...]})` from inside the nested
      `@babel/helper-compilation-targets` copy, confirming it walked up to
      the incompatible top-level v10.4.3 instead of a compatible nested
      copy.
- [x] Proved the fix by manually removing the redundant nested folder and
      re-running the same failing test: `Test Suites: 1 passed, 1 total /
      Tests: 42 passed, 42 total`.
- [x] Ran the **entire driver-app jest suite** after wiring the script into
      `postinstall` and reinstalling: **128 suites / 1457 tests, all
      passed** (previously: 0 suites could even load).
- [x] Ran the two PRs this issue was blocking
      (`__tests__/app/vehicleInfoScreen.test.tsx`, from #5029): all 13
      tests pass, including the 2 icon-fallback tests that could only be
      claimed as "unexecuted" before this fix.
- [x] `npx tsc --noEmit` — clean.
- [x] Confirmed the new script's no-op path is safe: after the redundant
      folder was already removed, re-running `yarn install --force` and
      the postinstall chain did not error or recreate a problem — the new
      script's `fs.existsSync` guard exits cleanly.
- [x] Confirmed this doesn't touch the committed lockfile: `git diff
      driver-app/yarn.lock` is empty — this fix is dev-tooling-only, no
      dependency version changes.

### What was NOT verified

- Did not verify this exact fix from a truly from-scratch `rm -rf
  node_modules && yarn install` in this sandboxed session (blocked by this
  environment's command-safety classifier on `rm -rf` of `node_modules`).
  Verified instead via `yarn install --force`, which re-resolves and
  re-links every package without requiring a prior manual delete, and
  which was confirmed to still reproduce the underlying nested-duplicate
  condition on at least one prior run in this session before the fix was
  in place.
- Did not verify against a real CI runner (GitHub Actions) — only this
  sandboxed session's environment. The failure and fix are about a
  `node_modules` linker outcome (not source code), so it should reproduce
  identically anywhere the same `yarn.lock` is installed with the same
  yarn v1 version, but a CI-specific quirk (different yarn/node version,
  different cache state) is not ruled out.
- Not a permanent guarantee: if a future dependency bump changes
  `@babel/plugin-transform-classes` or `@babel/helper-compilation-targets`
  versions such that they genuinely diverge (making a real nested copy
  necessary again), this script's no-op-if-missing behavior means it
  simply won't interfere — but if the *same* redundant-duplicate bug
  recurs with those new versions, this script won't catch a version
  mismatch at that new pair of versions; only the current 7.29.7/7.29.7
  duplicate.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, dev-tooling
      only, zero runtime/data impact).
- [x] Blast radius is stated, not assumed (dev/test tooling only, isolated
      to postinstall; explicit no-op safety for when the condition no
      longer applies).
- [x] No silent behavior change to an already-shipped flow — there is no
      user-facing flow here at all (§5).
