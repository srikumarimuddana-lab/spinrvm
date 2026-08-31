# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-25 |
| Author | Claude Code (interactive session) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rider-app (CI/build) |
| PR / commit link | (opened alongside this entry) |
| Related issue or gap ID | `ACTION_ITEMS.md` C39 |

## 1. Issue / gap identified

`rider-app — expo export (android + ios)` CI check fails on every rider-app PR: `yarn install
--frozen-lockfile` fails outright with `@supabase/supabase-js@2.112.3: The engine "node" is
incompatible with this module. Expected version ">=22.0.0". Got "20.20.2"` — the job's
`setup-node` step pins Node 20, and `2.112.3`'s `engines.node` requires `>=22.0.0`. Tracked as
`ACTION_ITEMS.md` C39, found 2026-08-24 on the unrelated PR #4475, confirmed pre-existing again
on #4552/#4554/#4557/#4558 this session (never this diff's fault, always stood down on).

## 2. Root cause

Dependabot bumped `@supabase/supabase-js` on `main` (commit `c21bd0f7`, PR #4171, a routine
`semver-minor` update — confirmed via the commit body: no CVE/security-advisory language,
purely a version bump) from `2.105.3` to `2.112.3`, without the CI workflow's Node version being
bumped alongside it. `driver-app` was never touched by that dependabot PR and still runs
`2.105.3` today — confirmed via a real CI run (`driver-app — expo export`, run `32903587028`)
that it passes cleanly on Node 20.

## 3. Fix / remediation

Chose option (b) from C39's own two listed options: pin `@supabase/supabase-js` back to
`2.105.3` (the exact pre-bump version) rather than bumping the CI runner to Node 22. Reasoning:
- No source file in `rider-app` or `shared/` imports `@supabase/supabase-js` directly — only a
  `.d.ts` peer-dependency type declaration references it (`shared/build-types/peer-deps.d.ts`).
  There is no runtime code path that would exercise version-specific behavior either way.
- `driver-app` already runs this exact version in production-facing CI today, so `2.105.3` is
  proven, not speculative.
- Reverting to a routine, non-security dependabot bump reintroduces no known CVE (confirmed —
  see §2).

## 4. Risk & impact on existing functionality

- Blast radius: isolated to `rider-app/package.json` + `rider-app/yarn.lock`. No source file
  changed. `yarn.lock` diff is scoped exactly to the `@supabase/*` sub-package family (`auth-js`,
  `functions-js`, `phoenix`, `postgrest-js`, `realtime-js`, `storage-js`, `supabase-js`) plus one
  transitively-pulled-in `@types/ws` line — verified via `git diff` before committing.
- Every resolved sub-package version now matches `driver-app/yarn.lock`'s already-shipped,
  already-CI-green versions exactly (checked line by line).
- No other rider-app dependency changed — `yarn install` (not `--frozen-lockfile`) only touched
  the packages whose resolution actually depends on the pinned version.
- Does not touch `admin-dashboard` or `backend` — `@supabase/supabase-js` there is a separate,
  independently-versioned dependency, out of scope for this fix.

## 5. User-experience effect

- Nobody — no source file changed, no behavior difference for any rider, no UI/API/copy
  changed. This is a build-tooling-only fix.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/package.json` | `@supabase/supabase-js`: `2.112.3` → `2.105.3` | Restore Node-20 compatibility; matches `driver-app`'s already-proven pin |
| `rider-app/yarn.lock` | Regenerated via `yarn install` (never hand-edited) | Lockfile must match the new `package.json` pin |

## 7. Before / after

```
# Before (rider-app/package.json)
"@supabase/supabase-js": "2.112.3",
```

```
# After
"@supabase/supabase-js": "2.105.3",
```

## 8. Rollback plan

`git revert` is fully safe and sufficient — this is a pure dependency-version change with no
data, schema, or running-service state involved. Reverting restores `2.112.3` and the original
(broken-on-Node-20) lockfile state; the CI failure this fix addresses would simply return.

## 9. Verification performed

- [x] Automated tests run — full `rider-app` Jest suite: **1593 tests, 123 suites, all passed**
      (the "worker process failed to exit gracefully" message is a pre-existing Jest teardown
      warning, not a test failure — confirmed unrelated to this change)
- [x] `tsc --noEmit` — clean, zero errors
- [x] **Reproduced the actual failing CI step**: `npx expo export --platform android` (the exact
      command the CI job runs) completed successfully after the pin — this is the closest
      available reproduction of the CI job itself, though this sandbox runs Node 22 (satisfies
      both the old and new pin), so it cannot reproduce the original Node-20 engine-check error
      directly; the fix's correctness instead rests on the `engines.node` field match confirmed
      in §3/§2, not on reproducing the failure locally
- [x] Blast-radius grep performed — confirmed zero runtime imports of `@supabase/supabase-js`
      anywhere in `rider-app`/`shared` source (only a type-declaration reference); confirmed the
      dependabot commit was a routine non-security bump, not a CVE fix being reverted
- [x] Reviewed against relevant CLAUDE.md convention(s) — release gate #8 ("verify a newer/
      patched dependency version actually works... before pinning it") applied in reverse: this
      pins to an *older* version, verified via the same discipline (ran the affected
      install/build/tests before pinning, didn't just downgrade and hope)
- [x] Feature-flagged if user-visible and non-trivial — n/a, not user-visible

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated (two files, one dependency family), not assumed
- [x] No silent behavior change — no behavior changed at all, build-tooling fix only

## What was NOT verified

- Not run under Node 20 directly (this sandbox has Node 22) — the fix's correctness rests on the
  `engines.node` field match plus `driver-app`'s existing green CI on the same pinned version,
  not on a literal local reproduction of the CI runner's exact Node version.
- iOS bundle export (`npx expo export --platform ios`) was not separately re-run in this
  session (only Android) — CI runs both; if iOS-specific behavior ever diverges from Android's
  for this dependency (unlikely, same JS package, no native code), that's unverified here.
- Does not address C39's underlying architectural question (should the CI workflow eventually
  move to Node 22 for good, independent of this specific dependency) — this fix only resolves
  the immediate CI-noise gate for `rider-app`. `ACTION_ITEMS.md` C39 should be updated to
  reflect this fix once the PR merges.
