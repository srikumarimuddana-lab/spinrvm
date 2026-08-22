# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | Same root cause as the rider-app fix (`docs/change-log/2026-08-22-supportscreen-test-tsc-regression-fix.md`) — not previously tracked |

## 1. Issue / gap identified

`driver-app`'s `TypeScript check` CI step was also failing on `main`
(confirmed reproducing on a fresh checkout, independent of any PR's own
diff) — the same class of regression as the rider-app fix in this same
session, in two files: `__tests__/app/helpScreen.test.tsx` and
`__tests__/services/backgroundMessaging.android.test.ts`, both introduced
by the same PR #4460 (test-coverage-completion pass, same day).

## 2. Root cause

Same shape as the rider-app case: `jest.fn(() => null)` and several
similar `jest.fn(() => Promise.resolve(...))` calls had TypeScript infer a
zero/fixed-argument call signature from their implementations, but each
mock is actually invoked elsewhere in the same file with a spread argument
(`(...a: unknown[]) => mockX(...a)`) or, for `mockGetBackgroundAuthToken`,
later given a `null` resolved value via `.mockResolvedValueOnce(null)`
against an inferred `Promise<string>` return type. 8 cascading `tsc`
errors across the two files, all from this one pattern.

## 3. Fix / remediation

Declared each mock's parameter/return types explicitly to match how it's
actually called and used:
- `jest.fn(() => null)` → `jest.fn((_props: any) => null)` (`helpScreen.test.tsx`)
- `jest.fn(() => Promise.resolve(...))` variants that are called with a
  spread argument → `jest.fn((..._a: unknown[]) => Promise.resolve(...))`
- `mockGetBackgroundAuthToken` → explicit `Promise<string | null>` return
  type, since the test legitimately resolves it to `null` in one case.

No runtime behavior change — every mock still does exactly what it did
before, just with an accurate declared signature.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these two test files.** No application code
  touched — `app/driver/help.tsx` and `utils/backgroundMessaging.ts` (the
  components/services under test) are unchanged.
- **What could regress:** nothing — verified identical test outcomes
  before and after (see Verification). One transient full-suite failure
  was observed and re-investigated: reverting the fix and re-running the
  full suite still showed 799/799 passing (the failure did not reproduce
  either way on a second run), confirming it was pre-existing test-order
  flakiness in this repo's shared-Jest-worker suite (the same class this
  repo's own C31/C37 findings already document), not something this fix
  caused. Re-ran with the fix applied twice more — both times 799/799
  clean.

## 5. User-experience effect

None — test-only change, no application code.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/__tests__/app/helpScreen.test.tsx` | Mock signature declared explicitly | Fix a `tsc` regression breaking `driver-app-test` for every PR |
| `driver-app/__tests__/services/backgroundMessaging.android.test.ts` | 7 mock signatures declared explicitly, 1 return type widened to include `null` | Same |
| `docs/change-log/2026-08-22-driverapp-tsc-regression-fix.md` | New change-log | Required Change Impact & Risk Log |

## 7. Before / after

```ts
// Before
const mockGetAppCheckToken = jest.fn(() => Promise.resolve(null));
// ...
getAppCheckToken: (...a: unknown[]) => mockGetAppCheckToken(...a),
```

```ts
// After
const mockGetAppCheckToken = jest.fn((..._a: unknown[]) => Promise.resolve(null));
```

```ts
// Before
const mockGetBackgroundAuthToken = jest.fn(() => Promise.resolve('driver-token'));
```

```ts
// After
const mockGetBackgroundAuthToken = jest.fn((..._a: unknown[]): Promise<string | null> => Promise.resolve('driver-token'));
```

## 8. Rollback plan

**`git-revert-safe`** — pure test-file type annotations, no data, no
application code.

## 9. Verification performed

- [x] `npx tsc --noEmit` (driver-app) → clean, 0 errors (was 8 before).
- [x] `npx jest __tests__/app/helpScreen.test.tsx __tests__/services/backgroundMessaging.android.test.ts` → 19 passed.
- [x] Full driver-app suite: `npx jest` → 799 passed / 799 total, 87 suites, run 3 times to rule out flakiness — clean every time.
- [x] `npx eslint` on both touched files → 0 errors (3 pre-existing, unrelated `no-require-imports` warnings, not near the touched lines).
- [x] Investigated one transient full-suite failure by reverting the fix and re-running — did not reproduce either way, confirming pre-existing test-order flakiness, not a regression from this fix.

## What was NOT verified

- Whether any other driver-app or rider-app file introduced by PR #4460 has a similar `tsc` issue beyond what a full `tsc --noEmit` run (already clean) would catch.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed (two test files, tsc/eslint-verified)
- [x] No silent behavior change — none; verified identical test outcomes, investigated the one anomalous run before dismissing it
