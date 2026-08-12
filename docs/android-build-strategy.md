# Android build strategy: Expo SDK 57 / RN 0.86.2

**Last verified:** 2026-08-11 (SDK 57 dependency-alignment pass; strategy unchanged since 2026-05-08)
**Active strategy:** Option C (Kotlin 2.2.21 + ksp 2.2.21-2.0.5 + Stripe at default 23.3+)
**Branch where this was solved:** `fix/rider-app-expo-sdk55` (under SDK 55 / RN 0.85.2; carried
forward unchanged through the SDK 57 / RN 0.86.2 upgrade — patch filenames re-targeted to
`+0.86.2`, `expo-modules-core+55.0.25.patch` retired as fixed upstream)

This doc captures the full reasoning and alternatives for the Android build chain,
originally worked out on Expo SDK 55 / RN 0.85.2 and still active on SDK 57 / RN 0.86.2.
If a future build breaks, **read the decision tree** before adding a reactive patch.

---

## TL;DR

React Native's gradle-plugin targets Kotlin 2.1.20 (per
`@react-native/gradle-plugin/gradle/libs.versions.toml` — true of both RN 0.85.2/SDK 55
where this was first solved and RN 0.86.2/SDK 57 today), but Stripe Android SDK 23.3+
requires Kotlin 2.2.21. We resolved the conflict by moving EVERYTHING forward to Kotlin
2.2.21 — patching `libs.versions.toml`, setting
`expo-build-properties.android.kotlinVersion: '2.2.21'`, and pinning ksp to `2.2.21-2.0.5`
to match. No dependencies are pinned backward.

## What's in the active layer

```
rider-app/                                       (driver-app mirrors this except no Stripe)
├── plugins/
│   ├── withGradleWrapper.js              ← active: Gradle 8.13 pin (RN 0.85/0.86 templates default Gradle 9, incompat with pre-2023 native-module build scripts)
│   ├── withForceCompileSdk.js            ← active: stamps compileSdk=36, targetSdk=36
│   └── withKspVersion.js                 ← active: stamps kspVersion=2.2.21-2.0.5
├── patches/
│   └── @react-native+gradle-plugin+0.86.2.patch    ← kotlin = "2.2.21" (was "2.1.20" upstream)
│       (companion react-native+0.86.2.patch is New-Arch codegen JS fallbacks, not Kotlin/Gradle)
├── app.config.ts                         ← expo-build-properties.android.kotlinVersion: '2.2.21'
└── build-options/                        ← cold storage (alternative strategies)
```

Retired since the SDK 55 writeup: `expo-modules-core+55.0.25.patch` (Promise.kt RN 0.85.2
ABI compat) — fixed upstream, no equivalent needed on expo-modules-core 57.x.

## Why three layers exist

| Component | Where Kotlin lives | Set by |
|---|---|---|
| Kotlin gradle plugin (compiler classpath) | `@react-native/gradle-plugin/gradle/libs.versions.toml` `kotlin` key | patch-package patch |
| Per-module kotlin libraries (kotlin-stdlib, kotlin-reflect) | `rootProject.ext.kotlinVersion` | `useExpoVersionCatalog()` reading `android.kotlinVersion` from gradle.properties |
| ksp gradle plugin | `rootProject.ext.kspVersion` | `withKspVersion` plugin writing to gradle.properties |
| compileSdk / targetSdk | `rootProject.ext.compileSdkVersion` | `withForceCompileSdk` plugin (defensive) + `useExpoVersionCatalog()` |

These four flows must all agree. A single number in `app.config.ts` doesn't reach all of
them — that's why we have multiple plugins and patches.

---

## Decision tree: which strategy when?

```
Build broken?
│
├─ At :app:checkReleaseAarMetadata?
│   → withForceCompileSdk plugin failed. Check rider-app/android/gradle.properties
│     after prebuild — should have `android.compileSdkVersion=36`. If not, the plugin
│     didn't run; check plugins[] order in app.config.ts (must be AFTER expo-build-properties).
│
├─ At :stripe_*:compileReleaseKotlin with "metadata version is X.Y, expected 2.2.0"?
│   → Stripe bumped Kotlin past our 2.2.21. Two paths:
│     (a) Bump ours to match — update KSP_VERSION in withKspVersion.js, kotlin in
│         the patches/, and kotlinVersion in app.config.ts
│     (b) Ship today — switch to Option A or B (see build-options/)
│
├─ At :expo-updates:kspReleaseKotlin with NoSuchMethodError on Companion APIs?
│   → ksp version doesn't match Kotlin compiler. Update `KSP_VERSION` constant in
│     withKspVersion.js. Find matching version at https://github.com/google/ksp/releases
│     (format: <kotlinMajor.minor.patch>-<kspBuild>).
│
├─ At :<some-expo-module>:compileReleaseKotlin with deprecation-as-error?
│   → That module wasn't tested under Kotlin 2.2.x. Two paths:
│     (a) patch-package the affected module to suppress the specific deprecation
│     (b) Switch to Option B (Kotlin 2.1.20 — closer to Expo's tested baseline)
│
├─ At :app:bundleReleaseJsAndAssets / Metro bundle phase?
│   → Not Android-build-strategy related. Probably a JS dep or babel issue. Check
│     metro.config.js and recent yarn.lock changes.
│
└─ Anything else?
    → Don't reactively add a plugin. Find which :module:task failed, look at that
      module's android/build.gradle, identify what variable is wrong. Then map it
      to one of the four flows above.
```

---

## Strategy options compared

| | Option A | Option B (RN's intended) | **Option C (active)** |
|---|---|---|---|
| Kotlin version | 2.0.21 | 2.1.20 | **2.2.21** |
| ksp version | 2.0.21-1.0.28 | 2.1.20-2.0.1 | **2.2.21-2.0.5** |
| Stripe Android SDK | 21.6.+ (Mar 2025) | 22.8.+ (Feb 2026) | **23.3.+ (default)** |
| Direction vs upstream | DOWN | NEUTRAL | **UP** |
| `libs.versions.toml` patched? | yes (down to 2.0.21) | no | **yes (up to 2.2.21)** |
| Stripe pin needed? | yes (via patch-package) | yes (to 22.8.+) | **no** |
| Loses Stripe SDK fixes? | ~12 months | ~3 months | **none** |
| Risk on Expo SDK 55 modules | LOW (native target) | LOW (RN intended) | MEDIUM (forward of tested) |
| Forward-compat with Expo SDK 56+ | WORST | MEDIUM | **BEST** |

Cold-stored details: `build-options/option-a-kotlin-2.0.21/`, `build-options/option-b-kotlin-2.1.20/`.

---

## What we tried that didn't work (and why)

### 1. Setting kotlinVersion in app.config.ts alone

**Tried:** `kotlinVersion: '2.0.21'` in `expo-build-properties` → expected this to flow to
the Kotlin compiler classpath via the `expoLibs` version catalog.

**Result:** `expoLibs.kotlin` got set correctly (visible in `[ExpoRootProject]` log), but
the kotlin-gradle-plugin classpath in `android/build.gradle` is `classpath('org.jetbrains.kotlin:kotlin-gradle-plugin')`
with NO version specified. Gradle resolved the version via `includeBuild` from
`@react-native/gradle-plugin`'s own libs.versions.toml — which had `kotlin = "2.1.20"`.

**Why:** `rootProject.ext.kotlinVersion` only affects per-module kotlin-stdlib dependency
declarations. The COMPILER VERSION is a separate concern, set at the buildscript classpath.

**Fix:** patch `@react-native/gradle-plugin/gradle/libs.versions.toml` directly (see active
patch).

### 2. Pinning Stripe via root gradle.properties (`withStripeAndroidPin` plugin)

**Tried:** Plugin wrote `StripeSdk_stripeVersion=21.6.+` to `rider-app/android/gradle.properties`.

**Result:** EAS build still downloaded stripe-android 23.3.0.

**Why:** Gradle's project property resolution gives the SUBPROJECT's gradle.properties
precedence over the root for that subproject's reads. `node_modules/@stripe/stripe-react-native/android/gradle.properties`
contains `StripeSdk_stripeVersion=23.3.+` and shadows our root override.

**Fix (cold-stored):** patch-package on `@stripe/stripe-react-native/android/gradle.properties`
directly. Only needed for Options A and B; Option C doesn't pin Stripe.

### 3. Trusting expo-updates' hardcoded kotlinVersion → kspVersion mapping

**Tried:** Set `kotlinVersion: '2.1.20'` and expected `expo-updates/android/build.gradle`
to resolve ksp to `2.1.20-2.0.1` (its mapping says it should).

**Result:** Got "ksp-2.0.21-1.0.28 is too old for kotlin-2.1.20" warnings, then
`NoSuchMethodError: KotlinTypeMapper$Companion.getLANGUAGE_VERSION_SETTINGS_DEFAULT`.

**Why:** Timing bug. `expo-updates/android/build.gradle` reads `rootProject["kotlinVersion"]`
at BUILDSCRIPT EVAL TIME — before `ExpoRootProjectPlugin` has had a chance to set
`rootProject.ext.kotlinVersion`. The lookup returns null and falls through to the default
branch (`return "1.9.24-1.0.20"`). Some other layer then resolves a stale ksp.

**Fix:** Set `kspVersion=…` in `gradle.properties` directly (Gradle reads gradle.properties
BEFORE buildscript blocks evaluate). That's `withKspVersion` plugin — works for all options.

---

## Removal criteria for active patches

Each active patch/plugin should be removable when its underlying issue is fixed upstream.
Track these:

| Patch / plugin | Remove when |
|---|---|
| `withForceCompileSdk` | Expo SDK ≥56 ships compileSdk 36 as default AND `useExpoVersionCatalog()` bridges gradle.properties on EAS (verify with build that has plugin disabled). **Now on SDK 57 the criterion is testable — needs one EAS Android build with the plugin disabled; not run in the 2026-08-11 alignment pass (no EAS builds).** |
| `withKspVersion` | `expo-updates/android/build.gradle` is fixed upstream to use a deferred lookup OR Expo SDK ≥56 ships with our kotlinVersion in its hardcoded mapping. **Same status: testable on 57, needs an EAS build to verify.** |
| `@react-native+gradle-plugin+0.86.2.patch` (kotlin 2.2.21) | `@react-native/gradle-plugin/gradle/libs.versions.toml` upstream sets `kotlin = "2.2.x"` (i.e., RN itself moves to 2.2). Still 2.1.20 in RN 0.86.2 — patch still required. |
| ~~`expo-modules-core+55.0.25.patch` (Promise.kt)~~ | **RETIRED** — fixed upstream; no equivalent patch exists for expo-modules-core 57.x. |

---

## How to switch strategies

See per-option READMEs:
- `rider-app/build-options/option-a-kotlin-2.0.21/README.md`
- `rider-app/build-options/option-b-kotlin-2.1.20/README.md`

Both have step-by-step switching procedures. Read them BEFORE making changes — Option A's
Stripe pin in particular has a non-obvious correct mechanism (patch-package on the
stripe-rn package, NOT a config plugin on root gradle.properties).

---

## Investigation log (for future debugging)

The full debugging chain that led to Option C:

1. **`bb052089`** — Driver-app addresses screen routed through Maps proxy (cost cut)
2. **`c3b6611b`** — Patched Promise.kt for RN 0.85.2 ABI (`String?` instead of `String`)
3. **(failed) `eea4a851`** — Promise.kt fixed → ksp/kotlin mismatch on `:expo-updates:kspReleaseKotlin`
4. **`a9ba04b6`** — Bumped compileSdk/targetSdk 35→36 in app.config.ts (didn't reach gradle.properties on EAS)
5. **`17114cdf`** — Downgraded kotlinVersion 2.1.20→2.0.21 to "match ksp" (reactive — should have upgraded ksp instead)
6. **`cb74ff8f`** — Added `withForceCompileSdk` plugin (compileSdk fix took, build progressed past AAR metadata check)
7. **`cfc1c19e`** — Patched `libs.versions.toml` to kotlin 2.0.21 (compiler classpath alignment)
8. **`b72bc96c`** — Added `withStripeAndroidPin` plugin (DIDN'T WORK due to gradle property precedence — see "What didn't work" §2)
9. **(this commit)** — Strategic pivot to Option C: Kotlin 2.2.21 across the board, no Stripe pin.

The pattern that should have triggered earlier strategic review: each fix unblocked one
build phase but introduced the next blocker. By failure #3 (the Stripe metadata error), it
was clear we were spiraling. The right move was to step back, audit the dependency chain,
and pick a coherent end-state — which is what this doc captures.

---

## Runtime: Android 16 hidden-API enforcement (LogRocket splash hang)

This doc is mostly about BUILD breaks. This entry is a RUNTIME break — the build
succeeds but the app hangs on first launch on a specific Android version.

**Symptom:** On Android 16 devices (e.g. Samsung S26) the app cold-starts, shows the
native splash, and never advances to the React UI. No crash / tombstone — a hang, not a
crash. The same APK works fine on older Android phones.

**Smoking gun in logcat:**

```
E om.spinr.driver: hiddenapi: Accessing hidden field
Landroid/graphics/PorterDuffColorFilter;->mColor:I (api=max-target-o)
from Lcom/logrocket/core/util/ReflectionUtils; (TargetSdkVersion=36)
using reflection: denied
```

…followed (~30s later) by `ActivityTaskManager: Activity transferring splash screen
timeout`.

**Why:** Android gates hidden-API access by `targetSdkVersion`, and enforcement tightens
with each OS release. `@logrocket/react-native`'s session-replay view serializer reflects
into framework-internal fields (here `PorterDuffColorFilter.mColor`) to record colors. On
Android 16 / targetSdk 36 that field is on the `max-target-o` blocklist, so the reflection
is denied on the UI thread during view serialization. That wedges startup: React never
renders its first frame, so `SplashScreen.hideAsync()` (called from `BrandSplash`'s
`onLayout`) never runs and the native splash stays up forever.

**Fix (shipped):** LogRocket is gated OFF on Android by default in both apps'
`app/_layout.tsx`. Override per build with `EXPO_PUBLIC_ENABLE_LOGROCKET` (`'true'` /
`'false'`); unset = iOS on, Android off. A 10s splash watchdog in `_layout.tsx` is the
backstop — it force-hides the native splash and reports the stall (Sentry warning) so a
future non-critical init can't silently brick cold start.

**Re-enable Android when:** LogRocket ships a release that no longer reflects into blocked
hidden APIs at the targetSdk we ship. Verify on a physical Android 16+ device (the hang is
device-OS-specific and won't reproduce on older emulators), then set
`EXPO_PUBLIC_ENABLE_LOGROCKET=true`.

**General lesson:** any dependency that reflects on framework internals (session replay,
screenshotting, view-tree analytics) is a latent runtime bomb that detonates on a future
Android release, not at build time. Re-test those SDKs on a current-gen device before each
targetSdk bump.
