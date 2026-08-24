# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | safety (rider-app fix touches the SOS/SafetySheet test) / drivers (driver-app fix touches the settings screen test) |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md C41 — new finding, same failure class as C31/C37 |

## 1. Issue / gap identified

`rider-app-test`/`driver-app-test` intermittently timed out on unrelated
test files (`rideOptionsScreen.test.tsx`, `driverProfileScreen.test.tsx`)
across three separate CI runs this session (PRs #4475, #4481, #4482),
each time treated as "known pre-existing flakiness" without a fix.

## 2. Root cause

Two test files (`safetySheetDismiss.test.tsx` in rider-app,
`settingsWavToggle.test.tsx` in driver-app) render components with a
mount-time async fetch and never await/flush it before the test function
returns. On some runs the promise resolves after RTL's implicit
`afterEach(cleanup)` unmounts the tree, calling `setState` outside
`act()` on an unmounted component — a leaked update that corrupts the
shared Jest worker and manifests as a timeout in whatever test file runs
next, same shape as C31 and C37.

## 3. Fix / remediation

Added a `flushPendingEffects()` helper to each file that awaits a
macrotask-boundary flush inside `act()`, called right after `render()` in
every test — discharging the pending async state update while the
component is still mounted, instead of leaving it to fire after teardown.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the two test files.** No application code
  changed — `shared/hooks/useSafetyPanelConfig.ts` and
  `driver-app/app/driver/settings.tsx` are unmodified; both already have
  correct real-world cleanup/cancellation behavior. This is purely a test
  harness fix.
- No other test file imports or depends on these two files.

## 5. User-experience effect

None — test-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/__tests__/safetySheetDismiss.test.tsx` | Added `flushPendingEffects()`, called after each `render()`; tests made `async` | Discharge SafetySheet's pending fetch before teardown |
| `driver-app/__tests__/screens/settingsWavToggle.test.tsx` | Same pattern | Discharge SettingsScreen's pending fetch before teardown |

## 7. Before / after

```tsx
// Before
it('closes from the X', () => {
  const onClose = jest.fn();
  const { getAllByLabelText } = render(<SafetySheet visible onClose={onClose} {...props} />);
  const targets = getAllByLabelText('Close safety options');
  fireEvent.press(targets[targets.length - 1]);
  expect(onClose).toHaveBeenCalled();
});

// After
it('closes from the X', async () => {
  const onClose = jest.fn();
  const { getAllByLabelText } = render(<SafetySheet visible onClose={onClose} {...props} />);
  await flushPendingEffects();
  const targets = getAllByLabelText('Close safety options');
  fireEvent.press(targets[targets.length - 1]);
  expect(onClose).toHaveBeenCalled();
});
```

## 8. Rollback plan

**`git-revert-safe`** — test-only change, no data/schema/config touched.

## 9. Verification performed

- [x] Both fixed files pass in isolation (4/4 rider-app, 2/2 driver-app)
- [x] Full rider-app suite re-run 3x: 1098/1098 (116 suites) clean every time
- [x] Full driver-app suite re-run 3x: 1089/1089 (110 suites) clean every time
- [x] Confirmed the specific `not wrapped in act(...)` warnings for
      SafetySheet/SettingsScreen no longer appear in any of the 6 runs
- [x] `npx tsc --noEmit` clean on both apps
- [x] `npx eslint` clean on both touched files
- [x] Blast-radius grep: no other file imports these two test files (not applicable, they're leaf test files); confirmed via `git diff --stat` that only these two files changed

## What was NOT verified

- Whether this specific pair of leaks is what actually caused the CI
  timeouts observed on #4475/#4481/#4482 specifically — CI's Jest
  worker-to-file assignment isn't deterministic or independently
  reproducible from this session. The fix stands on its own merits
  (these two files provably leak an unguarded post-unmount update)
  regardless of whether they were the exact trigger for those three
  incidents.
- A repo-wide sweep for the same unflushed-mount-effect pattern in other
  test files — flagged as a candidate class in ACTION_ITEMS.md C41, not
  swept systemically here.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — isolated to two test files, no app code touched
- [x] No silent behavior change — test-only, same assertions, same pass/fail outcomes
