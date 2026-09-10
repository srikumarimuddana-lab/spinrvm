# Change Impact & Risk Log — iOS heatmap softened toward a continuous gradient look (Option A of the Uber-style request)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | Claude Code (agent session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/vehicle-icon-movement-animation-8tys8o` |
| Related issue or gap ID | User shared a screenshot of Uber's driver-app demand heatmap (soft blurred gradient) as the target look; asked for a recommendation. User picked "do both" — ship an immediate iOS improvement now (this entry) and separately scope a full raster-gradient rewrite (Skia) as tracked follow-up work, not implemented here |

## 1. Issue / gap identified

Android's heatmap already uses `react-native-maps`' native `<Heatmap>` layer — a true GPU-blended gradient, architecturally the same technique behind Uber's screenshot. iOS has no equivalent: this app deliberately runs Apple Maps on iOS (`app.config.ts:48-54`, no Google Maps API key configured — adding one breaks the native build per that comment), and `react-native-maps`' native heat layer only works on Android or iOS-with-Google-Maps. iOS's existing fallback — 2 flat-opacity circles per grid cell, colors snapped to 1 of 5 discrete buckets — read as visibly blocky/banded rather than the soft continuous wash the user wants.

## 2. Root cause

Not a bug — a known, already-documented architectural gap (the file's own "HM-GRAD-1" comment). Two specific things made the existing iOS fallback look "blocky" rather than "blurred":
- **Discrete color buckets**: `weightToRampIndex` snapped every cell's weight into one of 5 fixed colors (`<0.2`, `<0.4`, ... `>=0.8`), so two cells with very different demand could render in identical colors, and the transition between tiers was a hard edge, not a gradient.
- **Small blob radius, few layers**: 2 circles per cell at 0.62x the cell's own size meant adjacent cells' blobs often didn't overlap at all, reading as isolated dots rather than a continuous heat field.

## 3. Fix / remediation

This is Option A from the two-path recommendation given to the user (the other, a true Skia-rendered raster gradient, is scoped separately as follow-up work, not implemented here). Three changes to `HeatmapCells.tsx`'s iOS-only render path:

1. **Continuous color interpolation** (`rampColorForRatio`) replaces the 5-bucket `weightToRampIndex` — every cell's color is now linearly interpolated between the two ramp stops bracketing its weight ratio, matching the native `<Heatmap>` gradient's own `startPoints` spacing so the two renderers never disagree about what a shade means.
2. **3 layered circles per cell** (`BLOB_LAYERS`: 1.0x radius/8% opacity, 0.62x/16%, 0.32x/30%) replace the previous flat 2-circle version, giving a softer radial falloff from center — a hand-rolled stand-in for a real Gaussian blur.
3. **Larger blob radius** (0.9x the cell size, up from 0.62x) so adjacent busy cells' blobs overlap and blend into a continuous region instead of sitting as separated dots with visible gaps.

`MAX_BLOBS` (the cap on how many cells get rendered as blobs) was tightened from 60 to 45 to offset the extra circle-per-cell (45 × 3 = 135 shapes vs. the old 60 × 2 = 120 — a modest, not dramatic, increase in native view count).

Android's rendering path (`USE_NATIVE_GRADIENT` branch) is completely untouched.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `HeatmapCells.tsx`'s iOS-only render branch.** `weightToRampIndex` and `hexToRgba` were module-internal (not exported, not used elsewhere — grepped to confirm) and are fully replaced by `rampColorForRatio`/`hexToRgb`/`rgbaString`.
- The `driverLocation` exclusion feature shipped earlier this session (stopping heatmap blobs from ringing the car icon) is preserved — `excludeRadiusM` still derives from `outerRadiusM`, which grew proportionally (from ~690m to ~1002m at the default 0.01° cell size used in tests), so the exclusion zone grew slightly too, consistent with the larger blob radius. No behavior regression: it still correctly excludes cells near the driver, just scaled to match the new (larger) blob size.
- The `driverLocation`/region-filter logic itself (which cells survive to be rendered at all) is completely unchanged — only how a surviving cell is drawn changed.
- No change to the Android native-gradient path, the `HeatmapCells` component's public props, or its two known callers (`index.tsx`, and confirmed via this session's earlier grep that `lib/androidAuto/carSurface.tsx` doesn't render this component at all).

## 5. User-experience effect

- **Driver-facing (iOS only)**: the demand heatmap should read as a softer, more continuous wash of color rather than a grid of discrete, hard-banded dots — directly responding to the design feedback and reference screenshot. Android is unaffected (already had the equivalent native gradient look).
- Not a change to what data is shown, gated by, or how cells are selected — purely how a surviving cell is painted.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/dashboard/HeatmapCells.tsx` | Replaced `weightToRampIndex`/`hexToRgba` with `rampColorForRatio`/`hexToRgb`/`rgbaString` (continuous color interpolation); replaced the flat 2-circle iOS blob with `BLOB_LAYERS` (3 layers, softer falloff); bumped `outerRadiusM`'s multiplier 0.62→0.9 for cell-to-cell blending; tightened `MAX_BLOBS` 60→45 | Close the "looks blocky, not blurred" gap per design feedback referencing Uber's driver-app heatmap |
| `driver-app/__tests__/components/HeatmapCells.test.tsx` | Updated the `surviving()` helper's Circle-count divisor (2→3) and the exclusion-radius comment's stated meters; added a new `describe` block covering layered-radius/opacity ordering, continuous color interpolation (not bucketed), and the top-tier opacity boost | New rendering behavior needs test coverage; existing tests' hardcoded "2 circles per cell" assumption needed updating regardless |

## 7. Before / after

```tsx
// Before — 2 flat circles, 5 bucketed colors, 0.62x radius
const idx = weightToRampIndex(cell.weight, maxWeight);
const color = colors.heatmapRamp[idx];
<Circle radius={outerRadiusM} fillColor={hexToRgba(color, 0.14)} .../>
<Circle radius={innerRadiusM} fillColor={hexToRgba(color, idx === 4 ? 0.5 : 0.32)} .../>
```

```tsx
// After — 3 layered circles, continuous color, 0.9x radius
const ratio = maxWeight > 0 ? cell.weight / maxWeight : 0;
const rgb = rampColorForRatio(ratio, colors.heatmapRamp);
const boost = ratio > 0.8 ? 1.4 : 1;
{BLOB_LAYERS.map((layer, i) => (
  <Circle radius={outerRadiusM * layer.radiusFactor}
    fillColor={rgbaString(rgb, Math.min(0.6, layer.opacity * boost))} .../>
))}
```

## 8. Rollback plan

No migration, no live data. Pure rendering-logic diff in one file — `git revert` is a complete rollback to the previous 2-circle/5-bucket look. No feature flag: this is a visual refinement to an existing fallback rendering path (the feature itself, `heatmapVisible`/polling, is unchanged), not a new capability, and there's no existing `app_settings` row for heatmap visual style to piggyback on.

## 9. Verification performed

- [x] `yarn jest __tests__/components/HeatmapCells.test.tsx` — 12/12 passed, including new tests proving continuous (non-bucketed) color interpolation and layered radius/opacity ordering
- [x] `yarn jest` (full driver-app suite) — 132 suites / 1500 tests passed
- [x] `npx tsc --noEmit` — clean
- [x] Blast-radius grep: `weightToRampIndex`/`hexToRgba` confirmed module-internal before removal; `HeatmapCells`'s two known callers unaffected
- [ ] **`npm run build` / EAS production build NOT run** — OTA-eligible JS-only change, no native deps touched
- [ ] Not visually verified on a real iOS device — driver-app has no visual-regression tooling and this environment has no simulator/device; the change was reasoned about and unit-tested at the color-math/geometry level (exact rgba values asserted in tests), not screenshotted against the reference image

## What was NOT verified

- **This is explicitly an incremental improvement, not a true Gaussian-blur match to the reference screenshot.** Layered flat-opacity circles are a real approximation technique but will still look more "layered circles" than a genuinely blurred raster image up close, especially at high zoom. The user was told this tradeoff directly and chose to ship it now alongside scoping the fuller fix (a Skia-based raster gradient overlay) as separate follow-up work — that follow-up has not been scoped/estimated yet in this session.
- The specific tuning values (3 layers, 0.9x radius, 8/16/30% opacity, 1.4x hot-tier boost) are a judgment call, not measured against the reference screenshot pixel-for-pixel or tested on a real device — they may need adjusting once seen live.
- No visual-regression tooling exists for driver-app — reasoned about and unit-tested, not screenshotted before/after.
