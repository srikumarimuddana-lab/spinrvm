# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | vikas@ngitservices.com |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (added at PR open) |
| Related issue or gap ID | Found during driver-app heatmap UX investigation (2026-09-04/05 session) |

## 1. Issue / gap identified

`HeatmapCells.tsx` accepts a `region` prop specifically to filter cells down to the visible map viewport, but its only call site (`index.tsx`) passed `region={null}` unconditionally — a dead code path. Every heatmap cell the server returned was always rendered (up to `MAX_POLYGONS`/`MAX_BLOBS`), regardless of what the driver actually had on screen.

## 2. Root cause

The `region` prop and its filtering logic in `HeatmapCells.tsx` were built, but the call site was never wired up to a live region value — likely because the only region tracking that existed (`currentRegionRef` in `useDriverDashboard.ts`) only carries `latitudeDelta`/`longitudeDelta` (for `MapControls`' zoom-in/out math), not the map's center (`latitude`/`longitude`), so it couldn't satisfy `HeatmapCells`' filter, which needs the full region.

## 3. Fix / remediation

Added a dedicated `heatmapRegion` state in `index.tsx`, populated via the `MapView`'s `onRegionChangeComplete` callback (fires once per pan/zoom gesture, not continuously like `onRegionChange` — deliberately not reusing that already-wired handler, since it only tracks deltas for a different consumer and would need a much higher update cadence than this needs). Passed `heatmapRegion` into `HeatmapCells`' `region` prop in place of the hardcoded `null`. `HeatmapCells`' own filter logic and rendering (native `<Heatmap>` on Android, concentric `<Circle>` pairs on iOS) are unchanged — this only activates the filter that already existed.

## 4. Risk & impact on existing functionality

- Blast radius: `HeatmapCells` has one call site (`index.tsx`); grepped for other consumers — none found. The new `heatmapRegion` state and `onRegionChangeComplete` handler are new, used only by this call site.
- `heatmapCells` (the data being filtered) is only non-empty while `rideState === 'idle' && isOnline` (`useDemandHeatmap`'s `shouldPoll` gate), matching the heatmap UI's own idle-only render gate — so this change has no effect outside that state.
- Before the map's first `onRegionChangeComplete` fires (immediately after mount, before any pan/zoom), `heatmapRegion` is `null`, and `HeatmapCells`' filter treats a `null` region as "show everything" — identical to the prior always-`null` behavior. So the worst case (cold start) is unchanged; the fix only improves behavior after the first camera settle.
- Performance impact is a reduction, not an increase: filtering to viewport means fewer native `Circle`/`Heatmap` points cross the bridge on a zoomed-in view, not more.

## 5. User-experience effect

Driver-facing, idle-online state only. Now the heatmap only renders cells actually within (or very near) the visible map area instead of the full unfiltered set, which should read as a *tighter, more relevant* heat picture when zoomed in — closer to how Uber's own demand-heat view scopes to the current viewport. No new UI element, no copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | Added `heatmapRegion` state; added `onRegionChangeComplete` handler on the `MapView`; changed `HeatmapCells`' `region` prop from hardcoded `null` to `heatmapRegion` | Activate the existing (previously dead) viewport-filter path in `HeatmapCells` |

`HeatmapCells.tsx` itself was **not** modified — its `region` filter logic already existed and needed no change, only a real value at the call site.

## 7. Before / after

```tsx
// Before
<HeatmapCells
  cells={heatmapCells}
  region={null}
  cellLatDeg={heatmapCellLat}
  cellLngDeg={heatmapCellLng}
/>
```

```tsx
// After
<HeatmapCells
  cells={heatmapCells}
  region={heatmapRegion}
  cellLatDeg={heatmapCellLat}
  cellLngDeg={heatmapCellLng}
/>
```
(`heatmapRegion` populated via a new `onRegionChangeComplete={(region) => setHeatmapRegion(region)}` on the `MapView`.)

## 8. Rollback plan

No feature flag — this only activates a pre-existing, already-shipped filter path with a safe (`null` = unfiltered) fallback state. Rollback is a plain `git revert`; no live data or ride-state path touched.

## 9. Verification performed

- [x] `npx tsc --noEmit -p tsconfig.json` for the full driver-app project — clean, 0 errors.
- [x] `npx jest __tests__/app/driverDashboardScreen.test.tsx` — 49/49 passed (no test currently exercises heatmap rendering directly, but this confirms no regression to the rest of the screen's mount/render path).
- [ ] No dedicated unit test exists for `HeatmapCells`' filter logic (it's `useMemo`'d inside a React component with no exported pure function), and none was added — the filter logic itself is untouched by this change (only its call-site wiring changed), so this is a pre-existing test gap, not a new one. Flagged rather than silently left unmentioned.
- [x] Blast-radius grep performed: confirmed `HeatmapCells`' single call site and no other region/onRegionChangeComplete conflicts.
- [x] Reviewed against relevant `CLAUDE.md` conventions: surgical/additive, no PIPEDA concern (region is the driver's own current viewport, never logged, never sent anywhere — purely local render-filtering of already-aggregated, already-k-anonymized server cells).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; `null` fallback state is the pre-existing behavior).
- [x] Blast radius is stated, not assumed.
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 completed).

## What was NOT verified

**Density/opacity tuning was deliberately NOT changed in this commit.** The broader ask included tuning the Android native `<Heatmap>` radius/opacity and iOS circle radii "closer to the Uber reference." I did not make that change: driver-app has no visual-regression tooling and no device/simulator was available in this session to actually compare the rendered result against the Uber reference screenshot, and the specific live-testing reports ("weak localized heat blob" on Android, "no visible heat" on iOS) have an unconfirmed root cause — it could be a rendering-parameter issue, or it could be genuinely sparse ride data in the area photographed, or the heatmap toggle being collapsed/off at capture time. Guessing at new radius/opacity constants with no way to confirm the result would be an unverifiable cosmetic change, which `CLAUDE.md`'s verification-disclosure rule requires flagging rather than shipping quietly. Recommend either (a) a build with visual capture from a real device to iterate against the Uber reference directly, or (b) confirming via Sentry/analytics whether the "no visible heat" iOS report correlates with empty-cell responses rather than a rendering parameter, before spending effort tuning constants that may not be the actual cause.
