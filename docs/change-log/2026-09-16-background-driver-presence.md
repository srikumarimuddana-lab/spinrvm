# Background driver discovery cadence

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Author | Codex |
| Surface / domain | driver-app / drivers, dispatch |
| Branch | codex/background-driver-presence |

## Issue and root cause

Drivers reportedly disappear from the rider home map within a minute of minimizing the app. The socket deliberately closes after 3 seconds; Redis presence expires after 30 seconds. Idle native GPS previously requested a 30-second interval, 30-second deferred delivery, and 50 metres of movement. Stationary callbacks could therefore stop, and even moving updates had no delivery margin. This configuration defect is confirmed; the exact reported device has not been reproduced.

## Remediation and alternatives

Request idle high-accuracy fixes every 10 seconds with zero movement threshold, including the no-argument startup path. Keep native automatic pausing disabled and the Android foreground service enabled. Increasing server presence TTL alone was rejected because movement-gated updates can stop indefinitely and a longer lease retains unreachable drivers longer. No backend, database, authentication, trip cadence, or money changes.

## Risk, blast radius, and user experience

More accurate/frequent idle sampling increases battery, network and backend load while online. Rider home discovery, estimates and dispatch indirectly benefit from fresh presence; administrators see more frequent location updates. No screen or notification copy changes. Existing installations retain old behavior until an updated JS bundle is loaded and the task is restarted or retuned.

Searched all driver-app consumers: useDriverDashboard starts, resumes and retunes this task; app/_layout registers it; Android Auto carLocationTask coordinates the shared native service and reasserts it; backgroundMessaging uses recovery and background authentication; sessionTeardown stops it. Internal geofence recovery/self-heal reuse the defaults. carFixChannel receives fixes, and tripLocationRecorder records/uploads them. No matching sibling fork in docs/known-forks.md. Trip sampling remains 4 seconds/10 metres. Existing idle-history/period-1 consumers receive more samples but their algorithms are unchanged.

This is a bounded configuration correction to existing tracking, without a new feature flag. Phone validation is required before rollout because this affects battery and dispatch discoverability.

## Files

| File | Change | Why |
|---|---|---|
| driver-app/utils/backgroundLocation.ts | Idle cadence and default accuracy | Keep stationary sampling within presence window |
| driver-app/utils/__tests__/backgroundLocation.test.ts | Startup and return-to-idle regression cases | Exercise options passed to native tracking |
| docs/change-log/2026-09-16-background-driver-presence.md | Impact and verification record | Document rollout limits |

## Before / after

```ts
// Before
{ timeInterval: 30_000, distanceInterval: 50, accuracy: Location.Accuracy.Balanced }
// After
{ timeInterval: 10_000, distanceInterval: 0, accuracy: Location.Accuracy.High }
```

## Rollback

No live data mutation or migration. Re-deliver the previous JS bundle through the established mobile release channel (or previous native build if delivered that way); restart tracking to restore its options. No database switch controls this cadence, so a mobile release rollback is required. Users can go offline to stop tracking immediately. Rollback restores the discovery defect.

## Verification

- New startup/return-to-idle tests failed against the original 30-second options, then passed after the fix.
- 94 tests passed across backgroundLocation.test.ts, backgroundLocation.reassert.test.ts, and useDriverDashboard.socketLifecycle.test.ts.
- Targeted ESLint: zero errors, two existing require-import warnings in unchanged test code.
- Independent read-only review found no actionable defects; checked installed Expo Android/iOS native option handling.
- Production Android/iOS Hermes bundle export passed: `npx expo export --platform android --platform ios --output-dir C:/Users/swarn/AppData/Local/Temp/spinr-background-presence-export` (3519 Android modules, 3423 iOS modules). This is a production JS bundle build, not a native APK/IPA release build.

## Not verified

No native release binary built, no deployment, no physical-device or battery test, and no active mobile visual/snapshot regression tooling. Native APIs do not guarantee callbacks within 30 seconds under all OS/network conditions. Longer background sessions still depend on existing token expiry/refresh behavior, unchanged here. Required canary: online stationary driver, minimize/lock screen, rider map and booking availability for several minutes; repeat moving, return from trip to idle, then go offline and confirm removal on both Android and iPhone.
