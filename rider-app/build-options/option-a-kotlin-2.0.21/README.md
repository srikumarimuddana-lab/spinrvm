# Option A — Kotlin 2.0.21 strategy

**Status:** Cold storage. NOT active.
**Verified working as of:** never reached green build (Stripe pin had a bug, see below).

## When to use this strategy

Resurrect this when:
- Option C (Kotlin 2.2.21) starts emitting deprecation-as-error in some Expo SDK 55 module
- AND that module has no fix available yet
- AND you need to ship something today

The constraint that drove Option A originally: the `expo-updates` build.gradle had a timing
bug that made it resolve ksp `2.0.21-1.0.28` even when Kotlin was set to 2.1.20. Pinning Kotlin
DOWN to 2.0.21 sidestepped the timing issue. Option C fixes it the right way (explicit
`kspVersion` in gradle.properties), so this fallback is only for catastrophic Option C failure.

## Trade-offs vs Option C

- **Loses:** ~12 months of Stripe Android SDK fixes (21.6.0 vs latest 23.x)
- **Gains:** known-cleaner compile against Expo SDK 55 modules (which were tested on 2.0.x)

## Files in this directory

| File | What it does |
|---|---|
| `@react-native+gradle-plugin+0.85.2.patch` | Patches `kotlin = "2.1.20"` → `"2.0.21"` in libs.versions.toml |
| `withStripeAndroidPin.js` | **BUGGY.** Writes `StripeSdk_stripeVersion=21.6.+` to ROOT gradle.properties — but stripe-react-native subproject's own gradle.properties shadows root, so this never takes effect. See "Correct mechanism" below before using. |

## How to switch FROM Option C TO Option A

1. **Replace patches:**
   ```bash
   cp build-options/option-a-kotlin-2.0.21/@react-native+gradle-plugin+0.85.2.patch \
      patches/@react-native+gradle-plugin+0.85.2.patch
   ```
2. **Edit `app.config.ts`:**
   - Change `kotlinVersion: '2.2.21'` → `'2.0.21'`
   - Remove `'./plugins/withKspVersion'` from plugins array (Option A relies on the expo-updates hardcoded mapping)
3. **Apply Stripe pin via patch-package** (the correct mechanism — NOT the buggy plugin):
   ```bash
   # Edit node_modules/@stripe/stripe-react-native/android/gradle.properties
   # Change: StripeSdk_stripeVersion=23.3.+ → StripeSdk_stripeVersion=21.6.+
   npx patch-package @stripe/stripe-react-native
   ```
   This generates `patches/@stripe+stripe-react-native+0.63.0.patch` which patch-package replays
   on every install (including EAS).
4. **Run `yarn install`** to apply the new patch.
5. **Local verify** with `npx expo prebuild --clean --platform android`, grep gradle.properties.

## Correct mechanism for the Stripe pin

The included `withStripeAndroidPin.js` writes to root `android/gradle.properties`. Gradle's
multi-project property resolution gives the SUBPROJECT's gradle.properties precedence over
the root for that subproject's reads — so `node_modules/@stripe/stripe-react-native/android/gradle.properties`
(which has `StripeSdk_stripeVersion=23.3.+`) wins over the root.

The fix: patch-package the stripe-react-native gradle.properties directly (step 3 above).

## Decision criteria to leave Option A again

Move back to Option C when:
- Kotlin 2.2.x has been the ecosystem default for 6+ months
- OR Expo SDK 56+ ships with Kotlin 2.2.x as its baseline
- OR Stripe Android stops shipping new features in 23.x line

## See also

- `docs/android-build-strategy.md` — master doc with full context
- `build-options/option-b-kotlin-2.1.20/README.md` — middle-ground alternative
