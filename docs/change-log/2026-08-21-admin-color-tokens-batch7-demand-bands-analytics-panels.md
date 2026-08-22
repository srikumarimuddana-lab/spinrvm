# Change Impact & Risk Log — #2816 Batch 7 sub-batch 23: demand-bands, marketplace/supply/efficiency/driver-offers panels

## Issue/gap identified
Five more admin-dashboard files still used raw Tailwind color utilities for a single-signal error
message pattern (four files) and a demand-heat-gradient map (one file) instead of the semantic
tokens/proper documentation.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `lib/demand-bands.ts`: `DEMAND_BANDS` (critical/high/elevated/building/balanced/oversupply — a
  deliberate 6-tier demand/supply-ratio heat gradient mirroring
  `backend/utils/surge_engine.py`'s `SURGE_TIERS` exactly, per the file's own extensive header
  comment about a prior color-drift incident) documented with a block
  `eslint-disable`/`eslint-enable` comment — the same "categorical/gradient map, too many states
  for 3 tokens" treatment used elsewhere in this migration. This is a single source-of-truth file
  consumed by both the monitoring map overlay and the heatmap's "Unmet Demand" cards, so its
  colors were left completely untouched, not just documented — any actual color change here would
  need to happen in lockstep with the backend's `SURGE_TIERS` and is out of scope for a token-only
  migration.
- `components/analytics/marketplace-overview-panel.tsx`: the fetch-error message
  (`text-red-600 dark:text-red-400`) → `text-destructive`; the "attribution honesty note" (a
  single warning-severity disclosure about pre-migration cancellation data) →
  `border-warning bg-warning/10 text-warning`.
- `components/analytics/supply-panel.tsx`, `efficiency-panel.tsx`, `driver-offers-panel.tsx`: the
  same fetch-error message pattern in each → `text-destructive`.

## Risk & impact on existing functionality
Color-only class swaps (and one added lint-suppression block) — no logic, props, or data flow
changed.
- `DEMAND_BANDS` is imported by `heatmap/page.tsx` (already touched in sub-batch 22 for an
  unrelated line) and the monitoring map overlay — grepped both; this sub-batch made zero changes
  to the array's actual color values, only added the documenting comment block, so neither
  consumer's rendering changes at all.
- The four analytics panels' error-message pattern is local to each file's own error-state render;
  no shared component involved.

## User experience effect
All five files are internal-admin-only analytics/monitoring screens. The `DEMAND_BANDS` heat
gradient is completely unchanged visually. The four error-message conversions and the
attribution-note styling are purely cosmetic — the underlying fetch-failure and data-quality
logic is unchanged.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/demand-bands.ts` | `DEMAND_BANDS` documented as an intentional categorical/gradient exclusion (no color values changed) | #2816 |
| `admin-dashboard/src/components/analytics/marketplace-overview-panel.tsx` | Fetch-error message + attribution note → destructive/warning tokens | #2816 |
| `admin-dashboard/src/components/analytics/supply-panel.tsx` | Fetch-error message → destructive token | #2816 |
| `admin-dashboard/src/components/analytics/efficiency-panel.tsx` | Fetch-error message → destructive token | #2816 |
| `admin-dashboard/src/components/analytics/driver-offers-panel.tsx` | Fetch-error message → destructive token | #2816 |

## Before/after snippet
```tsx
// four analytics panels — before (identical pattern in each)
<p className="text-sm text-red-600 dark:text-red-400">{error}</p>
// after
<p className="text-sm text-destructive">{error}</p>
```

## Rollback plan
Pure CSS class revert (plus one added lint-suppression block with no color change) — `git revert`
this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 5 changed files: 0 errors, 8 warnings (pre-existing unrelated
  `react-hooks/set-state-in-effect` advisories only — no raw-color warnings remain on any of the
  five files, confirming the `DEMAND_BANDS` suppression block covers the array correctly).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin session; the `DEMAND_BANDS` gradient itself was not re-verified against the
backend's `SURGE_TIERS` (out of scope — no values were changed).
