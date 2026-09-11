# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Live-testing report: "the heat map we don't need the square boundary around the color hue" — reported on both the phone and the Android Auto car display |

## 1. Issue / gap identified

The driver-app's demand heatmap shows a visible square/rectangular boundary around the color gradient instead of a soft, edge-free "heat glow," on both the phone screen and the Android Auto car display.

## 2. Root cause

Both manifestations trace to the same already-built, already-disabled feature flag: `SOFT_HEAT_RENDER_ENABLED` in `driver-app/lib/heatFalloff.ts`, shipped `false` "pending native-device verification" per its own prior doc comment.

With the flag off:
- **Android phone**: the native `<Heatmap>` layer's color gradient has no alpha-0 anchor at the low-density end, so the whole rectangular grid of demand cells ends in a hard visible edge instead of fading out.
- **iOS phone**: falls back to two flat, hard-edged circles per cell (no soft falloff).
- **Android Auto**: falls back to literal square grid-cell `MapsPolygon`s (`driver-app/lib/androidAuto/carSurface.tsx`) instead of the soft circle-ring blobs.

All three renderers, and the driver-position exclusion zone that sizes itself off the same geometry, already implement the fixed behavior behind this one flag — it was simply never flipped on because nobody had a real device to confirm it looked right on all three surfaces before now.

## 3. Fix / remediation

Flipped `SOFT_HEAT_RENDER_ENABLED` from `false` to `true` in `driver-app/lib/heatFalloff.ts`. No other production code changed — this is a one-line, pre-built, pre-tested behavior switch, not a new implementation.

## 4. Risk & impact on existing functionality

- **Blast radius**: grepped for every reader of `SOFT_HEAT_RENDER_ENABLED` (8 files: the 3 renderer/hook production files, and 4 test files that assert against it explicitly or implicitly). All are heatmap-specific; the flag has no other consumer.
- **What changes visually**: only the demand heatmap's rendering shape/gradient on all three surfaces (phone Android, phone iOS, Android Auto). No change to the underlying demand data, the color ramp's hue family, or any other map layer (route line, car marker, hotspot chips).
- **Radius/exclusion-zone side effect**: the soft path uses a wider blob radius factor (1.35× vs. legacy 0.62× a grid cell's span) so neighboring cells visually integrate into a continuous wash instead of standing alone — this also widens the "don't draw a heat blob under the driver's own car icon" exclusion zone proportionally (both already computed from the same shared factor, so they can't drift apart).
- **Performance**: the soft path renders more shapes per cell (5 nested rings vs. 2 flat circles on iOS/car), with the cell cap already tuned down accordingly in `HeatmapCells.tsx` (`MAX_SOFT_BLOBS = 40` vs. `MAX_BLOBS = 60`) specifically to keep native view count in the same order — this trade-off was already built in, not something this change introduces.
- **Tests updated, not weakened**: 3 test files had assertions pinned to the flag-off numeric behavior (`heatFalloff.test.ts`'s release-gate assertion, and geometry/exclusion-radius assertions in `HeatmapCells.test.tsx` / `useVisibleHeatmapCells.test.ts`). The release-gate assertion was updated to reflect the flip (with the reasoning recorded in its own comment); the two geometry-specific test files were given an explicit `jest.mock` forcing the flag back to `false` for their own scope, mirroring the exact pattern the pre-existing `HeatmapCellsSoft.test.tsx` already uses in reverse (forcing the flag to `true`) — so both the legacy and soft render paths stay independently, explicitly tested regardless of which one ships live.

## 5. User-experience effect

**Driver-facing.** The demand heatmap should now read as a smooth, edge-free "heat glow" matching the intended reference look, on the phone (both platforms) and on an Android Auto head unit, instead of a hard-edged square/rectangular patch of color. No other driver-facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/lib/heatFalloff.ts` | `SOFT_HEAT_RENDER_ENABLED` flipped `false` → `true`; comment updated to record why | This is the single flag gating all three heatmap renderers' soft-vs-hard-edged behavior |
| `driver-app/__tests__/lib/heatFalloff.test.ts` | Updated the release-gate assertion to expect `true`, with the reasoning recorded | The old assertion pinned the pre-flip state directly |
| `driver-app/__tests__/components/HeatmapCells.test.tsx` | Added a `jest.mock` forcing `SOFT_HEAT_RENDER_ENABLED: false` for this file's scope | This file tests region-filter/exclusion *logic*, written and asserted against the legacy renderer's shape (2 circles/cell, 0.62× radius factor) — forcing it keeps those assertions valid regardless of the live flag |
| `driver-app/hooks/__tests__/useVisibleHeatmapCells.test.ts` | Same `jest.mock` override, same reasoning, for the hook-level exclusion-radius test | Same as above |

## 7. Before / after

```ts
// Before
export const SOFT_HEAT_RENDER_ENABLED = false;
```

```ts
// After
export const SOFT_HEAT_RENDER_ENABLED = true;
```

## 8. Rollback plan

`git-revert-safe` — a single build-time boolean constant, no data written anywhere, no migration, no `app_settings` row. Reverting restores the prior hard-edged rendering exactly. (The flag's own doc comment notes a remote `app_settings`-backed kill switch was considered but not needed to ship this — a genuine emergency rollback is a normal code revert + OTA, same as any other client-only change in this app.)

## 9. Verification performed

- [x] All 5 heatmap-related test suites (`HeatmapCells.test.tsx`, `HeatmapCellsSoft.test.tsx`, `heatFalloff.test.ts`, `useVisibleHeatmapCells.test.ts`, `androidAutoDistribution.test.ts`): 48/48 passing.
- [x] Full driver-app suite: `npx jest` — 141/141 suites, 1597/1597 tests passing.
- [x] `npx tsc --noEmit` on driver-app — clean, no errors.
- [x] Blast-radius grep performed: confirmed exactly 8 files reference the flag, all heatmap-specific.
- [ ] **Not verified on a real device** — this environment has no phone or Android Auto head unit. The user reported the bug from real devices and will verify the fix the same way, via this repo's OTA auto-publish pipeline (no native rebuild needed — this is a pure JS/TS constant).

**What was NOT verified:** the actual visual result on a real Android phone, real iPhone, or a real Android Auto head unit — this is exactly the native-device evidence the flag was waiting for, and confirming it landed correctly requires the reporting user's own devices, not this environment. The underlying rendering math (ring alpha stacking, native gradient anchoring, radius sizing) was already unit-tested before this change and is unmodified by it — only the flag controlling which path ships is new.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single boolean revert, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (grepped, listed in §4 — exactly 8 files, all heatmap-specific)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the only visible effect: heatmap shape, nothing else)
