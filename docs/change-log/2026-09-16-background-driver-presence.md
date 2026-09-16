# Background driver discovery cadence

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Author | Codex |
| Surface / domain | driver-app, backend / drivers, dispatch |
| Branch | codex/background-driver-presence |

## Issue and root cause

Drivers reportedly disappear from the rider home map within a minute of minimizing the app. The socket deliberately closes after 3 seconds; Redis presence expires after 30 seconds. Idle native GPS previously requested a 30-second interval, 30-second deferred delivery, and 50 metres of movement. Stationary callbacks could therefore stop, and even moving updates had no delivery margin. This configuration defect is confirmed; the exact reported device has not been reproduced.

## Remediation and alternatives

Request idle high-accuracy fixes every 10 seconds with zero movement threshold, including the no-argument startup path. Keep native automatic pausing disabled and the Android foreground service enabled. Extend shared presence from 30 to 90 seconds after the latest successful update, within the dispatch domain's documented heartbeat-age upper bound. Every callback renews the window, so there is no fixed maximum background duration while updates continue. Explicit offline and revocation clears remain immediate. No database, authentication, trip cadence, or money-algorithm changes.

A map-only grace was rejected: it could display a driver whom estimates and matching exclude. Increasing TTL alone would not fix movement-gated idle callbacks, so both the mobile cadence fix and bounded server tolerance are needed.

## Risk, blast radius, and user experience

More accurate/frequent idle sampling increases battery, network and backend load while online. Shared presence consumers include REST nearby drivers, ride estimates, dispatch_service matching, matching.py initial/cascade filters, admin monitoring, surge supply counts and stale-intent reconciliation. These consumers now tolerate a gap up to 90 seconds. A fully disconnected driver can also remain eligible for up to 90 seconds, potentially delaying matching through offer timeouts or temporarily affecting surge supply; no claim that a live presence key proves delivery. Offer expiry/acceptance checks and push delivery remain in place. H3 location index expiry is already 90 seconds and is unchanged. No copy changes. Backend grace applies to existing clients after their next renewal; removal of the movement threshold requires the updated driver bundle.

Searched all driver-app consumers: useDriverDashboard starts, resumes and retunes this task; app/_layout registers it; Android Auto carLocationTask coordinates the shared native service and reasserts it; backgroundMessaging uses recovery and background authentication; sessionTeardown stops it. Internal geofence recovery/self-heal reuse the defaults. carFixChannel receives fixes, and tripLocationRecorder records/uploads them. No matching sibling fork in docs/known-forks.md. Trip sampling remains 4 seconds/10 metres. Existing idle-history/period-1 consumers receive more samples but their algorithms are unchanged.

This is a bounded configuration correction to existing tracking, without a new feature flag. Phone validation is required before rollout because this affects battery and dispatch discoverability.

Existing limitation found in review: dispatch_service treats an empty presence set as fail-open, whereas nearby/estimates and later matching filters exclude absent drivers when Redis is reachable. This change shares the lease duration; it does not repair every pre-existing consumer policy. Tests exercise local expiry and a mocked Redis MGET reader, not live Redis SET/expiry.

## Files

| File | Change | Why |
|---|---|---|
| driver-app/utils/backgroundLocation.ts | Idle cadence and default accuracy | Keep stationary sampling within presence window |
| driver-app/utils/__tests__/backgroundLocation.test.ts | Startup and return-to-idle regression cases | Exercise options passed to native tracking |
| backend/utils/driver_presence.py | Shared renewable 90-second lease | Tolerate delayed background updates consistently |
| backend/tests/test_ws_disconnect_presence_grace.py | Timed renewal, silence expiry, immediate clear tests | Exercise common discovery and matching presence readers |
| docs/change-log/2026-09-16-background-driver-presence.md | Impact and verification record | Document rollout limits |

## Before / after

```ts
// Before
{ timeInterval: 30_000, distanceInterval: 50, accuracy: Location.Accuracy.Balanced }
// After
{ timeInterval: 10_000, distanceInterval: 0, accuracy: Location.Accuracy.High }
```

Backend: `PRESENCE_TTL = 30` becomes `PRESENCE_TTL = 90`.

## Rollback

No schema migration or durable data mutation. Re-deliver the previous JS bundle through the established mobile release channel (or previous native build if delivered that way); restart tracking to restore its options. Roll back the backend release to restore 30-second renewals; existing 90-second keys naturally expire within 90 seconds or are overwritten at the next renewal. Do not flush shared Redis. No database switch controls these constants, so release rollback is required. Users can go offline to stop tracking immediately. Rollback restores the discovery defect.

## Verification

- New startup/return-to-idle tests failed against the original 30-second options, then passed after the fix.
- New backend clock-controlled tests failed at 60 seconds against the old lease; they cover renewal, silence expiry, restored updates and immediate explicit clear. Existing WebSocket revocation/ownership tests run alongside them.
- Backend test environment: disabled unrelated auto-loaded plugins (xonsh crashed without a console), explicitly loaded AnyIO and pytest-asyncio, and supplied HOSTNAME on Windows (the existing admin infrastructure fallback uses Unix-only os.uname). Initial broad run: 170 passed and that Windows hostname test failed; corrected-environment rerun: 171 passed, none skipped, across presence, estimate ghost filtering, prematch privacy, dispatch service, stale-intent reconciliation, admin monitoring and surge. Targeted Ruff passed. Security reviewer found no introduced blocker; existing empty-set dispatch policy is noted above.
- 94 tests passed across backgroundLocation.test.ts, backgroundLocation.reassert.test.ts, and useDriverDashboard.socketLifecycle.test.ts.
- Targeted ESLint: zero errors, two existing require-import warnings in unchanged test code.
- Independent read-only review found no actionable defects; checked installed Expo Android/iOS native option handling.
- Production Android/iOS Hermes bundle export passed: `npx expo export --platform android --platform ios --output-dir C:/Users/swarn/AppData/Local/Temp/spinr-background-presence-export` (3519 Android modules, 3423 iOS modules). This is a production JS bundle build, not a native APK/IPA release build.

## Not verified

No native release binary built, no deployment, no physical-device or battery test, and no active mobile visual/snapshot regression tooling. Native APIs do not guarantee callbacks within 90 seconds under all OS/network conditions. Longer background sessions still depend on existing token expiry/refresh behavior, unchanged here. Required canary: online stationary driver, minimize/lock screen, rider map and booking availability for several minutes; repeat moving, return from trip to idle, then go offline and confirm removal on both Android and iPhone. Extended-silence handling is intentional; an indefinitely visible driver with no activity is not safe to dispatch to.
