# Background driver discovery cadence

## Luna review: avoid a second queued presence renewal

The live endpoint already renews presence before queuing marker work. Its helper now receives `refresh_presence=False`, avoiding a duplicate Redis write and a delayed renewal after an offline clear. Batch callers retain the default helper renewal. Files: location.py, test_live_location.py, this log. Before: request and queued marker both renew; after: live request renews once. Blast radius is the live endpoint only; default helper behavior preserves other callers. An optional argument avoids changing batch semantics. Rollback: backend release rollback; no durable data change. Regression asserts one renewal while the actual marker helper still writes coordinates. Luna ran 49 focused backend settings/location tests successfully. No live Redis/offline race test was performed; this removes the redundant queued renewal, not all pre-existing read/write races.

## Review correction: marker writes independent of fanout

The earlier heartbeat-only correction still left moving drivers at stale persisted coordinates with fanout disabled. The live endpoint now always queues the existing integrity/order/coalescing-protected marker helper after validation. That helper already gates rider messages internally. `accepted:true` means queued, not guaranteed persisted; invalid/offline/revoked fixes still fail validation. This supersedes earlier descriptions of flag-off accepted:false. Alternative of enabling fanout was rejected because nearby/dispatch coordinates must not depend on optional rider delivery.

Files: backend/routes/drivers/location.py, backend/tests/test_live_location.py, this log. Blast radius: driver background live uploads and all nearby/dispatch readers of drivers.lat/lng. Risk: marker DB load with fanout off now uses the same existing write coalescing as enabled traffic. Rollback requires backend release rollback; do not use fanout=false to freeze markers. No historical-data or schema changes. Regression reproduced two failures before fix; endpoint test runs the real marker helper to assert coordinate write with no rider send. Focused test results recorded before PR completion; no live database or production latency test.

## Review correction: dashboard rollout control

Added Stationary driver tracking under Settings > Operations. It reads the stored Boolean, defaults off when absent, and uses the existing Save Changes action. The control explains battery/network cost, cache propagation and foreground resume for enable/rollback. This completes the supported admin workflow rather than requiring production SQL access. Files: admin settings page (control), e2e/settings.spec.ts (enable/save/reload/disable/save/reload), this log.

Blast radius: settings page's existing shared state/save payload and admin PUT; no shared component changes. Before: no control. After: authorized operators can toggle the new setting. Risk: accidental enablement increases sampling, so it remains false by default and the UI calls for device testing. Rollback: switch off, Save Changes, wait for settings caches then resume driver app. Existing backend/mobile caches each retain values for 60 seconds. No durable data or schema rollback required.

Verification: final Android/iOS production Hermes exports passed after the resume correction; 109 focused mobile and 11 admin/public backend tests passed. Admin build/browser validation results are recorded before PR completion. Existing settings visual baseline covers the initial Integrations tab, not this Operations control; that baseline is unchanged. No physical-device test or live settings write performed.

## Review correction: supported admin rollout writes

The new column was missing from SettingsUpdateRequest and its maintained schema snapshot, so admin PUT silently discarded rollout changes. Added the optional Boolean to the existing audited admin write path and drift snapshot, with enable/disable persistence and omission tests. No new endpoint or permission model. Consumers: dashboard settings save and any authenticated admin settings client; public/mobile readers remain unchanged. This supersedes the earlier SQL-only operational limitation. A custom SQL-only control was rejected because the existing admin settings path already provides access control, auditing and bounded cache expiry.

Files: backend/routes/admin/settings.py (allow field), backend/tests/test_admin_settings_write_allowlist_drift.py (snapshot and save regression), this log. Before: unknown field discarded; after: explicit true/false persisted, omitted field left unchanged. UX: operators can enable/roll back through supported settings requests. Risk: authorized admins can change stationary sampling and its battery/network cost. Rollback: save false and allow cache expiry plus foreground resume; no schema removal. Verification: three regression cases failed before the field was added; 11 targeted settings tests passed. Live database writes and real-device rollout remain unverified.

## Review correction: refresh running tracking on foreground resume

Issue/root cause: an already-running task returned immediately from startBackgroundLocation, so a parked driver without GPS callbacks never reapplied a changed rollout flag. Fix: that path now invokes the locked reassert helper, preserving persisted trip cadence and passing the caller's lifecycle cancellation guard through the asynchronous flag read. Before: running -> return. After: running -> guarded reassert -> return.

Files: backgroundLocation.ts (resume path), its utils/__tests__/backgroundLocation.test.ts (no-callback enable/disable and cancellation regression), this log. Consumers: useDriverDashboard's existing online/resume effect, go-online, recoverTripLocation and geofence recovery. No new timer or listener; the existing resume effect supplies the independent trigger. A polling timer was rejected because it cannot reliably wake a suspended mobile process. Android still defers native reconfiguration while backgrounded. Flag cache can retain its value for 60 seconds, so a resume inside that cache window can require a later resume after expiry. Already-foreground parked drivers can resume the app to apply changes.

Risk/UX: foreground resume now makes a serialized native option call for online tracking, without stopping the task. Offline/cancelled callers cannot apply after a flag read. Rollback: disable the stationary flag through settings and resume after cache expiry; no durable data changes. Verification: new regression failed before implementation; 109 tests passed across background recording, reassert and dashboard socket lifecycle. Phone behavior/battery and visual checks remain unverified; mobile has no visual snapshot tooling. Production exports are rerun for this correction before PR completion.

## Review correction: presence independent of delivery rollouts

Issue/root cause: with fanout and idle history flags at their false defaults, location-live returned before renewing presence and idle history dropped no-ride points. Valid background callbacks therefore could not prevent expiry.

Fix and user effect: the location-live endpoint now renews presence after checking session revocation, driver ownership/online state and GPS freshness, before checking the optional fanout flag. Accepted remains false when delivery is disabled; this means no marker/history delivery, not a failed heartbeat. Drivers sending fresh authenticated updates can remain discoverable independently of those rollouts. Stationary callbacks still depend on native sampling and the separately gated stationary mode.

Alternative: enabling both delivery/history flags was rejected because presence must not require optional history collection. Blast radius: backgroundLocation.ts is the production location-live caller; shared presence is read by nearby, estimates, matching, admin monitoring and surge supply. Existing batch and WebSocket paths are unchanged. The disabled endpoint now performs the same online/freshness validation as the enabled endpoint, returning 403/409/422 for invalid requests instead of an unconditional accepted:false. Enabled delivery also retains its existing later renewal; the extra Redis write is bounded by existing location rate limits.

| File | Change | Reason |
|---|---|---|
| backend/routes/drivers/location.py | Validate and renew before fanout gate | Keep presence independent of delivery |
| backend/tests/test_live_location.py | Flag-off and rejection regression cases | Prevent missing or unauthorized renewal |
| This change log | Impact and verification | Record rollout boundary |

Before: `fanout off -> accepted:false`. After: `session + owned online driver + fresh fix -> mark_present -> fanout gate`.

Rollback: restore the previous backend release; no durable data/schema changes or cleanup are needed for this correction. Existing leases expire naturally. Delivery flags intentionally do not disable heartbeat renewal. Risk: one driver lookup and Redis renewal now occur for valid requests even with fanout disabled; production latency has not been measured.

Verification: regression first failed (6 failures), then 27 tests passed across live-location, WebSocket live-location and disconnect-presence suites. No live Supabase/Redis or physical-device test was performed; no mobile code changed in this correction. Prior production bundle exports remain the mobile build evidence.

## Review correction: default-off remote rollout

This section supersedes earlier statements that no feature flag/schema change exists. Migration 428 adds `settings.driver_stationary_tracking_enabled BOOLEAN NOT NULL DEFAULT FALSE`; public GET /settings exposes it, defaulting false when missing. Operations controls it through the Supabase SQL settings row, not the admin settings form. No migration or production enablement has been performed.

Off/missing/unreadable: idle remains main's 4s/10m/Balanced. On: idle selects 4s/0m/High. Trip remains 4s/10m/High regardless of flag. `stationaryTrackingFlag.ts` checks the public endpoint with App Check, bounds the entire read to 3 seconds, caches for 60 seconds in each JS runtime and clears expired enablement on failure while reporting it to Crashlytics. It persists no enablement across process starts. All actual idle callers use the IDLE_CADENCE constant or default options; native start/retune/reassert flows therefore pass through the gate. A post-read session/cancellation check prevents obsolete go-online work starting tracking.

Remote rollout/rollback: after applying migration and deploying backend/app, an operator can set the flag true for a controlled validation window. Set it false to restore baseline without another mobile release:

```sql
UPDATE public.settings SET driver_stationary_tracking_enabled = false
WHERE id = 'app_settings';
```

Retain the column during rollback; remove only after retiring readers. Server and mobile caches each last up to 60 seconds. Reconfiguration occurs on native option application/self-heal, not immediately on the SQL write. Android cannot re-promote its foreground service while backgrounded, so restoration may wait until foreground resume; go offline still stops tracking. iOS reapplies on the next eligible self-heal/cadence callback. This is a remotely controlled rollout, not an instantaneous background stop switch. Initial false state means experimental stationary sampling remains dark until enabled; the shared backend 90s lease remains independent.

Files added/changed for review: migration428, backend/routes/settings.py and backend/tests/test_public_settings.py; driver-app/utils/stationaryTrackingFlag.ts and its unit test; backgroundLocation.ts, backgroundLocation.test.ts and backgroundLocation.reassert.test.ts. Existing settings table RLS unchanged; no new index or user-data table. Public endpoint exposes a boolean only. Flag read failures report no coordinates or credentials. No new production loop.

Verification: settings regression initially failed then 8 backend settings tests passed. 134 mobile tests passed across six suites, including strict boolean gate, cache/remote disable, timeout/late response, read failure, baseline/enable/rollback, trip bypass and cancelled startup. Android/iOS production Hermes exports passed after integration. ESLint: 0 errors, 15 test warnings. Migration and mobile code reviews found no functional blockers; PR schema and rollback declarations updated. Native release binary, actual migration, real-phone battery/locked-screen rollout and live SQL rollback remain unverified.

## Merge resolution against main (2026-09-16)

This section supersedes the original 10-second cadence and background-auth limitation below. Main added a user-approved four-second online cadence and coordinated background token renewal. Resolution preserves both, combines idle four-second timing with this PR's zero movement threshold and High accuracy, and retains the shared 90-second presence window. Android deferred delivery remains zero; iOS remains four seconds. Trip cadence stays four seconds/10 metres/High. Token renewal and native session-lock code are unchanged from main.

Resolved files: driver-app/utils/backgroundLocation.ts and its utils/__tests__/backgroundLocation.test.ts. Updated driver-app/__tests__/utils/backgroundLocation.reassert.test.ts to distinguish trip/idle by movement threshold, since timing and accuracy now match. The extra idle sampling relative to the original ten-second PR raises battery/network load further; physical-device testing remains required. Rollback and consumer blast radius below still apply.

Verification after merge: 124 tests passed in five suites covering background callbacks, reassert/recovery, socket lifecycle, background auth and native session locking; Android/iOS production Hermes exports passed. ESLint: zero errors, nine warnings in test code. Independent review confirmed upstream preservation. An additional authStore.refreshRace run reported two failures (delayed go-offline cleanup timeout and persisted-credential revocation assertion); that test and shared/store/authStore.ts match origin/main byte-for-byte, but these failures were not separately reproduced in a base checkout. No native release binary, phone validation, or deployment performed.

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
