# Change Impact & Risk Log — Android Auto car screen runs standalone

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude Code (session `01UDHyLUXfz`) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers, dispatch |
| PR / commit link | branch `claude/android-auto-react-native-izvv6c` — `e84d59a`, `10fe491`, `38643df`, `e7a9e68`, `df5fea4` (plus `0c87bc7`, a test-only fix) |
| Related issue or gap ID | Driver report: "the icons and screen are not loaded properly… and not refreshing or storing any data"; "I want to show the maps and location like how Google Maps does… without opening the app" |

## 1. Issue / gap identified

With the phone app closed, the Android Auto screen was inert: the car marker
froze at wherever the driver had last been seen, no ride state or earnings ever
loaded, and a ride offer arriving over FCM never reached the head unit. Reported
from a real Toyota, with photos, across several sessions.

## 2. Root cause

Three separate causes, all the same shape — the car screen depended on a React
tree that a car-only launch never mounts.

Android Auto starts the app's **JS context**, not its phone UI. `index.js`
executes (so `registerAutoPlay()` and the FCM background handler both run), but
`app/_layout.tsx` and `app/driver/(tabs)/index.tsx` are route modules and never
evaluate.

1. **Frozen marker** — the process is backgrounded as far as Android is
   concerned, and a backgrounded app with no foreground service has its
   foreground location updates throttled hard. `useCarLocation`'s
   `watchPositionAsync` went quiet for minutes. The throttle applies to the
   process, so re-requesting inside the hook could not fix it.
2. **No data** — `authStore.initialize()` is called only from `app/index.tsx` and
   `app/_layout.tsx`, and `useDriverDashboard()` is mounted only in
   `app/driver/(tabs)/index.tsx`. Both are phone screens. No token was ever
   obtained, so no request was ever issued. Not failing requests — *no requests*.
3. **No offers** — `services/backgroundMessaging.ts` correctly wakes on a
   killed-app offer and persists the payload to `spinr_pending_ride_offer`, but
   the only reader was `useDriverDashboard`'s `consumePendingOffer`.

Launching the phone Activity from the car — the intuitive fix — is not available:
Android 10+ blocks background activity starts, and Play's Car App Quality
guidelines forbid an app driving the phone UI from a head unit. Google Maps
solves this with a location foreground service plus a headless bootstrap, which
is what this change implements.

## 3. Fix / remediation

- **Location.** A display-only foreground service, `spinr-car-location`, started
  on head-unit connect and stopped on disconnect. It publishes to a new
  `carFixChannel` and **makes no network call**. When the driver is already
  online, `spinr-background-location` is running with its own notification, so
  that task now publishes to the same channel instead and the car task stands
  down — one service, one notification, never two.
- **Data.** `lib/androidAuto/carSession.ts` is the headless equivalent of
  `useDriverDashboard`'s mount effects: restore session → hydrate cached ride
  state → `fetchActiveRide` → `fetchEarnings('today')` → `GET /drivers/config`,
  then a 60 s backstop refresh and one on `AppState` → `active`.
- **Offers.** `backgroundMessaging.ts` gained an in-process listener channel that
  the car session subscribes to, so an offer goes straight into `useDriverStore`
  and `register.ts`'s existing `useDriverStore.subscribe(apply)` raises the
  head-unit alert.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (driver-app), but it touches two paths every
driver uses.** Named rather than implied:

| Shared thing | Other consumers (grepped) | Why this is safe |
|---|---|---|
| `handleBackgroundLocationTask` | every **online** driver's location upload | The added `publishCarFix` sits *after* the existing `checkLocationIntegrity` gate and *before* any auth or network work. It cannot change what is uploaded. Its 43 existing tests pass unmodified. |
| `setBackgroundMessageHandler` | every driver's killed-app ride offer | The offer branch is byte-identical apart from the payload being named instead of inlined. The republish happens **last**, after the durable write and the Notifee render. With no car connected there is no subscriber and the function is a no-op. |
| `spinr_driver_last_location` | written by `app/login.tsx` + `useDriverDashboard`; read by `useDriverDashboard.ts:444`; **deleted** by `useDriverDashboard.ts:638` on go-offline | Display-only on both surfaces. Checked all 7 `locationRef.current` assignment sites — the cache never feeds `locationRef`, which is what reaches the backend on go-online and WS reconnect. Shape unchanged; `at` is additive and existing readers destructure `lat`/`lng` only. |
| `consumePendingOffer` | the phone offer panel (mount + foreground resume) | Extracted verbatim to `services/pendingRideOffer.ts`; the hook delegates and keeps the vibration via an `onOffer` callback. |
| `useDriverStore` actions | the phone dashboard | The car issues only existing actions. Both surfaces live means `fetchActiveRide`/`fetchEarnings` may run twice within a minute — both are idempotent server reads writing the same store, so the second is a refresh, not a conflict. |

**Ride state machine.** The only state-machine interaction is the FCM
cancellation path, and it is guarded twice: the `ride_id` must match the ride the
car is showing, and `rideState` must be one of
`ride_offered` / `navigating_to_pickup` / `arrived_at_pickup`. A cancellation
naming an `in_progress` trip is logged loudly and **dropped** — CLAUDE.md is
explicit that `in_progress → completed` is the only transition out, so such a
push is a contract violation, and applying it would strand a driver mid-trip.

**Insurance periods.** Nothing here writes `is_online`. Showing a map does not
change a driver's period; an offline driver watching the car screen stays in
Period 0. No `driver_insurance_periods` row is written or read.

**PIPEDA.** The new location task performs **zero egress**. `publishCarFix`
updates an in-memory marker and the existing device-local cache. No new field is
collected, no new endpoint is called, and no coordinate is logged (the two
`console.warn` paths log a reason string only). Data minimisation holds: an
offline driver's position is drawn for them and transmitted to nobody.

**Background loops (backend).** None touched. This change is entirely client-side.

## 5. User-experience effect

**Driver-facing, and visible mid-session** to anyone who plugs in.

- **New notification.** "Spinr — Showing your location on the car screen",
  present only while a head unit is connected **and** the driver is offline. A
  driver who is already online sees no change: the existing "You're online and
  receiving ride requests" notification is reused. Two Spinr notifications at
  once is specifically prevented (see §4), and both use the same accent colour
  for the same reason.
  - Copy check: states plainly what it is doing and why, no jargon. Android
    requires a notification for a foreground service; it cannot be suppressed.
  - This was put to the product owner as an explicit trade-off against the
    alternative (no notification, stale map when offline) and chosen — the
    alternative is the reported symptom.
- **Car screen now loads.** Ride state, today's earnings and the real offer
  countdown appear on a car-only launch instead of an idle card and a hidden pill.
- **Offers reach the head unit** when the phone app is force-closed.
- **Nothing changes on the phone.** No phone screen, copy, or flow is altered.
- **Signed out is unchanged and deliberate**: map, marker and buttons work; no
  data appears. The bootstrap returns early rather than issuing 401s.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/lib/androidAuto/carFixChannel.ts` | **new** — position cache, subscriber set, AsyncStorage write-back, no React import | Two headless task handlers must publish fixes without pulling React onto the bundle-load path |
| `driver-app/lib/androidAuto/useCarLocation.ts` | reduced to the hook; subscribes to the channel | one owner of the cache, not two |
| `driver-app/lib/androidAuto/carLocationTask.ts` | **new** — `spinr-car-location` display-only foreground service | keeps the marker live with the phone app closed |
| `driver-app/utils/backgroundLocation.ts` | publishes accepted fixes to the channel; exports `isBackgroundLocationRunning()` and the shared notification colour; stops the car task on go-online | one service and one notification at a time |
| `driver-app/lib/androidAuto/carSession.ts` | **new** — headless bootstrap, refresh timer, FCM dispatch subscriber | nothing else loads the car's data |
| `driver-app/lib/androidAuto/register.ts` | starts/stops the location service and the session; tracks and clears the post-connect chrome timers; drops a dead import | wiring, plus a timer-leak fix (see §7) |
| `driver-app/services/pendingRideOffer.ts` | **new** — extracted from `useDriverDashboard` | the car needs the identical rules where the hook does not exist |
| `driver-app/hooks/useDriverDashboard.ts` | delegates to the extracted function | single implementation |
| `driver-app/services/backgroundMessaging.ts` | in-process dispatch listener channel; offer payload named rather than inlined | an offer must reach a connected head unit, not only AsyncStorage |
| `driver-app/lib/androidAuto/carSurface.tsx` | removed a dead `useAuthStore` import | left behind by `ff59126` |
| `driver-app/__mocks__/@shared/store/authStore.js`, `.../services/firebase.js` | **new** test stubs | jest maps every `@shared/*` import into `__mocks__/`; without these no test could reach either module |

## 7. Before / after

**Location on a car-only launch.** Before, the only source was a throttled
foreground watcher:

```ts
// Before — useCarLocation.ts, the sole source of fixes
const sub = await Location.watchPositionAsync(
  { accuracy: High, timeInterval: 2000, distanceInterval: 5 },
  (p) => setLoc({ ...p.coords }),
);
// Backgrounded process with no foreground service → callback goes quiet for
// minutes → the marker sits where the driver was.
```

```ts
// After — carLocationTask.ts, a foreground service Android does not throttle
await Location.startLocationUpdatesAsync(CAR_LOCATION_TASK, {
  accuracy: Location.Accuracy.High,
  timeInterval: 2_000,
  distanceInterval: 5,
  foregroundService: {
    notificationTitle: 'Spinr',
    notificationBody: 'Showing your location on the car screen',
    killServiceOnDestroy: true,
  },
});
// …started ONLY when background permission is already held (it never prompts)
// and only when spinr-background-location is not already running.
```

**Session restore.** Before it was fire-and-forget, so every request below it
raced the token into existence:

```ts
// Before — register.ts
const ensureSession = () => {
  const auth = useAuthStore.getState();
  if (auth.isInitialized || auth.isLoading) return;   // isLoading → give up
  auth.initialize?.().catch(...);                      // not awaited
};
ensureSession();                                       // then nothing follows
```

```ts
// After — carSession.ts
if (!auth.isInitialized) {
  if (auth.isLoading) await waitForInitialized();      // JOIN it, bounded 8s
  else await auth.initialize?.();                      // awaited
}
// …then hydrate → fetchActiveRide → fetchEarnings → GET /drivers/config
```

Joining rather than skipping matters: the refresh token is single-use and
rotates, so two concurrent `initialize()` calls is the race that signs drivers
out.

**Timer leak fixed in passing** (introduced by `456b75d` on this branch):

```ts
// Before — fire-and-forget, outlived the session, fired at a null template
[1200, 4000].forEach((delay) => { setTimeout(() => { ... }, delay); });
```

```ts
// After — tracked and cleared on disconnect
chromeRefreshTimers = [1200, 4000].map((delay) => setTimeout(() => { ... }, delay));
// stopChromeRefresh() runs in didDisconnect; a fake-timer test asserts
// getTimerCount() returns to 0.
```

## 8. Rollback plan

**No feature flag, and the honest reason why:** this is client-side React Native
delivered by OTA, and the repo's `app_settings` flag mechanism is a backend
convention with no equivalent hook on this surface. The rollback is therefore an
**OTA republish of the previous bundle**, which is a single `eas update` on the
same channel and reaches devices on next launch — not a store submission, and
not a native rebuild.

What makes that sufficient here, checked rather than assumed:

- **No migration, no server contract, no new endpoint.** The only backend call
  added is a `GET /drivers/config` the phone already makes.
- **No live-data mutation.** Nothing writes money, wallet deltas, ride state on
  the server, or `driver_insurance_periods`. There is nothing applied to live
  data that a code revert would leave behind.
- **The one durable artifact** is `spinr_driver_last_location`, whose shape is
  unchanged — `at` is additive, and both existing readers destructure `lat`/`lng`
  only, so a rolled-back build reads it exactly as before.
- **Nothing is left running.** `killServiceOnDestroy: true` means the foreground
  service dies with the app, so a driver mid-session at rollback time loses the
  notification and the service on next launch with no orphan.

If only the notification needs to go and the rest should stay,
`startCarLocationService()` can be made to return `'unavailable'` unconditionally
— a one-line OTA that disables the service and leaves every other path intact.

## 9. Verification performed

- [x] **Automated tests run** — full driver-app jest suite: **496 tests / 61
      suites, all passing**. 75 of those are new here:
      `carFixChannel.test.ts` (14), `carLocationTask.test.ts` (16),
      `carSession.test.ts` (22), `pendingRideOffer.test.ts` (7),
      `backgroundMessaging.test.ts` (9), plus 7 added to `register.test.ts`.
      Unit tier only — there is no integration or e2e tier for this surface.
- [x] **Blast-radius grep performed** — searched for: every caller of
      `handleBackgroundLocationTask`; all 7 `locationRef.current` assignment
      sites; every reader/writer of `spinr_driver_last_location`; every consumer
      of `PENDING_OFFER_KEY`; every importer of `useCarLocation` and
      `getLastCarFix`; `startLocationUpdatesAsync` call sites. Results in §4.
- [x] **Reviewed against CLAUDE.md conventions** — ride state machine (the
      `in_progress` cancellation refusal), PIPEDA (no egress, no coordinates in
      logs), insurance periods (no `is_online` write), observability (`warning`
      for degraded-but-recovered, `error` for actionable failures, no Sentry on
      transient sensor loss).
- [x] **`npx tsc --noEmit`** — unchanged at the 2 pre-existing errors in
      `shared/config/firebaseConfig.ts`.
- [x] **`npx eslint`** — 0 errors. One pre-existing warning remains (unused
      `HeatmapCell` type in `carSurface.tsx`).
- [ ] **Manual repro in staging** — NOT done, see §10.
- [ ] **Production build (`npm run build` / EAS)** — NOT run. This is an Expo
      surface with no web build; the equivalent gate is an EAS build, and none
      was produced from this branch.
- [ ] **Feature-flagged** — no. Justified in §8: no flag mechanism exists for
      this surface, and OTA republish is a genuine single-step rollback.

## 10. What was NOT verified

Stated plainly, because the gap is large and the automated coverage should not be
read as more than it is.

- **Nothing has run on a head unit.** Not one commit on this branch since the map
  first rendered has been seen in a car. Every assertion is against mocked
  `expo-location`, a mocked store, and a synthesised FCM payload.
- **Whether Android permits the foreground-service start from a car-only launch
  on a real device is unknown.** Android 12+ can refuse a background FGS start;
  the code treats refusal as a normal outcome and falls back to today's
  behaviour, but only a real phone can say which branch runs.
- **No real FCM message was sent.** Whether a killed-state dispatch push wakes
  the bundle *with a car connected* — and whether the offer alert renders on the
  head unit — needs a live ride to confirm.
- **Session restore from a genuine cold car-only launch is untested end to end.**
  The ordering is asserted against mocked store actions, not a Supabase round
  trip.
- **No visual or notification-shade regression tooling exists for this surface.**
  That the two notifications never appear together is proven by unit test and by
  reading `LocationTaskService.kt`, not by looking at a phone. This is a standing
  gap, already tracked, not a new one.
- **The jest suite was run with a sandbox-local config override.** This
  container's `node_modules/firebase` extraction is missing every subpath
  `package.json`, which breaks `jest.setup.js` at config time; the override
  replaces that one `moduleNameMapper` entry and changes nothing else. CI
  installs with `--frozen-lockfile` and is unaffected.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — the new notification is called out explicitly in §5
