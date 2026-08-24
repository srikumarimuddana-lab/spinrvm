# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code (coverage session, continuing ACTION_ITEMS.md B37) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | (filled after push) |
| Related issue or gap ID | ACTION_ITEMS.md B37 — "Fresh coverage sweep (2026-08-24, post-#4513/#4515-merge)" — rider-app `ride-options.tsx` bullet |

## 1. Issue / gap identified

`rider-app/app/ride-options.tsx` (2284 lines — the largest screen in the app) sat at 80.7% line
coverage, already extended twice by earlier sessions, with several untested branches: the loading
skeleton, the all-unavailable busy banner, the Retry action, the schedule row's clear-on-tap and
clear-icon paths, the saved-cards fetch-failure catch, the `firstAvailableIndex` branch of the
auto-select effect, three of five `proceedWithBooking` 409-reroute status branches, the
`isEngineError` crash-capture branch, the `onMapReady` callback, and the available-promos list's
discount-label/ineligible-tap/empty-state rendering.

## 2. Root cause

Not a bug — a coverage gap. The screen accumulated conditional branches (loading states, error
states, discount-label formatting, multi-way status routing) faster than the test file's branch
coverage kept pace, consistent with the "still has room" note left by the prior two coverage
rounds on this same file.

## 3. Fix / remediation

Test-only change. Added 14 new test cases to `rider-app/__tests__/rideOptionsScreen.test.tsx`
covering the branches listed above, following the same render/assert patterns already used in
the file (mocked stores, `TestRenderer` + `act`, `findByText`/`allText` helpers). Also changed the
`isEngineError` mock from a hardcoded `() => false` to a `jest.fn` so one test could flip it to
`true` and assert `recordNonFatal` fires — this only widens the existing mock's flexibility, no
behavior change to the mock's default (still `false` unless a test opts in).

No changes to `rider-app/app/ride-options.tsx` itself — this is a coverage-only change per the
task scope; no bug was found in the file during this review that would justify touching it.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated to one test file** (`rider-app/__tests__/rideOptionsScreen.test.tsx`).
  No production code changed. `ride-options.tsx` itself is untouched.
- The `isEngineError` mock widening (arrow function → `jest.fn`) is scoped to this test file's own
  `jest.mock('@shared/api/client', ...)` call, which only applies within this test module's
  execution — it does not affect any other test file's mock of the same module (each Jest test
  file gets its own module registry), and does not affect the real `@shared/api/client` module
  used at runtime.
- Full rider-app suite (123 suites / 1374 tests, all files) run and green — 0 regressions from the
  pre-existing 1360 tests plus the 14 new ones.

## 5. User-experience effect

None. Test-only change; no rider, driver, corporate-admin, or internal-admin-facing behavior is
touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/__tests__/rideOptionsScreen.test.tsx` | Added 14 tests; widened the `isEngineError` mock from a static `() => false` to a `jest.fn` so one test can override it | Close coverage gaps in `app/ride-options.tsx` per ACTION_ITEMS.md B37 |
| `ACTION_ITEMS.md` | Appended a note under the B37 "Fresh coverage sweep" entry documenting the before/after coverage numbers and what remains untested | Keep the coverage-ratchet tracking entry current |
| `docs/change-log/2026-08-24-ride-options-coverage-extend.md` | This file | Required Change Impact Log for a commit touching a live-tested surface's app directory (rider-app), per CLAUDE.md policy |

## 7. Before / after

Pure additive test code — no behavior-changing diff to `app/ride-options.tsx`. Coverage numbers
(via `npx jest --coverage --collectCoverageFrom='app/ride-options.tsx'
__tests__/rideOptionsScreen.test.tsx __tests__/ride-options-payment-sheet.test.tsx`):

```
# Before (43 tests)
Stmts 79.16% | Branch 70.58% | Funcs 66.1% | Lines 80.7%

# After (57 tests)
Stmts 84.55% | Branch 78.15% | Funcs 71.18% | Lines 87.13%
```

## 8. Rollback plan

`git revert` is a complete and sufficient rollback here — this is a pure test-file addition with
no production code, no migration, no data written to any live table, and no feature flag involved.
Reverting the commit removes the added tests and the `isEngineError` mock widening with no other
effect.

## 9. Verification performed

- [x] Automated tests run: `npx jest --coverage --collectCoverageFrom='app/ride-options.tsx'
      __tests__/rideOptionsScreen.test.tsx __tests__/ride-options-payment-sheet.test.tsx` (57/57
      pass); full rider-app suite `npx jest` (123 suites / 1374 tests, all green, 0 regressions)
- [ ] Manual repro steps followed in staging — N/A, test-only change, nothing to manually repro
- [x] Blast-radius grep performed: confirmed no other test file imports or mocks
      `rideOptionsScreen.test.tsx`'s local mocks; `isEngineError` mock change is scoped to this
      file's own `jest.mock()` call (per-test-file module registry)
- [x] Reviewed against relevant CLAUDE.md conventions: Testing Conventions (patterns match
      existing `__tests__/rideOptionsScreen.test.tsx` style); no state-machine/money/RLS/PIPEDA
      convention applies to a test-only diff
- [x] `npx tsc --noEmit` — clean
- [x] `npx eslint __tests__/rideOptionsScreen.test.tsx` — only pre-existing findings on unedited
      lines (mock-boilerplate `require()`-import warnings and `react/display-name` on inline mock
      components, all above line 188); none introduced by this change
- [x] **Real production build run**: `npm run build:web` (`expo export --platform web`) — see PR
      for exit status; this is the CLAUDE.md-required build check for any rider-app change (a
      passing dev server or `tsc --noEmit` alone is explicitly not treated as equivalent here)

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live-data caveat applies —
      test-only change)
- [x] Blast radius is stated, not assumed: isolated to one test file plus this doc/tracking update
- [x] No silent behavior change to an already-shipped flow — nothing shipped changed; UX field
      above states "None" explicitly rather than being left blank
