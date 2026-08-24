# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code (session_019QUP6QTCZtD1iXcGtd3gQE) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/coverage-become-driver` |
| Related issue or gap ID | ACTION_ITEMS.md B37 (coverage ratchet) — "Fresh coverage sweep (2026-08-24, post-#4513/#4515-merge)" driver-app tier |

## 1. Issue / gap identified

`driver-app/app/become-driver.tsx` (the 5-step driver signup wizard:
Intro/Personal/Vehicle/Documents/Review) sat at 80.68% line coverage / 67.04%
branch coverage per B37's latest ranked sweep, below the file's realistic
ceiling and below several other files already closed out in the same
sweep.

## 2. Root cause

Test-coverage gap, not a code defect: the existing `becomeDriverScreen.test.tsx`
(26 tests) covered the primary happy/validation paths but left several
error-tolerance and secondary-UI branches unexercised — network-failure
fallbacks (service areas, vehicle types, requirements, CRC consent text),
draft persistence/restore, the Android upload-source Alert's actual button
dispatch (only its shape was asserted), a fully-valid Vehicle-step
submission, the non-license keyword-matching branches, and both platforms'
date-picker close mechanics.

## 3. Fix / remediation

Added 16 new test cases to the existing test file, no production code
changed. Coverage moved 80.68% → 98.1% lines, 67.04% → 85.05% branches
(115/115 → 116/116 driver-app suites remain green; see finding below for
one thing surfaced but *not* fixed).

**Flagged, not fixed (out of scope for this test-only change):** while
writing a test for the Work-Eligibility requirement-name keyword branch, found
that `handleSubmit`'s `getExpiryFieldForReq` computes a
`work_eligibility_expiry_date` into the local `legacyExpiries` map exactly
like the license/insurance/inspection/background fields, but the payload
sent to `registerDriver()` only destructures the other four
(`license_expiry_date`, `insurance_expiry_date`,
`vehicle_inspection_expiry_date`, `background_check_expiry_date`) —
`work_eligibility_expiry_date` is silently dropped. This looks like a real
latent gap (a driver's Work Eligibility document expiry is captured in the
UI but never reaches the backend), not intentional. Per CLAUDE.md's
"surface assumptions, don't silently resolve them," this is reported here
rather than fixed — needs confirmation from the requirement's owner (is a
`work_eligibility_expiry_date` field even expected server-side today?)
before anyone changes `handleSubmit`'s payload shape.

## 4. Risk & impact on existing functionality

- Test-only diff. Zero production code in `app/become-driver.tsx` (or any
  other file under `app/`, `store/`, `hooks/`, `utils/`, `lib/`,
  `services/`, `api/`, `components/`) was touched.
- Blast radius: **isolated to one test file**,
  `driver-app/__tests__/app/becomeDriverScreen.test.tsx`. Grepped for other
  importers/consumers of `become-driver.tsx` — it is a route file
  (`app/become-driver.tsx`, Expo Router file-based routing) with no other
  module importing it directly; the only reader is the router itself.
  Grepped for other tests targeting the same screen — none found; this is
  the file's sole test suite.
- No interaction with the ride state machine, money/wallet deltas, or any
  of the 16 backend background loops — this is a driver-app frontend test
  file only.
- `driver-app/jest.config.js`'s `coverageThreshold` (set by B37: lines 65%,
  functions 60%, statements 63%) is untouched — this change only helps
  clear that global floor by a wider margin, it does not raise or lower it.

## 5. User-experience effect

None. No app code changed — drivers see identical behavior in the signup
wizard before and after this change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/__tests__/app/becomeDriverScreen.test.tsx` | +16 test cases (see commit message for the full list) | Close coverage gaps identified in ACTION_ITEMS.md B37's 2026-08-24 sweep |
| `ACTION_ITEMS.md` | Appended a dated sub-bullet under the B37 "Fresh coverage sweep (2026-08-24, post-#4513/#4515-merge)" entry with before/after numbers and the Work-Eligibility finding | Keep the coverage-ratchet backlog entry current, matching its existing prose style |

## 7. Before / after

Pure test-file addition — no existing test was modified or removed, so
there is no behavior-changing diff to show. Coverage numbers:

```
# Before (baseline re-measured this session)
File               | % Stmts | % Branch | % Funcs | % Lines
become-driver.tsx  |   80.28 |    67.04 |   89.83 |   80.68
Tests: 26 passed

# After
File               | % Stmts | % Branch | % Funcs | % Lines
become-driver.tsx  |   98.23 |    85.05 |    98.3 |    98.1
Tests: 42 passed
```

## 8. Rollback plan

`git revert` of this commit is a complete and sufficient rollback — this
change touches only test files and a backlog markdown file, nothing applied
to live data, no migration, no feature flag needed. No second deploy is
required either way since driver-app tests do not ship to the mobile
bundle.

## 9. Verification performed

- [x] Automated tests run: `npx jest --coverage --collectCoverageFrom='app/become-driver.tsx' __tests__/app/becomeDriverScreen.test.tsx` (42/42 passing, coverage above); full driver-app suite `npx jest --coverage` (116 suites, 1278 tests — 1277 passed, 1 pre-existing unrelated flake in `subscriptionScreen.test.tsx` that passes standalone, see below)
- [x] `npx tsc --noEmit` — clean
- [x] `npx eslint __tests__/app/becomeDriverScreen.test.tsx` — 1 error + 2 warnings, all three on pre-existing lines (39-103, the `datetimepicker` mock) that this change did not touch; nothing on the added lines
- [x] Real production build: `npm run build:web` (`expo export --platform web`) — exits 0, produced `dist/`
- [ ] Manual repro / staging check — not applicable, test-only change with no runtime behavior
- [x] Blast-radius grep performed: no other importer of `app/become-driver.tsx`; no other test file targets it
- [x] Reviewed against CLAUDE.md conventions: not a state-machine/money/RLS/PIPEDA-touching change; observability conventions n/a (no logging/metrics changed)
- [ ] Feature-flagged — not applicable, no user-visible change

**subscriptionScreen.test.tsx flake detail:** failed only in the full-suite
coverage-instrumented run (`Exceeded timeout of 15000 ms`), consistent with
the documented pattern in `driver-app/jest.config.js`'s comment on
`testTimeout` (CPU contention under coverage instrumentation as suite size
grows). Re-ran `npx jest __tests__/app/subscriptionScreen.test.tsx` alone:
24/24 passed in 2.5s. Unrelated to this change — that file and its subject
screen were not touched.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (isolated to one test file; grepped for other consumers)
- [x] No silent behavior change to an already-shipped flow — none made; the one code-level finding (Work-Eligibility expiry field dropped from the submit payload) is reported, not silently fixed
