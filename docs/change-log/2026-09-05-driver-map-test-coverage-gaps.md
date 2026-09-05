# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | vikas@ngitservices.com |
| Surface(s) | driver-app (tests only — no production code changed) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (added at PR open) |
| Related issue or gap ID | User asked for full-coverage confirmation on PR #4997's changes; 3 of 8 items there had no dedicated new test |

## 1. Issue / gap identified

3 of the 8 driver-map fixes shipped in PR #4997 had no test proving the new behavior itself — only confirmation that pre-existing tests still passed, which is a materially weaker guarantee. Specifically: the follow-camera throttle timing, the off-follow icon/color swap on the recenter button, and `HeatmapCells`' region viewport filter (which had no test at all, old or new).

## 2. Root cause

Not a code defect — a test-coverage gap acknowledged at the time each change shipped (explicitly disclosed in each item's own Change Impact Log or in conversation) rather than discovered later. This entry closes it.

## 3. Fix / remediation

Added tests only — **no production code was changed in this commit.**

- **Camera-follow throttle** (`driverDashboardScreen.test.tsx`): extended the shared `react-native-maps` mock to expose `animateCamera: jest.fn()` (previously only `fitToCoordinates`/`animateToRegion` were mocked, so the throttle's calls were silently swallowed by `?.()` optional chaining and unobservable). 3 new tests drive `location` changes with `jest.advanceTimersByTime` to prove: the leading-edge call fires immediately on mount, rapid updates within the 700ms window coalesce into one trailing call carrying the latest position, and a call after the window has fully elapsed fires immediately again.
- **Off-follow icon swap** (new `MapControls.test.tsx`): 6 tests render the real `MapControls` component (not a full mock, unlike the existing screen-level test) and assert on the rendered `Ionicons` element's `name`/`color` props and the button's accessibility attributes across `isFollowing` true/false/omitted, plus a live re-render transition and that `onRecenter` still fires regardless of follow state.
- **Heatmap region filter** (new `HeatmapCells.test.tsx`): 6 tests render the real component and count surviving `<Circle>` elements (this app's jest preset defaults `Platform.OS` to `'ios'`, so the soft-blob renderer is what's under test) to prove: out-of-bounds cells are excluded, a `null`/omitted region shows everything (the safe default), the region boundary is inclusive, and non-finite coordinates are dropped rather than crashing the native map.

## 4. Risk & impact on existing functionality

- Zero production risk: this commit touches only `__tests__/**` files. `MapControls.tsx`, `HeatmapCells.tsx`, and `index.tsx` are unmodified.
- The one production-adjacent-looking change is inside a test file: extending the `react-native-maps` mock's `useImperativeHandle` return value in `driverDashboardScreen.test.tsx` with `animateCamera: jest.fn()`. This is additive (an existing mocked method list gaining one more entry) and confirmed not to affect any of the other 49 pre-existing tests in that file (full suite re-run, all passed).
- `MapControls.test.tsx` and `HeatmapCells.test.tsx` are new files — no existing test could regress from their addition.

## 5. User-experience effect

None — test-only change, nothing shipped to any user.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/__tests__/app/driverDashboardScreen.test.tsx` | Added `animateCamera` to the `react-native-maps` mock; added 3 new tests in a `follow-camera throttle` describe block | Prove the camera throttle's leading/trailing/coalescing behavior, not just that the screen still renders |
| `driver-app/__tests__/components/MapControls.test.tsx` (new) | 6 tests for the `isFollowing` icon/color/accessibility swap | No test previously rendered the real component to check this |
| `driver-app/__tests__/components/HeatmapCells.test.tsx` (new) | 6 tests for the region viewport filter | No test previously existed for this component at all |

## 7. Before / after

Not applicable in the usual sense (no behavior-changing production diff) — this entry exists per `CLAUDE.md`'s Change Impact Log requirement for anything touching a live-tested surface's test suite, even test-only additions, so the coverage gap and its closure are on record.

## 8. Rollback plan

Trivial: `git revert` removes only test files and one mock addition; no user-facing or server-side effect either way.

## 9. Verification performed

- [x] `npx jest __tests__/app/driverDashboardScreen.test.tsx` — 52/52 passed (49 pre-existing unchanged + 3 new).
- [x] `npx jest __tests__/components/MapControls.test.tsx` — 6/6 passed (new file).
- [x] `npx jest __tests__/components/HeatmapCells.test.tsx` — 6/6 passed (new file).
- [x] `npx tsc --noEmit -p tsconfig.json` for the full driver-app project — clean, 0 errors.
- [x] Full driver-app suite re-run after all three additions — see final tally in the PR description.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, zero production impact).
- [x] Blast radius is stated, not assumed (test-only; one shared mock extended, confirmed non-breaking).
- [x] No silent behavior change to an already-shipped flow (§5 — none; nothing shipped changed).

## What was NOT verified

Same structural limitation as every other entry from this investigation: none of this — old or new — was confirmed on a real device. These tests prove the logic behaves as designed in isolation; they do not replace an actual driving re-test on the next build.
