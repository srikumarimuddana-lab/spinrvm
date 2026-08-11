# Change Impact & Risk Log — Migrate rider-app + driver-app from Expo SDK 57 to SDK 56 (RN 0.86.2 → 0.85.3)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (session on `claude/wallet-payment-vehicle-selection-70tjn0`) |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | rides (platform/release engineering) |
| PR / commit link | this branch |
| Related issue or gap ID | User directive after the Aug 1 breakage chain: run previous-stable SDK, not newest |

## 1. Issue / gap identified

The Aug 1 dependency churn (dependabot expo-stack bump `c23c60f` #2214 and
follow-ups) moved both apps to Expo SDK 57 / react-native 0.86.2 and broke the
release pipeline twice (RNGH renderer-shim resolution; brace-expansion
fingerprint crash). The owner's decision: return to the **previous stable**
SDK line rather than ride the newest. For the record: npm dist-tags mark 57
(`latest: 57.0.12`) as Expo's current stable — SDK 56 is the *previous*
stable (56.0.19). This migration is a deliberate latest-minus-one policy
choice, not a response to 57 being a beta.

## 2. Root cause (of the instability being addressed)

SDK 57 pairs with react-native 0.86, a native line this codebase had
accumulated four separate workarounds for (removed renderer shim redirect,
two version-named react-native patches, compile-from-source flag). The
proven July production builds ran the 0.85 native line (SDK 55 ↔ RN 0.85.2).
SDK 56 pairs with **RN 0.85.3** — the same native minor as those proven
builds.

## 3. Fix / remediation

Both apps moved to the SDK 56 canonical version set, sourced from
`expo@56.0.19`'s own `bundledNativeModules.json` (the table `expo install
--fix` uses; the CLI itself couldn't run — api.expo.dev is blocked by this
environment's proxy):

- `expo ~56.0.19`, `react-native 0.85.3`, `react 19.2.3` (unchanged)
- ~36 `expo-*` packages 57.x → 56.x per app (router → ~56.2.18, updates →
  ~56.0.24, sqlite → ~56.0.5, etc.)
- `react-native-reanimated 4.5.3 → 4.3.1`, `react-native-worklets 0.11.3 →
  0.8.3`, `react-native-screens ~4.26.2 → ~4.26.0`,
  `react-native-safe-area-context → ~5.7.0`, `@stripe/stripe-react-native
  0.63.0 → 0.64.0`, `@react-native-community/netinfo 11.5.2 → 12.0.1` (+
  matching resolutions pin), `datetimepicker 8.6.0 → 9.1.0`,
  `@sentry/react-native → ~7.11.0`, `jest-expo → ~56.0.5`,
  `@react-native/jest-preset → 0.85.3`
- Resolutions unpinned from 57: `expo-modules-core 57.0.8 → 56.0.23`,
  driver's `expo-constants 57.0.8 → 56.0.23` and `@expo/log-box 57.0.2 →
  56.0.14`
- **Unchanged on purpose**: `react-native-gesture-handler ~2.31.1` and
  `react-native-maps 1.27.2` (SDK 56 table values are identical),
  webview 13.16.1 (already the 56 value), navigation/async-storage/notifee
  (deliberate pins, SDK-agnostic), nitro-modules 0.35.9 + auto-play 0.4.7
  (community natives, not SDK-tabled)
- patch-package patches **renamed** `+0.86.2` → `+0.85.3` and verified to
  apply cleanly (they were originally authored on the 0.85 line — the
  ActivityIndicator hunk's own comment says "RN 0.85.2's" — so they carry
  back, not drop)
- **Removed** the RNGH renderer-shim Metro redirect from both metro configs:
  RN 0.85.3 ships `shims/ReactNative.js` again, so the 0.86 workaround is
  dead code
- `runtimeVersion` bumped in both apps (rider `2.0.0 → 3.0.0`, driver
  `2.5.0 → 3.0.0`): the SDK change alters every native module, so 57-era
  binaries must never pull 56 JS over the air, and vice versa
- iOS deployment targets left at 16.4 in BOTH apps: rider's is a product
  requirement (Voltra Live Activities need 16.4); and expo-build-properties
  **56**.0.25 defaults/validates 16.4 as well (checked in its
  `pluginConfig.js`), so the Aug 1 target bump is NOT reverted
- `buildReactNativeFromSource: true` kept (its own comment says the fix
  lands in SDK 56 — flag can likely be dropped after the first green native
  build; annotated in config)
- New Architecture stays ON (reanimated 4 requires it; matches July builds)

## 4. Risk & impact on existing functionality

- Blast radius: **both mobile apps, dependency + build config layer only**;
  zero app-source (.tsx) changes in this migration. Backend, admin, shared/
  untouched.
- The three fixes from this session survive the migration: payment-sheet +
  language-picker restructures are app-source (merged in PR #3655, upstream
  of this branch); the brace-expansion fingerprint fix was **re-verified on
  the 56-line expo-updates** (fingerprints compute in both apps).
- Native-module risk concentrates in the community natives not covered by
  the SDK table: nitro-modules/Android Auto, notifee, Firebase, LogRocket,
  Voltra. All predate SDK 57 in this repo. JS-level verification passed;
  their native compile is only provable in an EAS build (see §9/§10).
- Reanimated 4.5 → 4.3 is a minor-version downgrade: any animation API
  added in 4.4/4.5 and used by app code would fail — tsc across both apps
  is clean, which bounds this risk at the API-surface level.

## 5. User-experience effect

- None until new builds ship. **This migration CANNOT be delivered over the
  air**: it changes native modules, hence the runtimeVersion bumps. Both
  apps need fresh `eas build` binaries (internal track / TestFlight), after
  which OTA updates flow on runtime `3.0.0`.
- Existing installs keep working unchanged until they update.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/package.json` | 40 version pins 57→56 line + resolutions | SDK 56 canonical set |
| `rider-app/yarn.lock` | Regenerated | Follows manifest |
| `rider-app/app.config.ts` | runtimeVersion 3.0.0; source-build comment | Native-break isolation |
| `rider-app/metro.config.js` | RNGH shim redirect removed | Dead code on 0.85.3 |
| `rider-app/patches/*` | Renamed to +0.85.3 | patch-package version match |
| `driver-app/package.json` | 44 version pins + resolutions (incl. constants/log-box 57 pins) | Same |
| `driver-app/yarn.lock` | Regenerated | Same |
| `driver-app/app.config.ts` | runtimeVersion 3.0.0 | Same |
| `driver-app/metro.config.js` | RNGH redirect removed | Same |
| `driver-app/patches/*` | Renamed to +0.85.3 | Same |

## 7. Before / after

```
# Before: expo ~57.0.9 / react-native 0.86.2 / runtimeVersion 2.0.0 (rider), 2.5.0 (driver)
# After:  expo ~56.0.19 / react-native 0.85.3 / runtimeVersion 3.0.0 (both)
```

## 8. Rollback plan

- Nothing live changes until a build is cut: revert the migration commit(s)
  and reinstall — config-layer only, no data remediation.
- If a 3.0.0 build ships and misbehaves: existing installs are unaffected
  (runtime isolation); pull the store rollout / stop promoting the build;
  OTA rollback within runtime 3.0.0 via `eas update:republish` as usual.

## 9. Verification performed (in-session)

- [x] `tsc --noEmit` clean in BOTH apps (bounds API-drift risk of every
  downgraded package at the type level)
- [x] Full `jest` suite green in BOTH apps (exit 0)
- [x] Production JS bundle `expo export --platform android` green for rider
  (Hermes .hbc produced); driver export running at log-writing time —
  result recorded in the commit message
- [x] `@expo/fingerprint` computes in BOTH apps on the 56-line toolchain
- [x] patch-package applies both renamed patches cleanly on 0.85.3
- [x] Version table cross-checked against `expo@56.0.19` bundledNativeModules
  (not hand-picked)

## 10. What was NOT verified — and the required follow-up

- **Native compile (pods/gradle)** — impossible in this container. The first
  `eas build` (or `eas-native-build.yml` CI run) for each app is the real
  native gate. Watch: Firebase pods, nitro-modules/Android Auto, Voltra.
- **On-device feature pass** — the owner's explicit next step ("check all
  the features in 56"). Checklist: login/OTP → book ride (card + wallet
  payment sheet Done button) → live ride tracking → payment settle →
  language picker; driver: go online → receive offer → full trip flow →
  trip-location outbox (SQLite) → Android Auto if used → SOS.
- eslint (pre-existing plugin crash in this environment, unrelated).
- Web export (`build:web`) not re-run.

## 11. Sign-off

- [x] Rollback plan concrete (nothing live until a build ships; runtime isolation)
- [x] Blast radius stated (dependency/build layer, both apps, zero source changes)
- [x] UX field filled in (no change until new builds; OTA impossible for this change by design)
