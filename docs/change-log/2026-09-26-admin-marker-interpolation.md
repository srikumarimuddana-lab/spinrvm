# Change Impact & Risk Log — admin monitoring map: smooth driver marker movement

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code (agent session) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | Branch `claude/spinr-animations-admin-ux-x7nl5x`: feat(admin): add marker interpolation util for live maps; feat(admin): glide monitoring map driver markers between updates |
| Related issue or gap ID | UX enhancement program W4.1 (`.claude/plans/2026-09-25-ux-enhancement-program.md`) |

## 1. Issue / gap identified

Driver markers on the admin Live Monitoring map (`/dashboard/monitoring`) teleport from one position to the next on every location update, which makes vehicles hard to track by eye.

## 2. Root cause

`updateDriverMarker` in `monitoring-map.tsx` called `marker.setLngLat(newPosition)` directly on every update. Nothing interpolated between the previous and new position. Driver apps report location about every 4 s (`driver-app/utils/backgroundLocation.ts`, `timeInterval: 4_000`), and the page relays each report straight to the marker.

## 3. Fix / remediation

- **New pure util** `admin-dashboard/src/lib/map/marker-interpolation.ts`:
  - `interpolateMarker(from, to, elapsedMs, durationMs, opts)` lerps lat/lng and takes the shortest way round for bearing (350° → 10° turns 20°).
  - It returns the target immediately (`done: true`) in any of these cases: Reduce Motion is on, the jump is over 500 m, the previous update is more than 30 s old, a coordinate is non-finite, the move crosses the antimeridian, the duration is ≤ 0, or nothing moved.
  - Also exports `shouldSnap`, `lerpBearing`, `normalizeBearing`, `distanceMeters` and `prefersReducedMotion()`. The last one reads `matchMedia("(prefers-reduced-motion: reduce)")` live on every call.
- **Monitoring map wiring** (`monitoring-map.tsx`):
  - When an existing driver marker gets a new position, it glides there over 1 s. One shared `requestAnimationFrame` loop runs the glides and stops as soon as no marker is moving.
  - If a new update arrives mid-glide, the marker retargets from wherever it is drawn at that moment.
  - The glide is cancelled when the marker is removed, when the basemap-fallback retry rebuilds the map, and when the component unmounts.
  - A marker's **first placement is unchanged**: it is created at its exact position with no animation. A marker being shown or hidden by the same update also jumps straight to its position.
- **Bearing is supported by the util but not used on this map.** Monitoring driver markers are round letter badges with no heading. `page.tsx` drops the WS `heading` field, and `MonitoringDriver` has no bearing. Rotating the badges would be a restyle, which is out of scope.

Alternative considered: animating markers with a CSS `transition` on the marker element's `transform`. Rejected because MapLibre also writes that `transform` on every pan/zoom frame, so a CSS transition would make markers lag and swim during map pans. The rAF-plus-pure-util approach animates only the geographic position and leaves pan/zoom untouched.

A second alternative was motion.dev's `useReducedMotion()`, which `alert-feed.tsx` already uses. It was not chosen because that hook snapshots the setting once per mount (its own source has a TODO saying it does not update). Reading `matchMedia` on every update picks up a mid-session OS toggle on the next ping.

## 4. Risk & impact on existing functionality

- **Blast radius: single surface, one page.**
  - `MonitoringMap` / `MapHandles` are imported only by `src/app/dashboard/monitoring/page.tsx`.
  - `monitoring-map.render.test.tsx` renders the map.
  - `src/__tests__/dashboard/monitoring-map-demand-fill.test.ts` imports only the pure `areaFillColor`/`areaFillOpacity` helpers, which are unchanged.
  - `src/__tests__/dashboard/pages.smoke.test.tsx` **stubs the whole component out**, so it gives zero coverage of this change and is not counted as a test of it.
  - The new util has no other importer.
  - `MapHandles` (the imperative API the page calls) is unchanged in signature.
- **What else uses the same code path:** `updateDriverMarker` is called for WS `driver_location_update`, `driver_status_changed`, `drivers_snapshot`, the 5-minute (10 s when degraded) REST poll, the page's `onReady` replay and the map's own `load` replay. All of these go through the same move logic. Replays of an unchanged position are a no-op (`done` at t=0), so polls and replays do not start animations.
- **Follow mode:** the map still pans to the driver's *new* position immediately. The marker then glides into the centre over 1 s, rather than already being there.
- **Poll vs WS ordering (existing behaviour, now visible as motion):**
  - The 5-minute REST poll can re-apply a position slightly older than the latest WS ping.
  - Before, the marker teleported back and then forward on the next ping. Now it glides back briefly and then forward.
  - Same underlying data ordering as before, not introduced here. Noted in case an admin reports markers "stepping backwards".
- **Performance:**
  - While any marker is gliding, the page does one `setLngLat` (a DOM transform write) per gliding marker per frame. Before, it did one write per update.
  - With pings every ~4 s and 1 s glides, about a quarter of visible drivers are gliding at any moment.
  - No profiling was done at fleet scale (see §9).
  - Hidden tabs pause rAF, and the first frame after the tab returns jumps straight to the latest target.
- **No interaction** with the ride state machine, dispatch, money, insurance periods or the backend. This is display-only. Ride pickup/dropoff pins and route lines are untouched.
- **No PII** is logged or sent anywhere. The util and the wiring have no logging.

## 5. User-experience effect

- **Who sees it:** internal admins on `/dashboard/monitoring` only.
- **Mid-session:** yes, after deploy and a page reload. Online drivers' markers slide smoothly between pings instead of jumping.
  - Jumps over 500 m, gaps over 30 s, and admins with OS Reduce Motion on keep today's instant jump.
  - Reduce Motion is read live, so toggling it mid-session applies on the next ping.
- **No copy, colour, size or layout change.** Markers are not restyled.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/map/marker-interpolation.ts` | New pure util (lerp, shortest-path bearing, snap rules, live reduce-motion probe) | Shared by this map now and by the live-ride map and `/track` later (W4.1/W4.2) |
| `admin-dashboard/src/lib/__tests__/marker-interpolation.test.ts` | New: 25 unit tests | Covers bearing wrap 350↔10, snap threshold, stale gap, zero/negative/NaN duration, reduce motion, antimeridian, non-finite coords, no-move, `matchMedia` absent/live |
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-map.tsx` | Existing driver markers glide via one rAF loop; motion state cleaned up on remove/retry/unmount | The W4.1 behaviour |
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-map-marker-motion.render.test.tsx` | New: 9 render tests on the real component (maplibre mocked, manual rAF) | Covers exact first placement, same-position no-op, glide midpoint/landing, retarget mid-glide, >500 m snap, reduce motion, hidden marker, cancel on unmount, removed driver |
| `docs/change-log/2026-09-26-admin-marker-interpolation.md` | This entry | CLAUDE.md Change Impact Log requirement |

## 7. Before / after

```tsx
// Before — updateDriverMarker, existing-marker branch
} else {
    marker.setLngLat(lngLat);
    ...
    const wasVisible = driverVisibleRef.current.get(driver.id) ?? false;
```

```tsx
// After
} else {
    const wasVisible = driverVisibleRef.current.get(driver.id) ?? false;
    // Only glide a marker that stays on screen; one being shown or
    // hidden by this update jumps straight to its new position.
    moveDriverMarker(driver.id, marker, { lat: driver.lat, lng: driver.lng }, visible && wasVisible);
    ...
```

`moveDriverMarker` calls `marker.setLngLat(target)` directly, exactly as before, whenever `interpolateMarker(from, to, 0, …).done` is true or the marker isn't on the map. Otherwise it starts the rAF glide.

## 8. Rollback plan

- **Per viewer, no deploy:** turning on the OS/browser Reduce Motion setting restores the old instant-jump behaviour exactly (the snap path is the old `setLngLat(target)` call).
- **Whole surface:** there is no feature flag. The plan lists W4.1 as "Gate: none", and the change is display-only on one internal admin page with no data writes. Reverting means redeploying admin-dashboard on Vercel, either with the two `feat(admin)` commits reverted or by promoting the previous deployment in Vercel.
- Nothing is written to live data, so no data remediation is needed.

## 9. Verification performed

- [x] **Unit tests:** `npx vitest run src/lib/__tests__/marker-interpolation.test.ts` passed, 25/25.
- [x] **Render tests:** `npx vitest run src/app/dashboard/monitoring/` passed, including the 9 new motion tests and the existing `monitoring-map.render.test.tsx`, `monitoring-map-demand-fill.test.ts` and toolbar/jump-button tests.
- [x] **Mutation check on the render tests:**
  - Forcing the snap path always (the old behaviour) makes the glide, retarget and unmount-cancel tests fail (3).
  - Removing the reduce-motion input and the hidden-marker guard makes those two tests fail (2).
  - The code was restored afterwards.
- [x] **Full suite:** `npx vitest run` passed, 91 files / 805 tests when built. It passed again on the PR branch rebuilt from `main`, with 99 files / 841 tests.
- [x] **Typecheck:** `npx tsc --noEmit` is clean.
- [x] **ESLint on touched files:** 9 warnings / 0 errors before and 9 / 0 after, all pre-existing (`react-hooks/refs`, `set-state-in-effect`, `exhaustive-deps` cleanup-ref, at shifted line numbers). The new files have 0.
- [x] **Production build:** a real `npm run build` (Turbopack) passed: compiled successfully and generated 80/80 static pages, including `/dashboard/monitoring`.
  - This ran in a git worktree whose `node_modules` is a symlink to the main checkout's. Turbopack rejects a symlink that points outside its detected root, so for this local build only `next.config.ts` temporarily got `turbopack: { root: "/home/user/spinrvm" }`.
  - That change was restored immediately after and is not committed. Vercel's build uses a real `node_modules` and needs no such change.
- [x] **Blast-radius grep:**
  - Importers of `monitoring-map` across `src/` and `e2e/`.
  - Existing haversine/reduced-motion helpers in `src/` (none shared in `src/lib`).
  - `docs/known-forks.md` for the touched files (not listed).
- [x] **Visual-regression reasoning for the `dashboard-monitoring` baseline (`e2e/visual-regression.spec.ts`):**
  - The spec seeds one fixture driver at a fixed position via the mocked REST poll, and the WS stays failing.
  - Every call for that driver either creates the marker (exact placement, unchanged code path) or re-applies the identical position. The second case is `done` at t=0, so it is a direct `setLngLat`, with no rAF and no intermediate position.
  - The marker's element, style and size are unchanged, so the screenshot should be pixel-identical.
- [ ] Feature flag: none (plan: "Gate: none"). The reasons are in §8.

## What was NOT verified

- **No real browser run.**
  - The Playwright `visual-regression.spec.ts` / `monitoring.spec.ts` suites were not run locally. The "baseline unchanged" conclusion is reasoned (see §9), not screenshotted. CI's blocking `visual-regression-test` job is the real check.
  - The glide itself has never been seen on screen. Its behaviour is proven only in jsdom with a mocked MapLibre `Marker` and a manual rAF queue.
- **No real WebSocket feed.**
  - Not exercised against live `driver_location_update` traffic or real 4 s ping cadence.
  - Not checked for how it feels in follow mode, where the map pans to the target while the marker glides in.
- **No performance profiling** at fleet scale (hundreds of simultaneously moving markers). The per-frame DOM-write cost is reasoned about in §4, not measured.
- **Thresholds (1 s glide, 500 m snap, 30 s stale gap) are judgement calls** based on the 4 s driver ping cadence. They have not been tuned against production data.
- **The live-ride map (`src/app/dashboard/rides/live/[id]/live-map.tsx`) is not wired.** It is on this work stream's do-not-touch list (`rides/**`). Its driver marker moves with a single direct `driverMarkerRef.current.setLngLat([driverLng, driverLat])` in an effect keyed on the driver position. That is the one call site to swap for this util in a follow-up. **Update (2026-09-26):** wired in the follow-up; see `2026-09-26-live-ride-map-marker-glide.md`.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (Reduce Motion per viewer; redeploy/promote previous Vercel deployment for all)
- [x] Blast radius is stated, not assumed (one page; smoke-test stub called out as zero coverage)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5)
