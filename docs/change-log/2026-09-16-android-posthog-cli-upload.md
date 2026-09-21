# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Author | Cursor Grok |
| Surface(s) | rider-app / driver-app |
| Domain (Sentry tag) | admin |
| PR / commit link | uncommitted |
| Related issue or gap ID | Android production EAS `:app:createBundleReleaseJsAndAssets_PostHogUpload_*` (rider versionCode 27) |

## 1. Issue / gap identified

After the Metro `@posthog/core/surveys` fix, Android production Gradle still failed at `createBundleReleaseJsAndAssets_PostHogUpload_com.spinr.user@2.0.0+27_27` with `A problem occurred starting process 'command 'posthog-cli''`. Kotlin deprecation warnings from `react-native-screens` / `expo-modules-core` were not the failure.

## 2. Root cause

`posthog-react-native/expo` always applies `tooling/posthog.gradle` (and wraps iOS `posthog-xcode.sh`) to upload **JavaScript** sourcemaps via `posthog-cli`. That is error-tracking symbolication, not session replay. `uploadNativeSymbols` defaults off and does not disable the JS upload. EAS does not have `posthog-cli` on PATH and we do not depend on `@posthog/cli`. Gradle falls back to the bare command and cannot start the process.

## 3. Fix / remediation

Keep the PostHog plugin (native replay + MainActivity `onNewIntent`). Add `withSkipPostHogCliUpload` immediately after it so prebuild strips the Gradle apply and unwraps `posthog-xcode.sh`. Alternative rejected: add `@posthog/cli` and upload maps — that needs `POSTHOG_CLI_*` personal keys, would send JS maps to US PostHog, and is not required for replay.

## 4. Risk & impact on existing functionality

- **Blast radius:** rider-app and driver-app Expo prebuild only. Runtime `posthogReplay.ts` unchanged. Sentry sourcemap upload unchanged (Sentry plugin stays later in the list and wraps the unwrapped RN xcode script).
- **Could this regress?** If PostHog changes the `apply from: ...posthog.gradle` string, the strip no-ops and the next EAS Android build fails the same way (fail-closed). iOS TestFlight would have failed the same class of missing-CLI error; the xcode unwrap prevents that.
- **Ride state / money / loops:** none.

## 5. User-experience effect

- Nobody until a new native binary. No in-app copy. rider-app / driver-app have no visual-regression tooling — reasoned about, not screenshotted.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/expo/stripPostHogCliUpload.js` | Strip gradle apply + unwrap xcode wrapper | Shared, testable |
| `shared/expo/__tests__/stripPostHogCliUpload.test.ts` | Fixtures matching plugin 4.74.0 strings | Real coverage |
| `rider-app/plugins/withSkipPostHogCliUpload.js` | Expo glue | Must resolve `@expo/config-plugins` from the app |
| `driver-app/plugins/withSkipPostHogCliUpload.js` | Same | Sibling app |
| `rider-app/app.config.ts` / `driver-app/app.config.ts` | Plugin immediately after PostHog | Order matters |

## 7. Before / after

```
# Before — release bundle always execs posthog-cli
apply from: new File(["node", "--print", ".../posthog.gradle"].execute().text.trim())
# → :app:createBundleReleaseJsAndAssets_PostHogUpload_* fails
```

```
# After — upload hooks removed at prebuild; replay plugin remains
./plugins/withSkipPostHogCliUpload  // after posthog-react-native/expo
```

## 8. Rollback plan

Redeploy: revert these files and rebuild. No live data. Existing store binaries unchanged. Do not add `@posthog/cli` as a "rollback".

## 9. Verification performed

- [x] `shared/expo/__tests__/stripPostHogCliUpload.test.ts` via rider-app Jest
- [ ] Full EAS Android production bundle (needs commit + workflow from main)
- [x] Did not flip Metro package-exports; did not add `@posthog/cli`
- [ ] No visual-regression tooling for rider-app / driver-app

## 10. What was NOT verified

- A complete EAS `Run gradlew` after this lands.
- Whether a future PostHog plugin version changes the apply-from string (tests pin 4.74.0).
- Expo doctor / Kotlin deprecation warnings — pre-existing, not this fail.
