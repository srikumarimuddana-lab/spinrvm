# Change Impact & Risk Log — Android Auto map heading + live route

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Nighil (via Claude Code) |
| Surface(s) | driver-app (Android Auto surface; one shared-channel change on the phone dashboard) |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/android-auto-screens-2katir` — `93f13af`, `29aae47`, `abd991a`, `d8436eb`, `64b5da0` |
| Related issue or gap ID | Live testing: "the map is in fixed layout and car keep on rotating up and down"; "the polyline mapped at the start of the ride is not updating if i change the route" |

## 1. Issue / gap identified

Two reports from a real head unit:

1. **Map never turned.** Google Maps rotates the map so travel direction is up
   and holds the car steady; the Spinr car surface did the opposite — north-up
   map, spinning car marker. A previous fix (`f8983e2`) had already added
   course-up rotation, and it still looked fixed on hardware.
2. **Route line was frozen at the booking-time route.** A driver who deviated
   watched the head unit insist on the original route for the whole trip, while
   the phone beside them showed the real one.

## 2. Root cause

**Report 1 — two independent causes, which is why the earlier fix appeared to do nothing.**

- *The heading data was being destroyed upstream.* Only `watchPositionAsync`
  produces a course over ground; a one-shot `getCurrentPositionAsync` reports
  `0` or `-1`. The car has three one-shot paths (startup fix, plus a staleness
  watchdog every 3s) where the phone dashboard has none — and on Android Auto
  those dominate, because the phone sits backgrounded in a cradle and Android
  throttles its foreground watcher hard. So a good bearing from the occasional
  watcher callback was overwritten seconds later by a watchdog fix carrying
  none. `f8983e2` fed `here.heading` straight to the camera, so `mapHeading`
  collapsed to `0` and the map snapped back to north every few seconds. **This
  is also the direct answer to the earlier "direction changes on the phone but
  not in the car" report** — the phone has no watchdog, so its bearing survived.
- *The camera prop snaps.* react-native-maps compiles the controlled `camera`
  prop to `map.moveCamera`, which does not interpolate. Position jumped every
  ~2s and the bearing jumped with it, so a turn arrived as a lurch.

**Report 2 — the car was never wired to live routing.** `/rides/{id}/live-route`
(self-hosted OSRM) already exists and the phone has polled it every 20s for some
time, but it kept the result in **component state** in `app/driver/(tabs)/index.tsx`.
The car cannot read that, and on a car-only cold launch that component never
mounts at all. So `carRoute.selectCarRoute` only ever exposed
`planned_route_polyline` — write-once at ride creation (confirmed: no backend
route in `backend/routes/` or `services/` ever updates it; only `booking.py`
at creation and the two import-backfill scripts).

## 3. Fix / remediation

- `carFixChannel.carryHeading` carries the last known bearing onto a fix that has
  none, and `adoptCarFix` returns the merged value so callers render what the
  cache holds. Position is never carried — only the bearing.
- New pure `carCameraMath.ts`: heading normalization (rejects expo's `-1`),
  shortest signed turn, a 4° jitter threshold, and a viewport-aware
  span→Google-zoom conversion.
- `carSurface.tsx` moves off the controlled `camera`/`region` props onto
  `initialRegion` + imperative `animateCamera` interpolated over 700ms.
- `CarMarker` now receives the camera's committed bearing. Marker rotation is
  map-relative for a `flat` marker, so matching the two pins the car pointing up
  while the world turns underneath — the Google Maps behaviour that was asked for.
- New `hooks/liveRouteShared.ts` channel; the phone publishes its OSRM result
  into it, and the car subscribes — polling for itself only when no publisher is
  active. The surface prefers the live line and falls back to the planned one.

## 4. Risk & impact on existing functionality

**Blast radius: the Android Auto surface, plus one additive change to the phone dashboard's existing live-route effect.**

Greps performed: `adoptCarFix|publishCarFix|seedCarFix` (callers: `useCarLocation`,
`carLocationTask`, `utils/backgroundLocation`, two test files);
`live-route|liveRoute`; `planned_route_polyline` across `backend/` and `driver-app/`;
`deltaToZoom`.

| Area | Effect |
|---|---|
| `carFixChannel` consumers | `carLocationTask.ts` and `utils/backgroundLocation.ts` both publish via `publishCarFix`, which now merges and notifies with the merged fix. Their own call sites are unchanged. |
| Phone marker rotation | **Unchanged.** The phone reads its own `useDriverDashboard` pipeline, not `carFixChannel`. |
| Phone route rendering | **Unchanged.** `setRouteCoords` still drives the phone map exactly as before; the publish call is purely additive alongside it. |
| Ride state machine / dispatch / money | Untouched. No transition, no WS event, no fare path. |
| Backend | No change. The live-route endpoint already existed and is already polled at the same 20s cadence. |

Specific risks, stated rather than implied:

- **Request volume is deliberately not increased.** The publisher refcount means
  the car defers while the phone polls; it takes over only when the phone screen
  is unmounted. Worst case is the same one poll per 20s per active ride that
  exists today. The refcount is checked *per tick*, not once, so a driver opening
  the dashboard mid-trip silences the car's poller.
- **A stale or wrong route line is the real hazard here**, so `isLiveRouteUsable`
  rejects on ride-id mismatch, leg mismatch, fewer than two points, and age
  (> 3 poll intervals). On any of those the surface falls back to the planned
  line rather than drawing something misleading.
- **Behaviour change: a route line now appears on the PRE-PICKUP leg**, where
  previously there was none (the stored geometry runs pickup→dropoff, so drawing
  it while heading to the rider pointed the wrong way and was suppressed). This
  is new UX, not just a fix — see §5.
- **Heading rotation is new UX on a live surface.** It degrades to today's
  north-up whenever no course has been observed, which is also the fallback when
  the sensor never supplies one.
- **`carryHeading` holds a bearing across a stop.** A driver parked facing west
  keeps a west-facing map until the next real course arrives. That is correct
  (they *are* facing west) but it does mean the map does not return to north when
  stationary.

## 5. User-experience effect

- **Who sees it:** drivers with Android Auto connected. Riders, corporate admins
  and internal admins see nothing. The phone driver map is visually unchanged.
- **Mid-session visibility:** yes — an online driver sees all of this on their
  next trip after updating. Intended.
- **Changes a driver will notice:**
  - The map turns with them and the car icon stays pointing up (previously the
    reverse).
  - Camera movement glides instead of jumping every ~2s.
  - The route line follows the road they are actually on.
  - A route line now appears while driving to the pickup, where there was none.
  - Rotation freezes while the map is panned away, and resumes on Recenter —
    holding the last bearing rather than snapping to north, which is what the
    previous implementation did.
- **No copy changes.** Debug-panel strings changed (`mapHeading`, `route`,
  `zoomDelta`), but that overlay is diagnostic and not driver-facing chrome.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/lib/androidAuto/carCameraMath.ts` | **New.** Heading + zoom math | Pure, testable without a head unit |
| `driver-app/lib/androidAuto/carFixChannel.ts` | `carryHeading`; `adoptCarFix` returns merged; `publishCarFix` notifies with merged | Course-less fixes were wiping the bearing |
| `driver-app/lib/androidAuto/useCarLocation.ts` | All three `adoptCarFix` sites use the return value | Otherwise state and cache disagree on bearing |
| `driver-app/lib/androidAuto/carSurface.tsx` | `initialRegion` + `animateCamera`; committed heading; marker takes camera bearing; live-route preference; debug facts; removed `deltaToZoom` | The rotation and route fixes |
| `driver-app/hooks/liveRouteShared.ts` | **New.** Shared live-route channel | Phone held the route in unreadable component state |
| `driver-app/app/driver/(tabs)/index.tsx` | Publishes into the channel; claims publisher | Makes the phone the single poller |
| `driver-app/lib/androidAuto/useCarLiveRoute.ts` | **New.** Car-side subscriber + fallback poller | Car-only cold launch has no publisher |
| `driver-app/lib/androidAuto/__tests__/carCameraMath.test.ts` | **New**, 21 cases | — |
| `driver-app/hooks/__tests__/liveRouteShared.test.ts` | **New**, 13 cases | — |
| `driver-app/lib/androidAuto/__tests__/carFixChannel.test.ts` | +8 cases | — |

## 7. Before / after

```
# Before — camera
region={{ latitude, longitude, latitudeDelta: delta, longitudeDelta: delta }}
# then f8983e2:
camera={{ center, zoom: deltaToZoom(delta), heading: mapHeading, pitch: 0 }}
const mapHeading = here?.heading >= 0 ? here.heading : 0;   // watchdog -> 0 -> north
# Both move the camera without interpolating. Marker rotated; map did not.
```

```
# After — camera
initialRegion={{ … }}                       // initial framing only
map.animateCamera({ center, heading: cameraHeading ?? 0, zoom }, { duration: 700 })
// cameraHeading is committed via shouldCommitHeading (4 deg threshold),
// fed by a bearing carryHeading no longer lets a course-less fix destroy.
// CarMarker gets cameraHeading -> flat marker rotation cancels -> car points up.
```

```
# Before — route line
polyline: toDropoff ? extractPolyline(activeRide) : []
# planned_route_polyline, written once at booking. Never updated. None pre-pickup.
```

```
# After — route line
{route && RouteLine && (livePath ?? route.polyline).length > 1 && (
  <RouteLine path={livePath ?? route.polyline} />
)}
# livePath = OSRM route from the driver's live position, refreshed every 20s,
# guarded on ride id + leg + age; planned line remains the fallback.
```

## 8. Rollback plan

- **Nothing is persisted and no live data is touched.** All of this is render and
  fetch behaviour — no DB write, no wallet delta, no Stripe call, no ride-state
  transition. A code revert is a complete rollback, which is the narrow case
  `CLAUDE.md` accepts one for.
- **Revert without a store release:** driver-app is Expo, so `eas update` on the
  previous bundle rolls every connected head unit back on next launch.
- **Independently revertible, in this order:** `64b5da0` (car live route),
  `d8436eb` (shared channel + phone publisher), `abd991a` (map rotation),
  `29aae47` (heading carry-over), `93f13af` (camera math). Reverting only
  `64b5da0` + `d8436eb` restores the booking-time route line while keeping the
  rotation fixes; reverting `abd991a` alone returns to `f8983e2`'s camera prop.
- **Flag deferred, same reason as the previous entry:** the car surface reads
  `useDriverStore`, not `app_settings`, so a flag would mean adding a remote-config
  path this surface does not have — more new code than the change itself. Recorded
  as a judgement call.

## 9. Verification performed

- [x] **Type-check:** `npx tsc --noEmit` clean (the two pre-existing
      `shared/config/firebaseConfig.ts` errors for missing `firebase/*` types are
      unrelated and present on `main`).
- [x] **Lint:** `npx eslint` on all changed files — 0 errors. One pre-existing
      unused-import warning (`HeatmapCell`, `carSurface.tsx:27`) on an untouched line.
      Two lint findings raised during this work were fixed properly rather than
      suppressed: `react-hooks/set-state-in-effect` (heading now adjusted during
      render via React's documented pattern, matching `CarMarker.tsx`) and the same
      rule in the shared channel (now `useSyncExternalStore`, which also closes a
      real tear window the `demandHeatmapShared` pattern has).
- [x] **Logic verified by execution:** 70 assertions run against the compiled
      modules — 35 camera math, 16 fix-channel (including 4 pre-existing
      behaviours re-checked for regression), 19 live-route guard. All pass. See
      §11 for how these were run.
- [x] **Framing regression checked numerically:** the new viewport-aware
      `zoomForSpan` maps `carMapCamera`'s own constants to sensible Google zooms —
      `MIN_DELTA` 0.004 → z16.35 (street), `DEFAULT_DELTA` 0.02 → z14.02
      (neighbourhood), `MAX_DELTA` 0.4 → z9.70 (city) — matching the intent
      documented in that file, and within 0.11 of the old `deltaToZoom` at the
      default span.
- [x] **Blast-radius grep performed** — see §4 for the searches and results.
- [x] **Reviewed against `CLAUDE.md`:** ride state machine (untouched), money
      (untouched), PIPEDA (no new PII; no coordinates logged — the debug panel
      shows a truncated position, which is pre-existing behaviour), background-loop
      safety (the car's poller is refcount-gated so replicas of the same fetch do
      not stack).
- [ ] **Feature-flagged** — no; justified in §8.
- [ ] **Manual staging repro / hardware** — not performed; see §11.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius stated, not assumed
- [x] No silent behaviour change — §5 lists every driver-visible difference,
      including the new pre-pickup route line, which is an addition rather than a fix

## 11. What was NOT verified

- **The jest suite still cannot run in this container.**
  `node_modules/firebase/package.json` is missing from the install, so
  `jest.setup.js:67` fails module resolution and every driver-app suite dies
  before any test body executes (confirmed against an untouched test file). The
  **42 new/changed test cases across three files are committed but unexecuted by
  jest** and must be run in CI or on a clean install before they count as
  coverage. The 70 assertions in §9 were run by compiling the modules standalone
  with `tsc` and calling the real exported functions in node — that validates the
  logic, not the jest wiring.
- **No production build was run** (`npx expo export` / EAS). Only `tsc --noEmit`
  and eslint.
- **Nothing was rendered on hardware.** No head unit, no Android Auto DHU, no
  emulator. Everything below is reasoned from source and unconfirmed in the car:
  - That `animateCamera` interpolates bearing smoothly on the projected
    VirtualDisplay surface specifically.
  - That the flat-marker cancellation actually pins the car pointing up. This
    rests on Google Maps' documented behaviour that a `flat` marker's rotation is
    map-relative; it has not been seen.
  - That 4° is the right jitter threshold and 700ms the right glide. Both are
    judgement calls tuned by reasoning, not observation, and are single constants
    to adjust once someone drives it.
  - That the framing is unchanged in practice after the `region` → camera move.
    The numbers agree closely (above), but "close in zoom units" is not the same
    as "looks identical on the dash".
- **The live route has not been exercised against a real OSRM response.** The
  shape is taken from the endpoint's own return and the phone's existing consumer;
  no fixture from the live service was used, and the 20s cadence under real
  driving (how often the line visibly redraws) is unobserved.
- **The publisher hand-off is untested in a real dual-surface session.** That the
  car goes quiet when the driver opens the phone dashboard mid-trip, and resumes
  when they leave it, is asserted by the refcount unit tests but has not been
  watched happen with both surfaces live.
- **No visual/snapshot regression tooling exists for this surface** — a standing
  gap (`CLAUDE.md` release gate #6), and this change is largely visual.
