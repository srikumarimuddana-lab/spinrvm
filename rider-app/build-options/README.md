# Cold storage: alternative Android build strategies

This directory holds **inactive alternative build configurations**. Files here do NOT run.
They are reusable snapshots of strategies considered (and tried) for the Expo SDK 55 / RN
0.85.2 Android build chain.

## Index

| Strategy | Pin direction | When to consider |
|---|---|---|
| **Active (Option C)** — Kotlin 2.2.21 + ksp 2.2.21-2.0.5 + Stripe at default 23.3.+ | UP | (current — see `../plugins/`, `../patches/`, `docs/android-build-strategy.md`) |
| `option-b-kotlin-2.1.20/` | NEUTRAL (RN default) | Option C breaks on Expo SDK 55 modules; want middle-ground |
| `option-a-kotlin-2.0.21/` | DOWN | Catastrophic Option C failure; need ship-today fallback |

Each subdirectory has a `README.md` with the full switching procedure.

## Why this exists

The Expo SDK 55 / RN 0.85.2 / Stripe 23.x compatibility matrix is fragile. As of 2026-05:

- `@react-native/gradle-plugin/gradle/libs.versions.toml` declares `kotlin = "2.1.20"`
- `expo-updates/android/build.gradle` has a hardcoded `kotlinVersion → kspVersion` mapping that
  fails to read `rootProject.kotlinVersion` at buildscript-eval time (timing bug)
- Stripe Android SDK 23.3.x is compiled with Kotlin 2.2.21 (metadata version 2.2.0)

These three facts pull in three different directions. We picked Kotlin 2.2.21 (Option C) as
the alignment point because it's the version Stripe itself rolled back to, the ecosystem is
moving toward it, and it requires no Stripe pin.

But the wrong-version-of-something will break it eventually. When that happens, switch to A
or B per the runbook in each subdirectory's README.

## Decision tree

If the active build (Option C) starts failing:

```
Build fails →
├─ At :app:checkReleaseAarMetadata?
│  → withForceCompileSdk plugin issue. See `../plugins/withForceCompileSdk.js` removal
│    criteria. Probably the silent-clamp bug in expo-build-properties recurred.
│
├─ At :stripe_stripe-react-native:compileReleaseKotlin with metadata version mismatch?
│  → Stripe bumped Kotlin again. Two paths:
│      a) Stripe went FORWARD past 2.2.21 → bump our Kotlin to match (update C in place)
│      b) We need to ship today → switch to Option B (pin Stripe 22.8.+) or Option A (21.6.+)
│
├─ At :expo-updates:kspReleaseKotlin with NoSuchMethodError on Companion APIs?
│  → ksp version is wrong for the Kotlin compiler. Update KSP_VERSION constant in
│    `../plugins/withKspVersion.js`. See https://github.com/google/ksp/releases for matches.
│
├─ At :<some-expo-module>:compileReleaseKotlin with deprecation-as-error?
│  → Expo SDK 55 module wasn't tested under our Kotlin version. Either:
│      a) patch-package the affected module to fix the deprecation
│      b) Switch to Option B (Kotlin 2.1.20) which is closer to Expo's tested baseline
│
└─ Anything else?
   → Read the gradle log carefully, find the failing :module:task, look up that module's
     dependencies. Don't add a plugin reactively — figure out which strategy (A/B/C) the
     fix belongs to first.
```

## Mirror in driver-app

`driver-app/build-options/` holds the same structure. Driver-app does NOT include Stripe,
so its Option A and B notes omit the Stripe pin steps.
