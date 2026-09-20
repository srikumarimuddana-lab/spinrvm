# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Author | Cursor Grok |
| Surface(s) | rider-app / driver-app |
| Domain (Sentry tag) | admin |
| PR / commit link | uncommitted |
| Related issue or gap ID | Android production versionCode 26–28 all failed on PostHog Gradle upload |

## 1. Issue / gap identified

Three Android production EAS builds in a row died on PostHog, not on Expo SDK version drift. 26: Metro `@posthog/core/surveys`. 27–28: `:app:createBundleReleaseJsAndAssets_PostHogUpload_*` / `command 'posthog-cli'`. The skip-plugin workaround did not apply on the versionCode 28 job (`PostHogUpload` still ran). Kotlin `w:` lines and Sentry's successful sourcemap report are unrelated.

## 2. Root cause

Today's only `app.config.ts` addition was `'posthog-react-native/expo'`. That plugin always injects `posthog.gradle` (and iOS `posthog-xcode.sh`) to upload JS sourcemaps via `posthog-cli`. Expo SDK (`~57.0.18` rider / `~57.0.22` driver), `eas.json`, and runtimeVersion were not changed. The Expo dashboard "Open in PostHog" 500 (`a3cd011f-…`) is Expo's cloud integration, not this Gradle task.

## 3. Fix / remediation

Remove `'posthog-react-native/expo'` (and the skip plugin that existed only to patch it). Keep `posthog-react-native` in package.json, the Metro surveys mapper, and JS init. Autolinking still includes the native module. Native sourcemap/dSYM upload is out of scope.

## 4. Risk & impact on existing functionality

- **Blast radius:** Expo prebuild only. `posthogReplay.ts` unchanged. Sentry upload path unchanged (it already succeeded on versionCode 28).
- **Replay:** JS session replay still inits when the admin flag + `phc_` key are set. Without the Expo plugin we lose PostHog's MainActivity `onNewIntent` patch (push-tap capture — already constructor-disabled) and automatic JS map upload (error tracking — not used).
- **Ride state / money / loops:** none.

## 5. User-experience effect

None until a native binary ships. Flag stays off. No visual-regression tooling on rider/driver — reasoned about, not screenshotted.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app.config.ts` | Drop PostHog Expo plugin + skip plugin | Stop Gradle `posthog-cli` |
| `driver-app/app.config.ts` | Same | Sibling |
| `rider-app/plugins/withSkipPostHogCliUpload.js` | Deleted | Only existed to patch the Expo plugin |
| `driver-app/plugins/withSkipPostHogCliUpload.js` | Deleted | Same |
| `shared/expo/stripPostHogCliUpload.js` + test | Deleted | Unused after plugin removal |

## 7. Before / after

```
# Before (today's only app.config add)
'posthog-react-native/expo'
# → createBundleReleaseJsAndAssets_PostHogUpload_* execs posthog-cli
```

```
# After
# PostHog Expo plugin absent. JS SDK remains in package.json.
```

## 8. Rollback plan

Redeploy: put `'posthog-react-native/expo'` back and rebuild. That will re-break EAS until `posthog-cli` exists and is ≥ 0.16.0. No live data.

## 9. Verification performed

- [x] Confirmed today's app.config diff is +1 plugin line (PostHog), no Expo SDK bump
- [ ] EAS Android production not re-run yet
- [ ] rider-app / driver-app have no visual-regression tooling

## 10. What was NOT verified

- A production AAB after this removal.
- Whether Expo's "Open in PostHog" dashboard 500 is Expo-side (project 613547 vs Spinr 613330). That is not the Gradle failure.
