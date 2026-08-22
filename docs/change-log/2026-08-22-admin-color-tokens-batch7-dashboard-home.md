# Change Impact & Risk Log — #2816 Batch 7, sub-batch 47: dashboard-home rates + error banners

**Issue/gap identified**: `dashboard/page.tsx` (the admin home page) used `text-emerald-500`/`text-red-400` for the Completion Rate/Cancellation Rate figures, and `border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/10 text-amber-800 dark:text-amber-300` for two error/degraded-state banners (data-load failure, aggregates-disabled notice) — all instead of the `--success`/`--warning`/`--destructive` tokens.

**Root cause**: Predates the semantic-token system introduced for #2816.

**Fix/remediation**: Converted:
- Completion Rate figure → `text-success`; Cancellation Rate figure → `text-destructive` — the same single-signal-percentage pattern already converted for `analytics/page.tsx` in sub-batch 30.
- Dashboard-load-error banner → `border-warning/30 bg-warning/10 text-warning`.
- "Revenue figures need PostgREST aggregates" degraded-state banner → the same warning tokens.

Left untouched: `STAT_COLOR_CLASSES` (the 7-category KPI-icon color map for the stat cards — an explicitly documented technical constraint against dynamic Tailwind class interpolation, not a design choice about severity, and a categorical multi-KPI differentiation consistent with the established money-category-differentiation exclusion), the `BarStat` ride-breakdown bar colors (5-state categorical ride-status map, same class as other ride-status exclusions elsewhere), and the `RevenueCard` gradient backgrounds (solid-fill white-text cards, decorative branding gradients).

**Risk & impact on existing functionality**: Pure CSS class-name substitution on 4 elements — no logic, props, or conditional rendering changed. Blast radius: isolated to this one file (the admin home page, `/dashboard`).

**User experience effect**: Internal-admin-only surface. Visually equivalent in both themes.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/page.tsx` | Completion/Cancellation rate figures + 2 error/degraded banners → success/warning/destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before
{error && (
    <div className="rounded-md border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
        Dashboard data is temporarily unavailable. Check backend health and try again.
    </div>
)}
// after
{error && (
    <div className="rounded-md border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning">
        Dashboard data is temporarily unavailable. Check backend health and try again.
    </div>
)}
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 errors, 24 warnings (all pre-existing on the deliberately-untouched `STAT_COLOR_CLASSES`/`BarStat`/`RevenueCard` categorical/decorative color maps).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap).
