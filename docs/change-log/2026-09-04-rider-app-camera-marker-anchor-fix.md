# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | shared, rider-app |
| Domain (Sentry tag) | rides / drivers |
| PR / commit link | branch `claude/map-nav-investigation-round10`, commits `44265d0`, `3637ac4` |
| Related issue or gap ID | Round 10 — picks up round 9's flagged, unaddressed item: "the user's separately-reported 'live app navigation — still there are issues' was not investigated or addressed... (map/course-up camera vs in-app screen navigation vs turn-by-turn directions, all three flagged as relevant)" |

## 0. Investigation — which of the three readings this round addresses

Round 9's log left three plausible readings of "navigation issues" open: (a)
map/course-up camera behavior, (b) in-app screen-to-screen navigation
(expo-router transitions), (c) turn-by-turn driving directions. No live
device and no way to get fresh clarification from the user in this
background run, so this round worked from the existing evidence instead of
guessing:

- **(a) Map/course-up camera** — this is what the entire round 1–9 series
  (`2026-08-28` through `2026-09-03` change-logs) has been about, with a new
  live-testing report and matching fix landing on almost every single day
  in that window (bearing-source fix and camera-anchor fix on 2026-08-31,
  route-line erasure on 2026-09-01, native-compass-disable on 2026-09-03).
  Reading the current driver-app camera code
  (`driver-app/app/driver/(tabs)/index.tsx`) after all of that work did not
  turn up a new, currently-broken bug — it is a mature, heavily-commented
  implementation with each prior live-testing report's root cause
  documented inline. However, re-reading the **already-shipped, already-
  validated 2026-08-31 fix** for driver-app's camera-vs-icon anchor
  mismatch (`docs/change-log/2026-08-31-driver-map-marker-camera-anchor-fix.md`)
  against rider-app's own follow-camera code turned up a concrete,
  unaddressed instance of the *same root cause*: rider-app's two
  follow-camera screens (`ride-in-progress.tsx`, `driver-arriving.tsx`)
  still anchor `animateCamera`'s center on the **raw, undelayed** driver GPS
  fix, while `CarMarker`'s own icon renders `PLAYBACK_DELAY_MS` (5s) behind
  it — exactly what driver-app's own follow camera did before its 2026-08-31
  fix. The fix for driver-app was explicitly scoped away from rider-app at
  the time ("rider-app's own CarMarker.tsx... was not touched — out of
  scope, report was driver-app-specific"), so this gap was never closed.
- **(b) In-app screen navigation** — audited `rider-app/app/_layout.tsx`'s
  notification-tap router calls and the ride-flow screens' back-navigation
  guards. Found one theoretical `push`-vs-`replace` stacking pattern
  (notification-tap navigation pushes onto an already-active ride screen),
  but every ride-flow screen (`driver-arriving`, `driver-arrived`,
  `ride-in-progress`, `ride-completed`) already sets `gestureEnabled: false`
  (iOS swipe-back) and intercepts Android hardware back with a confirm
  sheet or its own handler — so a stacked duplicate screen, if it occurs, is
  not reachable through any back-navigation UI a rider could trigger. Not
  concrete or reproducible enough to act on this round; noted as open below.
- **(c) Turn-by-turn directions** — confirmed the app does not implement its
  own turn-by-turn; `driver-app/components/dashboard/ActiveRidePanel.tsx`'s
  `openMapsNavigation()` deep-links out to the driver's chosen external app
  (Waze / Google Maps / platform default), governed by
  `driver-app/store/navStore.ts`. Checked the iOS `LSApplicationQueriesSchemes`
  config (`comgooglemaps`, `waze`) — correctly declared, so `canOpenURL`
  works as documented. No bug found in the time available; not investigated
  further this round (see "still open" below).

**Decision**: scoped this round's fix to (a) — it is the most-supported by
the series' own history, and it is the one place this investigation found a
concrete, reproducible, previously-diagnosed-and-fixed-elsewhere bug still
live in shipped code, not a hypothesis.

## 1. Issue / gap identified

On rider-app's two driver-follow map screens (`ride-in-progress.tsx` during
the trip, `driver-arriving.tsx` while the driver is en route to pickup), the
follow camera can drift away from the car icon at driving speed, the same
way driver-app's camera did before its 2026-08-31 fix (live-testing report
that day: "icon missing" at 46 km/h).

## 2. Root cause

`CarMarker` (`shared/components/CarMarker.tsx`) renders the car icon
`PLAYBACK_DELAY_MS` (5 seconds) **behind** the raw GPS fix it's given — a
deliberate smoothing technique (the playback buffer, round 3). Both rider
screens' follow-camera effects anchored `animateCamera({center})` directly
on `currentDriver.lat`/`currentDriver.lng` — the same raw, undelayed fix —
instead of on where the icon is actually rendered. At driving speed the
camera's center and the icon's position can end up tens of meters apart,
the same class of bug already found and fixed for driver-app's own follow
camera (`docs/change-log/2026-08-31-driver-map-marker-camera-anchor-fix.md`).
That fix added an `onPositionChange` callback to driver-app's own copy of
`CarMarker` and wired it into driver-app's camera — but the shared copy of
`CarMarker` (`shared/components/CarMarker.tsx`, the one rider-app imports)
never received the callback, so the equivalent rider-app fix was never
possible until now.

Unlike driver-app, rider-app's camera doesn't rotate (stays north-up,
confirmed by round 6) and doesn't apply an additional "pin the car low"
ahead-offset, so the compounding described in the 2026-08-31 doc is milder
here — but the core anchor mismatch (camera vs. icon, 5s of travel apart at
speed) is the same mechanism.

## 3. Fix / remediation

- Added `onPositionChange?: (coordinate: TrackingLatLng) => void` to
  `shared/components/CarMarker.tsx`, mirroring driver-app's existing
  implementation exactly: fired every playback tick with the route-snapped,
  delayed position the ticker just applied to the icon.
- `ride-in-progress.tsx` and `driver-arriving.tsx`: added a `markerPosRef`
  populated by this callback; the follow-camera `animateCamera` call now
  centers on `markerPosRef.current ?? { rawLat, rawLng }` — the marker's own
  reported position when available, falling back to the raw fix only before
  the marker's first tick (e.g. immediately after mount), same fallback
  shape as driver-app's fix.
- Did **not** port `onBearingChange` — rider-app's camera never rotates, so
  there is no bearing to share.

## 4. Risk & impact on existing functionality

- **Blast radius — `shared/components/CarMarker.tsx` consumers** (grepped):
  `rider-app/app/(tabs)/index.tsx` (nearby-drivers multi-marker map),
  `ride-options.tsx` (nearby-drivers multi-marker map), `driver-arrived.tsx`
  (stationary, one-time `fitToCoordinates`, no follow camera),
  `driver-arriving.tsx` and `ride-in-progress.tsx` (the two screens
  changed). The new prop is optional and additive — the three untouched
  consumers pass no `onPositionChange` and render byte-for-byte the same as
  before. `driver-app/components/CarMarker.tsx` is a separate file,
  untouched.
- **Test-observable no-op for existing coverage**: both changed screens'
  existing unit tests mock `CarMarker` to `() => null}` (never invokes the
  callback), so `markerPosRef.current` stays `null` in every existing test
  and the camera keeps anchoring on the raw fix exactly as before — verified
  by running both suites unmodified after the change (all passing, no
  assertion needed updating).
- No change to ride state, dispatch, fares, or any backend-reachable path —
  purely client-side map-camera rendering.
- No change to `CarMarker`'s `_propsAreEqual` memo comparator was needed:
  it already doesn't compare callback-shaped props (same pattern as the
  existing, uncompared `ring` object's color/pulsing fields are compared by
  value, but no prior callback prop existed on the shared copy to set a
  precedent either way — matches driver-app's own comparator, which also
  excludes `onPositionChange`/`onBearingChange`).

## 5. User-experience effect

- **Rider-facing**, visible mid-session (a rider watching the live map
  during pickup or the trip itself). The car icon should stay better
  centered under the camera at driving speed instead of drifting toward the
  edge of the visible map. No copy, flow, or money-visible change.
- Not feature-flagged: additive, visual-only, mirrors an already-shipped,
  already-un-flagged fix for the equivalent driver-app bug — same
  justification precedent as every round in this series.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/CarMarker.tsx` | Added `onPositionChange` prop, fired from the existing playback ticker | Expose the marker's actual rendered position, ported from driver-app's copy |
| `rider-app/app/ride-in-progress.tsx` | Follow-camera effect anchors on `CarMarker`'s reported position via a new `markerPosRef`/`handleMarkerPositionChange`, falling back to the raw fix | Fix camera/icon anchor drift at speed |
| `rider-app/app/driver-arriving.tsx` | Same change | Same fix, en-route-to-pickup screen |
| `rider-app/__tests__/carMarkerPositionChange.test.tsx` | New — 2 tests pinning `onPositionChange`'s tick-accurate firing and optionality | Regression coverage (mirrors driver-app's own `onPositionChange` test) |

## 7. Before / after

```tsx
// Before (both screens) — camera anchors on the raw, undelayed GPS fix
map.animateCamera({ center: { latitude: dLat, longitude: dLng } }, { duration: 800 });
```
```tsx
// After — camera anchors on the icon's own (delayed) rendered position
const center = markerPosRef.current ?? { latitude: dLat, longitude: dLng };
map.animateCamera({ center }, { duration: 800 });
```

## 8. Rollback plan

Pure client-side, additive/optional-prop change — no migration, no data
touched. `git revert` of both commits (`44265d0`, `3637ac4`) is a complete
rollback; each is a separate commit so either can be reverted independently
(the `CarMarker` prop addition is harmless on its own even without the
screen wiring). No feature flag exists or was added — same precedent as
every prior round in this series (pure visual/camera behavior, no existing
flag mechanism for marker/camera rendering).

## 9. Verification performed

- [x] `npx tsc --noEmit` (rider-app) — clean.
- [x] `npx eslint` on all four touched/added files — 0 errors; 2
  pre-existing-pattern `no-require-imports` warnings in the new test file's
  `jest.mock()` factories, matching established codebase style (same as
  driver-app's `CarMarker.test.tsx`).
- [x] New regression test (`carMarkerPositionChange.test.tsx`, 2 tests)
  passing, run in isolation.
- [x] Both changed screens' existing suites (`rideInProgressScreen.test.tsx`,
  `driverArrivingScreen.test.tsx`) — 104/104 passing, unmodified — confirms
  the fallback-to-raw-fix path exactly preserves pre-existing test-observable
  behavior.
- [x] Full rider-app suite: **140/140 suites, 1949/1949 tests passing**
  (grew from round 9's 1894 baseline via this round's 2 new tests plus
  unrelated suite growth since).
- [x] Blast-radius grep: confirmed every `CarMarker` consumer in both apps
  (rider-app's 5 screens + `(tabs)/index.tsx`; driver-app's own separate
  copy, untouched) and which ones needed the change.
- [ ] Real production build (`expo export` / EAS) — **not run this round**.
  Prior rounds ran `npx expo export --platform web` as a bundling smoke
  test; this round did not repeat it (jest + tsc + eslint only) — flagged
  explicitly per CLAUDE.md §"Verification performed", not implied.

## 10. What was NOT verified / still open

- **No on-device or emulator verification** — no device/emulator in this
  environment, consistent with every round in this series. The
  camera-anchor fix is reasoned about at the code level (same value now
  feeds the camera that feeds the icon, by construction, mirroring the
  already-validated driver-app fix) but not observed live.
- **No real production bundle build was run** this round (see §9).
- **(b) In-app screen navigation** — audited (see §0) but no concrete,
  reproducible bug found worth shipping a fix for this round. The one
  theoretical push-vs-replace stacking pattern on notification tap is
  currently unreachable via any back-navigation UI (gestures disabled,
  hardware back intercepted) on the affected screens — flagged as a
  standing, low-confidence hypothesis for a future round, not fixed here.
- **(c) Turn-by-turn directions** — audited (see §0) and found working as
  designed (deep-links to the driver's chosen external app); no bug found
  in the time available, and not investigated in depth (e.g. did not test
  the `pickup_nav_lat`/`pickup_nav_lng` vs `pickup_lat`/`pickup_lng` cast in
  `ActiveRidePanel.tsx`'s pickup button, which uses an `as any` cast that
  wasn't traced further). Still fully open for a future round.
- **This round does not claim the user's original "navigation issues"
  report is resolved.** It closes one concrete, previously-diagnosed gap
  under the map-camera reading (a). Readings (b) and (c) remain open, and
  even within (a), further live-testing reports may surface additional
  issues — this series has produced a new one on almost every prior day.
