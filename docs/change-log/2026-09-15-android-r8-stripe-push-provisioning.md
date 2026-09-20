# Change Impact & Risk Log — Rider R8 Stripe push-provisioning dontwarn

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-15 |
| Author | Cursor Grok (EAS production minify failure) |
| Surface(s) | rider-app (Android release minify only) |
| Domain (Sentry tag) | payments (build-config; Stripe native refs, no charge path change) |
| PR / commit link | follow-on to `14fecf852` production minify-on |
| Related issue or gap ID | EAS build `04071f79` `:app:minifyReleaseWithR8` |

## 1. Issue / gap identified

The first rider production-profile build with `SPINR_ANDROID_MINIFY=1` failed R8:
missing `com.stripe.android.pushProvisioning.*` classes referenced by
`@stripe/stripe-react-native`.

## 2. Root cause

Stripe Issuing "add card to Google Wallet" is an optional native AAR. The React Native
SDK still references those classes. Unminified release builds ignore missing optional
types; R8 (AGP 8) treats them as errors. We do not depend on the Issuing AAR.

## 3. Fix / remediation

Add `extraProguardRules: '-dontwarn com.stripe.android.pushProvisioning.**'` to rider
`expo-build-properties`. Package-wide because the inner class letter (`$f` vs `$g`)
drifts across Stripe versions. Do not add the Issuing AAR.

**Alternative:** `implementation` the push-provisioning AAR. Rejected — unused Issuing
surface and extra native code for a feature we don't ship.

## 4. Risk & impact on existing functionality

**Blast radius: rider Android release builds only.** Driver has no Stripe RN SDK.
PaymentSheet / `confirmPayment` do not use push provisioning. `-dontwarn` does not
strip Stripe payment classes; it tells R8 missing Issuing types are expected.

Installed apps unchanged until a new minified AAB is built and installed.

## 5. User-experience effect

Nobody until the next rider AAB installs. No copy change. Google Wallet "add this
Spinr card" was never a product feature.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app.config.ts` | `extraProguardRules` dontwarn | Unblock R8 |
| `docs/android-build-strategy.md` | Record the rule | Keep-rule registry |

## 7. Before / after

```ts
enableMinifyInReleaseBuilds: ANDROID_MINIFY,
enableShrinkResourcesInReleaseBuilds: false,
```

```ts
enableMinifyInReleaseBuilds: ANDROID_MINIFY,
enableShrinkResourcesInReleaseBuilds: false,
extraProguardRules: '-dontwarn com.stripe.android.pushProvisioning.**',
```

## 8. Rollback plan

Remove `extraProguardRules` and set production `SPINR_ANDROID_MINIFY` back to `0`, then
rebuild. The failed minified AAB never reached Play. No DB/Stripe/ride-state rollback.

## 9. Verification performed

- [x] Matched EAS log missing classes to Stripe RN push-provisioning optional module.
- [x] Confirmed rider depends on `@stripe/stripe-react-native@0.75.0`; driver does not.
- [ ] Next EAS rider production build `:app:minifyReleaseWithR8` success.
- [ ] Device smoke of PaymentSheet on the new internal AAB.

## 9b. What was NOT verified

No local Gradle/R8 run (needs EAS). Driver minify still independent. Sentry mapping
upload still not wired.
