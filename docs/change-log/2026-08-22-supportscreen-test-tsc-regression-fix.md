# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | Found while diagnosing an unrelated PR's CI failure (`rider-app-test`'s TypeScript check) — not previously tracked in ACTION_ITEMS.md |

## 1. Issue / gap identified

`rider-app/__tests__/supportScreen.test.tsx`'s `TypeScript check` CI step
was failing on `main`, breaking `rider-app-test` for every PR (verified:
reproduces on a fresh checkout of `main`, independent of any PR's own
diff). 4 `tsc` errors, all stemming from one root cause.

## 2. Root cause

`const mockSupportScreen = jest.fn(() => null);` — TypeScript infers the
mock's call signature from its implementation (`() => null`, zero
parameters), so `jest.fn()`'s generic resolves to a zero-argument mock.
The very next line calls it with an argument
(`mockSupportScreen(props)`), which `tsc` correctly flags as "Expected 0
arguments, but got 1." The same root cause cascades into 3 more errors
later in the file where `mockSupportScreen.mock.calls[0][0]` is read — the
inferred zero-arg tuple type has no index 0 to read.

Introduced by PR #4460 (test coverage completion pass, same day) —
confirmed via `git log` on this file.

## 3. Fix / remediation

One-line fix: `jest.fn((_props: any) => null)` — the mock's declared
signature now matches how it's actually called and read, resolving all 4
`tsc` errors with no change to the test's runtime behavior (the mock
function still returns `null` either way).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one test file.** No application code
  touched — `app/support.tsx` (the component under test) is unchanged.
- **What could regress:** nothing — this is a type-annotation fix with
  identical runtime behavior. Verified: same 3 tests, same assertions, same
  pass/fail outcomes before and after (only `tsc` was failing, not `jest`
  itself, which never actually ran the tests due to the type-check gate).

## 5. User-experience effect

None — test-only change, no application code.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/__tests__/supportScreen.test.tsx` | `jest.fn(() => null)` → `jest.fn((_props: any) => null)` | Fix a `tsc` regression breaking `rider-app-test` for every PR |
| `docs/change-log/2026-08-22-supportscreen-test-tsc-regression-fix.md` | New change-log | Required Change Impact & Risk Log |

## 7. Before / after

```ts
// Before
const mockSupportScreen = jest.fn(() => null);
```

```ts
// After
const mockSupportScreen = jest.fn((_props: any) => null);
```

## 8. Rollback plan

**`git-revert-safe`** — pure test-file type annotation, no data, no
application code.

## 9. Verification performed

- [x] `npx tsc --noEmit` (rider-app) → clean, 0 errors (was 4 before).
- [x] `npx jest __tests__/supportScreen.test.tsx` → 3 passed.
- [x] Full rider-app suite: `npx jest` → 693 passed / 693 total, 87 suites, 0 regressions.
- [x] `npx eslint __tests__/supportScreen.test.tsx` → clean.

## What was NOT verified

- Whether any other file introduced by PR #4460 (the same-day pass this regression traces to) has a similar `tsc` issue — only this one file, the one actually breaking CI, was checked. A full `tsc --noEmit` run (already performed above) would surface any others; none found beyond this file.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed (one test file, grep/tsc-verified)
- [x] No silent behavior change — none; verified identical test outcomes
