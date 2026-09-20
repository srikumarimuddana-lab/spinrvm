# Four-second online background location trial

## Scope and implementation checklist

Two subtasks: first update the recovery-test assertions to distinguish accuracy
rather than timing, and commit that one-file prerequisite. Then commit three
files: backgroundLocation.ts, its callback/cadence test, and this impact log.
Verify existing tests, reproduce the old default/batching behavior with regression
tests, implement, and review before committing.
TodoWrite is unavailable; this checklist tracks the subtask.

- [x] Verify baseline and reproduce the timing regression.
- [x] Change the native defaults and delivery options; verify and review.
- [x] Record results and commit the three-file change.

## Impact

Issue: riders observe updates roughly every 30 seconds even after the driver
travels more than 50 metres. The earlier authentication repair allows uploads,
but does not change native sample/delivery timing.

Root cause established in source: online idle registration requests 30 seconds
and 50 metres and also sets deferredUpdatesInterval to 30 seconds. The installed
Expo native consumers on Android and iOS require that deferred time to elapse;
distance does not bypass it. A refused trip retune retains those old options.
The precise reason the device stayed on idle cadence remains unverified.

Chosen trial: request 4 seconds and 10 metres whenever online, retain High
accuracy during trips and Balanced accuracy between trips, and disable extra
deferred batching on Android. iOS ignores timeInterval, so retain its four-second
deferred delivery interval rather than allowing distance-only callbacks.
Alternative: keep idle at 30 seconds and rely on trip retuning;
rejected for this trial because a rejected retune would retain the observed delay.
The user accepted the battery/data trade-off of the four-second online trial.

Risk: more native callbacks, requests and battery use between rides. Four seconds
is a requested interval, not a delivery guarantee: distance, OS scheduling,
accuracy and network availability still apply. The iOS native provider is
primarily distance-driven; its four-second deferred interval regulates delivery.
No new SDK, schema, auth, fare arithmetic, or upload endpoint changes.

Blast radius: useDriverDashboard starts/retunes the task; recovery/geofence and
reassertDispatchTask reuse the same defaults. Android Auto uses task liveness to
avoid a competing provider. Existing trip recording and location-live fanout use
the delivered callbacks. No sibling backgroundLocation fork is listed in
docs/known-forks.md. Rider code has no changes.

User effect: online driver background callbacks no longer have the configured
30-second time gate, including when trip retuning fails. No layout changes; no
active mobile visual regression tooling exists, and no screenshots are claimed.

| File | Change | Reason |
|---|---|---|
| driver-app/utils/backgroundLocation.ts | Online default 4s/10m; Android batching off, iOS delivery 4s | Avoid idle fallback delay without flooding uploads |
| driver-app/utils/__tests__/backgroundLocation.test.ts | Native boundary and failed-retune regressions | Catch wrong options on online/trip/recovery paths |
| driver-app/__tests__/utils/backgroundLocation.reassert.test.ts | Distinguish retained trip accuracy from idle | Equal timing must not weaken recovery coverage |
| docs/change-log/2026-09-16-background-four-second.md | Impact and evidence | Review and rollout |

Before: online 30s/50m; deferredUpdatesInterval follows cadence (30s or 4s).
After: online/trip 4s/10m; deferredUpdatesInterval is zero on Android, 4s on iOS.

Rollback: reinstall the preceding driver build/update to restore timing. The
existing background_location_fanout_enabled flag can stop rider live delivery,
but does not roll back GPS frequency or battery use. No data migration to undo.

Verification: baseline 80 tests passed. Three new boundary regressions failed
against the old settings, then passed after the change. Final focused run:
84 tests passed across callback/cadence and recovery suites. Two iOS boundary
regressions also failed before retaining the required iOS delivery interval.
Native Expo APIs
are mocked; the production registration/recovery/callback logic is real.
Physical-device cadence, battery, server P95, and network load require a canary;
not established by mocked native tests or bundle export. Observe callback/request
frequency and HTTP 429s in the canary against the backend's 60/minute location
limit. Security review identified the iOS over-delivery risk; retaining its 4s
gate addresses the avoidable source. Review does not establish actual OS timing.

Final checks: driver TypeScript and targeted backgroundLocation ESLint passed;
git diff --check passed. Production Expo exports succeeded for both Android and
iOS (Hermes, --max-workers 2). These are JavaScript production bundles, not a
native APK/IPA compilation, EAS deployment, or physical-device test. Dependencies
were reused via a node_modules junction in the isolated worktree; no dependency
or lockfile edits. Independent review found no outstanding blockers after the
iOS delivery gate was retained.

Canary: install the driver build/update containing this change, go offline then
online to apply the new task options, and test pickup and an active ride with
Maps in front and the phone locked. Check rider event frequency as well as GPS
capture frequency, request volume/429s, and battery before wider release.
