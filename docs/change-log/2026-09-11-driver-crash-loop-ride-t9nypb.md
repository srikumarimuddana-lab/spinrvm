# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (session `01Ro32rhyxmV2ws4MaPhPUSX`) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | _pending_ |
| Related issue or gap ID | Sentry `CRIMSON-SMOKE-7445-PV` (crash) and `CRIMSON-SMOKE-7445-PP` (noise). Live-testing ride `SPR-T9NYPB` (`ec8d772a-…-938c1f47d486`), 2026-09-11 13:51–14:10 UTC, Pixel 9 Pro XL / Android 17 / driver `2.0.0+28`. Full incident write-up: artifact "SPR-T9NYPB Post-Mortem". |

This log covers **fixes 1 and 2** of that post-mortem's nine-item plan. Nothing else from the plan ships here.

## 1. Issue / gap identified

During an 18-minute test ride the driver app cold-started **seven times** (Sentry `driver-app cold start` markers at 14:00:59, 14:01:35, 14:02:53, 14:03:47, 14:04:07, 14:07:29, 14:10:27 UTC). Two of those deaths are captured:

- **`PV`, 13:58:16 and again 14:21:41 — a main-thread crash while the app was backgrounded** (`in_foreground: false` on both events). Stack: Fabric `SurfaceMountingManager.removeViewAt` → `com.rnmaps.maps.MapView.onDetachedFromWindow` → `com.google.android.gms.maps.MapView.onPause` → `DeferredLifecycleHelper.zae` → `NullPointerException: Attempt to invoke virtual method 'boolean java.util.LinkedList.isEmpty()' on a null object reference`, chained under `IllegalStateException: Cannot remove child at index 1 from parent ViewGroup [21]`. Issue first seen 2026-08-20; **archived_forever in Sentry** despite being a process-killing crash.
- **`PP` × 8 in the same window — `ExpoLocation.startLocationUpdatesAsync has been rejected: "Couldn't start the foreground service. Foreground service cannot be started when the application is in the background"`**, extra `location: reassert_failed`, every event `in_foreground: false`.

Each relaunch wiped in-memory ride state (the "0.2 km traveled" counter reset in the driver's screenshot), and the relaunch after 13:58:16 came back with no stored session (separate fix, not in this PR).

## 2. Root cause

**Fix 1 (`PV`).** `app/driver/(tabs)/index.tsx` remounts the whole `MapView` (`mapKey` bump) whenever `isOnline` flips false → true or `rideState` returns to `idle`. When the app is relaunched **in the background** — the Android Auto host rebinding the car-app service while the phone is locked — store hydration flips `isOnline` false → true with the activity paused. Removing a `react-native-maps` `MapView` from the tree while paused runs `GoogleMap.onPause` on a map whose deferred-lifecycle state was never created, and Google Maps NPEs on the main thread. Both `PV` events carry `in_foreground: false`; the 14:21:41 one has `app_start_time: 14:10:20` and `view_names: ["/driver"]` — the dashboard, backgrounded, 11 minutes after the ride.

**Fix 2 (`PP`).** `utils/backgroundLocation.ts`'s once-a-minute self-heal (`reassertDispatchTaskUnlocked`, called from inside the background task body) calls `startLocationUpdatesAsync` on the live task to re-promote the shared foreground service. On Android, `expo-location@57.0.16` refuses that call natively unless the activity is resumed — `LocationModule.kt:326`: `if (!AppForegroundedSingleton.isForegrounded && options.foregroundService != null) throw ForegroundServiceStartNotAllowedException()`, and the flag is set only by `OnActivityEntersForeground`/`OnActivityEntersBackground`. The throw happens **before** `registerTask`, so the running task is untouched: a backgrounded re-assert can neither repair nor harm anything. It is pure cost — one native call and one Sentry **error** per backgrounded driver per minute.

**Corrected assumption, stated for the record:** the post-mortem's first draft of fix 2 assumed the rejected re-assert was what starved GPS. Reading the native source disproved that; the ride's GPS holes line up with the process deaths and relaunches (14:02:53, 14:03:47, 14:04:07, 14:07:29, 14:10:20), not with the `PP` events. Fix 2 is therefore noise-removal and a correct replay, not a tracking fix. Tracking recovers by stopping the deaths (fix 1 here; memory work in the plan's item 3).

## 3. Fix / remediation

**Fix 1 — `index.tsx`.** The `mapKey` remount is deferred while `AppState.currentState` is `background` or `inactive`: the intent is parked in `pendingMapRemountRef` and replayed once on the next `active` transition by a new `AppState` listener. The remount conditions themselves are unchanged; only *when* the remount executes moves. By the time the driver looks at the phone the map is a fresh instance either way.

**Fix 2 — `backgroundLocation.ts`.** `reassertDispatchTaskUnlocked` returns early on Android when `AppState.currentState !== 'active'` (the JS mirror of the native flag), after confirming the task is running. A single `AppState` listener is installed lazily on first deferral and removed when it fires; it replays the heal through the locked `reassertDispatchTask()` exactly once. iOS is untouched (no foreground-service gate exists there). Genuine foreground failures still go to `recordNonFatal(..., 'reassert_failed')`.

## 4. Risk & impact on existing functionality

- **Blast radius, fix 1: isolated.** One effect in one screen. `mapKey` has no other writer. `PR #5236`'s marker remount (`markerFocusKey`) and `PR #5222`'s `mapReadyKey` gate are untouched and compose with this: a deferred remount still reads as not-ready in the render the new key appears in.
- **Blast radius, fix 2: two callers of `reassertDispatchTaskUnlocked`, both checked.** (a) The background task body (`backgroundLocation.ts:302`, via the locked wrapper) — the ~60 s tick; (b) `lib/androidAuto/carLocationTask.ts:382`, `stopCarLocationServiceUnlocked({ repairDispatch: true })` — the Android Auto disconnect path that previously lost an on-trip driver's promotion (`SPR-PE7TTB`). For (b), a disconnect while the phone is locked previously *attempted* the repair and failed natively; now it is parked and runs the moment the app is foregrounded — strictly earlier than the old backstop (the next 60 s tick after foregrounding). No caller depended on the rejection.
- **What could regress (fix 1):** a driver who goes online from the car while the phone is locked and then looks at the phone sees the map remount on unlock instead of having already remounted. A `mapKey` remount resets the camera; that reset now happens at the moment of unlock. If `AppState` were stuck reporting `background` while the activity is actually resumed (not a known RN failure mode), the remount would wait for the next real `change` event — the offline→online car-marker restore would be delayed, not lost.
- **What could regress (fix 2):** if a demotion happens while backgrounded, the service stays demoted until foreground — exactly as before, since the repair could never succeed in the background. Nothing new is lost.
- **`AppState` in the headless task context:** the background task's JS runs in the app's runtime when it is alive (state mirrors the activity) and in a headless runtime otherwise (`currentState` is `background`). Both are the correct answer for the gate.
- **Pre-existing, noted, not fixed here:** `SW` "Car location starved: 0 fixes/min" at 13:52:57 fired while the car was stationary at the pickup with `distanceInterval: 5` — likely a false positive for a parked car. Not investigated.

## 5. User-experience effect

Driver-facing, Android. The app no longer dies when it is relaunched or re-hydrated in the background (one of the two captured death causes). The Sentry error stream loses one fake error per backgrounded driver per minute. No visible change otherwise; nothing mid-session for a driver already in the app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | `pendingMapRemountRef`; remount effect checks `AppState.currentState`; new `AppState` listener replays a parked remount on `active` | Never detach a `MapView` while the activity is paused |
| `driver-app/__tests__/app/driverDashboardScreen.test.tsx` | 2 tests: immediate remount while active; deferred while backgrounded, replayed once on `active`, not on `inactive`, not on later foregrounds | Regression guard; the deferred case fails against the previous code |
| `driver-app/utils/backgroundLocation.ts` | `react-native` import; Android foreground gate in `reassertDispatchTaskUnlocked`; `deferReassertUntilForeground` one-shot listener; `_resetDeferredReassert` test hook | Skip a native call that cannot succeed; replay once when it can |
| `driver-app/__tests__/utils/backgroundLocation.reassert.test.ts` | New file, 7 tests: active path, backgrounded skip with no error, single listener across repeats, one-shot replay, task-not-running, iOS unchanged, genuine failure still reported | First direct test coverage of this function |
| `docs/change-log/2026-09-11-driver-crash-loop-ride-t9nypb.md` | This log | Live-tested surface |

## 7. Before / after

```tsx
// index.tsx — BEFORE
if ((rideState === 'idle' && prevRideState !== 'idle') || (isOnline && !prevOnline)) {
  setMapKey((k) => k + 1);
}

// AFTER
const wantsRemount =
  (rideState === 'idle' && prevRideState !== 'idle') || (isOnline && !prevOnline);
const paused = AppState.currentState === 'background' || AppState.currentState === 'inactive';
if (wantsRemount && paused) pendingMapRemountRef.current = true;
if (wantsRemount && !paused) setMapKey((k) => k + 1);
// + AppState 'change' listener: on 'active' with a parked remount → setMapKey once
```

```ts
// backgroundLocation.ts — BEFORE
if (!(await Location.hasStartedLocationUpdatesAsync(TASK_NAME))) return;
const { status } = await Location.getBackgroundPermissionsAsync();
...
await _applyTaskOptions(...);   // rejected natively whenever the activity is paused

// AFTER
if (!(await Location.hasStartedLocationUpdatesAsync(TASK_NAME))) return;
if (Platform.OS === 'android' && AppState.currentState !== 'active') {
  deferReassertUntilForeground();   // one-shot replay on the next 'active'
  return;
}
const { status } = await Location.getBackgroundPermissionsAsync();
...
```

## 8. Rollback plan

`git-revert-safe` — no data, no persistence, no native change. In practice `eas update:republish` of the prior update group (OTA, JS-only). Reverting restores the background crash and the per-minute error.

## 9. Verification performed

- `npx jest __tests__/app/driverDashboardScreen.test.tsx` — **58 passed** (2 new).
- `npx jest __tests__/utils/backgroundLocation.reassert.test.ts` — **7 passed** (new file).
- Affected neighbours: `__tests__/lib/**`, `backgroundMessaging*.test.ts`, `authStore.refreshRace.test.ts` — **152 passed across 10 suites**, no failures.
- `npx tsc --noEmit -p driver-app` — **0 errors**.
- `npx eslint` on the four changed files — **0 errors** (76 pre-existing style warnings in `index.tsx`, untouched).
- Root causes verified against: both `PV` Sentry events (native stack, `in_foreground: false`, `app_start_time`), the `PP` events' extra data, `expo-location@57.0.16` native source (`LocationModule.kt:315-334`, `LocationTaskConsumer.kt:176-236`), the ride's `driver_location_history` trail (gaps vs cold-start markers), and `index.tsx`'s remount conditions.
- **No production build run** — JS-only change, ships OTA. Per the 2026-09-10/11 lesson (three inferred marker fixes failed on device), this should go to the `preview` channel and the test phone before the `production` channel.

## 10. What was NOT verified

- **Not reproduced on a device.** The crash mechanism is read from the native stack and source, and the fix is the direct negation of the trigger (no `MapView` detach while paused) — but it has not been observed to prevent the crash on hardware. Reproduce: go online with Android Auto connected, lock the phone, toggle offline/online from the car or let the app be relaunched by the host; confirm no new `PV` events and that the map is present on unlock.
- **Whether `PV` has a second trigger.** Both captured events fit the `mapKey` path; `rideState → idle` in the background (e.g. a rating dismissal from the car) would take the same deferred path, but no other `MapView` unmount route was audited.
- **The five uncaptured deaths** (14:01:35, 14:02:53, 14:03:47, 14:04:07, 14:07:29). Nothing here claims to fix them; ANR/NDK capture (plan item 8) is what would name them.
- **The OOM (`SX`)** is not addressed here (plan item 3).
- **`AppState` fidelity inside the headless task runtime** is reasoned from RN's implementation, not measured on this device.
