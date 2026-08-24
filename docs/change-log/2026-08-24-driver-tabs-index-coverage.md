# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | driver-app (test-only) |
| Domain (Sentry tag) | drivers |
| PR / commit link | see PR opened from `claude/coverage-driver-tabs-index` |
| Related issue or gap ID | `ACTION_ITEMS.md` B37 — "Fresh coverage sweep (2026-08-24, post-#4513/#4515-merge)" driver-app tier, `driver/(tabs)/index.tsx` |

## 1. Issue / gap identified

`driver-app/app/driver/(tabs)/index.tsx` (the driver's live dispatch/safety
dashboard) was the worst-covered driver-app file in the latest B37 coverage
sweep at 74.5% line coverage.

## 2. Root cause

Not a bug — a coverage gap. The file is the largest remaining driver-app
screen (~15 stacked hooks/effects: countdown timer, OSRM live-route
polling, saved-polyline reuse, airport-zone overlay, location-permission
fallback, SOS/Safety panel switch, trip-completion modal) and several of
its effect branches (interval ticks, `AppState` foreground resyncs, async
fetch success/failure branches, ref-callback wiring) were never exercised
by `__tests__/app/driverDashboardScreen.test.tsx`.

## 3. Fix / remediation

Test-only change. Added 17 test cases to
`__tests__/app/driverDashboardScreen.test.tsx` (34 → 51 tests) covering:
the ride-offer countdown timer (interval tick-down, and the foreground
`AppState` resync against `offer_expires_at` in both the still-pending and
already-expired cases); the OSRM live-route poller (success + publish,
no-routable-polyline fallback, fetch-throw fallback, and the
not-polled-while-idle guard); the saved-planned-polyline reuse branch for
`ride_offered` (both the `fitToCoordinates` happy path and the
<2-usable-points empty-route branch); airport sub-zone polygon rendering
(3+ points vs. the dropped <3-point case); the denied-location "Open
Settings" button; `MapControls`' `onRecenter` wiring; the `MapView`
`onRegionChange` → `currentRegionRef` write; the `SafetyOverlay` close
callback; and the trip-completion confirmation modal's `onRequestClose`.

While chasing a flaky assertion on the saved-polyline test, found and
fixed a latent flaw in this test file's own `react-native-maps` mock: the
mocked `MapView`'s `useImperativeHandle` call had no deps array, so it
re-installed a brand-new `{ fitToCoordinates, animateToRegion }` pair on
every re-render — discarding whatever had just been called on the
previous instance before a test's assertion could observe it. Added `[]`
deps so the mock behaves like a real native ref (stable identity across
renders, mutated in place), matching how the actual `react-native-maps`
ref works. This only affects the shared mock inside the test file, never
production code.

No production code (`app/driver/(tabs)/index.tsx` itself) was touched —
confirmed via `diff` against a pre-edit backup before finalizing.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `driver-app/__tests__/app/driverDashboardScreen.test.tsx`.**
  No production source file was modified. Ran a `diff` between the
  pre-session copy of `app/driver/(tabs)/index.tsx` and the post-session
  version to confirm zero drift (debug `console.log` lines added while
  investigating the mock-ref issue were reverted before finishing).
- The `useImperativeHandle([]  deps)` fix lives inside this one test
  file's local `jest.mock('react-native-maps', ...)` factory — it is not
  a shared mock file (`__mocks__/`), so no other test file's `MapView`
  mock is affected. Grepped `driver-app/__tests__/` for other
  `jest.mock('react-native-maps'` call sites: none found — this is the
  only test file that mocks `react-native-maps` for the dashboard screen.
- Ran the full driver-app suite (`npx jest --coverage`, no path filter):
  116/116 suites, 1279/1279 tests pass — 0 regressions anywhere in the
  app, not just this file.
- No behavior change to any shipped screen; nothing here is reachable by
  a rider, driver, or admin.

## 5. User-experience effect

None. Test-only change; no production code touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/__tests__/app/driverDashboardScreen.test.tsx` | Added 17 test cases (countdown timer, AppState resync, OSRM live-route poller, saved-polyline reuse, airport-zone polygon, Open Settings button, MapControls recenter, MapView onRegionChange, SafetyOverlay close, completion-modal onRequestClose) and added `[]` deps to the mocked `MapView`'s `useImperativeHandle` | Close the B37 driver-app coverage gap on the worst-covered file; the deps fix was needed to make the saved-polyline assertion reliable |
| `ACTION_ITEMS.md` | Appended a sub-bullet under the B37 "Fresh coverage sweep" entry recording before/after coverage and remaining gaps | Keep the coverage-ratchet backlog current per the section's own convention |

## 7. Before / after

Pure test-only additive change (new test cases) plus one test-fixture fix.
The fixture fix is the only line with pre-existing behavior:

```
# Before (test file's react-native-maps mock)
ReactActual.useImperativeHandle(ref, () => ({ fitToCoordinates: jest.fn(), animateToRegion: jest.fn() }));
```

```
# After
ReactActual.useImperativeHandle(ref, () => ({ fitToCoordinates: jest.fn(), animateToRegion: jest.fn() }), []);
```

## 8. Rollback plan

`git revert` is sufficient and safe — this is a test-only change with no
production code path, no migration, no feature flag, and nothing applied
to live data. Reverting only removes test coverage, it does not change
runtime behavior.

## 9. Verification performed

- [x] Automated tests run: `npx jest --coverage --collectCoverageFrom='app/driver/(tabs)/index.tsx' __tests__/app/driverDashboardScreen.test.tsx __tests__/screens/driver-dashboard-route.test.ts` (targeted, 51/51 pass, 88.65% lines / 85.53% statements / 75.16% branches / 89.74% functions on the target file) and `npx jest --coverage` (full driver-app suite, 116/116 suites, 1279/1279 tests pass, global thresholds — lines 65% / functions 60% / statements 63% — still met)
- [x] `npx tsc --noEmit` — clean, no errors
- [x] `npx eslint __tests__/app/driverDashboardScreen.test.tsx` — 0 errors, 13 pre-existing-style warnings (require-imports, array-type, import-order — matching the file's existing conventions, none introduced new error classes)
- [x] Real production build run: `npm run build:web` (`expo export --platform web`) — exited 0, bundled 2916 modules, produced `dist/` with web bundles + assets
- [ ] Manual repro steps followed in staging — N/A, test-only change, nothing to repro against a running app
- [x] Blast-radius grep performed: searched `driver-app/__tests__/` for other `jest.mock('react-native-maps'` call sites — none found; the fixed mock only affects this one test file
- [x] Reviewed against relevant `CLAUDE.md` convention: Testing Conventions (test files live in `__tests__/`, mock at the boundary, don't hit real deps) — followed the existing file's established mocking pattern throughout
- [x] Feature-flagged: N/A, test-only, no user-visible behavior

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level remediation needed)
- [x] Blast radius is stated, not assumed (isolated to one test file; grepped for other consumers of the fixed mock; full-suite run confirms zero regressions elsewhere)
- [x] No silent behavior change to an already-shipped flow — none occurred; this is additive test coverage only, and the one fixture fix only changes test-mock behavior, never production code
