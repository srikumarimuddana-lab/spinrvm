# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-27 |
| Author | Claude Code |
| Surface(s) | rider-app (test-only) |
| Domain (Sentry tag) | n/a — test infrastructure, no production code path |
| PR / commit link | (filled in on PR) |
| Related issue or gap ID | CR-2026-005 (issue #2437) |

## 1. Issue / gap identified

`rider-app-test` CI job failed on every PR due to one hanging/timing-out test: `searchDestinationPinIntegrity.test.tsx › editing the destination text › clears the previously selected dropoff pin (no stale-pair booking)`. Jest killed it after its 5000ms per-test timeout with "Exceeded timeout of 5000 ms", plus a "Force exiting... async operations that kept running" warning.

## 2. Root cause

The screen under test (`app/search-destination.tsx`) schedules real (non-mocked, non-faked) `setTimeout` calls on mount — a 100ms "focus the active field" effect, and `react-native`'s own `VirtualizedList` internal cell-render timer once the recents list mounts. Under normal load these fire and settle quickly. Under CI's more constrained/contended CPU, they occasionally combine with GC pauses to push the test's actual wall-clock past Jest's 5000ms timeout, even though the test's own assertion is pure synchronous store state (`useRideStore.getState().dropoff`) that never depends on those timers firing. Confirmed via local reproduction: the same test occasionally took ~11s under load and passed cleanly (~0.4–1.3s) otherwise — a load-dependent race, not a logic bug or genuine infinite hang.

## 3. Fix / remediation

Scoped `jest.useFakeTimers()` / `jest.useRealTimers()` (in a `try`/`finally`) around the single flaky test, matching the existing convention already used elsewhere in this suite (`useCompletedRouteRefresh.test.tsx`, `aiChatDeviceLocation.test.ts`). With fake timers, the screen's internal `setTimeout` calls never touch the real event loop during this test, removing the load-dependent variance entirely. No advancing/flushing of the fake timers was needed since the assertion doesn't depend on their side effects. No application code changed — test-file-only.

## 4. Risk & impact on existing functionality

- **Blast radius: single test file, single test.** Only `rider-app/__tests__/searchDestinationPinIntegrity.test.tsx` changed; the fake-timers scope is wrapped in `try`/`finally` so it cannot leak into the other 3 tests in the same file or any other file (`jest.useRealTimers()` always runs before the test returns, even on assertion failure).
- Grepped for other consumers of this test file / `renderScreen()` helper: none — the helper is local to this file, not imported elsewhere.
- No production/application code touched — `app/search-destination.tsx` and the store are unchanged.

## 5. User-experience effect

None. Test-infrastructure-only change; no rider/driver/corporate-admin/internal-admin facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/__tests__/searchDestinationPinIntegrity.test.tsx` | Wrapped the one flaky test's body in `jest.useFakeTimers()` / `jest.useRealTimers()` (`try`/`finally`) | Eliminates load-dependent real-timer race that occasionally pushed the test past Jest's 5000ms timeout in CI |

## 7. Before / after

```tsx
// Before
it('clears the previously selected dropoff pin (no stale-pair booking)', async () => {
  useRideStore.setState({ dropoff: GORDON });
  const renderer = await renderScreen();

  await act(async () => {
    dropoffInput(renderer).props.onChangeText('4321 wakeling');
  });

  expect(useRideStore.getState().dropoff).toBeNull();
});
```

```tsx
// After
it('clears the previously selected dropoff pin (no stale-pair booking)', async () => {
  jest.useFakeTimers();
  try {
    useRideStore.setState({ dropoff: GORDON });
    const renderer = await renderScreen();

    await act(async () => {
      dropoffInput(renderer).props.onChangeText('4321 wakeling');
    });

    expect(useRideStore.getState().dropoff).toBeNull();
  } finally {
    jest.useRealTimers();
  }
});
```

## 8. Rollback plan

`git revert` is sufficient and complete. Test-file-only change, no data, schema, or deployment involved.

## 9. Verification performed

- [x] Reproduced the original failure locally (`npx jest __tests__/searchDestinationPinIntegrity.test.tsx`), confirming the same `Exceeded timeout of 5000 ms` failure mode described in CR-2026-005, and confirmed it as load-dependent (passed cleanly on most runs, failed once under contention) rather than a deterministic hang.
- [x] Ran the fixed test in isolation 5 consecutive times: 4/4 passed every time, no timeout.
- [x] Ran the full `rider-app` suite (`npx jest`, all 49 suites / 421 tests) with the fix applied: **421 passed, 421 total**, no regressions, no new "force exiting" warnings.
- [x] Confirmed no other file imports or depends on this test file's local helpers.
- [ ] Not verified against CI's exact machine/CPU characteristics (this fix removes the mechanism that caused the load-dependent variance, but the fix's effectiveness on GitHub Actions' specific runners will only be confirmed once this PR's `rider-app-test` job runs).

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`
- [x] Blast radius is stated, not assumed — single test, single file, no other consumers
- [x] No silent behavior change to an already-shipped flow — test-only change, zero application code touched
