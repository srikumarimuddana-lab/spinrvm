# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Author | Cursor Grok |
| Surface(s) | rider-app / driver-app |
| Domain (Sentry tag) | admin |
| PR / commit link | uncommitted |
| Related issue or gap ID | Android production EAS EAGER_BUNDLE fail after PostHog plugin (builds 7303c04e, 8361bc82) |

## 1. Issue / gap identified

Android production EAS builds for rider and driver failed in the Bundle JavaScript / EAGER_BUNDLE phase with `Unable to resolve module @posthog/core/surveys` from `posthog-react-native`. Expo doctor warnings (duplicate `expo-application`, RN Directory metadata, SDK version drift) were recorded as `warning` and the build continued; they did not abort the job.

## 2. Root cause

`posthog-react-native@4.74.0` statically imports `@posthog/core/surveys`. That path exists only as a package.json `exports` subpath (`./surveys` → `dist/surveys/index.js`). Both apps set `resolver.unstable_enablePackageExports = false` so Metro uses Sentry's CJS build and avoids a Hermes release crash (`expo/expo#36589`). With exports off, Metro looks for `node_modules/@posthog/core/surveys.js` and fails.

## 3. Fix / remediation

Keep package-exports off. Add a Metro `resolveRequest` interceptor (shared helper used by both apps) that maps `@posthog/core/<subpath>` onto the CJS files under `node_modules/@posthog/core/dist/`. Alternative rejected: re-enabling package exports globally — that would reintroduce the Sentry Hermes "property is not writable" launch crash, which this repo documents as release-build-only and not re-tested.

## 4. Risk & impact on existing functionality

- **Blast-radius grep:** `unstable_enablePackageExports`, `resolveRequest`, `posthog-react-native`, `@posthog/core`. Callers of the new helper: `rider-app/metro.config.js`, `driver-app/metro.config.js` only. Sentry resolution is unchanged (exports stay false).
- **Could this regress a working flow?** If the interceptor maps a PostHog subpath to a missing/wrong file, Metro still fails the bundle (fail-closed). If it incorrectly intercepts an unrelated module, only `@posthog/core/`-prefixed names are handled. Path traversal (`..`) is rejected.
- **Ride state / money / loops:** none.
- **Other consumers of metro.config.js resolveRequest:** web stubs, NativeComponent stubs, `@types/` empty, driver `@tanstack/react-query` pin. PostHog intercept runs after `@types/` and before those; it only returns when the CJS file exists.

## 5. User-experience effect

- **Who sees it:** nobody until a new native binary ships. Failed Android builds never reached Play Internal Testing.
- **Mid-session:** no. This is a build-time Metro resolver change.
- **Copy / visual:** none. rider-app and driver-app have no visual-regression tooling — reasoned about, not screenshotted. admin-dashboard baselines are untouched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/metro/resolvePosthogCoreSubpath.js` | Map `@posthog/core/*` subpaths to CJS `dist/` files | One helper for both apps |
| `shared/metro/__tests__/resolvePosthogCoreSubpath.test.ts` | Temp-dir mapping + traversal + miss cases | Real coverage without Expo |
| `rider-app/metro.config.js` | Call helper from `resolveRequest` | Unblock rider Android bundle |
| `driver-app/metro.config.js` | Same | Unblock driver Android bundle |

## 7. Before / after

```
# Before — Metro (exports off) cannot find the surveys subpath
import "@posthog/core/surveys"
# → Unable to resolve module @posthog/core/surveys
```

```
# After — same import resolves to CJS without turning package-exports on
resolvePosthogCoreSubpath(__dirname, '@posthog/core/surveys')
# → node_modules/@posthog/core/dist/surveys/index.js
```

## 8. Rollback plan

Redeploy is the only path: revert these Metro files and rebuild. No live data, no feature flag, no migration. Existing store binaries are unchanged until a successful production AAB is submitted.

## 9. Verification performed

- [x] Automated tests: `shared/metro/__tests__/resolvePosthogCoreSubpath.test.ts` (run via rider-app Jest)
- [ ] Manual: production `eas build --platform android` not re-run in this change (needs commit + GitHub workflow)
- [x] Blast-radius grep: `unstable_enablePackageExports`, `@posthog/core`, `resolveRequest` in both metro configs
- [x] Did not flip `unstable_enablePackageExports`
- [ ] Production `npm run build` N/A (native Metro, not admin-dashboard)
- [ ] No visual-regression tooling for rider-app / driver-app

## 10. What was NOT verified

- A full EAS Android production bundle after the fix (GitHub workflow must run from main after this lands).
- Whether other `@posthog/core` subpaths (`error-tracking`, `vendor/*`) appear in this SDK version's import graph — they are mapped preventatively; only `surveys` was in the failing log.
- Expo doctor findings (duplicate `expo-application`, RN Directory, SDK version pins) — pre-existing, EAS treated as warning, not this fail.
- Hermes runtime of the PostHog CJS surveys module (surveys stay constructor-disabled; the module is still in the bundle because of a static import).
