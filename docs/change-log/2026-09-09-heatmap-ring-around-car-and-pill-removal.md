# Change Impact & Risk Log — Heatmap blobs rendering around the driver's own car icon; demand-legend pill removed

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | Claude Code (agent session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/vehicle-icon-movement-animation-8tys8o` |
| Related issue or gap ID | User-reported, live-tested build, with screenshots: iOS "concentric circles around the car icon" (3 rings, different colors); separately, the demand-heatmap pill was reported overlapping the SOS button (user's explicit decision this session: remove the pill entirely) |

## 1. Issue / gap identified

Two related but distinct reports on the driver-app map screen:

1. **iOS**: the driver's own vehicle icon appears surrounded by multiple concentric, differently-colored rings, with no indication of what they are.
2. **All platforms**: the demand-heatmap "pill" (`DemandLegend`) sits top-right and was reported overlapping the SOS button.

## 2. Root cause

**(1) Ring-around-car.** `HeatmapCells` (`driver-app/components/dashboard/HeatmapCells.tsx`) draws the demand heatmap. On iOS (Apple Maps has no native gradient heat layer) it fakes one with **two overlapping translucent `<Circle>` overlays per grid cell** (an outer low-opacity ring + an inner, higher-opacity ring — see the file's own "HM-GRAD-1" comment). Nothing about this rendering excludes the cell the driver is currently standing in. `CarMarker` separately draws the driver's own colored presence ring (`ownMarkerRing` in `index.tsx`: green while idle, pulsing amber while `ride_offered`/`navigating_to_pickup`/`arrived_at_pickup`, blue/red — `colors.primary`, which is Spinr's brand red `#FF3B30`/`#FF453A` — while `trip_in_progress`). A driver idling in or near a busy demand cell would see: the cell's outer heat-blob ring, its inner heat-blob ring (possibly a different ramp color if an adjacent cell's blob also reaches that point — the dark-theme ramp's top tier, `#4E211E`, reads as near-black), *plus* their own presence ring — three concentric, differently-colored circles with no visual distinction between "this is ambient demand" and "this is my own vehicle's status." This matches the reported "black, red, green" concentric rings.

Separately, and independently confirmed while investigating this: the render site for `HeatmapCells` in `index.tsx` had **no `rideState === 'idle'` guard of its own** — unlike every sibling heatmap widget (`DemandLegend`, `ForecastStrip`, `HotspotChips`, the airport-zone polygons), which all explicitly check `rideState === 'idle'`. The "idle only" invariant held today only because `useDemandHeatmap`'s own effect clears `cells` to `[]` the moment `rideState` leaves `'idle'` — i.e. one hook, not the render site, was the only thing preventing heat-blobs from appearing during an active ride. The existing test suite's own `describe('demand heatmap overlay (idle only)', ...)` block never actually asserted this for `HeatmapCells` — it mocked non-empty cells during `trip_in_progress` and only checked `DemandLegend`/`HotspotChips` were absent, leaving this specific gap uncovered.

**(2) Pill overlap.** Not investigated further this entry — the user made an explicit product decision (via `AskUserQuestion`, this session) to remove the pill outright rather than reposition it, so no further root-causing of the overlap itself was needed.

## 3. Fix / remediation

- `HeatmapCells` now accepts an optional `driverLocation` prop. Any cell whose center falls within ~1.3x its own blob radius of `driverLocation` is dropped before rendering, on **both** the Android native-gradient path and the iOS soft-blob path — so a heat blob can never visually ring the driver's own car icon, regardless of theme or ride state.
- `index.tsx` now passes `driverLocation={location?.coords ?? null}` and explicitly gates the `HeatmapCells` render on `rideState === 'idle'`, matching every sibling heatmap widget — the invariant now holds by construction at the render site, not only inside the hook.
- The `DemandLegend` pill (and its now-unused `heatmapVisible`/`heatmapStatus`/`heatmapLayer`/`setHeatmapLayer` bindings) is removed from `index.tsx` entirely, per the user's explicit decision. `heatmapSurge` (drives the separate surge-multiplier chip) and the `useDemandHeatmap` hook call itself are untouched — surge data still depends on it. `ForecastStrip` and `HotspotChips` (separate widgets, not part of the pill, don't overlap SOS) are untouched. The `DemandLegend` component file and its barrel export are left in place — unused now, not deleted, since deleting a component isn't what was asked.

## 4. Risk & impact on existing functionality

- **Blast radius, `HeatmapCells.tsx`**: grepped for every consumer. Two hits beyond this screen and its own tests: `driver-app/lib/androidAuto/carSurface.tsx` (Android Auto head-unit map) only *shares the same fallback grid-size constants in a comment* — it does not import or render the `HeatmapCells` component; it computes its own `carHeatCells` independently and already has its own `rideState !== 'idle'` guard (line 314). Not affected by this change.
- **Blast radius, `DemandLegend` removal**: grepped for every usage — only `index.tsx` (removed), `components/dashboard/index.ts` (barrel export, untouched), and its own test file (updated). No other screen renders it.
- **`heatmapSurge`** (surge-multiplier chip) is a separate field off the same `useDemandHeatmap` hook and was not touched.
- New `driverLocation` prop is optional and defaults to "exclude nothing" when omitted — no behavior change for any caller that doesn't pass it (none currently exist besides this one call site).

## 5. User-experience effect

- **Driver-facing, visible only during `idle`** (the only state either piece of UI ever rendered in, before and after): the demand-heatmap pill is gone; the driver no longer sees a heat blob rendered directly on/around their own vehicle icon when parked in a busy area. Ambient heatmap circles for *other* nearby cells are unaffected.
- Not visible mid-ride — both changes only affect the `idle` map view.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/dashboard/HeatmapCells.tsx` | Added `driverLocation` prop; cells within ~1.3x blob radius of it are excluded on both render paths | Stop heat-blobs from rendering on top of the driver's own CarMarker |
| `driver-app/app/driver/(tabs)/index.tsx` | `HeatmapCells` render now gated on `rideState === 'idle'` explicitly and passes `driverLocation`; `DemandLegend` render block removed along with its now-orphaned hook bindings and import | Close the missing render-site guard; remove the pill per user decision |
| `driver-app/__tests__/components/HeatmapCells.test.tsx` | New `describe` block covering the `driverLocation` exclusion (drops a cell at the driver's position, keeps a far one, back-compat when omitted) | New behavior needs test coverage |
| `driver-app/__tests__/app/driverDashboardScreen.test.tsx` | Replaced the two `DemandLegend` visibility tests with one "never renders" test; the existing "idle-only gating" test now also asserts `heatmap-cells` is absent during an active ride, closing the gap described in §2 | Tests must match the new UI and close the coverage gap that let the missing guard ship unnoticed |

## 7. Before / after

```tsx
// Before — driver-app/app/driver/(tabs)/index.tsx
{heatmapCells.length > 0 && Platform.OS !== 'web' && (
  <HeatmapCells cells={heatmapCells} region={heatmapRegion}
    cellLatDeg={heatmapCellLat} cellLngDeg={heatmapCellLng} />
)}
...
{rideState === 'idle' && heatmapVisible && (
  <View style={{ position: 'absolute', top: insets.top + 4, right: 16, zIndex: 60 }}>
    <DemandLegend status={heatmapStatus} visible={heatmapVisible}
      isV2={heatmapIsV2} layer={heatmapLayer} onLayerChange={setHeatmapLayer} />
  </View>
)}
```

```tsx
// After
{rideState === 'idle' && heatmapCells.length > 0 && Platform.OS !== 'web' && (
  <HeatmapCells cells={heatmapCells} region={heatmapRegion}
    cellLatDeg={heatmapCellLat} cellLngDeg={heatmapCellLng}
    driverLocation={location?.coords ?? null} />
)}
// DemandLegend block removed entirely.
```

## 8. Rollback plan

No migration, no live data. Pure UI/rendering diff across 2 source files (+ 2 test files) — `git revert` is a complete rollback. No feature flag: this is a passive rendering fix (fewer overlays drawn) and a UI-element removal, not a new user-facing capability requiring staged rollout, and the repo's `app_settings` flag mechanism has no existing heatmap-visibility flag to piggyback on.

## 9. Verification performed

- [x] `yarn jest __tests__/components/HeatmapCells.test.tsx __tests__/app/driverDashboardScreen.test.tsx` — 60/60 passed (dev-server/mocked test run, not a production build)
- [x] `npx tsc --noEmit` — clean, no type errors
- [x] Blast-radius grep performed for both `HeatmapCells` and `DemandLegend` consumers (listed in §4)
- [x] Reviewed against CLAUDE.md's "surgical changes" convention: `ForecastStrip`/`HotspotChips`/`heatmapSurge` explicitly left untouched
- [ ] **`npm run build` / EAS production build NOT run** — this is a React Native/Expo app; there is no separate "production build" step distinct from the native/EAS build pipeline, and no EAS build was triggered as part of this fix (OTA-only change, no native code touched)
- [ ] Not manually tested on a real iOS device against a live demand-heatmap dataset — the failure mode (concentric rings while idling in a busy cell) requires live traffic data to reproduce visually; the fix was verified against the unit-level distance math (new `HeatmapCells.test.tsx` cases), not a screenshot comparison

## What was NOT verified

- **This is the most likely root cause based on code inspection, not a confirmed reproduction against the user's actual screenshots** — I cannot view the attached images directly; the "black, red, green" concentric-ring description was reasoned from the heatmap ramp colors (`colors.heatmapRamp`, dark-theme top tier `#4E211E` reads near-black) and the presence-ring colors (`colors.success` green, `colors.primary` red) that would be visible together only while idling in/near a busy cell. If the user's screenshots were taken during an **active ride** rather than idle, this fix's `rideState === 'idle'` gate would have prevented the heat-blobs from co-occurring with the ring in the first place under the code as it stood before this fix too (cells clear via the hook) — in that case a different cause remains open and should be re-examined against a fresh build.
- No visual-regression tooling exists for driver-app (per CLAUDE.md) — this change was reasoned about and unit-tested at the geometry/gating level, not screenshotted before/after.
- The `excludeRadiusM` multiplier (1.3x the blob's own outer radius) is a judgment call, not measured against a real device screen — it may need tuning once verified against a live build.
