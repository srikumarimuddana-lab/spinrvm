# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code (session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch: `claude/driver-app-expo-sdk57-upgrade`) |
| Related issue or gap ID | #3261 (follow-up to #3260 / #2209) |

## 1. Issue / gap identified

`driver-app` on `main` (merged via #2214) has the same internally-inconsistent Expo SDK state that #3260 fixed for rider-app: the `expo` core package and `expo-constants`/`expo-asset` were still pinned to the 55.x line while `expo-router`, `react-native` (0.86.2), and most other `expo-*` submodules had already moved to the 57.x line. This is an unsupported combination, not a real SDK upgrade — Dependabot's tight `~55.0.28` semver range on the anchor `expo` package blocked it from moving.

## 2. Root cause

Same root cause as #2209/#3260: Dependabot bumps each package independently against its own semver range. `expo`'s range was narrow enough to stay on 55.x while `expo-router` (which has a wider range) moved ahead to 57.x — an internally-inconsistent pairing that `yarn install` doesn't reject, but that Expo's own tooling (`expo-doctor`, `expo export`) does eventually catch, since SDK 56+ removed direct `@react-navigation/*` import support in app code once the `expo` package's own version crosses that threshold. Because driver-app's `expo` core hadn't moved yet, that specific check hadn't fired — but was a build failure waiting to happen the moment `expo` core caught up (issue #3261 called this out explicitly before this fix).

## 3. Fix / remediation

Completed the SDK 57 upgrade properly, following the exact same process used for rider-app (#3260):

1. Bumped `expo` `~55.0.28` → `~57.0.9`, `expo-constants` `~55.0.16` → `~57.0.8`, `expo-asset` `~55.0.17` → `~57.0.8` — versions cross-checked against Expo's own `bundledNativeModules.json` compatibility manifest for the `sdk-57` branch (same source used for rider-app), not assumed.
2. Bumped `react-native-screens` `~4.23.0` → `~4.26.2` (matches `expo-router@57.x`'s real requirement; the existing 4.23.0 pin predates a since-superseded regression, same history as rider-app's screens pin).
3. Bumped `react-native-reanimated` `4.3.0` → `^4.5.3` and `react-native-worklets` `~0.8.1` → `^0.11.3` (reanimated's own peer dependency only supports RN 0.83–0.86 as of 4.4.0+; worklets range must pair at 0.10.x–0.11.x).
4. Bumped `react-native-svg` `15.15.3` → `15.15.4` per the compatibility manifest.
5. Bumped devDependencies: `@react-native/jest-preset` `0.85.2` → `0.86.2` (must track the `react-native` version exactly), `jest-expo` `~55.0.18` → `~57.0.3`.
6. Bumped the `expo-modules-core` yarn resolution `55.0.25` → `57.0.8` (already present as a resolution from a prior fix; needed updating to match).
7. Added tsconfig `paths` entries for `react/jsx-runtime` / `react/jsx-dev-runtime` pointing at `@types/react/...` — SDK 57's `expo/tsconfig.base.json` switched `jsx` mode from `"react-native"` to `"react-jsx"` (automatic runtime), which broke type resolution for the sibling `shared/` workspace package the same way it did for rider-app. Added the matching Jest `moduleNameMapper` override (jest-expo auto-converts tsconfig paths into Jest mappings, which would otherwise redirect `require('react/jsx-runtime')` to a types-only `.d.ts` file with no runtime code) — mirrors the file's existing pattern already documented for bare `react`.
8. Ran Expo's official `expo-codemod sdk-56-expo-router-react-navigation-replace` codemod against `app/` and `components/` — SDK 56+ no longer supports direct `@react-navigation/*` imports in app code when `expo-router` is in use. Migrated 4 files (`app/documents.tsx`, `app/driver/(tabs)/profile.tsx`, `app/driver/lost-and-found.tsx`, `components/activity/ActivityView.tsx`) from `@react-navigation/native` → `expo-router/react-navigation` for `useFocusEffect`. Mechanical import-path change only — the codemod does not touch runtime behavior, confirmed against Expo's migration docs (same codemod, same reasoning as #3260).
9. Updated the one test that mocked the old import path (`__tests__/components/ActivityView.test.tsx`).
10. Deleted 4 obsolete `patch-package` patches after reading each line-by-line and confirming the underlying fixes already ship upstream in the newly-installed versions (identical patches to the ones already verified and deleted for rider-app in #3260 — `EXReactRootViewFactory.mm`'s `bundleConfiguration:` param, `ExpoReactNativeFactory.mm`'s `hostDidStart` gated behind `#if TARGET_OS_OSX`, `Promise.kt`'s nullable `code: String?` overrides, and `RNSVGImage.mm`'s observer calls now gated behind `#if REACT_NATIVE_MINOR_VERSION > 84`). Renamed/regenerated `react-native+0.85.2.patch` → `react-native+0.86.2.patch` and `@react-native+gradle-plugin+0.85.2.patch` → `+0.86.2.patch` via `npx patch-package <pkg>` (diffs are substantively identical — only git blob hashes changed).
11. Added yarn resolutions for `expo-constants` (`57.0.8`) and `@expo/log-box` (`57.0.2`) after `expo-doctor` flagged duplicate native-module installs (nested `expo-constants@57.0.7` under `expo-linking`/`expo-notifications`, and nested `@expo/log-box@57.0.1` under `expo`) — forced a single deduplicated version of each.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to driver-app.** No other surface (rider-app, backend, admin-dashboard) reads driver-app's `package.json`/`yarn.lock`/`tsconfig.json`/`jest.config.js`/patches. `shared/` is a sibling package consumed by file-path (`file:../shared`), not modified by this change.
- **Grep performed**: confirmed no other file in driver-app imports `@react-navigation/native`/`elements` directly beyond the 4 migrated files (`grep -rn "@react-navigation" app/ components/`). Confirmed no other test file mocks the old import path beyond the 1 updated.
- Ride/dispatch/state-machine, wallet/payment, and insurance-period logic are entirely untouched — this is a dependency/build-tooling change with zero application-logic diff.
- The parallel driver-app-only Playwright E2E failure seen in CI on the prior rider-app PR (#3260) is a pre-existing, unrelated issue in this same app — will need to be re-checked independently once this PR's CI runs, but is not caused by this diff (no driver-app source/logic files touched beyond the 4 mechanical import-path changes).

## 5. User-experience effect

- **No visible change to drivers** from merging alone. Mobile builds only trigger on a `[build]` commit-message marker per this repo's convention; `expo-updates` OTA is scoped to `runtimeVersion`, which only changes on a deliberate new native build. Merging this PR ships nothing to an already-installed driver app.
- If/when a future `[build]`-triggered release is cut from a branch containing this change, there is no intended behavior change — the `useFocusEffect` import migration is a mechanical specifier swap with an identical runtime API (per Expo's own migration doc), and all other changes are dependency-version bumps.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/package.json` | Bumped `expo`, `expo-constants`, `expo-asset`, `react-native-screens`, `react-native-reanimated`, `react-native-worklets`, `react-native-svg`, `@react-native/jest-preset`, `jest-expo`; updated `expo-modules-core` resolution; added `expo-constants`/`@expo/log-box` resolutions | Complete the SDK 57 upgrade Dependabot left half-done; dedupe native modules |
| `driver-app/yarn.lock` | Regenerated via `yarn install` | Reflect the above version bumps |
| `driver-app/tsconfig.json` | Added `react/jsx-runtime` / `react/jsx-dev-runtime` path mappings | Fix `shared/` type resolution broken by SDK 57's `jsx: "react-jsx"` tsconfig change |
| `driver-app/jest.config.js` | Added matching `moduleNameMapper` overrides for the two jsx-runtime paths | Prevent jest-expo's tsconfig-paths auto-conversion from redirecting runtime `require()` calls to a types-only file |
| `driver-app/app/documents.tsx`, `app/driver/(tabs)/profile.tsx`, `app/driver/lost-and-found.tsx`, `components/activity/ActivityView.tsx` | `@react-navigation/native` → `expo-router/react-navigation` import for `useFocusEffect` | SDK 56+ / expo-router no longer supports direct react-navigation imports in app code; official codemod migration |
| `driver-app/__tests__/components/ActivityView.test.tsx` | Updated `jest.mock()` target to match the new import path | Keep the mock aligned with the migrated import |
| `driver-app/patches/expo+55.0.26.patch`, `expo-modules-core+55.0.25.patch`, `react-native-svg+15.15.3.patch` | Deleted | Fixes confirmed already shipped upstream in the newly-installed versions |
| `driver-app/patches/react-native+0.85.2.patch` → `react-native+0.86.2.patch`, `@react-native+gradle-plugin+0.85.2.patch` → `+0.86.2.patch` | Renamed/regenerated | Still-needed patches must track the new package versions |

## 7. Before / after

```
# Before (driver-app/package.json)
"expo": "~55.0.28",
"expo-constants": "~55.0.16",
"expo-asset": "~55.0.17",
"react-native-screens": "~4.23.0",
"react-native-reanimated": "4.3.0",
"react-native-worklets": "~0.8.1",

# After
"expo": "~57.0.9",
"expo-constants": "~57.0.8",
"expo-asset": "~57.0.8",
"react-native-screens": "~4.26.2",
"react-native-reanimated": "^4.5.3",
"react-native-worklets": "^0.11.3",
```

```
# Before (driver-app/app/documents.tsx and 3 other files)
import { useFocusEffect } from '@react-navigation/native';

# After
import { useFocusEffect } from 'expo-router/react-navigation';
```

## 8. Rollback plan

`git-revert-safe` — plain `git revert` restores the SDK 55/56 dependency set and import paths exactly. Nothing has been built or shipped from this branch; no data-level state (wallet, ride, insurance-period) is touched by this change, so no data remediation is needed on rollback.

## 9. Verification performed

- [x] Unit tests: `jest` — 343/343 passing (identical count to baseline; only 1 test's mock target changed, no new/removed tests)
- [x] TypeScript: `tsc --noEmit` — zero errors
- [x] Production build: `npx expo export --platform web` — 2 web bundles built successfully, zero errors (this is the exact check that would hard-fail SDK 56+'s react-navigation restriction — verified it passes)
- [x] `expo-doctor` — 18/20 checks pass; the 2 remaining failures are network-blocked in this sandbox (`Check Expo config schema` and `Validate packages against React Native Directory` both hit hosts this environment's proxy blocks — same pattern already confirmed and documented for rider-app in #3260), not code issues
- [x] `expo lint` — 235 problems (102 errors, 133 warnings), **identical to the pre-upgrade baseline measured against `main` in an isolated git worktree** (`git worktree add` + `yarn install --frozen-lockfile` + `expo lint` on `origin/main`). Confirms this upgrade introduces zero new lint findings; existing errors are pre-existing and tracked separately (`security-gates.yml`'s driver-app lint-error budget is 178, well above the 102 measured here both before and after).
- [x] Blast-radius grep performed: confirmed no other file imports `@react-navigation/native`/`elements` directly beyond the 4 migrated files; confirmed no other test mocks the old import path beyond the 1 updated; confirmed driver-app's `package.json`/lockfile/tsconfig/jest config/patches are read by no other surface.
- [ ] Not run: an actual iOS/Android native build via EAS — no device/simulator/native toolchain available in this environment.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain git revert, no data dependency)
- [x] Blast radius is stated: isolated to driver-app
- [x] No silent behavior change to an already-shipped flow — mobile builds only ship on `[build]` marker; this merge alone changes nothing for an installed driver app

## What was NOT verified

- No device or simulator was available in this environment — only JS-level verification (`tsc`, `jest`, `expo lint`, `expo export --platform web`, `expo-doctor`) was possible. A real native build via EAS should be smoke-tested before any `[build]`-triggered release ships off a branch containing this change.
- 2 of `expo-doctor`'s 20 checks call hosts this sandbox's network policy blocks (confirmed via the same investigation done for rider-app's #3260 — `docs.expo.dev`/`api.expo.dev`/`api.reactnative.directory` return 403 on the CONNECT tunnel here); those 2 checks were not able to run to completion.
- No visual/snapshot regression tooling exists for driver-app, so purely visual claims (there are none in this diff — only import specifiers and dependency versions changed) could not be screenshotted; not applicable here since no UI code changed.
