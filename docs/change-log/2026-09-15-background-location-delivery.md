# Background location delivery recovery

| Field | Value |
|---|---|
| Date / author | 2026-09-15 / Codex |
| Surface / domain | backend, driver-app, rider-app / rides, drivers |
| Issue | Driver marker stays at the minimize position; rider also becomes stale. |

## Backend fanout

Root cause: v2 REST uploads persist breadcrumbs and update `drivers`, but unlike
foreground WS GPS never emit `driver_location_update`. Rider ride-in-progress
stops polling when its own socket connects. Successful persistence is not delivery.

Remediation: select the greatest accepted capture time (not enqueue sequence),
then publish a trusted, recent fix through the existing cross-replica manager.
Recheck current ride assignment and accepted/arrived/in-progress visibility.
Alternative: continuous rider polling alone; existing live fanout wins on latency
and avoids depending on coalesced database markers for every update.

Blast radius: v2 trip uploader in driver dashboard/background task; rider socket
and ride store receive the existing event with additive ride/capture metadata.
Legacy ingestion, idle recording, fares, immutable history and ride states unchanged.
One deferred indexed ride lookup per published batch; no new lookup on ack path.
Risk: interleaving REST/WS events; existing HWM is not globally atomic. Client
ordering and stale-socket fallback need separate validation. No raw GPS logs added.
UX: riders can receive background movement mid-ride once the rollout flag is on.

| File | Change | Why |
|---|---|---|
| backend/routes/drivers/location.py | Deferred fanout / capture selection | Restore live delivery |
| backend/tests/test_background_location_fanout.py | Actual ingestion/task regressions | Reproduce missing events and gate disclosure |
| this log | Risk and verification record | Live-testing release gate |

```python
# Before: latest = next(reversed(accepted_points)); DB marker only
# After: latest = max(accepted_points, key=captured_at); DB + guarded rider event
```

Rollout/rollback: `app_settings.background_location_fanout_enabled` defaults false;
enable only after staging/canary; false stops fanout without redeploy. No production
setting changed. Capture selection can be reverted by backend deploy; no historical
data rewritten. No app build or deployment performed. Tests exercise production
functions with DB, integrity and event bus boundaries mocked; real Redis delivery,
native GPS, OEM battery behavior and real rides remain unverified.

Verification: 26 targeted backend tests passed (fanout + location-batch); the
new suite failed on missing fanout before the fix. Independent reviewer caught
naive/aware timestamp mixing; normalization and regression now pass. Ruff clean.

## Driver resume marker

Root cause: refreshLocation updated React coordinates but CarMarker ignores props
after its fix feed is seeded. A timer repeatedly stamped an old position as new,
making genuine movement during backgrounding appear implausibly fast on resume.
Fix: feed real recent resume measurements with capture time and existing integrity/
accuracy gates; remove fabricated fixes. Failed GPS cannot mark an old cache healthy.
Alternative: remount CarMarker on every resume; feeding the existing channel avoids
resetting map animation and keeps the raw capture timestamp contract.
Consumers: dashboard hook is used by driver/(tabs)/index.tsx; both forked CarMarkers
already hold after capped extrapolation (shared markerPlayback), neither is edited.
Risk/UX: standstill now uses the existing 1.5s extrapolation cap then holds; physical
stop/resume animation needs device validation. No layout or new copy changes.
Files: useDriverDashboard.ts (producer), its socketLifecycle test (actual hook), this log.
Before: `setLocation(loc)` / cached fix plus `Date.now()` heartbeat.
After: `markerFixFeed.emit({...fix, timestampMs: loc.timestamp})` / no fake fixes.
Rollback: revert app update; isolated producer repair has no data migration or live
money side effects. 11 hook tests pass; 3 new regressions failed before the fix.
Native APIs mocked; no production Android build or visual regression tooling.

## Cancellable native startup

Before: an asynchronous permission/arbiter wait could outlive the online caller.
After: `startBackgroundLocation(config, canStart)` rechecks the caller and encrypted
signed-out marker inside the existing native task arbiter, before registration.
Alternative: hook-only precheck; insufficient because native startup also awaits.
Consumers: dashboard toggle/hydration, recoverTripLocation, geofence recovery and
Android Auto handover retain their existing behavior when no callback is supplied.
Risk: canceled startups return false; dashboard must suppress errors for obsolete
calls. No new task or service, no permission or fare change. Revert app update to
roll back; ephemeral lifecycle guard only. Tests run the real start function with
native APIs mocked, including a permission wait that finishes after going offline.
Files: backgroundLocation.ts, backgroundLocation.test.ts, this log.

## Service restore and idle live transport

Startup now belongs to the online dashboard lifecycle (mount, phase and resume),
not only profile hydration. Existing native arbiter/cadence retained; revoked
background permission produces an actionable message without routine prompting.
Resume replaces the foreground watcher; obsolete callbacks/subscriptions are removed.
21 hook/hydration tests and 58 background-service tests pass with native boundaries
mocked. No Android/iOS production build performed; there is no mobile visual gate.
The obsolete hydration source assertion was replaced by actual-hook coverage.

Separate root cause: when idle history is disabled, headless fixes produce no
durable batch, and foreground WS is closed while backgrounded. New rate-limited
`POST /drivers/location-live` accepts a recent ephemeral fix only from an online,
authenticated driver, checks session revocation, derives assignment server-side,
and reuses integrity-gated marker/presence/fanout. No history insert or fare change.
Alternative: enable idle history globally; rejected because history consent/rollout
must not control live dispatch location. Capture and live delivery stay independent.
Backend changes: location.py, test_live_location.py, this log. Added endpoint is
dark behind the same delivery flag; migration 427 adds its settings column and
admin write allowlist. Rollback: set flag false, allow 60s cache expiry. No schema
migration or flag activation executed against Supabase. Additive schema tested by
inspection and admin settings drift guard, not a live migration run.

Headless sender now captures first, then independently uploads the newest trusted
recent fix to location-live while draining history. Both fetches use a 10s abort
deadline; failures retain the durable outbox and the next native fix retries live
position. Alternative: reuse legacy location-batch; rejected because it also
persists breadcrumbs and would duplicate the v2 recording contract.
Files: backgroundLocation.ts, its actual-task regression suite, this log.
Risk: one additional HTTP request per native callback; deploy backend before app.
Existing access-token policy is unchanged: an expired token defers uploads until
the foreground renews it. Long screen-lock testing across token expiry is REQUIRED;
this patch does not claim uninterrupted live delivery across that existing limit.
Rollback: disable backend flag for live delivery; revert app update for sender.
