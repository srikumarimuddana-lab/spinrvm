# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude, at user request ("Do we want to change the colors...whats your recommendation and reason" → "push the DemandLegend swatch-border contrast fix too") |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | commit following this log |
| Related issue or gap ID | Recommended earlier this session while reviewing the demand-heatmap legend live-tested in Regina; user asked for the color ramp itself to be evaluated, this is the follow-up fix |

## 1. Issue / gap identified

The `DemandLegend` pill's 5-swatch color ramp (quiet → busy) has real WCAG 1.4.11
graphical-object contrast failures against its own pill background, in **both** light
and dark theme — not just light mode as originally suspected. Measured with the
`dataviz` skill's validator (`contrast()`, WCAG relative-luminance formula):

| Swatch (light-mode step) | vs. `colors.surface` (`#FFFFFF`) | 3:1 floor |
|---|---|---|
| `#FFE3E0` | 1.21:1 | FAIL |
| `#FFB3AC` | 1.71:1 | FAIL |
| `#FF7A6E` | 2.54:1 | FAIL |
| `#FF3B30` | 3.55:1 | pass |
| `#B71C1C` | 6.57:1 | pass |

| Swatch (dark-mode step) | vs. `colors.surface` (`#1C1C1E`) | 3:1 floor |
|---|---|---|
| `#4E211E` | 1.26:1 | FAIL |
| `#7F2D26` | 1.87:1 | FAIL |
| `#B2382E` | 2.83:1 | FAIL |
| `#FF453A` | 4.99:1 | pass |
| `#FF8A80` | 7.45:1 | pass |

The two lowest-magnitude steps in both themes are effectively invisible against the
pill's surface — a driver in a quiet area sees an almost-blank pill with no visual
signal at the "Quiet" end of the legend.

## 2. Root cause

The ramp itself is not wrong — per the `dataviz` skill's color-formula method, this is
a correctly-structured single-hue sequential/ordinal ramp (light→dark, monotone), and
re-stepping it to force every swatch past 3:1 against a white/near-black surface would
require either raising the light end's lightness floor (flattening the "quiet" signal
toward invisibility from the other direction) or giving up the wide light-to-dark span
that makes the ramp readable as a magnitude gradient at all. The actual gap is that
each swatch is rendered as a small filled rectangle with **no boundary stroke**, so
where the fill nearly matches the background there's nothing else marking the shape's
edges. WCAG 1.4.11 permits exactly this case to be resolved with a visible boundary
around the graphical object, instead of forcing the fill itself past 3:1.

`colors.border` (`#E5E7EB` light / `#38383A` dark) — the theme's general-purpose
divider color — was the first color considered for that boundary, but it's
deliberately subtle: measured at only 1.24:1 (light) / 1.45:1 (dark) against
`colors.surface`, it would itself fail the same 3:1 floor and barely be visible next
to a near-white/near-black pill. `colors.textDim` clears the floor by a wide margin in
both themes (5.74:1 light / 7.69:1 dark against `colors.surface`) and is already used
elsewhere in this same component (legend labels, status text), so it's a color the
ramp's surroundings already read as "secondary UI ink," not a new color introduced
for this fix.

## 3. Fix / remediation

Added a 1px `colors.textDim` border to every swatch (both the real ramp and the
loading-state shimmer swatches, which share the same `styles.swatch` base). This
gives every swatch — including the two per-theme steps whose fill alone fails 3:1
against the pill — a boundary that clears 3:1 against the surface, so the shape
itself is always visible regardless of how close its fill color sits to the
background.

No ramp colors changed. No layout change (border replaces space the swatch already
occupied at its existing `width`/`height`; `boxSizing` isn't a concern here since RN
borders are drawn inset by default, matching how the pre-existing `borderRadius: 2`
already rendered).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one shared style object in one component.**
  `styles.swatch` is used in exactly two places, both inside `DemandLegend.tsx`
  itself: the loading-state shimmer row (line ~57) and the real ramp row (line ~89).
  Grepped `driver-app/` for any other importer of `DemandLegend` or consumer of its
  exported styles — none; the only mount point is
  `driver-app/app/driver/(tabs)/index.tsx`, unchanged by this diff.
- **No other consumer of `colors.heatmapRamp` or `colors.textDim` is affected** — this
  diff doesn't touch the theme file, only how one component borders its own swatches.
- **Loading-state shimmer swatches also get the new border.** Previously they were
  filled with `colors.border` at 0.4 row-level opacity and had no border at all; now
  they additionally get a `colors.textDim` outline (also inside the 0.4-opacity row).
  This makes the shimmer state marginally more visible as 5 distinct cells rather than
  a single blurred bar — a strict improvement consistent with the intent of the
  shimmer (signal "content is loading here"), not a regression.
- No behavior change to `status` transitions, data fetching, or the loading-shimmer
  fix landed earlier this session (`useDemandHeatmap.ts`) — this diff only touches
  the swatch's own `StyleSheet` entry.

## 5. User-experience effect

**Driver-facing, visible mid-session.** Every driver viewing the demand-legend pill
(currently live in Saskatoon and Regina, both service areas + their airport zones) now
sees all 5 ramp steps as distinct, bordered rectangles in both light and dark mode,
instead of the "quiet" end of the ramp visually disappearing into the pill background.
Purely a legibility improvement — the underlying demand data and color meaning are
unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/dashboard/DemandLegend.tsx` | Added `borderWidth: 1, borderColor: colors.textDim` to `styles.swatch` | Fix WCAG 1.4.11 contrast failures on 3 of 5 ramp swatches in both light and dark theme |
| `docs/change-log/2026-08-27-demand-legend-swatch-contrast-fix.md` | This log | UX/visual change to a live-tested driver-facing surface |

## 7. Before / after

```ts
// Before
swatch: {
  width: 16,
  height: 10,
  borderRadius: 2,
},
```

```ts
// After
swatch: {
  width: 16,
  height: 10,
  borderRadius: 2,
  borderWidth: 1,
  borderColor: colors.textDim,
},
```

## 8. Rollback plan

`git revert` is complete and sufficient — pure style-object change, no data,
migration, or server-side component.

## 9. Verification performed

- [x] Measured every ramp swatch's actual WCAG contrast ratio against `colors.surface`
      in both themes using the `dataviz` skill's `validate_palette.js` `contrast()`
      export (values quoted in section 1), rather than reasoning about it visually.
- [x] Measured the candidate border colors (`colors.border` vs `colors.textDim`)
      against `colors.surface` the same way before choosing `colors.textDim` —
      confirmed `colors.border` would not have actually fixed the failure.
- [x] `npx tsc --noEmit -p .` (driver-app) — clean, no errors.
- [x] `npx eslint components/dashboard/DemandLegend.tsx` — zero findings (file was
      already clean before this change; still clean after).
- [ ] **Not run against a real device/simulator, no screenshot taken.** No automated
      visual-regression tooling exists for driver-app (CLAUDE.md standing note), and
      no test file exists for `DemandLegend.tsx` at all (pre-existing gap, not
      introduced here) — verification is contrast-math + typecheck + lint, not a
      rendered comparison. Flagging per CLAUDE.md gate 6 rather than asserting "no
      visible diff" from code alone.

## What was NOT verified

- Did not capture a before/after screenshot on-device — reasoned from the measured
  contrast ratios and a direct reading of the render branches, consistent with how
  the loading-shimmer fix earlier this session was verified (same standing gap).
- Did not re-run the `dataviz` skill's full categorical-palette validator against
  this ramp — that validator is scoped to categorical (series-identity) palettes; a
  sequential ramp is expected to fail it by design (see the skill's own scope note),
  so the relevant check here is the WCAG `contrast()` helper used above, not the
  categorical six-check suite.
- Did not add a component test for `DemandLegend.tsx` — none exists for this file
  today; adding one is a larger, separate lift (mocking `useTheme`/`useLanguageStore`)
  outside the scope of this contrast-only fix.

## 10. Sign-off

- [x] Rollback plan is concrete (plain `git revert`, no data-layer component).
- [x] Blast radius is stated, not assumed — isolated to one shared style object, two
      consumers, both inside the same file, no external importer.
- [x] No silent behavior change to an already-shipped flow beyond the stated,
      intentional visual fix — no data/status-transition logic touched.
