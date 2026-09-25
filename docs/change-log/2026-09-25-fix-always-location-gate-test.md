# Change Impact & Risk Log: driver-app `alwaysLocationGate` test order-dependent failure

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (AI-assisted) |
| Surface(s) | driver-app (test files only) |
| Domain (Sentry tag) | drivers (test infrastructure; no runtime code) |
| PR / commit link | branch `claude/fix-always-location-gate-test` |
| Related issue or gap ID | CI `driver-app-test` intermittent red: job 108211618388 (PR #5820), earlier on PR #5813 |

## 1. Issue / gap identified

`driver-app-test` intermittently failed with `FAIL utils/__tests__/alwaysLocationGate.test.ts — Test suite failed to run — TypeError: Cannot read properties of undefined (reading 'create')` at `expo/src/errors/AppEntryNotFound.tsx`. The real `expo-location` was loading even though the test file and `jest.setup.js` both mock it. The same commit passed on other runs and in a plain local full run.

## 2. Root cause

This is a test-order leak through Jest's per-worker resolver cache. Test flakiness is not the cause.

- `hooks/__tests__/useDriverDashboard.socketLifecycle.test.ts` mocked five **installed** modules with `{ virtual: true }`: `react-native`, `@react-native-community/netinfo`, `expo-router`, `@react-native-async-storage/async-storage` and `expo-location`.
- `jest-resolve`'s `getModuleID()` (v30.3.0) memoises the module ID in `_moduleIDCache`, keyed only by `from + moduleName + options`. The `Resolver` instance is shared by every test file that runs in the same worker. A virtual mock makes `getModuleID` return `user:expo-location:...` (the bare name) instead of `user:<abs path>/node_modules/expo-location/...`. Which mocks are virtual is recorded per test file, but that state is not part of the cache key.
- `socketLifecycle` loads the real `hooks/useDriverDashboard.ts`, which imports the real `utils/alwaysLocationGate.ts`. That caches the **virtual** ID for (`alwaysLocationGate.ts`, `expo-location`) and (`alwaysLocationGate.ts`, `react-native`) in the worker's resolver.
- When `alwaysLocationGate.test.ts` later runs in the **same worker**, `jest.setup.js` and the test file register their `expo-location` mocks under the real-path ID. The `import * as Location from 'expo-location'` in `alwaysLocationGate.ts:8` gets the stale virtual ID from the cache, matches no registered mock and loads the real module. That module pulls in `expo/src/Expo.fx.tsx` → `AppEntryNotFound.tsx`, which requires `react-native` from a fresh (uncached) `from`, gets this test's stub (no `StyleSheet`) and crashes on `StyleSheet.create`.
- It is intermittent because it only happens when CI's worker scheduling puts both files on one worker with `socketLifecycle` first.

Candidates I checked and ruled out:
- **Stale transform cache:** CI caches only `node_modules`, not Jest's cache directory.
- **Duplicate `expo-location` copies:** there is exactly one.
- **`babel-plugin-jest-hoist` not hoisting:** I inspected the transformed test, and every `jest.mock` call is hoisted above the requires.
- **`resetModules`/`isolateModules`:** not used.

## 3. Fix / remediation

I removed `{ virtual: true }` from mocks of modules that are actually installed, so every test registers and looks up the same real-path module ID:
- `useDriverDashboard.socketLifecycle.test.ts`: the 5 mocks above. I also updated one comment that called the `react-native` mock "virtual".
- `__tests__/services/backgroundMessaging.test.ts`: `@notifee/react-native`, which is installed, so it had the same hazard for any later suite that mocks notifee.

No assertions changed. No tests were skipped or disabled.

## 4. Risk & impact on existing functionality

- Only test files changed, so there is no production code path.
- The mock factories are unchanged. Only the ID under which each factory is registered changed. Both edited suites pass.
- A non-virtual `jest.mock` of an installed module is the normal pattern already used by every other driver-app test, including `alwaysLocationGate.test.ts`.
- Blast radius: isolated to the driver-app Jest run.
- There is no interaction with background loops, the ride state machine or money paths.

## 5. User-experience effect

None. This change is test-only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/hooks/__tests__/useDriverDashboard.socketLifecycle.test.ts` | dropped `{ virtual: true }` from 5 mocks; one comment word | virtual IDs leaked into the shared resolver cache and broke later suites' mocks |
| `driver-app/__tests__/services/backgroundMessaging.test.ts` | dropped `{ virtual: true }` from the `@notifee/react-native` mock | same class of hazard (module is installed) |
| `docs/change-log/2026-09-25-fix-always-location-gate-test.md` | this entry | CLAUDE.md change-log requirement |

## 7. Before / after

```ts
// Before (socketLifecycle, same shape for all 5)
jest.mock('expo-location', () => ({ /* ... */ }), { virtual: true });
```

```ts
// After
jest.mock('expo-location', () => ({ /* ... */ }));
```

Forced-order evidence (custom test sequencer, `--runInBand`, so both files share one worker):

| Order | Before | After |
|---|---|---|
| socketLifecycle → alwaysLocationGate.test | **FAIL** (1/1), same `reading 'create'` stack as CI | PASS 5/5 sequential runs (34/34 tests each) |
| alwaysLocationGate.test → socketLifecycle | PASS | PASS |

## 8. Rollback plan

Test-only change with no deploy or data impact. `git revert` of the commit is sufficient.

## 9. Verification performed

- [x] Reproduced the CI failure deterministically with a forced file order (`socketLifecycle` first, `--runInBand`). It passes in the reverse order.
- [x] After the fix: the forced-order repro passes 5/5 sequential runs.
- [x] Full driver-app CI command `jest --ci --coverage --forceExit --maxWorkers=2`: 167/167 suites, 2040/2040 tests, coverage thresholds met.
- [x] `tsc --noEmit` for driver-app is clean.
- [x] Blast-radius grep: I searched for `virtual: true` across `driver-app/`, `rider-app/`, `admin-dashboard/` and `shared/` (see §11).
- [ ] No production build is needed (no app code changed).

## 10. What was NOT verified

- I did not observe the fix on CI's real worker scheduling. It was proven by forcing the failing order locally, which is the exact condition CI hits nondeterministically.
- The local run used the main checkout's `driver-app/node_modules`, whose `yarn.lock` is identical to `origin/main`, rather than a fresh `yarn install --frozen-lockfile`.
- No visual tooling is relevant (no UI change).

## 11. Follow-ups (not fixed here: out of this PR's driver-app scope)

- `rider-app/__tests__/notificationPermission.test.ts:43` and `rider-app/hooks/__tests__/useScheduledRideReminder.test.ts:45` mock `expo-notifications`, which is installed in rider-app, with `{ virtual: true }`. This is the same hazard for any rider-app suite that imports the same modules in the same worker.
- `rider-app/hooks/__tests__/useScheduledRideReminder.test.ts:49` mocks `'../../../config'` with `{ virtual: true }`. That path does not resolve to a module (the repo root `config/` holds only a JSON file), so virtual is legitimate there. No action needed.
- `admin-dashboard/` and `shared/` have no `virtual: true` mocks.
