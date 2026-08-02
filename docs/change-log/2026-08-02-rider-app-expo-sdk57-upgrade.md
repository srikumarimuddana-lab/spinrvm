# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | rider-app (mobile) |
| Domain (Sentry tag) | n/a (mobile build/dependency change, not a backend domain) |
| PR / commit link | (this PR) |
| Related issue or gap ID | Rework of PR #2209 (Dependabot `expo-stack` group bump, 32 updates) |

## 1. Issue / gap identified

PR #2209 (Dependabot) bumped ~27 `expo-*` submodules and `react-native` to the SDK 57 line, but left the core `expo` package itself pinned at `~55.0.28` (its own semver range was too tight for Dependabot to move it) and missed `expo-constants` entirely. That left rider-app in an internally-inconsistent state — submodules built for SDK 57 paired with an SDK 55 anchor package — which is not a state Expo supports or tests. #2209 also had 6 of 7 failing CI checks that were pre-existing/unrelated noise (missing visual-regression baselines, Trivy CVEs on the backend image, etc.), obscuring the one real, PR-caused failure (a Node 20-vs-22 engine mismatch) and making it hard to tell what was actually broken.

## 2. Root cause

Dependabot groups packages by name pattern (its `expo-stack` group) and bumps each package independently within its own declared semver range. `expo`'s own `package.json` range (`~55.0.26`) only allows patch bumps within 55.x, so Dependabot correctly left it alone by its own rules — but that means the group bump silently produced an unsupported version combination rather than a real SDK upgrade. Nothing in Dependabot's process cross-checks package versions against Expo's own compatibility manifest.

## 3. Fix / remediation

Completed the SDK 57 upgrade properly, verified against Expo's own `bundledNativeModules.json` compatibility manifest (fetched directly from `expo/expo`'s `sdk-57` branch) rather than assumption:

- `expo`: `~55.0.26` → `~57.0.9`; `expo-constants`: `~55.0.16` → `~57.0.8` (the one package Dependabot's diff missed); all other `expo-*` submodules and `react-native` (`0.85.2` → `0.86.2`) match what Dependabot already proposed — confirmed against the manifest, not just accepted at face value.
- `resolutions.expo-modules-core`: `55.0.25` → `57.0.8`, `jest-expo`: `~55.0.18` → `~57.0.3`, `@react-native/jest-preset`: `0.85.2` → `0.86.2` — supporting pins that must track the SDK line but aren't Expo's own submodules, so Dependabot's grouping never touches them.
- Removed 2 obsolete `patch-package` patches (`expo`, `expo-modules-core`) after confirming line-by-line that both patched issues (an iOS `RCTHost` API signature mismatch, an Android `Promise.kt` nullability mismatch) are already fixed upstream in the SDK 57 source — not deleted on assumption. Regenerated the 2 patches that still apply (`react-native`, `@react-native/gradle-plugin`) under their new version-numbered filenames.
- `react-native-screens`: `~4.23.0` → `~4.26.2` — required because `expo-router@57.x` itself declares `^4.26.0` and installing without this bump left two versions of the native module installed simultaneously (`expo-doctor`'s duplicate-dependency check caught this). The earlier `4.23.0` pin was to dodge a `4.24.0` regression (per `driver-app`'s app.config.ts comment); `4.26.2` is past that specific bug and is the version Expo's own SDK 57 pairing expects.
- `react-native-reanimated`: `^4.3.0` → `^4.5.3`, `react-native-worklets`: `^0.8.1` → `^0.11.3` — `4.3.0`'s own peer dependency declares `react-native: 0.81 - 0.85`, which does not cover `0.86.2`; `4.4.0`+ is the first stable line declaring `0.83 - 0.86` support and the matching `0.10.x-0.11.x` worklets range.
- **`tsconfig.json`**: SDK 57 changed Expo's base tsconfig `jsx` mode from `"react-native"` to `"react-jsx"` (the automatic runtime), which now requires resolving `react/jsx-runtime` — previously a no-op setting. Files in the sibling `../shared/` workspace package have no reachable `node_modules/react` via normal upward directory resolution (confirmed: no `shared/node_modules`, no repo-root `node_modules/react`), so they failed to type-check under the new mode. Added `react/jsx-runtime`/`react/jsx-dev-runtime` path mappings to `@types/react`'s own declaration files, following the exact pattern the file already uses for bare `react`.
- **`jest.config.js`**: the same tsconfig-paths-to-moduleNameMapper auto-conversion this file's own existing comment already documents for `react` applied to my new paths too, redirecting Jest's runtime `require('react/jsx-runtime')` to a type-only `.d.ts` file with no executable code. Added the same override pattern already established for `react` to redirect back to the real runtime modules.
- **6 files, import-path migration**: as of SDK 56, `expo-router` no longer supports importing `@react-navigation/*` packages directly in application code (runtime API unchanged, only the module specifier moves — confirmed against Expo's own official migration doc). Ran Expo's official codemod (`expo-codemod sdk-56-expo-router-react-navigation-replace`) against `app/`, `components/`, `hooks/`, `lib/`, `store/`; it rewrote 6 real `import { useFocusEffect } from '@react-navigation/native'` call sites to `import { useFocusEffect } from 'expo-router/react-navigation'`. Manually reverted one unrelated cosmetic JSX-paren reformat the codemod's printer introduced in `ride-options.tsx`, and updated `useBottomSheetGuard.test.tsx`'s `jest.mock('@react-navigation/native', ...)` to mock the new import path (the only test that mocked the old path).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to rider-app.** Grepped for other consumers before starting: driver-app has its own independent `package.json`/lockfile (untouched); backend has zero dependency relationship to any JS package (different runtime). No file under `shared/`, `backend/`, or `driver-app/` was modified.
- **`shared/` package**: not modified, but its `.tsx` files are pulled into rider-app's own TypeScript compilation graph (via imports, not `shared/tsconfig.json`, which is never actually applied in that case) — this is why the `jsx-runtime` fix lives in rider-app's own `tsconfig.json`, not `shared/`'s.
- **A related, more serious finding surfaced while investigating this**: `driver-app` on `main` (merged via #2214) has the *same* `expo`-core-vs-submodules mismatch this PR fixes for rider-app — `expo` is still `~55.0.28` there while `expo-router` (`~57.0.8`) and `react-native` (`0.86.2`) have already moved. I verified `driver-app`'s `expo export --platform web` currently still succeeds, but only because the SDK-56+ `expo-router`/`react-navigation` restriction reads the `expo` package's own version to decide whether to enforce — since driver-app's `expo` core is still 55.x, that check hasn't triggered yet even though `expo-router` v57's code is already running against it. This is a live, already-merged inconsistency, not something this PR touches (out of scope — different app, different PR) — flagging it explicitly rather than leaving it to be rediscovered, since it's the same class of risk as what's fixed here.
- **No production build is triggered by this merge.** Per this repo's own convention (`CLAUDE.md`), mobile builds only run on a commit message containing `[build]` — merging this PR does not build or ship anything to the App Store/Play Store, and cannot reach an already-installed app via OTA (`expo-updates` OTA is scoped to `runtimeVersion`, which only changes on a deliberate new native build).

## 5. User-experience effect

None directly from merging. If/when someone triggers a new rider-app native build off this branch, riders on that build get: no visible UI change (this is a dependency-version alignment, not a feature or design change), assuming the on-device behavior matches what's verified here — see "What was NOT verified" below for the real boundary of that assumption.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/package.json` | expo/expo-*/react-native/react-native-screens/reanimated/worklets versions bumped to their SDK 57 pairing; `expo-modules-core` resolution, `jest-expo`, `@react-native/jest-preset` bumped to match | Complete, verified SDK 57 upgrade (see §3) |
| `rider-app/yarn.lock` | Regenerated via `yarn install` | Lockfile for the above |
| `rider-app/tsconfig.json` | Added `react/jsx-runtime`/`react/jsx-dev-runtime` path mappings | SDK 57's `jsx: "react-jsx"` mode broke type resolution for `shared/` files |
| `rider-app/jest.config.js` | Added `moduleNameMapper` overrides for the same two paths | Prevent Jest from resolving the new tsconfig paths to type-only files |
| `rider-app/app/(tabs)/account.tsx`, `activity.tsx`, `index.tsx`, `rider-app/app/lost-and-found.tsx`, `payment-confirm.tsx`, `ride-options.tsx`, `rider-app/hooks/useBottomSheetGuard.ts` | `@react-navigation/native` → `expo-router/react-navigation` import | Official SDK 56+ expo-router/react-navigation migration (codemod) |
| `rider-app/hooks/__tests__/useBottomSheetGuard.test.tsx` | Updated `jest.mock()` target to match | The only test mocking the old import path |
| `rider-app/patches/expo+55.0.26.patch`, `expo-modules-core+55.0.25.patch` | Deleted | Both patched issues confirmed already fixed upstream in SDK 57 |
| `rider-app/patches/react-native+0.85.2.patch` → `react-native+0.86.2.patch`, `@react-native+gradle-plugin+0.85.2.patch` → `+0.86.2.patch` | Renamed/regenerated | Still-needed patches, retargeted at the new installed version |

## 7. Before / after

`rider-app/tsconfig.json` (representative — full diff is larger):

```
# Before
"react": [
  "./node_modules/@types/react"
],
"react-native": [
```

```
# After
"react": [
  "./node_modules/@types/react"
],
"react/jsx-runtime": [
  "./node_modules/@types/react/jsx-runtime"
],
"react/jsx-dev-runtime": [
  "./node_modules/@types/react/jsx-dev-runtime"
],
"react-native": [
```

Import-path migration (6 files, same shape each time):

```
# Before
import { useFocusEffect } from '@react-navigation/native';
```

```
# After
import { useFocusEffect } from 'expo-router/react-navigation';
```

## 8. Rollback plan

`git-revert-safe`. This is a dependency/config change with no data migration, no schema, no server-side state — reverting the commit restores the SDK 55 dependency set, tsconfig, jest config, and import paths exactly. The regenerated `yarn.lock` reverts cleanly with it. No second deploy or manual cleanup step needed, and (per §4) nothing has been built or shipped from this branch yet, so there is no live state to unwind either.

## 9. Verification performed

- [x] `yarn install` — clean, zero patch-apply errors/warnings after patch cleanup (verified twice: once showing the 2 real failures pre-cleanup, once clean after)
- [x] `npx tsc --noEmit` — zero errors (was 12 `TS2875`/`TS7016` errors before the tsconfig fix)
- [x] `CI=true npx jest --silent` — 434/434 tests passing (matches the exact known baseline count from a prior commit's own verification note), including after fixing the one test that broke from the import-path migration
- [x] `npx eslint .` — 28 errors, matching this repo's documented, accepted baseline for rider-app exactly (per the CI lint-trend gate's own stated threshold); confirmed none of the 28 are in any file this PR touches
- [x] `npx expo-doctor` — 18/20 passing (was 17/20 before the `react-native-screens` fix; the duplicate-dependency check is now clean)
- [x] `npx expo export --platform web` — full bundle succeeds (2498 modules, zero errors) — this is the same check that failed on the original #2209 diff before the react-navigation migration
- [x] Blast-radius grep performed — see §4
- [x] Every version bump cross-checked against Expo's own `bundledNativeModules.json` manifest (fetched from `expo/expo`'s `sdk-57` branch) rather than assumed correct from Dependabot's proposal alone
- [x] Both removed patches read line-by-line and confirmed already-fixed upstream before deletion (not assumed obsolete)

## 10. What was NOT verified

- **No on-device or simulator testing.** This sandbox has no iOS/Android device, simulator, or native build toolchain. Everything above is JS-level verification (install, typecheck, unit tests, lint, web bundle export) — it catches anything that breaks the build or an existing unit test, but cannot catch a runtime behavior change (a screen rendering differently, a gesture/animation regression from the reanimated/worklets bump, a navigation-transition difference from the react-navigation import migration) that only manifests when the app actually runs.
- **`expo-doctor`'s 2 remaining failures** (config-schema validation, React Native Directory metadata check) could not be resolved or ruled out with certainty — both call external hosts (`api.expo.dev`, `api.reactnative.directory`) that this sandbox's network policy blocks (confirmed via direct `curl`, both return `403` on the CONNECT tunnel). These may or may not surface a real issue; they should be re-run from an unrestricted environment before this ships to a real build.
- **A real device/simulator smoke test is still needed before anyone triggers a `[build]` release off this branch** — per the risk assessment already discussed, merging this PR is safe with respect to the currently-live app (see §4), but shipping a new build without that smoke test would carry the residual runtime-behavior risk noted above.
- **driver-app's parallel mismatch (§4) is not fixed here** — flagged, not resolved, since it's a different app/PR.
