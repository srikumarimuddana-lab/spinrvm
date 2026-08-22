# Change Impact & Risk Log — #2816 Batch 7, sub-batch 42: heatmap per-area surge badge

**Issue/gap identified**: `heatmap/page.tsx`'s per-area demand card used `bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30` for the "×surge" badge instead of the `--warning` token.

**Root cause**: Predates the semantic-token system introduced for #2816.

**Fix/remediation**: Converted the surge badge to `bg-warning/10 text-warning border-warning/30`, consistent with the surge-as-caution-signal convention already used elsewhere (e.g. `earnings/page.tsx`'s "Surge Revenue" KPI, converted in sub-batch 36).

Left untouched (decorative/documented exclusions): the "Active Demand" (orange) and "Surge Active" (amber) stat-card icons — decorative multi-column stat differentiation, same class as the earlier-established money-category-differentiation exclusion; the 6-hour demand-forecast bar chart (`bg-orange-600`/`bg-orange-500/70` for peak/non-peak bars) — a chart-fill color, not a status signal; and `DEMAND_BANDS`/`bandForRatio` from `lib/demand-bands.ts` (imported, not modified here) — already documented in an earlier session as a documentation-only exclusion since it mirrors the backend's `SURGE_TIERS` exactly and any recoloring would need backend coordination out of scope for a token-only migration.

**Risk & impact on existing functionality**: Pure CSS class-name substitution on one `<Badge>` — no logic, props, or conditional rendering changed. Blast radius: isolated to this one file.

**User experience effect**: Internal-admin-only surface (`/dashboard/heatmap`). Visually equivalent in both themes.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/heatmap/page.tsx` | Per-area surge badge → warning tokens | #2816 |

**Before/after snippet**:
```tsx
// before
<Badge variant="outline" className="text-xs bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30">
    {area.multiplier.toFixed(2)}× surge
</Badge>
// after
<Badge variant="outline" className="text-xs bg-warning/10 text-warning border-warning/30">
    {area.multiplier.toFixed(2)}× surge
</Badge>
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 9 warnings (all pre-existing on the deliberately-untouched decorative icons/forecast bars plus one unrelated `react-hooks/set-state-in-effect`).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap).
