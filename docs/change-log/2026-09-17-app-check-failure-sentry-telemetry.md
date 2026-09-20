# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-17 |
| Author | agent (Claude Code) |
| Surface(s) | shared (rider-app + driver-app) |
| Domain (Sentry tag) | auth |
| PR / commit link | (uncommitted at write time) |
| Related issue or gap ID | Sentry `CRIMSON-SMOKE-7445-ZG` (`SpinrApiError: App Check token required`, driver-app Android) |

## 1. Issue / gap identified

Android drivers on the Play-installed production build get 401 `App Check token required` on every authenticated call (dashboard shows "You're Offline" / "Retry loading earnings" / "Vehicle N/A"), but nothing anywhere records *why* the device could not mint a Firebase App Check token. iOS mints tokens fine.

## 2. Root cause (of the blind spot, not of the Android failure itself)

- `shared/services/errorReporting.ts` drops every `console` breadcrumb in `beforeBreadcrumb` (they routinely carry GPS), so the `console.error` in `getAppCheckToken()` never reaches Sentry.
- `initializeAppCheck()` failures were only `console.log`ged. When init fails, `_appCheckInstance` stays null and `getAppCheckToken()` returns null *before* calling `getToken()`, so the Crashlytics record added in PR #5495 never fires either. An init failure produced zero telemetry.
- Crashlytics is the only sink for the token-fetch diagnostics, and it is not where the team monitors (Sentry is).

## 3. Fix / remediation

Added `reportAppCheckFailure(stage, error, recordNative?)` to `shared/utils/appCheckDiagnostics.ts`:
- formats the native error with the bounded `formatAppCheckError` (code / message / nativeErrorCode / nativeErrorMessage only, never the token or raw native object);
- sends one `captureMessage(..., 'error')` with fingerprint `['appcheck-failure', stage, code]` so each root cause is its own Sentry issue across all devices, and tags `domain=auth`, `appcheck_stage`, `appcheck_code`;
- dedupes per process on `stage|code|message[:120]` with a 20-key cap — `getAppCheckToken()` runs on every API request, so an unregistered device would otherwise emit an event per request, and a per-attempt-varying message suffix cannot defeat the dedupe or grow the set unboundedly;
- does not mark a failure as sent while Sentry is uninitialised (`isSentryActive()` false: headless FCM launch, Android Auto cold start), so it is still reported if Sentry comes up later in the same process; the Crashlytics non-fatal is recorded once regardless;
- optionally forwards the same bounded message to the native Crashlytics recorder (preserves PR #5495's Crashlytics behaviour);
- never throws.

`formatAppCheckError` was corrected to read `nativeErrorCode` / `nativeErrorMessage` — the fields `@react-native-firebase/app`'s `NativeFirebaseError` actually defines (`app/lib/internal/NativeFirebaseError.ts`). The previous `nativeError` field does not exist on that class and was always `undefined` in production; the device log captured 2026-09-17 23:37 confirms the prior output carried only `code` and `message`.

`shared/services/firebase.ts` now calls it from both catch blocks (`init` and `token`). Both catch blocks log the bounded diagnostics (not the raw native error) at `console.error`; the init path previously used `console.log`.

Alternative considered: stop dropping console breadcrumbs for the `[Firebase]` prefix. Rejected: breadcrumbs only attach to some *other* event, and a device whose every call 401s may never send one; a first-class message is deterministic and cheaper to alert on.

## 4. Risk & impact on existing functionality

Blast radius (grep `appCheckDiagnostics`, `getAppCheckToken`, `initFirebaseServices`):
- `shared/utils/appCheckDiagnostics.ts` — consumed only by `shared/services/firebase.ts` and its test.
- `shared/services/firebase.ts` — imported by `rider-app/app/_layout.tsx`, `driver-app/app/_layout.tsx`, `driver-app/lib/androidAuto/carSession.ts`, `shared/hooks/queries/notificationQueries.ts`, `shared/api/client.ts` (via the token provider). Only the two catch blocks changed; the success paths and return values are untouched. `getAppCheckToken()` still returns `null` on failure, so `appCheckHeader()` / `isAppCheckTokenReady()` behave exactly as before.
- New import edge: `appCheckDiagnostics` → `errorReporting`. `errorReporting` imports only `react-native`, so no cycle.
- Sentry volume: bounded to one event per distinct failure per app process (plus one per stage). No per-request events.
- Not affected: ride state machine, dispatch, payments, backend App Check enforcement (unchanged, still on).

## 5. User-experience effect

None. No copy, navigation, or request behaviour changes. Rider/driver see exactly what they saw before; the difference is the failure is now visible in Sentry.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/appCheckDiagnostics.ts` | Added `reportAppCheckFailure`, `AppCheckFailureStage`, `_resetAppCheckFailureReportsForTests`; `formatAppCheckError` reads the real `nativeErrorCode`/`nativeErrorMessage` fields | Single, tested reporting path with capped dedupe; native reason was silently dropped before |
| `shared/services/firebase.ts` | Init and token catch blocks call `reportAppCheckFailure`; both log bounded diagnostics at error level; extracted `recordToCrashlytics` helper | Init failures were invisible; token failures were Crashlytics-only |
| `rider-app/__tests__/appCheckDiagnostics.test.ts` | Fixture rebuilt on the real `NativeFirebaseError` shape; 9 new tests | Fingerprint/tags, per-stage grouping, dedupe incl. varying suffix and cap, native forwarding, Sentry-uninitialised path, never-throws |

## 7. Before / after

```ts
// Before — shared/services/firebase.ts
} catch (e) {
  console.log('[Firebase] App Check init error:', e);          // nowhere else
}
...
} catch (e) {
  const diagnostics = formatAppCheckError(e);
  console.error('[Firebase] App Check token fetch error:', diagnostics);
  if (crashlyticsApi) crashlyticsApi.recordError(..., new Error(`[AppCheck] token fetch failed: ${diagnostics}`));
  return null;
}
```

```ts
// After
} catch (e) {
  console.log('[Firebase] App Check init error:', e);
  reportAppCheckFailure('init', e, recordToCrashlytics);       // Sentry + Crashlytics, once
}
...
} catch (e) {
  console.error('[Firebase] App Check token fetch error:', e);
  reportAppCheckFailure('token', e, recordToCrashlytics);      // Sentry + Crashlytics, once per distinct error
  return null;
}
```

## 8. Rollback plan

JS-only, shipped via EAS Update. Roll back by publishing the previous update group on the `production` branch (`eas update:republish --branch production --group <previous-group-id>`), or `eas update:rollback`. No backend, DB, or store-build change; nothing is applied to live data.

## 9. Verification performed

- [x] TDD: first 5 tests written before implementation and observed failing (`_resetAppCheckFailureReportsForTests is not a function`), then passing.
- [x] `spinr-observability-reviewer` run against the diff; W2 (dedupe-before-init), W3 (dedupe key / cap), W4 (raw error in console), W5 (init log level), W6/T1 (wrong native field), I4 (code in fingerprint), T4 (`toMatchObject`), T6 (varying-suffix + cap tests) fixed in this diff.
- [x] `rider-app`: `npx jest __tests__/appCheckDiagnostics.test.ts __tests__/notificationPermission.test.ts __tests__/errorReporting.test.ts` → 3 suites, 28 tests passed (11/11 on the diagnostics suite).
- [x] `driver-app`: `npx jest lib/androidAuto/__tests__/carSession.test.ts hooks/__tests__/goOnlinePermission.test.ts` → 2 suites, 36 tests passed (both import the shared Firebase service).
- [x] `rider-app` `npx tsc --noEmit -p tsconfig.json` (covers `shared/` via tsconfig paths) → exit 0, no errors.
- [x] Blast-radius grep listed in §4.

## 10. What was NOT verified

- Not run on a device: the native `@react-native-firebase/app-check` error shape on a real Play Integrity failure was not observed here. `formatAppCheckError` already handles `Error`, plain objects, and primitives, so an unexpected shape degrades to `{message}` rather than throwing.
- No production build (`eas build`) was run — JS-only change, but the OTA publish itself is not part of this diff.
- No visual tooling exists for rider-app/driver-app; there is no visual change to reason about.
- The Android root cause itself (Play Integrity / Cloud-project linking) is not fixed by this change; this change is what makes it observable in Sentry.
- Reviewer W1, not addressed here: driver-app's headless FCM launch (`driver-app/index.js`, `services/backgroundMessaging.ts`) and the Android Auto car-only cold start never run `app/_layout.tsx`, so `initErrorReporting()` never runs there and Sentry stays uninitialised for that process. In those contexts this change records the Crashlytics non-fatal only; the Sentry event is deferred until Sentry initialises in the same process, which for a pure headless process is never. Initialising Sentry in the headless entry point is a separate change.
- Reviewer T5/T7: no test exercises the two `firebase.ts` catch blocks themselves (module-scope `require` of native Firebase makes that file hard to unit-test; there is no `firebase.test.ts` in either app, pre-existing), and no driver-app test covers this path.
