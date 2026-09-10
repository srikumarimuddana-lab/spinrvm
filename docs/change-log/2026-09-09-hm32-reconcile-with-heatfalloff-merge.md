# Change Impact & Risk Log — Reconciled HM-32 branch with a parallel heatmap-falloff PR merged to main

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | Claude Code (agent session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/vehicle-icon-movement-animation-8tys8o`, merge of `origin/main` |
| Related issue or gap ID | Discovered while checking on a user-triggered EAS Android build: PR #5149 showed `mergeable_state: dirty` against `main`, because a separate session had, independently and in parallel, landed its own more rigorous fix for the same underlying "iOS heatmap looks blocky" problem (commits `3ce93a1`..`256ef3d`, `lib/heatFalloff.ts` + `SOFT_HEAT_RENDER_ENABLED`) |

## 1. Issue / gap identified

Two sessions independently built competing solutions to the same problem (a soft, Uber-style iOS demand-heatmap gradient) on divergent branches. Both touched `HeatmapCells.tsx`'s same lines. Per the user's explicit choice ("adopt theirs, rebase mine on top"), this entry reconciles the two rather than picking one side of the git conflict mechanically.

## 2. Root cause

Not a bug — parallel, uncoordinated work on the same file. The other session's `lib/heatFalloff.ts` is the more rigorous of the two: a properly-derived Gaussian ring falloff (painter's-algorithm-inverted per-ring alpha, so stacked opacity follows a true Gaussian instead of stepping), covers all three renderers (Android's native `<Heatmap>`, iOS's Circle stand-in, **and Android Auto** — which this session's own HM-32 work never touched), and ships **dark** behind a compile-time flag (`SOFT_HEAT_RENDER_ENABLED = false`) pending real device verification. This session's own `BLOB_LAYERS`/`rampColorForRatio` iOS fix (committed earlier the same day) shipped **live** (unconditionally active) with less rigorous math and no Android Auto coverage.

## 3. Fix / remediation

- Adopted `lib/heatFalloff.ts` and its `HeatmapCells.tsx` integration from `main` wholesale — this session's own `BLOB_LAYERS`/`rampColorForRatio` approach (from `docs/change-log/2026-09-09-heatmap-soft-gradient-style-ios.md`) is fully superseded and removed.
- **Re-applied on top** (the parts `main`'s version didn't have): the `driverLocation` prop and its exclusion logic (a genuinely different concern — which cells render at all vs. how a rendered cell looks — not overlapping with their fix), via the `useVisibleHeatmapCells` hook, kept but simplified to source `cellCenter`/`METERS_PER_LAT_DEG`/`HEAT_BLOB_RADIUS_FACTOR` from `heatFalloff.ts` instead of duplicating them.
- Moved `weightToRampIndex` (previously a private helper duplicated only in `HeatmapCells.tsx`) into `heatFalloff.ts` as an export — a small, additive change to their module, consistent with its own stated purpose ("shared by all... renderers") — so the new Skia overlay (HM-32) can use the identical color bucketing instead of a third copy.
- Rebased `HeatmapGradientOverlay.tsx` (the Skia component) onto `heatFalloff.ts`'s `cellCenter`/`hexToRgba`/`weightToRampIndex` instead of the now-deleted `utils/heatmapColor.ts`. Skia's own real image-space blur means it doesn't need `ringAlphas()`'s stacked-circle technique — it draws one fully-opaque circle per cell and lets the blur do the softening — but color selection is now identical to every other renderer.
- Deleted `utils/heatmapColor.ts` and its test (fully superseded).
- **`SOFT_HEAT_RENDER_ENABLED` was left `false`** (not flipped) — the other session's own stated reasoning ("nothing in this repo can screenshot Apple Maps, Google Maps or an Android Auto head unit — so it stays off until someone captures native evidence on all three") is a live, unresolved safety condition this reconciliation should not silently override. Flipping it is a follow-up decision for after real device evidence exists (the same evidence gate HM-32 itself is waiting on).

## 4. Risk & impact on existing functionality

- **Blast radius: `HeatmapCells.tsx`, `useVisibleHeatmapCells.ts`, `HeatmapGradientOverlay.tsx`, `lib/heatFalloff.ts`, and their test files.** No behavior change to Android's or iOS's currently-*live* rendering (both still resolve to the pre-existing 2-circle/discrete-bucket path, since the flag stays off) — the reconciliation is almost entirely a code-organization change plus HM-32's own not-yet-shipped Skia path.
- Full driver-app suite (139 suites / 1566 tests) passes after reconciliation, including `origin/main`'s own new `HeatmapCellsSoft.test.tsx` (exercises the soft-ring path by mocking the flag true) and `heatFalloff.test.ts` (28 tests covering the Gaussian ring math) — neither needed any change to pass against the reconciled code.
- Two pre-existing, intermittent test-file-pollution flakes were observed during this reconciliation (`backgroundMessaging.android.test.ts`, `notifeeService.test.ts`) — both pass 100% in isolation and pass cleanly on a repeat full-suite run; neither is related to this change (confirmed by reproducing the same class of flake before any heatmap work began this session).

## 5. User-experience effect

No live behavior change (flag stays off). Once flipped (a future decision), the effect is as already described in the other session's own commits and this session's earlier `docs/change-log/2026-09-09-heatmap-soft-gradient-style-ios.md` (superseded) — a softer, continuous-falloff iOS heatmap.

## 6. Files modified (this reconciliation only)

| File path | What changed | Why |
|---|---|---|
| `driver-app/lib/heatFalloff.ts` | Added `weightToRampIndex` export | Consolidate the last remaining duplicate color-bucketing logic |
| `driver-app/hooks/useVisibleHeatmapCells.ts` | Sources `cellCenter`/`METERS_PER_LAT_DEG`/`HEAT_BLOB_RADIUS_FACTOR`/`SOFT_HEAT_RENDER_ENABLED` from `heatFalloff.ts` instead of its own copies | Single source of truth for geometry constants |
| `driver-app/components/dashboard/HeatmapCells.tsx` | Merged: `main`'s Gaussian-ring rendering + this branch's `driverLocation` exclusion | Conflict resolution per user's explicit choice |
| `driver-app/components/dashboard/HeatmapGradientOverlay.tsx` | Color/geometry helpers now import from `heatFalloff.ts` instead of the deleted `utils/heatmapColor.ts` | Keep the Skia overlay consistent with the now-canonical shared module |
| `driver-app/utils/heatmapColor.ts`, `driver-app/utils/__tests__/heatmapColor.test.ts` | Deleted | Fully superseded by `heatFalloff.ts` |
| `driver-app/__tests__/components/HeatmapCells.test.tsx` | Replaced with `main`'s version + this branch's driver-exclusion describe block re-added | Match the reconciled component's actual behavior |
| `driver-app/hooks/__tests__/useVisibleHeatmapCells.test.ts`, `driver-app/__tests__/components/HeatmapGradientOverlay.test.tsx` | Import fixes (`cellCenter` now from `heatFalloff.ts`); `outerRadiusM` expected value updated for the legacy (flag-off) 0.62 factor | Match the reconciled hook |

## 7. Rollback plan

`git revert` of this merge commit is possible but would reintroduce the conflict rather than cleanly resolve it — not recommended. If the reconciliation itself needs undoing, the safer path is a fresh, deliberate re-reconciliation rather than a revert.

## 8. Verification performed

- [x] Full driver-app suite: 139 suites / 1566 tests passing (repeat run, to rule out flakiness)
- [x] `npx tsc --noEmit`: clean
- [x] Grepped for every remaining reference to the removed `BLOB_LAYERS`/`rampColorForRatio`/`utils/heatmapColor` — none found
- [x] `origin/main`'s own new tests (`HeatmapCellsSoft.test.tsx`, `heatFalloff.test.ts`) pass unmodified against the reconciled code

## What was NOT verified

- Same boundary as both original features: nothing has been visually verified on a real device. This reconciliation is a code-correctness and structural exercise, not new device evidence.
- Whether `SOFT_HEAT_RENDER_ENABLED` should be flipped is explicitly left as an open, separate decision — not resolved here.
