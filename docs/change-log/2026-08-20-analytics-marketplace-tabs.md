# Change Impact & Risk Log — Four marketplace metric tabs

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-dashboard-analytics-review-xsjyuk` |
| Related issue or gap ID | User request: business-decision metrics, per service area and overall |

## 1. Issue / gap identified

The aggregates added in migrations 351/352 had no UI. Separately, four
CLAUDE.md KPI targets (match rate, rider/driver cancellation rate, driver
utilization) still had no visual surface, and the Analytics page opened on
failure triage rather than on whether the business was working.

## 2. Root cause

Missing capability. The preceding two commits built the data layer only.

## 3. Fix / remediation

Four new tabs on `/dashboard/analytics`, all driven by the page's existing
shared area+date filter bar:

- **Overview** (now the landing tab) — conversion funnel, KPI-vs-target
  cards, unmet demand, daily volume.
- **Supply** — driver time split across insurance Periods 1/2/3, utilization
  against the 55% target, daily supply hours and utilization.
- **Efficiency** — time-to-match, assignment→trip-start, pickup ETA error,
  deadhead.
- **Financial** — gross bookings, fare composition, surge penetration,
  corporate/consumer mix.

Supporting shared modules: `chart-palette.ts` (one categorical palette for
every Analytics chart) and `kpi-tile.tsx` (`StatTile`, `KpiCard`,
`SampleNote`, `fmtSecs`, `fmtMoney`).

**Chart-design decisions, made against the `dataviz` guidance rather than by
eye:**

- The categorical palette is derived from the Spinr brand tokens as
  `.claude/context/brand-spinr.md` requires ("derive them from this palette
  rather than picking an unrelated one"), then **validated with the
  skill's script, not reasoned about**. Light mode: all checks pass, with a
  contrast WARN on emerald (2.47:1) and amber (2.09:1). Dark mode: the light
  steps for emerald (L 0.696) and amber (L 0.769) **fail** the lightness band
  against the `#1C1C1E` dark surface, so dark uses selected deeper steps
  (`#059669`, `#D97706`) — a selected palette, not an automatic flip. Dark
  passes all six checks.
- The light-mode contrast WARN is not dismissable, so every chart using the
  palette carries direct value labels or an adjacent numeric readout, and
  every multi-series chart carries a legend. Identity is never colour-alone.
- **No dual-axis charts.** The daily-supply chart initially plotted an
  active-driver *count* on an *hours* axis; that was caught and split into
  two single-measure charts (hours; utilization %) rather than adding a
  second scale.
- Percentiles are rendered as a table, not plotted — four numbers read better
  written than charted — and each row carries its sample size with an
  explicit "small sample" warning under n=30.
- The utilization chart carries a dashed 55% target reference line so "is
  this good?" is answerable from the chart itself.

## 4. Risk & impact on existing functionality

**Blast radius: admin-dashboard, one route, plus new files only.**

Six new files under `src/components/analytics/`; no existing component was
modified. The only edits to existing files are `analytics/page.tsx` (imports,
four `TabsTrigger`s, four `TabsContent`s, and the default tab) and the two API
modules (additive exports).

**One deliberate behavior change:** the default tab moves from
`cancellations` to `overview`. An admin who lands on `/dashboard/analytics`
sees the marketplace funnel first instead of the cancellation breakdown. The
Cancellations tab is unchanged and one click away.

No shared UI primitive was modified — `Card`/`Table`/`Tabs`/`Select` are
consumed as-is, so no other page inherits a change. No backend change. No
rider, driver, corporate, money, or state-machine path is touched.

The panels each own their fetch and error state, so a failure in one tab
cannot blank the others. Tabs render lazily via Radix, so the three
non-active tabs issue no requests until selected — four new endpoints do not
mean four new requests on page load.

## 5. User-experience effect

**Internal admin only.** Nothing rider-, driver-, or corporate-facing;
nothing visible mid-session to anyone using the apps.

Admins get four new tabs and a different landing tab. Every new number is
read-only. Where a metric has a CLAUDE.md target, the tile states the target
and whether it is being met, with an icon and words — never colour alone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.../components/analytics/chart-palette.ts` | New. Validated light/dark categorical palette | One visual system across all Analytics charts |
| `.../components/analytics/kpi-tile.tsx` | New. StatTile, KpiCard, SampleNote, formatters | Shared stat-tile vocabulary; KPI-vs-target readout |
| `.../components/analytics/marketplace-overview-panel.tsx` | New. Funnel + KPI cards + daily volume | Overview tab |
| `.../components/analytics/supply-panel.tsx` | New. Period split, utilization, daily supply | Supply tab |
| `.../components/analytics/efficiency-panel.tsx` | New. Percentile table + deadhead | Efficiency tab |
| `.../components/analytics/financial-panel.tsx` | New. Bookings, surge, corporate mix | Financial tab |
| `.../app/dashboard/analytics/page.tsx` | Four tabs added; default tab → overview | Mount the panels |
| `.../lib/api/analytics-payouts.ts`, `.../lib/api.ts` | Four client fns + re-exports | Reach the new endpoints |

## 7. Before / after

```tsx
// Before — page opened on failure triage
<Tabs defaultValue="cancellations">
  <TabsList>
    <TabsTrigger value="cancellations">Cancellation Breakdown</TabsTrigger>
    <TabsTrigger value="acceptance">Driver Acceptance Rates</TabsTrigger>
```

```tsx
// After — opens on marketplace health; triage tabs retained
<Tabs defaultValue="overview">
  <TabsList>
    <TabsTrigger value="overview">Overview</TabsTrigger>
    <TabsTrigger value="supply">Supply</TabsTrigger>
    <TabsTrigger value="efficiency">Efficiency</TabsTrigger>
    <TabsTrigger value="financial">Financial</TabsTrigger>
    <TabsTrigger value="cancellations">Cancellations</TabsTrigger>
    <TabsTrigger value="acceptance">Driver Acceptance</TabsTrigger>
    <TabsTrigger value="offers">Dispatch Offers</TabsTrigger>
    <TabsTrigger value="forecast">Demand Forecast</TabsTrigger>
```

## 8. Rollback plan

`git revert` is sufficient and complete. Frontend-only, no migration, no
schema change, no write path, no persisted state, no live data touched. The
six panel/support files are new; reverting removes them and restores the
two-tab page.

No feature flag. The change is internal-admin-only and read-only, and the
four new tabs are additive — the risk is confined to the one-line default-tab
change, which is trivially revertible on its own.

## 9. Verification performed

- [x] **Real production build run** — `npm run build`, exit 0, full route table emitted. `tsc --noEmit` exit 0 after each panel.
- [x] **Palette validated with `scripts/validate_palette.js`, not by eye** — light and dark modes checked separately against their own surfaces (`#FFFFFF` / `#1C1C1E`). Dark-mode failure found and fixed with selected deeper steps. Results recorded in `chart-palette.ts`'s header comment.
- [x] Checked against the dataviz anti-patterns: no dual-axis chart (one was caught and split), categorical hues in fixed order and never cycled, sequential/diverging rules not violated (no ramp used), legend present on every multi-series chart, no number printed on every point, grid/axes recessive, text in text tokens rather than series colour.
- [x] Every panel written with `dark:` variants and theme-aware chart chrome via `hsl(var(--*))` CSS variables.
- [x] Blast-radius grep — no existing component modified; the four new client functions had no prior consumers.

## 10. What was NOT verified

- **Nothing was rendered.** No dev server, no browser, no screenshots. Four new tabs containing eleven charts are verified only by type-check and a successful production build. **A manual pass is required before merge** — chart label collisions, funnel bar-label overflow at small widths, and the tab row's horizontal scroll behaviour are all things a clean compile says nothing about. The dataviz guidance itself says to render and look at the output; that step could not be completed in this environment.
- **This repo has no visual/snapshot regression tooling for admin-dashboard** (standing gap).
- **Dark mode was never viewed.** The palette is validated numerically and the classes carry `dark:` variants, but no one has looked at these tabs in dark mode.
- **No data has ever flowed through these panels.** Every one is driven by an endpoint whose SQL has never been executed (migrations 351/352 are un-run). Field names in the panels were written against the endpoints' Python response shapes; a mismatch between those and what the un-run SQL actually returns would surface only at runtime. This is the largest single risk in this branch.
- **No tests.** `admin-dashboard/__tests__` has no analytics coverage and none was added; the panels' formatting helpers (`fmtSecs`, `fmtMoney`, the null-vs-zero handling) are unit-testable and untested.
- Responsive behaviour reasoned from Tailwind breakpoints, not measured at real viewport sizes.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] Default-tab change documented in §5/§7 rather than shipped silently
- [ ] **Open gate: manual render pass (light + dark) before merge** (see §10)
- [ ] **Open gate: depends on migrations 351/352 being dry-run and applied** — the tabs are inert until then
