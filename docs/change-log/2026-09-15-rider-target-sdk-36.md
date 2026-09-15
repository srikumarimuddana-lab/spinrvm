# Change Impact & Risk Log — Rider targetSdk 35 → 36 (match driver)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-15 |
| Surface(s) | rider-app (Android build config only) |
| Related | Play submit “Target SDK of artifact is too low”; Google requires API 36 for updates as of 2026-08-31 |

## 1. Issue

Rider and driver were not the same. Both already use **minSdk 25**. Driver targets **36**. Rider still targeted **35** in `expo-build-properties` and `withForceCompileSdk.js` (the plugin overwrites Gradle and had `TARGET_SDK = '35'` even though comments said 36).

## 2. Fix

Set rider `targetSdkVersion` / `TARGET_SDK` to **36**. **minSdk stays 25** on both apps.

## 3. Risk

Next rider production AAB behaves under Android 16 rules (same as driver). No mid-session change for installed apps. Older phones still install via min 25.

## 4. Rollback

Set rider target back to 35 and rebuild. Play will reject that update under the Aug 2026 policy.

## 5. Verification

- [x] Driver already min 25 / target 36 / compile 36.
- [ ] Next rider EAS production AAB manifest shows targetSdk 36.
- [ ] Play internal submit accepts the new AAB.
