# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (agent session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (attached to the PR this commit ships in) |
| Related issue or gap ID | Sentry issue — `ExpoLocation.startLocationUpdatesAsync` rejected, user 6d2732f9-59cd-4b36-b53f-e42eda95a63c |

## 1. Issue / gap identified

Sentry logs a JS error event for driver-app: `Error: Call to function 'ExpoLocation.startLocationUpdatesAsync' has been rejected. → Caused by: Couldn't start the foreground service. Foreground service cannot be started when the application is in the background.`

## 2. Root cause

This is Android 12+'s documented restriction: an app cannot start (or re-promote) a foreground service while its process is backgrounded, with no active OS-granted exemption in play. `backgroundLocation.ts`'s periodic self-heal (`reassertDispatchTaskUnlocked`, fired ~once/min from inside the already-running location task, and by other callers of `reassertDispatchTask`) re-asserts the dispatch task's options to re-promote the shared Android location service if something demoted or killed it. When that re-assert fires with the app genuinely backgrounded and no exemption available, `Location.startLocationUpdatesAsync` rejects with exactly this OS string, the `catch` block calls `recordNonFatal(...)`, and that routes to Sentry.

This is **not a crash** — every call site that can reach `_applyTaskOptions` already wraps the outcome in a `try`/`catch` (traced `recoverTripLocation`, the WS `location_health` handler in `useDriverDashboard.ts`, the FCM `location_health` handler in `backgroundMessaging.ts`, and `reassertDispatchTaskUnlocked` itself), so the app keeps running and tracking retries on the next opportunity (next self-heal tick, a geofence exit — which Android exempts from this restriction — a WS/FCM nudge, or the driver reopening the app). The only problem is that this *expected, self-recovering* condition was being reported to Sentry as an error, which — given the self-heal cadence (~1/min) — can flood one event per minute for every driver who leaves the app backgrounded for a while, on a case the code already fully recovers from without help.

## 3. Fix / remediation

Added `_isBackgroundedForegroundServiceRejection()`, matched on Android's fixed OS string ("...application is in the background"), walking the error's `.cause` chain (Expo wraps the real native reject reason there under a generic "...has been rejected." outer message — this matches the exact shape in the Sentry report). `reassertDispatchTaskUnlocked`'s catch now checks this first: on a match, it logs a `console.warn` and returns without calling `recordNonFatal` (so no Sentry event); every other error is unchanged — still reported via `recordNonFatal` exactly as before. No change to tracking/recovery behavior itself — this is purely an observability classification fix, matching the pattern this same file already uses elsewhere (`handleBackgroundLocationTask`'s durable-upload-deferred catch, and the geofencing "not monitoring" catch) and the CLAUDE.md observability rule "Degraded-but-recovered → warning log + metric (never Sentry — noise)".

## 4. Risk & impact on existing functionality

- Change is confined to one `catch` block in `reassertDispatchTaskUnlocked`. Grepped `backgroundLocation.ts` and its test file for every other `recordNonFatal` call site (`bg_start_refused_signed_out`, `recover_refused_signed_out`, `stop_liveness_probe_failed`, `stop_updates_failed`, `liveness_probe_failed`) — none of those are touched; they still report unconditionally.
- No change to when/how often the self-heal fires, when tracking starts/stops, or cadence (TRIP_CADENCE / IDLE_CADENCE) — purely whether one specific, already-recoverable failure gets reported to Sentry.
- Does not affect billed-distance/SGI insurance-period tracking correctness: samples were never lost by this rejection (it only ever blocks a repromotion of the *notification/foreground status*, not capture — capture continues via the existing native task once it's running, and the durable SQLite outbox already covers gaps regardless).
- Blast radius: isolated to this one catch block; `carLocationTask.ts` (Android Auto) was checked and does not call this function or reach the same code path independently.

## 5. User-experience effect

None. No UI, notification, or driver-facing behavior changes — this only changes what gets reported to Sentry, not what the app does. Not visible mid-session or otherwise to any driver.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/backgroundLocation.ts` | Added `_isBackgroundedForegroundServiceRejection()`; `reassertDispatchTaskUnlocked`'s catch checks it first and skips `recordNonFatal` on a match | Stop reporting an expected, self-recovering Android 12+ platform restriction as a Sentry error |
| `driver-app/utils/__tests__/backgroundLocation.test.ts` | Added a describe block covering: the exact production rejection shape does not call `recordNonFatal`; an unrelated re-assert failure still does | Regression coverage |

## 7. Before / after

```ts
// Before
} catch (e) {
  recordNonFatal(e, { domain: 'drivers', surface: 'driver-app', location: 'reassert_failed' });
}
```

```ts
// After
} catch (e) {
  if (_isBackgroundedForegroundServiceRejection(e)) {
    console.warn('[BgLocation] Re-assert deferred — foreground service restart blocked while backgrounded');
    return;
  }
  recordNonFatal(e, { domain: 'drivers', surface: 'driver-app', location: 'reassert_failed' });
}
```

## 8. Rollback plan

Pure code change, no flag, no persisted state, no native/manifest change — a `git revert` of this commit is a complete rollback. Worst case of reverting is that this specific expected condition resumes generating Sentry noise, which is the current (pre-fix) production state — no data or user-facing regression either way.

## 9. Verification performed

- [x] Automated tests run: `yarn jest utils/__tests__/backgroundLocation.test.ts` → 55 passed, 0 failed (includes the 2 new cases).
- [x] `tsc --noEmit` — no new errors on the changed files.
- [x] `eslint` on both changed files — 0 new warnings/errors (2 pre-existing `no-require-imports` warnings elsewhere in the test file, unrelated to this change).
- [ ] Manual repro on a real Android device — **not done**; this environment has no Android device/emulator to reproduce "app backgrounded + FGS demoted" against real Android 12+ platform behavior.
- [x] Blast-radius grep performed: every `recordNonFatal` call site in `backgroundLocation.ts`, every caller of `reassertDispatchTask`/`reassertDispatchTaskUnlocked`.
- [x] Reviewed against CLAUDE.md observability convention ("degraded-but-recovered → warning log + metric, never Sentry").
- [x] Not user-visible — no feature flag needed (pure observability change, no behavior change).

## What was NOT verified

- Not verified against a real Android 12+ device — reasoned from Android's documented `ForegroundServiceStartNotAllowedException` behavior and the exact error text Sentry captured, not reproduced live. No visual-regression tooling applies (this is non-UI native/JS logic, not a screen).
- Did not confirm whether this same rejection is also reaching Sentry via a path outside `reassertDispatchTaskUnlocked` (e.g. a future/other caller of `_applyTaskOptions` added without a catch) — the fix only covers the confirmed path; if the same noise reappears from elsewhere, that would need its own grep.
