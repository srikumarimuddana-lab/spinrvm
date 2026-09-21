# Change Impact & Risk Log — Android R8 minification (staged to internal profiles)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (Play Console "App optimization: Low" follow-up) |
| Surface(s) | rider-app, driver-app (Android release builds only) |
| Domain (Sentry tag) | drivers / rides (build-config change; no runtime domain owns it) |
| PR / commit link | branch `claude/determined-knuth-jtsriw` |
| Related issue or gap ID | Google Play Console → App optimization panel (rating: **Low**) |

## 1. Issue / gap identified

Google Play Console rates both apps' **App optimization** as **Low**, reporting
Optimization `-`, Shrinking `-`, R8 configuration `-`, and Obfuscation `2%`, with the
recommendation "Upgrade to AGP version 9.0 and use R8 to get the best performance".

## 2. Root cause

**R8 has never been enabled for these apps' Android release builds.** Both apps are Expo
managed builds — there is no committed `android/` directory, so `minifyEnabled` is only
reachable through `expo-build-properties` in `app.config.ts`, and neither app set
`enableProguardInReleaseBuilds`. Expo's default is off, so every release build shipped
unminified, unshrunk and unobfuscated. This was known and written down:
`driver-app/app.config.ts` already said *"Minification is currently OFF (Expo default; no
`enableProguardInReleaseBuilds`/`enableMinifyInReleaseBuilds` anywhere in this config),
making this rule a no-op today"* next to its inert Nitro keep rule.

The reported 2% obfuscation is **not** something our build produced — it is vendor AARs
(Play Services, Firebase) that ship pre-obfuscated and land in the DEX either way.

**AGP 9 migration is separate and was not attempted.**
[Android's official compatibility table](https://developer.android.com/build/releases/agp-9-0-0-release-notes#compatibility)
requires Gradle 9.1.0 for AGP 9.0, not 9.5+. This repo pins Gradle 8.13 via
`plugins/withGradleWrapper.js`; changing AGP needs a coordinated toolchain and
native-module compatibility review. An Expo issue specific to Gradle 9.5 does
not prove every AGP 9 combination is blocked. R8 itself does not require AGP 9.

## 3. Fix / remediation

Enable R8 code shrinking + obfuscation + optimization for Android release builds in both
apps, **gated behind an env var so it reaches internal builds only**:

- `ANDROID_MINIFY = process.env.SPINR_ANDROID_MINIFY === '1'` feeds
  `expo-build-properties.android.enableMinifyInReleaseBuilds`.
- `eas.json` sets `SPINR_ANDROID_MINIFY=1` on the non-production profiles — rider: `preview`;
  driver: `preview`, `android-auto`. Development/test remain debug clients with `0`.
- **`production` explicitly sets it to `0`.** Production-profile builds stay unminified until a
  human validates a minified preview build on a real device. Flipping production on is
  then a one-line `eas.json` edit, not a code change.

Precision worth stating: driver-app's `android-auto` profile is `distribution: store`, so
a minified **AAB** will reach Play from it — but its `submit` config targets the
`internal` track, not production, and it is the only profile that exercises a real head
unit. That is the right place for the first minified bundle; it does not change what the
production track serves.

This is CLAUDE.md pre-merge gate 3 (ship dark, verify in staging/canary, then flip on)
applied to a native build setting, and the rollout posture the user explicitly chose.

**Resource shrinking (`enableShrinkResourcesInReleaseBuilds`) was deliberately NOT
enabled.** Two reasons: Play's three percentages are R8 *code* metrics, so it earns
nothing on that panel; and `shrinkResources` removes resources reachable only by name,
which is exactly how driver-app's Notifee ride-offer channel reaches
`res/raw/ride_offer.mp3` (`plugins/withRideOfferSound`). A silently silenced ride offer is
a dispatch regression, not an app-size win.

**Adversarial review before implementing** (gate 10). Alternative considered: hardcode
`enableProguardInReleaseBuilds: true` in both configs — a 2-line diff that fixes the Play
score on the next upload. Rejected because the first minified binary in existence would be
the one riders and drivers get: R8 breakage in this stack surfaces *only* in release builds
(cf. [expo/expo#30267](https://github.com/expo/expo/issues/30267), expo-video crashing under
ProGuard), and there is no staging gate in front of a store upload. The env gate costs 3
extra lines and buys a real device check. Second alternative: branch on EAS's own
`EAS_BUILD_PROFILE` instead of a new var — rejected because flipping production on would
then require a code change and review rather than a one-line config edit.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface, and wider than the diff looks.** This is four lines of
config, but R8 rewrites *every* Java/Kotlin class in the APK, so the real blast radius is
every native module in both apps. There is no way to narrow it by reading the diff — only
by running a minified build. That is why it is staged.

R8 breaks code that resolves classes/methods by name at runtime (reflection, JNI
registration, serialization). Most AARs ship consumer ProGuard rules that R8 applies
automatically (React Native core, Expo modules, Firebase, Play Services, Stripe, OkHttp,
Notifee), which is why this is normally safe. The modules most worth distrusting here:

| Module | App | Why R8 could break it |
|---|---|---|
| `@iternio/react-native-auto-play` + `react-native-nitro-modules` | driver | Nitro resolves hybrid objects **by class name**. Keep rule exists but has never been exercised by a real build. |
| `@shopify/react-native-skia` | driver | JNI-heavy native rendering. |
| `expo-task-manager` / `expo-location` background task | driver | Background task handlers are registered and looked up **by string name**. |
| `@notifee/react-native` | both | Channel + notification resources referenced by name. |
| `@logrocket/react-native` | both | Its classes are linked in and R8 will process them, but LogRocket is gated **OFF on Android** by default in both apps' `app/_layout.tsx` (Android 16 hidden-API hang — see `docs/android-build-strategy.md`). Only a live risk on a build that sets `EXPO_PUBLIC_ENABLE_LOGROCKET=true`. |
| `@stripe/stripe-react-native` | rider | Payment sheet; money path. |
| `@react-native-firebase/*` (app-check, messaging, crashlytics) | both | Play Integrity attestation + FCM token handling. |

Deliberately **no speculative keep rules were added** beyond driver-app's existing,
vendor-documented Nitro rule. Over-keeping defeats the optimization and hides which rule is
actually load-bearing; the preview build is the evidence-gathering step, and rules get
added with a failure to point at.

**Known observability cost, not yet remediated:** once R8 obfuscates, Java/Kotlin stack
traces in **Sentry** become unreadable unless the ProGuard mapping file is uploaded.
`@sentry/react-native`'s Expo plugin uploads JS sourcemaps, not the ProGuard mapping —
that needs the separate `sentry-android-gradle-plugin`. Firebase Crashlytics is unaffected
(its Gradle plugin uploads mappings automatically), and JS stack traces — the large
majority of errors in a React Native app — are untouched, since R8 does not process the JS
bundle. Bounded, but real. **Wiring up Sentry mapping upload should be treated as a
prerequisite for the production flip, not for this change.**

**No `android.runtimeVersion` bump, in either app.** R8 minifies Java/Kotlin only; the JS
bundle is byte-identical. An OTA update built from this commit is equally valid against a
minified or unminified binary, so the OTA fence does not move.

## 5. User-experience effect

**Nobody, today.** Production store builds are unchanged — `production` explicitly sets the
flag to `0`, so the next store upload is byte-for-byte as unminified as the last one, and the Play
Console rating will still read Low until someone flips it. Nothing is visible mid-session to
a rider mid-ride or a driver online. No copy or notification changes.

Whoever installs the next internal `preview`/`android-auto` build gets the first
minified binary. Expected effect there: smaller download, slightly faster cold start. The
risk is that a native module breaks in ways listed above.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app.config.ts` | Added `ANDROID_MINIFY` const; added `enableMinifyInReleaseBuilds: ANDROID_MINIFY` to `expo-build-properties.android` | Enables R8 when the profile opts in |
| `rider-app/eas.json` | `1` in `preview`; `0` in development/test/production | Release-only validation; explicit production default |
| `driver-app/app.config.ts` | Same two changes; **also corrected the now-stale comment** on the existing Nitro keep rule, which claimed minification was off and the rule a no-op | That comment would actively mislead the next reader after this change |
| `driver-app/eas.json` | `1` in `preview` + `android-auto`; `0` in development/test/production | `android-auto` included because Android Auto is the highest-risk R8 surface and needs the head-unit check |

## 7. Before / after

```ts
// Before — rider-app/app.config.ts, expo-build-properties.android
android: {
    minSdkVersion: 25,
    compileSdkVersion: 36,
    targetSdkVersion: 35,
    kotlinVersion: '2.2.21',
},
// minifyEnabled defaults to false → Play: Optimization "-", Shrinking "-", R8 config "-"
```

```ts
// After
const ANDROID_MINIFY = process.env.SPINR_ANDROID_MINIFY === '1';
// ...
android: {
    minSdkVersion: 25,
    compileSdkVersion: 36,
    targetSdkVersion: 35,
    kotlinVersion: '2.2.21',
    enableMinifyInReleaseBuilds: ANDROID_MINIFY,   // true only on preview for rider
},
```

## 8. Rollback plan

**A new native binary is required.** Set `"SPINR_ANDROID_MINIFY": "0"` in
the affected profile's `env` in `eas.json`, rebuild and reinstall/distribute — the next build is unminified. No
migration, no live data touched, no wallet/ride/insurance-period rows involved. Already-
installed minified internal builds are replaced by the next build from that profile; there
is nothing to reconcile. An OTA update cannot undo native minification in an installed APK.

For Android Auto, set the flag to `0`, rebuild with a higher versionCode, submit
the replacement AAB to the same Play internal track, and update installed apps.
If production is enabled later, its rollback likewise needs a replacement native
release; validate the preview and head-unit builds before making that change.

## 9. Verification performed

- [x] **Blast-radius check** — grepped the whole repo for `enableProguardInReleaseBuilds` /
      `enableMinifyInReleaseBuilds` / `minifyEnabled` / `shrinkResources` / `proguard`:
      confirmed zero prior minification config anywhere, and that driver-app's
      `extraProguardRules` was the only ProGuard artifact in the repo. Diffed both apps'
      `package.json` dependency lists to enumerate the native modules R8 will process
      (table in §4).
- [x] **`eas.json` validity** — both files re-parsed with a strict JSON parser after
      editing, and asserted programmatically that the flag is set on exactly the intended
      profiles and **explicitly `0` on production, development and test** in both apps.
- [x] **Original author's config syntax check (before review fixes)** — `tsc --noEmit --noResolve` on both `app.config.ts` files: no
      syntax errors (no `TS1xxx`). Compared the error profile against the same files at
      `HEAD`: identical except exactly one additional `TS2591` per file, which is
      "cannot find name `process`" from `@types/node` being absent in this container — both
      files already use `process.env` 12–13 times, so this adds no real type error.
- [x] **Reviewed against CLAUDE.md conventions** — pre-merge gates 1 (blast radius),
      2 (additive — a new flag, no existing value repurposed), 3 (ship dark / flip on),
      7 (rollback stated before merge), 10 (alternative named and rejected in §3, plus a
      `spinr-cicd-infra-reviewer` pass on the actual diff).
- [x] **Feature-flagged** — `SPINR_ANDROID_MINIFY`, off by default, off in production.

## 9b. What was **NOT** verified

State plainly, because the boundary here is unusually wide:

- **No Android build or Expo prebuild was run in this review.** The full app dependency
  trees and Android toolchain were not installed. No device/head-unit build evidence
  is attached, so nothing here proves R8 produces a working APK/AAB.
- **The Nitro keep rule was retained, not validated on a head unit.**
- Published `expo-build-properties@57.0.17` was downloaded during review; its config
  validator normalizes the old alias and its Android plugin writes
  `android.enableMinifyInReleaseBuilds`. App-config evaluation passed 39 cases;
  this does not run the complete Expo prebuild or installed app.
- **No visual-regression coverage exists for rider-app or driver-app at all** (CLAUDE.md
  gate 6). Not that it would apply — this change renders nothing — but stating it rather
  than letting silence imply coverage.
- Nothing was checked on a real device, a real head unit, or against live Supabase/Stripe.

### Required before flipping `production` on

0. **Confirm R8 actually ran.** Check the release build log for a successful
   `:app:minifyReleaseWithR8` task and retain `app/build/outputs/mapping/release/mapping.txt`.
   The generated `android.enableMinifyInReleaseBuilds=true` property confirms config
   forwarding only; it does not prove the release task ran. A green debug build
   cannot validate R8. Check the resolved profile env if the property is missing/false.
1. Build `preview` and install its release APK. For driver's `android-auto`, build
   the store AAB, submit using the `android-auto` submit profile, and install from
   Play's internal track. An AAB cannot be sideloaded as an APK.
2. Rider: launch, sign in, book a ride, and complete a **Stripe** payment sheet.
3. Driver: go online, receive a ride offer with **audible** `ride_offer.mp3`, accept it, and
   confirm background location keeps updating with the app backgrounded.
4. Driver: connect a **real Android Auto head unit** and confirm the car surface renders —
   this is what actually tests the Nitro keep rule.
5. Confirm **Firebase App Check / FCM** still arrive server-side (these fail silently, so
   check the dashboard, not the app). LogRocket needs checking only if that build sets
   `EXPO_PUBLIC_ENABLE_LOGROCKET=true`; it is off on Android otherwise.
6. Wire up **Sentry ProGuard mapping upload** (§4) so Java frames stay symbolicated.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (set the profile flag to `0`, rebuild and reinstall; not device-tested)
- [x] Blast radius is stated, not assumed (§4 — stated as wider than the diff)
- [x] No silent behavior change to an already-shipped flow — production is untouched by
      design, and §5 says so explicitly rather than implying "no user impact" from the
      diff's size


## PR #5418 review corrections — 2026-09-14

### Config correction

- Issue/root cause: the rollout instructions name the legacy ProGuard Gradle key.
  Expo normalizes the deprecated config alias to `enableMinifyInReleaseBuilds`;
  the generated property is `android.enableMinifyInReleaseBuilds`. The alias is
  backward-compatible, so this is not evidence that the original build was broken.
- Fix: use the current config key in both apps and explicitly set resource shrinking
  to false. Retain the driver's existing Nitro keep rule.
- Files: `rider-app/app.config.ts`, `driver-app/app.config.ts`, and this impact log.
- Before: `enableProguardInReleaseBuilds: ANDROID_MINIFY`; after:
  `enableMinifyInReleaseBuilds: ANDROID_MINIFY` and
  `enableShrinkResourcesInReleaseBuilds: false`.
- Blast radius: both native Android release builds and their bundled native modules;
  no backend, ride-state, wallet, or API mutation. No iOS configuration change.
- UX: no mid-session change or new copy; only subsequently built Android binaries.
- Alternative: retain the alias and correct only the docs. Using the current key
  avoids depending on the compatibility shim and matches the generated Gradle key.
- Rollback: set the affected profile flag to `0`, rebuild and reinstall/distribute
  a new binary. Neither an env change nor an OTA update alters an installed APK.
- Verification: a one-off Node 24 script strips TypeScript and evaluates both real
  config functions for unset, `0`, `1`, `true`, `false`, and empty flag values;
  checks the current key, explicit resource setting, and retained Nitro rule.
  The current-key assertion fails on the original configuration and passes after
  the edit (12 cases). This does not execute Expo config plugins or Gradle.
- Not verified: no native build, prebuild, device/head-unit test, Sentry mapping
  upload, or visual test. These apps have no active visual regression tooling.


### EAS profile correction

- Issue/root cause: both `test` profiles use `developmentClient: true`, which takes
  precedence over `buildType: apk` and selects `:app:assembleDebug`. A release-only
  R8 flag cannot validate minification there. Production also omitted the flag,
  allowing a value from the selected EAS environment or invoking shell to opt in.
- Fix/files: both `eas.json` files explicitly set `0` on development, test and
  production; only preview and driver's android-auto set `1`. This log records it.
- Before: test=`1`, production/development unset. After: all three explicitly `0`.
- Alternative: convert test to release. Rejected to preserve its existing Metro
  development-client workflow; preview already serves release APK testing.
- Risk/UX: both apps' build configuration only; no mid-session or iOS build-type
  change. Production remains unminified even when the ambient flag is `1`, provided
  EAS applies the committed profile env as documented. Debug clients are retained.
- Rollback: change the affected profile value to `0`, then rebuild/reinstall;
  do not delete the variable, since that can reveal an inherited value of `1`.
- Verification: Node profile checks first failed on the original missing explicit
  off value. After the edit, 39 config/profile cases pass: 12 direct flag cases and
  27 profile/env cases covering all 9 profiles with ambient unset/`0`/`1` values.
  Confirmed test clients remain debug and Android Auto submission remains internal.
  JSON parse and `git diff --check` pass. A before/after comparison also reproduced
  ambient production `1` becoming explicit `0` and confirmed all unrelated Expo/EAS
  fields (including iOS and submission settings) are unchanged. These checks model documented profile env
  precedence; they do not run EAS or prove env forwarding on a build worker.
- Not verified: no native APK/AAB build or installed-binary rollback/device test.


### Toolchain documentation correction

- Issue/root cause: the original PR conflated an Expo Gradle 9.5 issue with AGP 9.0's
  minimum Gradle requirement and incorrectly declared all AGP 9 combinations blocked.
- Fix/files: corrected this log and `docs/android-build-strategy.md` against Android's
  official AGP 9.0 compatibility table (Gradle 9.1.0). No toolchain setting changed.
- Risk/UX: documentation only; no native or runtime behavior change. Rollback is a
  documentation revert. Verification: official source checked; no AGP upgrade tested.
