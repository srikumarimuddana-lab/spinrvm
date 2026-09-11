# Change Impact & Risk Log — HM-32: Skia raster-gradient heatmap for iOS

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | Claude Code (agent session) |
| Surface(s) | driver-app (phone screen, iOS only — Android untouched) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/vehicle-icon-movement-animation-8tys8o` (5 commits: shared-logic extraction, projection math, Skia component, lazy-require safety, screen wiring) |
| Related issue or gap ID | `ACTION_ITEMS.md` HM-32 — user shared a reference screenshot (Uber driver-app demand heatmap: soft blurred gradient) after the same-session incremental iOS fix (`docs/change-log/2026-09-09-heatmap-soft-gradient-style-ios.md`) and explicitly chose to also build this fuller fix ("do both") |

## 1. Issue / gap identified

Android's demand heatmap uses `react-native-maps`' native `<Heatmap>` layer — a true GPU-blended gradient. iOS (deliberately Apple Maps, not Google Maps — `app.config.ts` explains why switching would be a much bigger change than this feature warrants) has no native equivalent, so it fell back to layered translucent circles (`HeatmapCells.tsx`, this session's own earlier fix). That fallback is a real improvement over the original 2-circle version but is still fundamentally an opacity-layering trick, not a real blur — it doesn't match the soft, continuous look in the user's reference screenshot.

## 2. Root cause

Not a bug — an architectural gap. Apple MapKit (via `react-native-maps`) has no image-overlay hook for a raster layer, and no built-in heat-density primitive. Getting a *true* blurred gradient on iOS requires computing the blur client-side and rendering it as a screen-space overlay.

## 3. Fix / remediation

Five commits, in dependency order:

1. **Extract shared logic** (`41e7c3f`): moved `rampColorForRatio`/`hexToRgb`/`rgbaString` to `utils/heatmapColor.ts` and the cell-filtering (finite-coordinate guard, viewport bounds, driver-exclusion zone, sort+cap) to a new `useVisibleHeatmapCells` hook, out of `HeatmapCells.tsx`. Pure refactor — the existing `HeatmapCells.test.tsx` needed zero changes and still passes unchanged.
2. **Projection math** (`3e75cb1`): `utils/heatmapProjection.ts` — a pure, fully unit-tested (including its own inverse, for a round-trip check) linear projection between a map region's lat/lng bounds and canvas pixel coordinates.
3. **Skia component** (`c8415b8`): added `@shopify/react-native-skia` (2.11.2 — verified peer-compatible with this app's existing `react-native-reanimated ^4.5.5`, `react-native-worklets ^0.11.4`, React 19.2.3, RN 0.86.3; no Expo config plugin needed). New `HeatmapGradientOverlay` component renders each visible cell as a fully-opaque screen-space `Circle`, all inside one Skia `Group` whose `layer` applies a real image-space Gaussian blur (`Blur`, an image filter) to the Group's *rasterized composite* — overlapping cells from different demand tiers genuinely merge into one continuous blurred field, unlike the opacity-layering fallback.
4. **Lazy-require safety** (`34f5661`): switched from a static top-level import to the same lazy `require()`-in-try/catch guard `carSurface.tsx` already uses for its own risky imports. A static import would throw at module-evaluation time — before React renders anything — on a JS bundle that reaches a native binary built before this dependency existed (exactly the OTA/native-build mismatch class this session already found real instances of). No React error boundary can catch an import-time crash; this degrades to "no gradient overlay" instead.
5. **Screen wiring** (this commit): `HeatmapCells`'s render site in `index.tsx` is now Android-only (it renders a `<Heatmap>`/`<Circle>` map annotation, which must be a `MapView` child). `HeatmapGradientOverlay` renders as a **sibling** of `MapView` (Skia draws to its own canvas view, not a map annotation), positioned absolutely over the same bounds. Two new pieces of state feed it: `mapViewport` (measured via `onLayout` on the wrapping View, which shares `MapView`'s own `StyleSheet.absoluteFill` sizing) and `liveHeatmapRegion` (fed by the *already-existing* `onRegionChange` continuous handler, iOS-only).

**Drag behavior, per explicit user choice**: "best-effort follow" via `onRegionChange` — a JS-bridge event, not a UI-thread-synced value. Confirmed via research ([react-native-maps#5086](https://github.com/react-native-maps/react-native-maps/issues/5086), [#4762](https://github.com/react-native-maps/react-native-maps/issues/4762)) that true frame-synced tracking isn't achievable through `react-native-maps`' public iOS API without a custom native module bridging MapKit's camera to a Reanimated shared value — a materially larger, separate project, not attempted here. The overlay will visibly lag/stutter on fast pans; it snaps to the correct position once the gesture settles (`onRegionChangeComplete` still drives `heatmapRegion`, used as the fallback when `liveHeatmapRegion` is still null on first render).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to driver-app's phone-screen map, iOS only.** Android is untouched — `HeatmapCells` still renders exactly as before (same component, same props, same internal `USE_NATIVE_GRADIENT` branch), just gated by an explicit `Platform.OS === 'android'` at the call site instead of `!== 'web'`.
- Grepped every consumer of the extracted shared modules and the new component: `HeatmapCells.tsx` (updated to use them), `HeatmapGradientOverlay.tsx` (new), their test files. `lib/androidAuto/carSurface.tsx` (Android Auto surface) does not render either `HeatmapCells` or the new overlay — confirmed via grep, unaffected.
- **New native dependency** (`@shopify/react-native-skia`): this is the single biggest risk-shape change in this feature. Mitigated three ways: (a) the lazy-require guard means a stale native binary degrades gracefully instead of crashing; (b) peer-dependency compatibility was verified against the actually-installed package versions, not assumed; (c) `npx tsc --noEmit` was checked against the real installed `.d.ts` files for every Skia API call (`Canvas`/`Group`/`Circle`/`Paint`/`Blur` props), not written from memory or an unverified example.
- **A native rebuild is required before this ships to any real device** — this is explicitly NOT an OTA-eligible change (new native module). Existing JS-only OTA updates to a build that predates this commit continue working normally; they simply won't have this feature until a new native build reaches the device.
- `onRegionChange`'s existing behavior (`currentRegionRef` update, used by `MapControls`' zoom math) is unchanged — the new `setLiveHeatmapRegion` call is purely additive within that same handler.

## 5. User-experience effect

- **Driver-facing (iOS only, idle state only)**: the demand heatmap should now render as Skia's real Gaussian-blurred gradient instead of layered opacity circles — the intended "Uber-style" soft look. During an active drag, the gradient will visibly lag/stutter (an accepted, explicit tradeoff — see §3) rather than track the map perfectly; it corrects once the gesture ends.
- Android: no change. Not idle state: no change (gated exactly as before).
- If Skia ever fails to load on a given device (see §4), the driver simply sees no heatmap gradient — no crash, no broken screen.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/heatmapColor.ts` | New — `rampColorForRatio`/`hexToRgb`/`rgbaString`, extracted from `HeatmapCells.tsx` | Shared color math between both iOS renderers |
| `driver-app/hooks/useVisibleHeatmapCells.ts` | New — cell-filtering hook, extracted from `HeatmapCells.tsx` | Shared cell-selection logic between both iOS renderers |
| `driver-app/components/dashboard/HeatmapCells.tsx` | Refactored to use the two shared modules above (behavior-preserving) | DRY — the new overlay needs identical filtering/color logic |
| `driver-app/__tests__/components/heatmapCellGeometry.test.ts` | Source-contract checks updated to point at whichever file now owns each pinned pattern | The finite-guard/sort-copy patterns moved files |
| `driver-app/utils/heatmapProjection.ts` | New — geo↔screen projection (+ inverse) | The Skia overlay needs to place cells in canvas pixel space |
| `driver-app/components/dashboard/HeatmapGradientOverlay.tsx` | New — the Skia Canvas/Group/Blur/Circle component | HM-32's actual raster-gradient renderer |
| `driver-app/package.json`, `driver-app/yarn.lock` | Added `@shopify/react-native-skia@2.11.2` | New dependency for the above |
| `driver-app/components/dashboard/index.ts` | Export `HeatmapGradientOverlay` | Barrel consistency with sibling dashboard components |
| `driver-app/app/driver/(tabs)/index.tsx` | `HeatmapCells` render gated `Platform.OS === 'android'`; new `HeatmapGradientOverlay` sibling render for iOS; new `mapViewport`/`liveHeatmapRegion` state; `onRegionChange` and a new `onLayout` feed them | Wires the new renderer into the live screen |
| `driver-app/__tests__/app/driverDashboardScreen.test.tsx` | Split the "renders when cells present" test by platform; added the mock for the new component; "idle-only gating" test now also checks the new component | New render path needs coverage |
| Multiple new test files (`heatmapColor.test.ts`, `useVisibleHeatmapCells.test.ts`, `heatmapProjection.test.ts`, `HeatmapGradientOverlay.test.tsx`, `HeatmapGradientOverlayMissingNativeModule.test.tsx`) | New | Coverage for every new pure-logic module and the component itself, including its failure mode |

## 7. Before / after

```tsx
// Before — index.tsx, iOS and Android both got HeatmapCells
{rideState === 'idle' && heatmapCells.length > 0 && Platform.OS !== 'web' && (
  <HeatmapCells cells={heatmapCells} region={heatmapRegion}
    cellLatDeg={heatmapCellLat} cellLngDeg={heatmapCellLng}
    driverLocation={location?.coords ?? null} />
)}
```

```tsx
// After — Android keeps HeatmapCells; iOS gets the Skia overlay, as a
// MapView SIBLING (not a child — Skia has no map-annotation slot)
{rideState === 'idle' && heatmapCells.length > 0 && Platform.OS === 'android' && (
  <HeatmapCells cells={heatmapCells} region={heatmapRegion} ... />
)}
</MapView>
{Platform.OS === 'ios' && rideState === 'idle' && heatmapCells.length > 0 && (
  <HeatmapGradientOverlay cells={heatmapCells} region={liveHeatmapRegion ?? heatmapRegion}
    cellLatDeg={heatmapCellLat} cellLngDeg={heatmapCellLng}
    driverLocation={location?.coords ?? null} viewport={mapViewport} />
)}
```

## 8. Rollback plan

**Not a simple `git revert` for the dependency itself** — `@shopify/react-native-skia` is a native module; removing it from `package.json`/`yarn.lock` requires a fresh native build to actually take effect on a device (same asymmetry as adding it). The JS-level wiring (which renderer shows on which platform) IS a clean `git revert` — reverting the screen-wiring commit alone restores `HeatmapCells` on iOS with zero native rebuild needed, since `HeatmapCells.tsx` itself was never deleted, only stopped being called from this one site on iOS. If the Skia native module itself turns out to be a problem (crashes, unacceptable performance) even after a native build ships it, the fix is: revert the screen-wiring commit (restores the iOS fallback instantly via the next OTA push, no new native build needed for THAT part) and separately decide whether to remove the dependency entirely (needs a native build either way).

No feature flag: this is additive to a single platform's fallback rendering path, and given the change requires a native build regardless, a JS-level flag would only control something that's already gated by "has this build shipped or not."

## 9. Verification performed

- [x] `yarn jest` (full driver-app suite): 137 suites / 1537 tests passing after all 5 commits
- [x] `npx tsc --noEmit`: clean after every commit, including the actual installed Skia package's real `.d.ts` files (not written from an unverified example)
- [x] Peer-dependency compatibility checked against the real installed versions (`npm view`), not assumed
- [x] Blast-radius grep performed at each step (documented per-commit)
- [x] Graceful-degradation behavior (missing native module) proven by a dedicated test that mocks the module's `require()` to throw
- [x] Researched (not assumed) the actual limits of `react-native-maps`' iOS camera-sync API before committing to the "best-effort follow" drag behavior, rather than attempting true live-tracking and discovering the limitation after building it
- [ ] **`npm run build` / EAS native build NOT run** — this environment cannot trigger EAS builds (no credentials). This is the single most important unverified item: **nothing in this feature has been seen rendering on an actual device or simulator.**
- [ ] Not visually verified against the reference screenshot — driver-app has no visual-regression tooling

## What was NOT verified

- **Nothing in this feature has run on a real device, simulator, or EAS build.** Every check performed (type-checking against real installed types, unit tests against mocked Skia components, peer-dependency resolution) is as much verification as is possible without device/build access — but none of it confirms the Skia rendering actually looks correct, performs acceptably, or that the native module links successfully in a real iOS build. **Before this ships to any real driver, an EAS native build must be produced and the heatmap visually checked on a real device or simulator.**
- The blur radius (30px) and per-cell circle radius (45px, matching Android's native `<Heatmap radius={45}>`) are judgment calls, not tuned against the reference screenshot or real device output.
- The "best-effort follow" drag behavior's actual felt lag/stutter is unverified — it's a reasoned prediction based on `onRegionChange`'s documented bridge-throttled nature, not a measured result.
- Whether `@shopify/react-native-skia`'s bundle-size/cold-start cost is acceptable for this app was not measured (no device to profile on).
- The EAS native build pipeline itself (`eas-native-build.yml`) has not been run with this dependency present — the first real build attempt is the first real confirmation the native module actually links successfully in this project's specific native configuration.
