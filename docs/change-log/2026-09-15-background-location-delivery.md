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
