# Change Impact & Risk Log — Android Auto: first hardware validation and the fixes it forced

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-16 |
| Author | Android Auto / driver-app |
| Surface(s) | driver-app, rider-app (one config plugin), CI |
| Domain (Sentry tag) | drivers |
| PR / commit link | Branch `claude/android-auto-react-native-izvv6c` — commits `9c63593`…`cd3a85e` (follow-up to merged PR #3758) |
| Related issue or gap ID | `docs/carplay-android-auto.md` "unproven on hardware"; audit `[23-6]` keystore custody |

## 1. Issue / gap identified

The Android Auto integration had never run on a head unit. `docs/carplay-android-auto.md`
said so explicitly: *"Still unproven on hardware … no release branch should merge
until that build passes."* The first real attempt surfaced five distinct blockers in
sequence — three of them pre-existing repo defects unrelated to Android Auto, which
would have bitten the next production release regardless.

Observed how: manual testing on a real vehicle head unit, plus an on-surface debug
panel added mid-investigation because the car screen had no other diagnostic channel.

## 2. Root cause

Five separate causes, found in order:

1. **Play upload rejected — wrong signing key.** The registered upload key
   (SHA1 `B7:F7:…`) had no custodian on record; EAS held a different keystore
   (`D7:51:…`). Resolved by a Play upload-key reset, not by code.
2. **Play upload rejected — `AD_ID` permission.** `react-native-fbsdk-next`
   declares `com.google.android.gms.permission.AD_ID` in its library manifest, so
   the merger pulled it into the AAB, contradicting the Play Console
   advertising-ID declaration. Both apps already set
   `advertiserIDCollectionEnabled: false` and run Meta matching server-side, so
   the declaration was accurate and the manifest was wrong.
3. **Play flagged `ACTIVITY_RECOGNITION`.** `expo-sensors` declares it for the
   whole module, but only its `Pedometer` needs it. Only the `Accelerometer` is
   used (`utils/sensorIntegrity.ts`, GPS-spoof detection), which requires no
   permission.
4. **Blank map on the car surface.** The OTA carrying the car code was published
   without `--environment`, so `eas update` rebuilt the JS bundle with no EAS
   environment loaded and every `EXPO_PUBLIC_*` value inlined as an empty string.
   This is the hazard already documented at
   `driver-app/hooks/useDriverDashboard.ts:89`; `EXPO_PUBLIC_BACKEND_URL` survives
   it via a hardcoded fallback in app.config `extra`, the Maps key had none.
5. **Car marker frozen pointing north.** A `liteMode` prop added during (4) on a
   since-disproved compositing theory. Lite mode is a static bitmap that "cannot
   be tilted or rotated at all" and no-ops `animateMarkerToCoordinate`.

Two theories were pursued and disproved: that the Maps API key was restricted to
the wrong signing SHA-1 (disproved — the phone map worked from the same build),
and that a GL `SurfaceView` could not composite onto the car's `VirtualDisplay`
(disproved — the debug panel reported `surface: rendering` while drawing its own
text correctly).

## 3. Fix / remediation

- Config plugin `withoutUnusedPermissions` strips `AD_ID` (both apps) and
  `ACTIVITY_RECOGNITION` (driver-app) via `tools:node="remove"`, chosen over
  flipping the Play declarations because the declarations were the accurate side.
- `eas-build.yml` now passes `--environment` on every `eas update`, for both apps.
- The car surface no longer gates the map on a JS-inlined env var; the
  AndroidManifest key is the authority.
- `liteMode` removed, restoring marker rotation and the glide between fixes.
- An on-surface debug panel (`carDebug.ts`, `CarDebugPanel.tsx`) plus `logError`,
  because `register.ts`'s `log()` was `__DEV__`-gated — compiling every Android
  Auto diagnostic out of exactly the release builds that can reach a head unit.
- Car card restyled: brand tokens from `shared/theme` replacing invented hex, and
  earnings promoted to the hero element with cumulative daily context.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface.** Two changes reach beyond the car layer.

**`eas-build.yml` (highest risk in this set).** It is the OTA path for **both**
rider-app and driver-app. Every future `eas update` now ships `EXPO_PUBLIC_*`
values that were previously blank. That is the intended correction, but it means
the next OTA to any channel is a larger behavioural change than its diff looks:
any surface silently running on a fallback (`EXPO_PUBLIC_BACKEND_URL` via
`extra`) or degraded (`EXPO_PUBLIC_SENTRY_DSN`, `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`)
starts using its real configured value. Watch the first production OTA after this
lands; do not assume it is inert.

**`withoutUnusedPermissions` (rider-app).** rider-app gains the `AD_ID` strip
though it has no Android Auto code. Verified it has the identical fbsdk posture
(`rider-app/app.config.ts:252`, `advertiserIDCollectionEnabled: false`) and would
hit the same Play rejection on its next store submission. `ACTIVITY_RECOGNITION`
is deliberately absent there — `expo-sensors` is not a rider-app dependency
(confirmed absent from both `package.json` and `node_modules`).

**Not touched:** `useDriverStore`, ride state machine, dispatch, insurance-period
writes, money/wallet paths, backend, migrations, background loops. The car layer
remains view-and-controller only and calls the same store actions the phone does.

**Watch on-device:** GPS-spoof detection (`utils/sensorIntegrity.ts`) is a
fraud-prevention surface and `ACTIVITY_RECOGNITION` was removed near it.
Android's raw accelerometer (`TYPE_ACCELEROMETER`) requires no permission —
`ACTIVITY_RECOGNITION` gates the step counter and Activity Recognition API, which
are never imported — so removal should not affect it, but this was reasoned from
the SDK contract, not exercised on a device.

## 5. User-experience effect

**Driver-facing, and visible mid-session** to a driver with the car connected:

- The car screen now renders a live map (previously blank white).
- The car marker rotates to heading and glides between fixes.
- Ride-offer and completed-trip cards lead with earnings at display size, under a
  "YOU'LL EARN" / "YOU EARNED" label; the completed card adds
  "Today · $187.40 · 12 rides" when the store has a summary.
- Colours shift to the real brand palette, matching the phone.
- A "Debug" header action appears in idle on non-production builds only.

No copy change reaches riders, corporate admins, or internal admins. No
notification copy changed. Nothing changes for a driver who never connects a car.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/plugins/withoutUnusedPermissions.js` | New config plugin | Strip library-contributed permissions we don't use |
| `rider-app/plugins/withoutUnusedPermissions.js` | Same plugin | Same fbsdk posture; same latent Play rejection |
| `driver-app/app.config.ts` | Register plugin with AD_ID + ACTIVITY_RECOGNITION | Both were Play submit blockers |
| `rider-app/app.config.ts` | Register plugin with AD_ID | Latent blocker on next store submission |
| `.github/workflows/eas-build.yml` | `--environment` on both jobs' `eas update` | OTAs were stripping every `EXPO_PUBLIC_*` |
| `.github/workflows/generate-upload-keystore.yml` | New one-off workflow | Upload-key reset needed `keytool`, no local terminal |
| `driver-app/lib/androidAuto/carSurface.tsx` | Un-gate map, drop liteMode, debug facts, earnings wiring | Map render + marker rotation + diagnosability |
| `driver-app/lib/androidAuto/carDebug.ts` | New debug store | Car screen had no diagnostic channel |
| `driver-app/lib/androidAuto/CarDebugPanel.tsx` | New on-surface panel | Read failures without tethering a laptop |
| `driver-app/lib/androidAuto/carTheme.ts` | New token module | Car layer had invented its own palette |
| `driver-app/lib/androidAuto/carCard.ts` | Brand tokens; optional earnings context | Brand parity; earnings hero |
| `driver-app/lib/androidAuto/CarTripCard.tsx` | Restyled, earnings as hero | Make the money the largest element |
| `driver-app/lib/androidAuto/register.ts` | `logError`, always-record `log`, Debug toggle | Diagnostics were compiled out of release builds |

## 7. Before / after

**`register.ts` — diagnostics discarded in the only builds that reach a car:**

```ts
// Before — __DEV__ is false in release builds, and Android Auto ignores
// sideloaded Car App Library apps, so this logged nothing on any head unit.
const log = (...args: unknown[]) => {
  if (__DEV__) console.log('[android-auto]', ...args);
};
```

```ts
// After — console stays quiet in release, the buffer always records.
const log = (...args: unknown[]) => {
  if (__DEV__) console.log('[android-auto]', ...args);
  pushDebug('info', ...args);
};
const logError = (...args: unknown[]) => {
  console.error('[android-auto]', ...args);
  pushDebug('error', ...args);
};
```

**`eas-build.yml` — OTAs shipped empty `EXPO_PUBLIC_*`:**

```bash
# Before
eas update --branch "$CHANNEL" --non-interactive --message "$MESSAGE"
```

```bash
# After
case "$CHANNEL" in
  production) EAS_ENV=production ;;
  *)          EAS_ENV=preview ;;
esac
eas update --branch "$CHANNEL" --environment "$EAS_ENV" \
  --non-interactive --message "$MESSAGE"
```

## 8. Rollback plan

Mixed, per change — none of this writes live data, so no data-level remediation
is required.

- **Car UI / debug panel / liteMode:** publish an OTA from the previous commit.
  No rebuild, no store round-trip, effective on next app launch. This is the only
  rollback needed for anything driver-visible.
- **`eas-build.yml`:** revert the workflow file; the next OTA reverts with it. The
  updates already published are not recalled by a revert — an OTA cannot be
  un-published, only superseded, so roll forward with a corrected bundle rather
  than expecting the revert to reach installed apps.
- **Permission plugins:** require a native build to take effect, and equally to
  undo. Not revertible by OTA. Acceptable because the alternative is a Play
  submission that cannot proceed at all.
- **Debug panel:** already gated by `isCarDebugAvailable()`
  (`EXPO_PUBLIC_ENV !== 'production'`), so no production exposure to roll back.

## 9. Verification performed

- [ ] **Automated tests — NOT RUN.** The driver-app jest suite cannot execute in
      this environment: `jest.setup.js:67` fails to resolve `firebase/auth` via
      `moduleNameMapper`, so all 6 `lib/androidAuto` suites abort with **0 tests
      executed**, identically before and after these changes (confirmed on stash).
      The 56 Android Auto unit tests have not run against any of this work.
- [x] Manual validation on a real vehicle head unit — app discovery, template
      creation, surface render, live map, debug panel.
- [x] Blast-radius grep performed: every `expo-sensors` import and `Pedometer`
      usage across both apps; every `EXPO_PUBLIC_BACKEND_URL` consumer and its
      fallbacks; `.easignore` vs `.gitignore` handling in both apps; both
      `eas update` call sites; every `TripCard` literal for the new field.
- [x] Reviewed against CLAUDE.md: "do not silently swallow errors" (drove the
      `logError` split), PIPEDA data minimisation (drove removing permissions
      rather than declaring them), Car App Quality animation ban (drove the
      static-only card design).
- [x] `tsc` error count unchanged at 2 (both pre-existing in
      `shared/config/firebaseConfig.ts`); `eslint` 0 errors across
      `lib/androidAuto` (2 warnings pre-existing in `carSurface.tsx`);
      `expo config --type prebuild` evaluates clean for both apps.
- [ ] Feature flag — not applicable; the debug panel is env-gated, the rest is
      the car surface itself.

## 10. What was NOT verified

- **No test run at all.** See above. `register.test.ts` asserts on
  `headerActionsFor`, and the idle branch changed (added the Debug action), so it
  likely needs updating. `carCard.test.ts` has no case for `earningsTodayLabel`.
  Both are unverified.
- **No visual regression tooling exists for the car surface**, so the redesigned
  card's layout, type scale and contrast were reasoned about, not screenshotted.
  This is a standing repo gap, not specific to this change.
- **Marker rotation and the rich card have not been seen on hardware** — both
  landed after the last device test and need an OTA to validate.
- **The permission strips have not been observed in a merged AndroidManifest**;
  no EAS build was run from this environment.
- **GPS-spoof detection was not exercised on a device** with
  `ACTIVITY_RECOGNITION` absent.
- **`eas update --environment` was not run**; its effect on the published bundle
  is reasoned from EAS semantics.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — `eas-build.yml` reaches both apps
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in
- [ ] **Outstanding:** jest suite must be repaired and the Android Auto tests run
      before this is treated as verified. Recommend a `[CR]` for the
      `firebase/auth` resolution failure — it is a decayed gate, not a
      side-effect of this work.
