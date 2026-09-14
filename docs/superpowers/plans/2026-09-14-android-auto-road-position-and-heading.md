# Android Auto Road Position and Heading Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkboxes for tracking. Work sequentially; do not delegate unless separately authorized.

**Goal:** Correct the Android Auto vehicle marker's road position and travel direction during pickup navigation, active trips, and idle driving.

**Architecture:** Preserve measurement time through the existing location channel. Produce one coherent display position/bearing for the vehicle and camera, and constrain route snapping using movement evidence and geometry valid for the playback time. Reuse existing route services and the Google Maps renderer.

**Tech stack inspected:** Expo 57, expo-location ~57.0.16, React Native 0.86.3, react-native-maps 1.27.2, @iternio/react-native-auto-play 0.5.13, TypeScript, Jest. Source manifests override older SDK numbers in repository prose.

**Spec:** The user's September 14 request and subsequent clarification that this also happens during rides, including two photos displaying “Trip in progress.” This document includes the proposed implementation design. Current-source inspection reference: `00662d574`; installed APK/OTA revision is unverified.

**Status:** Planning only. No application code changed. Two current-code diagnostic reproductions were executed; no native head-unit reproduction or full test suite has been run for this plan.

## Evidence and diagnosis

The supplied photos demonstrate an active-trip display problem. Photo 2 visibly separates the vehicle from the orange route. Photo 1 places the vehicle near the route's starting end. A still photo does not establish the measured geographic heading, which route response was displayed, or whether the installed build contains the latest merged fixes. The user's report of northbound travel appearing southbound remains an explicit acceptance case.

| Finding | Evidence in current source | Confidence / consequence |
|---|---|---|
| Route snapping is already wired | `driver-app/lib/androidAuto/carSurface.tsx:670` passes `routeCoordinates`; merged in `eb83fecb5` | Confirmed. Repeating that change will not fix the remaining defects. Verify whether it reached the installed bundle. |
| Camera and marker follow different timelines | `carSurface.tsx:276–352` follows `here` and its heading; `CarMarker.tsx:656` samples delayed playback. `shared/utils/markerPlayback.ts` sets a 5,000 ms delay. Android Auto omits the marker position/bearing callbacks used by the phone dashboard. | Confirmed wiring mismatch. Causes relative movement and heading disagreement around turns; does not by itself prove geographical off-road error. |
| Route direction overrides actual movement | `shared/utils/vehicleTracking.ts:265` returns `snap.bearing` whenever movement clears the threshold. | Reproduced: northbound movement bearing 0°, south-directed route selected bearing 180°. Continuity indices do not establish actual travel direction. |
| Measurement timestamps are discarded | `CarLatLng` lacks timestamp/accuracy/speed; foreground and background producers strip these fields. `adoptCarFix` stamps arrival time and accepts every fix. | Reproduced: a previous position arriving after a newer northbound position is adopted and yields 180°. This is a code failure mode; occurrence in the photographed ride needs runtime evidence. |
| Active-trip background delivery can replay older positions | `backgroundLocation.ts:277–311` records and then publishes each sample in a delivered batch. The separate car task only selects the array's last element. | Confirmed producer behavior. The central channel needs chronological arbitration across all producers. |
| Fresh route may exclude delayed marker position | `useCarLiveRoute.ts` polls every 6 seconds for a route from the current driver position; the marker renders roughly 5 seconds behind and snaps within 35 m. | Mechanically plausible and needs a replay regression: at 50 km/h, five seconds is about 69 m, before smoothing lag. A newly shortened route may no longer cover the displayed location. |
| Accuracy is lost before smoothing | Both marker copies rebuild `{ ...rawCoord, timestampMs: ts }` before `smoothFix`, dropping `MarkerFix.accuracyM` despite its documentation. | Confirmed plumbing gap. Use actual accuracy; do not promise that smoothing alone performs road matching. |

Diagnostic inputs used synthetic coordinates, not rider GPS records:

```ts
// Current behavior reproduced by transpiling and calling the actual TS modules.
const from = { latitude: 50, longitude: -104 };
const to = { latitude: 50.0001, longitude: -104 };
const southRoute = [{ latitude: 50.001, longitude: -104 }, from];
// snapToRoute(to, southRoute) + selectBearing(...movedMeters: 11...)
// Actual: { bearing: 180, source: 'route' }; measured movement: 0 degrees.

// adoptCarFix(newer north point), then adoptCarFix(older south point)
// Actual: position rewinds and heading becomes 180. Even an extra runtime
// timestampMs field is ignored by the present implementation.
```

## Global constraints and design decisions

- Each implementation commit changes at most three files and represents one logical change. Split at testable boundaries if it exceeds roughly 200 changed lines. Commit before starting the next subtask. Use available task tracking; this plan's checkboxes substitute if TodoWrite is unavailable.
- Preserve raw trip recording, uploads, fare settlement, ride transitions, SOS payload semantics, and existing location integrity gates. Display rejection must never delete a billing/audit breadcrumb.
- Use clockwise world bearings: north 0°, east 90°, south 180°, west 270°. A flat Android marker uses world rotation; do not subtract the camera bearing again or apply an unconditional 180° icon rotation. [Google marker documentation](https://developers.google.com/maps/documentation/android-sdk/marker).
- Location `timestamp` is measurement time in milliseconds, not callback arrival time. Preserve it separately from arrival time. [Expo Location documentation](https://docs.expo.dev/versions/latest/sdk/location/).
- Start with the existing 35 m snap ceiling and playback delay. Increasing the snap radius can pull a vehicle onto an adjacent road. Reducing delay alone cannot repair reversed heading or out-of-order data. Tune latency separately using measured replay/device results.
- Route geometry describes the planned path, not proof of the road actually driven. A clear detour must show actual travel, even if that means the marker is temporarily off the orange line.
- Keep changed matching behavior opt-in for Android Auto until verified. Proposed bundle switch: `EXPO_PUBLIC_ANDROID_AUTO_TRACKING_V2`, default false, evaluated once per session. It is a proposed build/OTA switch, not an existing remote setting or instantaneous kill switch. Apply existing Spinr feature-flag/release gates before production enablement.
- Review both marker forks and `docs/known-forks.md`. Shared geometry consumers include both marker copies, shared `RouteLine`, and the driver's deviation detection. Preserve default utility behavior for non-opted-in consumers.
- Add no raw coordinates, addresses, rider names, or photo copies to logs, Sentry, or committed test fixtures. Debug only ages, source enums, deviation distance, heading delta, route generation, and rejection reasons. Record source attribution accurately.
- Alternative considered: replace the display with a navigation SDK or introduce full road-network map matching. The existing pipeline has reproducible correctness faults; fixing these first has smaller integration cost and preserves current service usage. Arbitrary-road matching remains a separate conditional extension described below.

## Implementation sequence

### Task 1 — Preserve GPS metadata without changing acceptance behavior

**Files (3):** `driver-app/lib/androidAuto/carFixChannel.ts`; `driver-app/lib/androidAuto/useCarLocation.ts`; create `driver-app/lib/androidAuto/__tests__/useCarLocation.test.tsx`.

**Interface:** Extend `CarLatLng` additively with `timestampMs?: number`, `accuracyM?: number | null`, and `speedMps?: number | null`. Existing heading and coordinate readers remain compatible. Keep measurement age separate from diagnostic arrival counters.

- [ ] Write a hook test with real carFixChannel and a fake Expo provider. Deliver a fix captured two seconds before callback time. Assert the accepted object retains that exact timestamp and accuracy. Mock only native location and persistence boundaries.
- [ ] Run `npm test -- --runInBand lib/androidAuto/__tests__/useCarLocation.test.tsx`; observe missing metadata before the fix.
- [ ] Thread metadata through startup, watch, watchdog, and cached-seed paths using this mapping:

```ts
const fix = {
  latitude: p.coords.latitude, longitude: p.coords.longitude,
  heading: p.coords.heading ?? null, timestampMs: p.timestamp,
  accuracyM: p.coords.accuracy, speedMps: p.coords.speed,
};
```

- [ ] Use cache capture time when present; never label an undated cache entry fresh. Cover a seed racing a live update. Verify all Android Auto tests and commit `fix(android-auto): retain foreground GPS measurement metadata`.

### Task 2 — Preserve metadata from headless producers

**Files (3):** `driver-app/lib/androidAuto/carLocationTask.ts`; `driver-app/utils/backgroundLocation.ts`; create `driver-app/lib/androidAuto/__tests__/carProducerMetadata.test.ts`.

**Consumes:** Task 1's optional metadata. **Produces:** Timestamped display publications from both background producers.

- [ ] Test an unsorted three-sample car-task batch and a trip batch whose recorder awaits while a newer foreground sample arrives. Assert native timestamps survive and all trip samples are still recorded.
- [ ] Observe the failures using `npm test -- --runInBand lib/androidAuto/__tests__/carProducerMetadata.test.ts`.
- [ ] Forward the same metadata mapping as Task 1. In the car-only task select the newest valid sample by timestamp, not array position. Preserve existing integrity checks. In the trip recorder retain every raw sample and its ordering; centralized acceptance in Task 3 arbitrates display updates.
- [ ] Run producer and background-location tests; commit `fix(android-auto): timestamp headless display fixes`.

### Task 3 — Reject stale/out-of-order display fixes before deriving course

**Files (3):** `driver-app/lib/androidAuto/carFixChannel.ts`; `driver-app/lib/androidAuto/__tests__/carFixChannel.test.ts`; `driver-app/lib/androidAuto/useCarLocation.ts`.

**Interface:** Add an internal acceptance decision carrying an accepted flag/reason and current snapshot. Keep legacy public readers compatible; rejected updates must not be published or persisted. The foreground hook must render the accepted snapshot, not its local raw candidate.

- [ ] Add the exact regression below, plus duplicate timestamp, invalid latitude/longitude, clock skew, startup seed, and mixed-producer cases:

```ts
jest.setSystemTime(1_800_000_020_000);
const newer = { latitude: 50.0001, longitude: -104, heading: 0,
  timestampMs: 1_800_000_020_000 };
adoptCarFix(newer);
adoptCarFix({ latitude: 50, longitude: -104, heading: 0,
  timestampMs: 1_800_000_018_000 });
expect(getLastCarFix()).toMatchObject(newer);
```

- [ ] Run the channel tests and observe the rewind failure.
- [ ] Before mutation, reject non-finite/out-of-range coordinates, duplicate/older timestamps, timestamps over 5 seconds in the future, and display samples older than the existing 5-second freshness budget. These are proposed initial policy bounds to validate on hardware. Reject rather than restamping an invalid measurement as now. Undated legacy entries may seed but cannot displace timestamped live fixes or establish course.
- [ ] Derive movement using elapsed measurement time and the existing plausibility rules. Do not reset the accepted-fix age/heading baseline for a rejected fix. Count arrivals separately from accepted updates. Persist the accepted capture time as `at`.
- [ ] Bound the hook watchdog to one outstanding request; test cleanup and late completion. Validate north/south/east/west, no GPS bearing, stationary jitter, and real U-turns. Commit `fix(android-auto): prevent late GPS fixes from reversing travel`.

### Task 4 — Make route snapping direction-aware, opt-in

**Files (3):** `shared/utils/vehicleTracking.ts`; `rider-app/__tests__/vehicleTracking.test.ts`; create `shared/utils/__tests__/directionalRouteSnap.test.ts`.

**Interface:** Add optional matching options after existing arguments, e.g. `{ travelBearing?: number | null; maxHeadingErrorDeg?: number }`; omitted options preserve current callers. Extend bearing selection to accept a reliable playback/movement reference when opted in.

- [ ] Reproduce northbound travel over a south-directed route, crossing streets, parallel opposing lanes, duplicate vertices, and slow playback ticks below the existing 3 m chord threshold.
- [ ] A concrete expected contract is:

```ts
const p = { latitude: 50.0001, longitude: -104 };
const south = [{ latitude: 50.001, longitude: -104 },
               { latitude: 50, longitude: -104 }];
expect(snapToRoute(p, south, 35, null,
  { travelBearing: 0, maxHeadingErrorDeg: 60 })).toBeNull();
```

- [ ] Run targeted geometry tests before implementation.
- [ ] Filter zero-length segments and candidates over the heading-error limit before distance/continuity ranking. Apply the same heading constraint in unrestricted fallback. Use wrapped angular difference. A 60° limit is an initial conservative test parameter, not a measured production optimum. When travel is reliably opposite the route, render observed travel until rerouting supplies compatible geometry.
- [ ] Route bearing may refine an agreeing movement reference; it must not override contradictory movement or invent orientation at rest. Treat lateral correction onto a road separately from longitudinal movement. A valid moving playback tangent remains usable below 3 m per tick. Preserve the last reliable heading while stationary, and allow a real U-turn once supported by fresh movement.
- [ ] Run existing vehicleTracking and RouteLine tests plus new cases; commit `fix(tracking): gate route alignment by observed travel direction`.

### Task 5 — Retain geometry covering delayed playback across route refreshes

**Files (3):** create `driver-app/lib/androidAuto/carPlaybackRoute.ts`; create `driver-app/lib/androidAuto/__tests__/carPlaybackRoute.test.ts`; `driver-app/lib/androidAuto/useCarLiveRoute.ts`.

**Interfaces:** Define `CarPlaybackRouteSnapshot = { rideId: string; leg: 'pickup' | 'dropoff'; receivedAtMs: number; polyline: LatLng[] }` and a pure `selectPlaybackRoute(snapshots, rideId, leg, position, travelBearing, nowMs)` returning a compatible snapshot or null. Expose route receipt time from useCarLiveRoute's existing shared snapshot; retain state locally in the consuming surface.

- [ ] Replay constant-speed movement at 50 km/h, 2-second fixes, 5-second playback and 6-second route refresh. The new remaining route begins approximately 69 m ahead of playback. Test that an earlier same-leg route still covering the marker is selected instead of forcing the marker onto the new start vertex.
- [ ] Retain at most four snapshots for at most the existing 18-second route usability window. Prefer the newest direction-compatible route covering the display point within 35 m. Older geometry is eligible only for a previously traversed portion established by accepted fixes; it must not restore an obsolete future detour.
- [ ] Never concatenate unrelated routes with a straight connector. On uncertain continuity return null; use honest unsnapped position. Clear history on ride/leg change, completion, logout, or surface-session reset. Test new ride, pickup-to-dropoff, reroute, stale response, and empty geometry.
- [ ] Validate the helper with fake time and literal synthetic polylines; commit `fix(android-auto): retain route coverage for marker playback`.

### Task 6 — Correct marker ingest and matching on both forks

**Task 6A files (3):** `driver-app/components/CarMarker.tsx`; `driver-app/__tests__/components/CarMarker.test.tsx`; `shared/components/__tests__/CarMarkerParity.test.ts`.

**Interface:** Add optional `trackingV2?: boolean` and `fixAccuracyM?: number | null`. New mode consumes Task 4's matching policy. Keep default behavior unchanged. During the staged driver-only commit explicitly declare the temporary parity exception and remove it in Task 6B before merging.

- [ ] Use real smoothing, playback and geometry in new regressions. Existing tests which replace playback with fabricated results prove wiring only. Test accuracy influence, first fix snapping, same-coordinate updates with a newer timestamp, reversed route geometry, and cold start heading.
- [ ] Reject non-monotonic measurements before mutating smoothing state. Forward `accuracyM` into `smoothFix`. In v2, use reliable movement/playback reference for both candidate selection and bearing selection; avoid double-smoothing already processed input.
- [ ] Snap the first display position when justified, rather than briefly seeding raw off-road coordinates. Do not choose a route bearing from lateral snap displacement. Keep the heading unknown/held until evidence establishes travel.
- [ ] Replay a right-angle road with sparse fixes: each accepted snap target must be on its segment. If a 500 ms native animation crosses a bend, split the motion at the intervening route vertex with distance-proportional durations; do not interpolate directly across the corner. Cancel old segments when retargeting or unmounting. Test intermediate motion targets as well as endpoints.
- [ ] Run marker tests; split ingest and corner-animation commits if needed to respect the 200-line limit. Commit `fix(driver): apply coherent position and bearing playback`.

**Task 6B files (3):** `shared/components/CarMarker.tsx`; `rider-app/__tests__/carMarkerPositionChange.test.tsx`; `shared/components/__tests__/CarMarkerParity.test.ts`.

- [ ] Port the common ingest fixes and opt-in props, remove the temporary parity exception, and repeat real-geometry regressions for the shared marker. Preserve rider north-up defaults. Run marker, parity, rider arriving/arrived/in-progress, home, and ride-options tests. Commit `fix(shared): preserve marker tracking parity`.

### Task 7 — Wire a single Android Auto display timeline

**Files (3):** `driver-app/lib/androidAuto/carSurface.tsx`; create `driver-app/lib/androidAuto/useCarTrackingCamera.ts`; create `driver-app/lib/androidAuto/__tests__/carSurfaceTracking.test.tsx`.

**Interfaces:** The new hook consumes marker `onPositionChange` and `onBearingChange`, view readiness, pan/zoom state, and initial GPS seed. It returns stable callbacks and schedules coherent camera updates. V2 enablement is evaluated from the proposed bundle switch once per session.

- [ ] Mount the real surface and marker with a fake native Maps boundary and real geometry. Inject timestamped northbound fixes then a turn. Assert the camera's settled center/bearing match the marker display sample, including when raw GPS is already beyond the turn.
- [ ] Pass capture timestamp, accuracy, v2 mode, and Task 5's matching geometry to CarMarker. Replace the hand-written narrow lazy-import prop shape with a type-only reference to the real marker component.
- [ ] Use marker callbacks as the continuing camera source. Initialize the first heading immediately from available evidence; the current `prevRawHeading` initialization can skip a stable first bearing. Keep zoom/pan controls, hold camera rotation while panned, and restore the latest display bearing immediately on recenter.
- [ ] Coalesce callbacks into a single update carrying position and heading from the same playback tick. Keep at most one scheduled trailing camera update and ensure animation duration does not outlast its scheduling interval. Cancel on unmount/generation change. Verify on a native device because callback targets precede native animation completion.
- [ ] Feed the accepted display position into `RouteLine.vehiclePosition` for traveled-line trimming. Use the same route selection for marker and visible near-vehicle line; do not show an unrelated stale planned line as if it still describes the driver-to-destination path. Historical geometry covers only the played-back traveled portion.
- [ ] Test an initially known north heading, 359°→1°, parked heading retention, pan/recenter without another GPS fix, reconnect, phone backgrounding, route refresh, and leg changes. Commit `fix(android-auto): synchronize camera and marker tracking`.

### Task 8 — Verify live-route availability and response ordering

**Files (2):** `driver-app/lib/androidAuto/useCarLiveRoute.ts`; create `driver-app/lib/androidAuto/__tests__/useCarLiveRoute.test.tsx`.

- [ ] Test delayed responses through ride/leg changes, same-leg overlap, a failed request, wrong-leg responses, and a phone publisher that remains registered while results age out.
- [ ] Prevent overlapping requests and ignore superseded responses. Preserve ride/leg identity from the response and existing snapshot freshness guards. If stale registered publishers are reproduced, make takeover depend on a lease/heartbeat or fresh results, with mutual exclusion; do not add an independent uncoordinated poller. A lease change touching shared publishers requires a separately decomposed follow-up before implementation.
- [ ] Surface fetch failures through the existing error reporting path, excluding PII. An expired live route must not silently become a confident wrong-road snap. Preserve truthful fallback behavior and the 6-second cadence; do not increase requests to hide stale local data.
- [ ] Run the hook/shared-channel tests; commit `fix(android-auto): guard live route freshness and ordering` only for reproduced changes.

### Task 9 — Device validation and release record

**Files (2):** create `docs/change-log/2026-09-14-android-auto-tracking-v2.md`; create `docs/testing/android-auto-tracking-replay.md`.

- [ ] Fill the repository Change Impact Log with actual changed files, affected consumers, before/after behavior, release switch, completed tests, native build status, and remaining limits. Do not copy photo PII into these documents.
- [ ] Run focused suites, then the relevant driver/rider integration suites, TypeScript, and lint. Check test alias mappings: driver Jest currently redirects many shared imports into `__mocks__`; override those in the new integration suite so actual smoothing/playback/matching runs.

```powershell
# From driver-app; commands use installed dependencies.
npm test -- --runInBand lib/androidAuto __tests__/components/CarMarker.test.tsx
npx --no-install tsc --noEmit
npm run lint
# From rider-app, including the shared test root configured there.
npm test -- --runInBand vehicleTracking directionalRouteSnap CarMarkerParity carMarkerPositionChange RouteLine
npx --no-install tsc --noEmit
```

- [ ] Produce a release-like Android preview build using the existing `preview` profile and matching Expo environment, then validate a store-delivered Android Auto build using the existing `android-auto` profile when distribution is authorized. A Metro test, web export, or TypeScript pass is not native visual verification. Do not execute external build/submission/publication merely because this plan lists it.
- [ ] Compare installed version, runtimeVersion and OTA update ID with the tested revision. Verify first on DHU with injected routes, then with a passenger/tester observing a real head unit. Tests must include a car-only launch and a phone with its screen off.
- [ ] Review the actual diff with the appropriate Spinr reviewer before commit/merge as required by CLAUDE.md. Stage v2 on a dedicated preview update branch/environment; confirm the preview channel is not shared with an unrelated test cohort before publication. Production defaults remain off until device acceptance.
- [ ] Rollback: publish the prior compatible update or a bundle with v2 disabled to the affected channel, subject to update compatibility and restart behavior. This is not instantaneous; record how installed devices receive it. No DB or trip-record rollback is involved.

## Acceptance matrix

| Scenario | Required result |
|---|---|
| North, east, south, west; known straight road | Correct world bearing; proposed device target ≤10° error after settling with good GPS. Course-up icon aligns with travel. |
| Northbound movement with a south-directed stale route | No forced 180° rotation. Incompatible snapping is rejected and travel remains northbound. |
| Duplicate/late background batch | Accepted display capture time never decreases; position and heading do not rewind; every raw trip sample still records. |
| Fresh route begins ahead of delayed marker | Existing traversed same-leg geometry continues to cover playback; marker is not dragged to the new first vertex. |
| 90° turn with sparse fixes | Snapped targets remain on route; intermediate animation does not visibly cut through the block. |
| Parallel roads, junction, genuine detour/U-turn | No snap to an opposite-direction or incompatible street; actual detours and U-turns remain possible. |
| Stopped at a light | No random rotation caused by positional scatter or placeholder heading; no fabricated forward travel after the bounded prediction window. |
| Pan, recenter, reverse-camera interruption | Controls work; camera recovers the current display sample without a long spin or stale-seed rewind. |
| Pickup→trip→completed; next ride | No geometry or bearing policy from the previous ride leaks into the next leg; stationary orientation may be held, not invented. |
| Network loss or poor GPS | Honest unsnapped/held display with diagnosable freshness; no assertion that a guessed road is known. |
| Idle without a route | Correct chronological position and travel bearing. No claim of guaranteed road snapping where no road geometry exists. |

Proposed synthetic geometry target: accepted snapped positions within 1 m of the selected polyline. This measures algorithm correctness, not satellite or basemap accuracy. Measure actual capture-to-display lag, GPS accuracy, and route-response age during device tests; five-second playback remains a known latency until separately tuned.

## Conditional extension: road matching without usable route geometry

These fixes address reproducible errors and active-route coverage. They do not provide universal road matching while idle or taking a detour. The present renderer cannot obtain a road network from map pixels. If good, chronological GPS still needs correction without route geometry, design a separate authenticated backend road-matching service using the existing OSRM infrastructure: bounded recent-point windows, capture timestamps, accuracy and direction constraints, confidence threshold, rate limit, and explicit raw-position fallback. Verify deployed OSRM matching capability, service capacity and map-provider licensing before choosing that implementation. Do not call public OSRM services or introduce a paid Roads API dependency implicitly. This extension requires its own backend/API/client subtasks and release review; it is not silently included in the client fix estimate.

## Completion boundary

The fix is complete only after deterministic regressions pass and both pickup and in-progress journeys pass on an Android Auto device using the identified build. Photos prove the symptom, and the two executed diagnostics prove failure modes in current source; neither establishes which combination happened in the reported ride. Preserve that distinction in the final implementation report.
