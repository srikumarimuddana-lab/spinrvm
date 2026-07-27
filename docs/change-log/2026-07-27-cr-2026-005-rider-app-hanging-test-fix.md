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

The screen under test (`app/search-destination.tsx`) always renders a `FlatList` for the recents/saved-places section — even with an empty `data` array — which mounts `react-native`'s real `VirtualizedList` internals. `VirtualizedList` schedules its own internal cell-render `setState` via a real (non-mocked) `setTimeout`, independent of any test's `act()` calls. When this test file ran alongside the other 48 rider-app spec files in the same CI worker, that timer firing outside `act()` raced whichever test ran next, intermittently pushing this specific test's wall-clock past Jest's 5000ms timeout — even though the test's own assertion is pure synchronous store state (`useRideStore.getState().dropoff`) that never depends on the timer firing.

Two intermediate test-layer mitigations were tried and both worked reliably in every local reproduction (15+ runs, including CI's exact `yarn test --ci --coverage --forceExit` invocation and serial full-suite runs) but **both still failed identically in CI** on two separate pushes:
1. Scoping `jest.useFakeTimers()`/`jest.useRealTimers()` around the one test — didn't touch the root cause (`VirtualizedList`'s timer still exists, just frozen-but-unflushed, which can itself leave state pending past the test boundary).
2. Flushing those fake timers inside `act()` after render — same issue persisted in CI.

Since local reproduction never matched CI's failure rate despite extensive attempts (including CI's exact command), further guessing at timing mitigations was abandoned in favor of removing the actual timer source.

## 3. Fix / remediation

Mocked `react-native/Libraries/Lists/FlatList` to a minimal non-virtualized stand-in — renders `ListHeaderComponent` plus a synchronous `data.map(renderItem)`, with no `VirtualizedList`, no internal timers, nothing outside `act()`. This is scoped to this one test file via `jest.mock()` (does not touch the real `FlatList` used anywhere else, including in production). The recents/saved-places rows this suite's other tests query (`tapping a recent entry` describe block) live inside `ListHeaderComponent`, not `FlatList`'s `data`/`renderItem` (which is always empty on this screen), so existing test behavior is fully preserved. Also removed the now-unnecessary fake-timer scaffolding from the one test, since the actual timer source no longer exists. No application code changed — test-file-only.

## 4. Risk & impact on existing functionality

- **Blast radius: single test file.** Only `rider-app/__tests__/searchDestinationPinIntegrity.test.tsx` changed; the `FlatList` mock is scoped to this file's Jest module registry only — no other test file or production code is affected.
- Grepped for other consumers of this test file / `renderScreen()` helper: none — the helper and mocks are local to this file, not imported elsewhere.
- No production/application code touched — `app/search-destination.tsx` and the store are unchanged. The real `FlatList`/`VirtualizedList` still renders exactly as before in the actual app; only this test's simulated render substitutes a lighter stand-in.

## 5. User-experience effect

None. Test-infrastructure-only change; no rider/driver/corporate-admin/internal-admin facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/__tests__/searchDestinationPinIntegrity.test.tsx` | Added a `jest.mock('react-native/Libraries/Lists/FlatList', ...)` stand-in (header + synchronous item map, no internal timers); removed now-unnecessary fake-timer scaffolding from the one previously-flaky test | Eliminates `VirtualizedList`'s real internal `setTimeout`-driven `setState` at the source — the actual mechanism that raced other test files in the same CI worker — rather than continuing to fight its symptoms with timer mitigations that didn't hold in CI |

## 7. Before / after

```tsx
// Before — real FlatList mounts VirtualizedList, which schedules a real
// setTimeout-driven internal setState outside any test's act() call
import { TextInput, TouchableOpacity, Text } from 'react-native';
// ...no FlatList mock...
```

```tsx
// After — FlatList replaced with a synchronous, timer-free stand-in
jest.mock('react-native/Libraries/Lists/FlatList', () => {
  const ReactLib = require('react');
  const MockFlatList = ({ ListHeaderComponent, data, renderItem, keyExtractor }: any) =>
    ReactLib.createElement(
      ReactLib.Fragment,
      null,
      ListHeaderComponent,
      ...(data ?? []).map((item: any, index: number) =>
        ReactLib.createElement(
          ReactLib.Fragment,
          { key: keyExtractor ? keyExtractor(item, index) : String(index) },
          renderItem({ item, index }),
        ),
      ),
    );
  return { __esModule: true, default: MockFlatList };
});
```

## 8. Rollback plan

`git revert` is sufficient and complete. Test-file-only change, no data, schema, or deployment involved.

## 9. Verification performed

- [x] Reproduced the original failure locally and via two intermediate fixes that passed locally but reproduced the identical CI failure twice — documented in Section 2 as the investigation trail.
- [x] After the `FlatList` mock: ran the fixed test in isolation, confirmed no `VirtualizedList`/`act()` warnings at all (previously present in every prior run) via `--detectOpenHandles`.
- [x] Ran the full `rider-app` suite with CI's exact invocation (`yarn test --ci --coverage --forceExit --reporters=default`) **13 times consecutively across both the pre-simplification and final versions**: **421/421 passed every time**, no regressions.
- [x] `yarn tsc --noEmit` passes clean (exit 0) — CI's TypeScript check step runs before tests and would fail the job independently if the new mock had a type error.
- [x] Confirmed no other file imports or depends on this test file's local helpers or the `FlatList` mock (file-scoped `jest.mock()`).
- [ ] Local reproduction never matched CI's 100% failure rate on the two intermediate fixes despite matching CI's exact command — the true CI-vs-local discrepancy for those approaches was never fully explained, only worked around structurally. If this final fix does not hold in CI either, the next step is the diagnostic-instrumentation approach (temporary timing logs read from an actual CI run) rather than further guessing.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`
- [x] Blast radius is stated, not assumed — single file, `jest.mock()` is module-registry-scoped to this file only, no other consumers
- [x] No silent behavior change to an already-shipped flow — test-only change, zero application code touched; the real `FlatList` is unaffected everywhere else
