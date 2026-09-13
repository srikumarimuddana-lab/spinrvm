# Android route restoration: PRs #5332 and #5333

| Field | Value |
|---|---|
| Date / author | 2026-09-13 / Codex |
| Domain | driver-app, rider-app, shared maps |
| PRs | #5332 (native fix), #5333 (alternative JS mitigation) |
| Risk / blast radius | Medium / both Android apps |

## Issue and root cause

The PRs describe Android map child-index crashes and routes disappearing during
rides. #5332 backports the 1.29.0 feature-list changes into Expo's pinned maps
1.27.2. #5333 instead removes the dashboard's ride-state Fragment key, reducing
child churn without repairing native detach/restore ownership. These are
alternative approaches; do not merge their overlapping dashboard/test changes
without reconciling them.

Executing #5332's Java methods in a JVM harness exposed three remaining cases:

1. Detach, attach (restore pending), detach again replaces `savedFeatures` with
   the now-empty `features` list, losing children and pending React insertions.
2. A callback queued for an earlier attachment can initialize/restore a map
   while detached or after a newer attachment has taken ownership.
3. Detaching before map readiness saves no Google state. Saved children then
   never restore because the callback was conditional on `savedMapState` alone.

These are reproducible native-method defects. Their contribution to the reported
device symptoms is not yet confirmed by a rebuilt Android app. The cold-start
test seeds native children directly; normal JS initially gates them on readiness.

## Remediation and files

| File | Change and purpose |
|---|---|
| `driver-app/patches/react-native-maps+1.27.2.patch` | Preserve pending children; invalidate obsolete restore callbacks; restore without saved Google state. |
| `rider-app/patches/react-native-maps+1.27.2.patch` | Identical native correction for the separately installed rider dependency. |
| `scripts/test_maps_lifecycle.py` | Compile and execute actual lifecycle/accessor Java methods against stubbed platform boundaries. |
| `driver-app/app/driver/(tabs)/index.tsx` | Keep the existing key; replace overstated guidance with the verified behavior and device gate. |
| This document | Record analysis, verification, risk, and release requirements. |

Before: `savedFeatures = new ArrayList<>(features)` on every detach; any queued
restore could run; restoration required `savedMapState != null`.

After: save only when `savedFeatures == null`; capture an attachment generation
and reject obsolete/destroyed callbacks; restore when saved map state **or** saved
children exist. Existing errors are not caught or softened by this correction.

## Impact and user experience

The native patch affects every Android MapView, not just RouteLine. Consumers
checked: driver dashboard, driver ride-detail, Android Auto carSurface; rider
home, pick-on-map, confirm-pickup, ride-options, driver-arriving, driver-arrived,
ride-in-progress, ride-details, and ride-completed; shared AppMap, RouteLine,
RoutePins, and CarMarker. Marker/overlay restoration and transitions between
idle heatmaps and ride routes need device coverage alongside polylines.

Expected effect after installing a new binary: routes survive interrupted map
restoration. No API, backend ride state, payment, location capture, notification,
or iOS behavior changes. Installed binaries receive no fix through OTA alone.
The phone and car overlay keys remain unchanged.

## Verification

- The expanded harness failed on #5332 in all three cases above before the fix.
- Fresh npm 1.27.2 copies: both patches apply with `patch-package --error-on-warn`.
- Both patched copies pass six lifecycle cases: normal/detached insertion,
  empty restore, interrupted restore, stale callback ordering, cold detach,
  and callback after destruction. The two patch files are identical.
- 88 focused JS tests pass: dashboard, shared RouteLine, driver ride-detail,
  rider ride-details, and rider ride-completed. Both apps pass `tsc --noEmit`.
- Independent code review found no actionable defect in the new restore guards.
- No native Android build or device/head-unit test was performed. The harness
  stubs Android/Google rendering and `onMapReady`; it does not cover the initial
  constructor callback or prove native collection/rendering behavior.

After installing app dependencies, run from the repository root with Python and
a JDK on PATH (repeat with `rider-app`):

```text
python scripts/test_maps_lifecycle.py driver-app/node_modules/react-native-maps/android/src/main/java/com/rnmaps/maps/MapView.java
```

## Release and rollback

Before merge/release, build both Android apps from this PR's final commit using
`eas-native-build.yml` with `platform=android`, an internal profile, and
`auto_submit=false`. Install the resulting binaries and verify pickup, trip
start, dropoff, completion, and idle; rapidly background/foreground and disconnect/
reconnect Android Auto during restoration. Check for crashes, missing/stale routes,
markers, and heatmaps. Retain logcat around failures, without publishing PII.

A future removal of either overlay key requires a separate device comparison.
Passing this patch's device checks alone does not validate that follow-up change.

Rollback requires an Android binary built from the previous known-good native
patch and reinstallation/distribution. There is no remote flag for this native
code; OTA and a backend rollback cannot undo it. No data cleanup is required.
