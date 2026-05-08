# Option B — Kotlin 2.1.20 strategy (RN's intended default)

**Status:** Cold storage. NOT active.
**Verified working:** Never tested — designed but never built.

## When to use this strategy

Resurrect this when:
- Option C (Kotlin 2.2.21) breaks on Expo SDK 55 modules with deprecation-as-error issues
- AND you want to stay closer to Expo SDK 55's tested baseline
- AND you can tolerate pinning Stripe back ~4 months (vs Option C's no-pin)

This is the "trust upstream defaults" path. Kotlin 2.1.20 is what
`@react-native/gradle-plugin@0.85.2`'s libs.versions.toml ships — so we don't patch that
file at all in Option B.

## Trade-offs

|  | vs Option A | vs Option C |
|---|---|---|
| Stripe SDK age | newer (22.8 vs 21.6) — gains 1 year of fixes | older (22.8 vs 23.x) — loses ~6 months |
| Kotlin patches needed | no libs.versions.toml patch (cleaner) | no libs.versions.toml patch (cleaner) |
| Aligned with Expo SDK 55 baseline | better | worse |
| Aligned with current ecosystem | worse | best |

## Files in this directory

This directory is mostly notes — Option B requires fewer custom patches than A or C because
it stays on the upstream default Kotlin version.

| File | What it does |
|---|---|
| `README.md` (this file) | Switching guide |

## How to switch FROM Option C TO Option B

1. **Remove the Kotlin patch** (revert libs.versions.toml to upstream):
   ```bash
   rm patches/@react-native+gradle-plugin+0.85.2.patch
   ```
   Then `rm -rf node_modules` and `yarn install` — patch-package will not re-create the patch.
2. **Edit `app.config.ts`:**
   - Change `kotlinVersion: '2.2.21'` → `'2.1.20'`
3. **Update `withKspVersion.js` constant:**
   ```js
   const KSP_VERSION = '2.1.20-2.0.1';  // matches Kotlin 2.1.20 (latest stable as of 2026-05)
   ```
4. **Apply Stripe pin to 22.8.+ via patch-package** (correct mechanism):
   ```bash
   # Edit node_modules/@stripe/stripe-react-native/android/gradle.properties
   # Change: StripeSdk_stripeVersion=23.3.+ → StripeSdk_stripeVersion=22.8.+
   npx patch-package @stripe/stripe-react-native
   ```
5. **Local verify** with `npx expo prebuild --clean --platform android`.

## Why not just use this from the start?

We considered. Reasons we picked Option C over B:

1. Stripe is the most actively maintained dep — keeping it current matters more than holding it back
2. Kotlin 2.2.21 is where Stripe rolled BACK to from 2.3.10 (so it's a known-stable point)
3. Forward-compat: Expo SDK 56 will likely jump to 2.2.x
4. Option C means no downgrades anywhere — every version is forward of the SDK 55 baseline

If any of those assumptions break, B is the right fallback.

## Verifying ksp version availability

Before switching, confirm `2.1.20-2.0.1` is still on Maven Central (it should be — older
ksp versions aren't removed). Check:
- https://central.sonatype.com/artifact/com.google.devtools.ksp/symbol-processing-gradle-plugin
- Or: `gh api repos/google/ksp/releases --jq '.[].tag_name' | grep 2.1.20`

## See also

- `docs/android-build-strategy.md` — master doc
- `build-options/option-a-kotlin-2.0.21/README.md` — pin everything backward
