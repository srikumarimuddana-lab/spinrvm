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

**The AGP 9.0 half of Play's recommendation is not actionable and was not attempted.**
RN 0.86.3 / Expo SDK 57 pins AGP 8.12; AGP 9 requires Gradle 9.5+, which Expo SDK 57's
`expo-gradle-plugin` blocks ([expo/expo#49550](https://github.com/expo/expo/issues/49550)).
This repo *additionally* pins Gradle 8.13 on purpose — `plugins/withGradleWrapper.js`
records that Gradle 9 breaks `react-native-maps-directions` and `@logrocket/react-native`.
AGP 9 therefore waits on an Expo SDK upgrade; it is not a config flip. R8 is the entire
reachable win today, and it is what moves all three reported percentages.

## 3. Fix / remediation

Enable R8 code shrinking + obfuscation + optimization for Android release builds in both
apps, **gated behind an env var so it reaches internal builds only**:

- `ANDROID_MINIFY = process.env.SPINR_ANDROID_MINIFY === '1'` feeds
  `expo-build-properties.android.enableProguardInReleaseBuilds`.
- `eas.json` sets `SPINR_ANDROID_MINIFY=1` on the non-production profiles — rider: `test`,
  `preview`; driver: `test`, `preview`, `android-auto`.
- **`production` deliberately does not set it.** Store builds stay unminified until a
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

**Nobody, today.** Production store builds are unchanged — `production` does not set the
flag, so the next store upload is byte-for-byte as unminified as the last one, and the Play
Console rating will still read Low until someone flips it. Nothing is visible mid-session to
a rider mid-ride or a driver online. No copy or notification changes.

Whoever installs the next internal `test`/`preview`/`android-auto` build gets the first
minified binary. Expected effect there: smaller download, slightly faster cold start. The
risk is that a native module breaks in ways listed above.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app.config.ts` | Added `ANDROID_MINIFY` const; added `enableProguardInReleaseBuilds: ANDROID_MINIFY` to `expo-build-properties.android` | Enables R8 when the profile opts in |
| `rider-app/eas.json` | `SPINR_ANDROID_MINIFY=1` in `test` + `preview` env | Internal builds only; production untouched |
| `driver-app/app.config.ts` | Same two changes; **also corrected the now-stale comment** on the existing Nitro keep rule, which claimed minification was off and the rule a no-op | That comment would actively mislead the next reader after this change |
| `driver-app/eas.json` | `SPINR_ANDROID_MINIFY=1` in `test` + `preview` + `android-auto` env | `android-auto` included because Android Auto is the highest-risk R8 surface and needs the head-unit check |

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
    enableProguardInReleaseBuilds: ANDROID_MINIFY,   // true only on test/preview
},
```

## 8. Rollback plan

**Config-only, no redeploy and no code change.** Remove `"SPINR_ANDROID_MINIFY": "1"` from
the affected profile's `env` in `eas.json` and rebuild — the next build is unminified. No
migration, no live data touched, no wallet/ride/insurance-period rows involved. Already-
installed minified internal builds are replaced by the next build from that profile; there
is nothing to reconcile.

Because production was never switched on, there is no store-side rollback to plan for. If
production is flipped on later, the rollback for *that* change is the same one-line revert
plus a new store upload — which is why the flip should not happen until a preview build has
been device-validated.

## 9. Verification performed

- [x] **Blast-radius check** — grepped the whole repo for `enableProguardInReleaseBuilds` /
      `enableMinifyInReleaseBuilds` / `minifyEnabled` / `shrinkResources` / `proguard`:
      confirmed zero prior minification config anywhere, and that driver-app's
      `extraProguardRules` was the only ProGuard artifact in the repo. Diffed both apps'
      `package.json` dependency lists to enumerate the native modules R8 will process
      (table in §4).
- [x] **`eas.json` validity** — both files re-parsed with a strict JSON parser after
      editing, and asserted programmatically that the flag is set on exactly the intended
      profiles and **absent from `production` and `development`** in both apps.
- [x] **Config syntax** — `tsc --noEmit --noResolve` on both `app.config.ts` files: no
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

- **No Android build of any kind was run — not even a prebuild.** `node_modules` is not
  installed in this container and npm egress is blocked by proxy policy (HTTP 403 on
  `registry.npmjs.org`), so `yarn install`, `npx expo prebuild`, `expo config` and any
  Gradle invocation were all impossible. **Nothing here proves R8 produces a working APK.**
  Everything above is static reasoning over config.
- **The Nitro keep rule has still never been exercised.** It was inert from the day it was
  added. This change is the first thing that can make it load-bearing, and only on a
  profile nobody has built yet.
- **`expo-build-properties@57.0.x` was not read from disk** to confirm
  `enableProguardInReleaseBuilds` over the newer `enableMinifyInReleaseBuilds` spelling.
  The key was confirmed from Expo's documented plugin config and from this repo's own
  driver-app comment naming both spellings; the package itself could not be fetched.
- **No visual-regression coverage exists for rider-app or driver-app at all** (CLAUDE.md
  gate 6). Not that it would apply — this change renders nothing — but stating it rather
  than letting silence imply coverage.
- Nothing was checked on a real device, a real head unit, or against live Supabase/Stripe.

### Required before flipping `production` on

0. **Confirm R8 actually ran.** The gate fails *safe*: if the profile `env` doesn't reach
   the prebuild step (a known intermittent eas-cli behaviour —
   [expo/eas-cli#2812](https://github.com/expo/eas-cli/issues/2812)), the build still
   succeeds, just unminified. So a green build proves nothing. Grep the EAS build log for
   the `:app:minifyReleaseWithR8` Gradle task, or check
   `android/gradle.properties` in the build log for
   `android.enableProguardInReleaseBuilds=true`. If it's absent, the flag never arrived —
   fix the plumbing before concluding anything about steps 1-6.
1. Build `preview` (or `android-auto` for driver) and install the minified APK on a device.
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

- [x] Rollback plan is concrete and testable (delete one `eas.json` line, rebuild)
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
  JSON parse and `git diff --check` pass. These checks model documented profile env
  precedence; they do not run EAS or prove env forwarding on a build worker.
- Not verified: no native APK/AAB build or installed-binary rollback/device test.
