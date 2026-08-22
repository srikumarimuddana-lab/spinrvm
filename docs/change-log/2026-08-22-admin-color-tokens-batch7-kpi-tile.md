# Change Impact & Risk Log — #2816 Batch 7, sub-batch 50: shared kpi-tile component

**Issue/gap identified**: `components/analytics/kpi-tile.tsx` — a shared component (`StatTile`, `KpiCard`, `SampleNote`) consumed across multiple analytics panels (supply-panel, driver-offers-panel, efficiency-panel, financial-panel, marketplace-overview-panel, demand-forecast-panel) — used hardcoded `text-emerald-600`/`text-amber-600`/`text-red-600` for its `good`/`warn`/`bad` tone prop, `border-amber-400`/`text-emerald-600`/`text-amber-600` for the KPI-target pass/fail card, and `text-amber-500` for the small-sample-size indicator, instead of `--success`/`--warning`/`--destructive` tokens.

**Root cause**: Predates the semantic-token system introduced for #2816.

**Fix/remediation**: Converted all three:
- `StatTile`'s tone→class mapping (`good`→success, `warn`→warning, `bad`→destructive).
- `KpiCard`'s meeting/below-target card border and headline figure (meeting→success, below→warning — a KPI miss is presented as a caution, not a hard failure, matching the component's existing pass/fail design intent).
- `SampleNote`'s small-sample-size icon → warning.

**Risk & impact on existing functionality**: This is a **shared component** — grepped for all consumers before editing: `analytics/supply-panel.tsx`, `analytics/driver-offers-panel.tsx`, `analytics/efficiency-panel.tsx`, `analytics/financial-panel.tsx`, `analytics/marketplace-overview-panel.tsx`, and `analytics/demand-forecast-panel.tsx` all import `StatTile`/`KpiCard`/`SampleNote` from this file. None of them pass raw class strings into these components — they only pass the `tone` prop (`"good"`/`"warn"`/`"bad"`/`"neutral"`) and a `KpiReading` object, both untouched string/shape contracts — so this change is purely internal to the shared component's rendering and cannot break any caller. Pure CSS class-name substitution; no logic, props, or exported function signatures changed.

**User experience effect**: Internal-admin-only surfaces (all analytics panels under `/dashboard/analytics`). Visually equivalent in both themes across every panel that uses these tiles.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/analytics/kpi-tile.tsx` | `StatTile` tone map, `KpiCard` pass/fail styling, `SampleNote` small-sample icon → success/warning/destructive tokens | #2816 |

**Before/after snippet**:
```tsx
// before
const toneCls =
    tone === "good" ? "text-emerald-600 dark:text-emerald-400"
    : tone === "warn" ? "text-amber-600 dark:text-amber-400"
    : tone === "bad" ? "text-red-600 dark:text-red-400"
    : "text-foreground";
// after
const toneCls =
    tone === "good" ? "text-success"
    : tone === "warn" ? "text-warning"
    : tone === "bad" ? "text-destructive"
    : "text-foreground";
```

**Rollback plan**: `git revert` — pure class-name substitution, no data/migration involved.

**Verification performed**:
- `eslint` on the changed file: 0 problems (fully clean, no warnings of any kind).
- `vitest run`: 339/339 tests pass across all 35 test files.
- `tsc --noEmit`/`npm run build`: not re-run per-batch — the pre-existing, diff-unrelated `@spinr/shared` Turbopack failure in this environment was already root-caused via `git stash` against unmodified `origin/main` in sub-batch 31's PR (#4371).

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap) — with this component used across 6 different analytics panels, the visual equivalence was reasoned about (same hue family via token, contract unchanged) rather than screenshotted across every consumer page.
