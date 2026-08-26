# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 51

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
Three `components/analytics/*` panels used hardcoded Tailwind color utilities instead of
the shared semantic theme tokens: fixed `text-red-600 dark:text-red-400` error text in
`financial-panel.tsx` and `efficiency-panel.tsx`, and a fixed green/amber/gray
`DATA_BASIS_COLORS` confidence-badge map in `demand-forecast-panel.tsx`.

## Root cause
These panels were built before the `--success`/`--warning`/`--destructive` semantic
tokens existed in `globals.css`, so error/status colors were hand-picked per component
instead of reusing the shared tokens other panels already converted to.

## Fix/remediation
- `financial-panel.tsx`: error message `text-red-600 dark:text-red-400` → `text-destructive`.
- `efficiency-panel.tsx`: identical error message pattern → `text-destructive`.
- `demand-forecast-panel.tsx`: `DATA_BASIS_COLORS` 3-state map (`historical_average` /
  `limited_history` / `default_pattern`) converted from hardcoded green/amber/gray classes
  to `bg-success/15 text-success`, `bg-warning/15 text-warning`, `bg-muted text-muted-foreground`
  respectively — this is a genuine 3-state signal map (data-provenance confidence), not a
  decorative theme, so token conversion (not documentation) is correct here.

Left untouched (established exclusions, consistent with prior sub-batches in this
migration):
- `financial-panel.tsx` line 145: the amber `Zap` "Surge penetration" icon — a small
  decorative icon accent next to a label, not itself a badge/signal.
- `demand-forecast-panel.tsx`: the "peak hour" amber/orange decorative theme (6 lines —
  `Zap`/`Sun` icons, the `peak_hours_count` figure, and the peak-row background/border/text)
  — an already-established decorative-theme exclusion carried forward from earlier
  sub-batches, not a genuine multi-state signal.

## Risk & impact on existing functionality
All three edits are local, single-purpose class-string swaps inside leaf presentational
components (`FinancialPanel`, `EfficiencyPanel`, `DemandForecastPanel`) — none of the three
are shared/imported by any other component, so there is no blast radius beyond the file
itself. No props, state shape, or exported symbols changed. The `--success`/`--warning`/
`--destructive` tokens are pre-existing (already used elsewhere in this migration) and
resolve to colors already verified for light/dark contrast in earlier sub-batches of this
same effort.

## User experience effect
Purely a color-token substitution — the rendered error-message color and the confidence
badge's three states resolve to visually equivalent (already-approved) tokens under both
light and dark themes. No layout, copy, or behavior change. Admin-portal-facing only
(Analytics tab), not visible to riders/drivers.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/components/analytics/financial-panel.tsx` | `text-red-600 dark:text-red-400` → `text-destructive` on the error message | #2816 token migration |
| `src/components/analytics/efficiency-panel.tsx` | `text-red-600 dark:text-red-400` → `text-destructive` on the error message | #2816 token migration |
| `src/components/analytics/demand-forecast-panel.tsx` | `DATA_BASIS_COLORS` map converted to `bg-success/15 text-success` / `bg-warning/15 text-warning` / `bg-muted text-muted-foreground` | #2816 token migration (genuine 3-state signal) |

## Before/after snippet
```tsx
// financial-panel.tsx / efficiency-panel.tsx — before
<p className="text-sm text-red-600 dark:text-red-400">{error}</p>
// after
<p className="text-sm text-destructive">{error}</p>
```
```tsx
// demand-forecast-panel.tsx — before
const DATA_BASIS_COLORS: Record<string, string> = {
  historical_average: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200",
  limited_history: "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-200",
  default_pattern: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
};
// after
const DATA_BASIS_COLORS: Record<string, string> = {
  historical_average: "bg-success/15 text-success",
  limited_history: "bg-warning/15 text-warning",
  default_pattern: "bg-muted text-muted-foreground",
};
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior hardcoded
classes with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on all three edited files: 0 errors. 13 pre-existing warnings remain,
  all either already-established documented exclusions (peak-hour theme, surge icon) or
  unrelated pre-existing `react-hooks` warnings on these files — no new warnings introduced.
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) was **not** re-run this sub-batch — the pre-existing,
  diff-unrelated `@spinr/shared` "Unknown module type" Turbopack failure was already
  root-caused against unmodified `origin/main` in sub-batch 31/PR #4371 (confirmed via
  `git stash`); this sub-batch's changes are plain Tailwind class-string edits with no
  import/module changes, so a fresh full-build re-run was not warranted.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing gap,
  `ACTION_ITEMS.md`) — the token substitutions were reasoned about against previously
  contrast-verified token values from earlier sub-batches, not screenshotted.
- Not tested against a live Supabase/staging deployment — only against the existing mocked
  `vitest` fixtures for these panels.
