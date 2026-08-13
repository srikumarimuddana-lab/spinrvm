# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | AD-05: 3607168, AD-02: 7b25aa8, AD-01: 0dbca59, AD-03: 697740f |
| Related issue or gap ID | AD-01, AD-02, AD-03, AD-05 (admin track of driver heatmap feature set) |

## 1. Issue / gap identified

The admin dashboard lacked operational tooling for the driver heatmap feature: no way to tune heatmap config (cell size, decay, refresh), no surge history visualization per area, no live demand/supply overlay on the monitoring map, and no unmet-demand view for ops to identify underserved areas.

## 2. Root cause

Admin-facing ops tooling was not part of the initial driver heatmap implementation (P0-P3 phases). These were planned as a separate admin track (AD-01 through AD-05, AD-04 blocked on C11/ADR-010).

## 3. Fix / remediation

Four tickets implemented:

- **AD-05**: Heatmap ops config tab on service-areas page — toggle v2 on/off, tune k_floor/cell dimensions/decay/refresh, manage driver ID allowlist. Uses `getSettings`/`updateSettings` (app_settings table).
- **AD-02**: Surge history chart on service-area detail — per-area AreaChart showing multiplier over time (6h-7d range), peak/avg stats, manual-override indicator. Uses `getSurgeHistory` endpoint.
- **AD-01**: Live demand/supply overlay on monitoring map — toolbar toggle, 2-min polling of `getSurgeStatus`, data-driven MapLibre fill coloring by demand ratio, floating legend panel.
- **AD-03**: Unmet-demand section on heatmap page — summary cards (total demand/supply/gap/surge count), per-area demand cards sorted by ratio with color-coded borders and demand bars, 6-hour forecast bar chart. Uses `getSurgeStatus` + `getDemandForecast`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to admin-dashboard only**. No backend changes. No new API endpoints — all four tickets reuse existing endpoints (`/api/admin/settings`, `/api/admin/surge/status`, `/api/admin/analytics/surge-history`, `/api/admin/analytics/demand-forecast`).
- **Monitoring page (AD-01)**: Added `showDemand` filter to `MonitoringFilters`. Default is `false`, so existing behavior is unchanged until toggled. The `demandData` prop is `undefined` when demand mode is off. The areas sync useEffect now applies data-driven fill paint (previously hard-coded) — when no demand data, colors fall back to the same values as before via the `demandFillColor`/`demandFillOpacity` functions.
- **Heatmap page (AD-03)**: Purely additive — new section below existing legend card. Existing heatmap functionality is untouched.
- **Service-areas page (AD-05, AD-02)**: Added a new tab (`heatmap`) and a new component (`SurgeHistoryChart`) rendered below GeneralTabForm. Other tabs are unaffected. New imports (`getSettings`, `updateSettings`, `getSurgeHistory`, Recharts) add to bundle size but are code-split by Next.js.
- **No interaction with background loops, ride state machine, or money/wallet deltas.**
- Other consumers of `MonitoringFilters` (toolbar.tsx): updated to include `showDemand` toggle. No other files import this type.

## 5. User-experience effect

- **Who sees a difference**: internal admin only (admin-dashboard users).
- **Mid-session visibility**: yes — admins using the monitoring page will see a new "Demand" toggle in the toolbar (defaulted off). Admins on the service-areas page will see a new "Heatmap Config" tab. Admins on the heatmap page will see the new "Unmet Demand" section below the existing content.
- **No copy/notification changes to rider or driver.**

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Added Flame icon import, heatmap tab, SurgeHistoryChart component (~95 lines), HeatmapConfigTab component (~100 lines), HeatmapNumericField helper | AD-05 config tab + AD-02 surge chart |
| `admin-dashboard/src/app/dashboard/monitoring/types.ts` | Added `showDemand` to MonitoringFilters, added `AreaDemandSupply` interface | AD-01 type definitions |
| `admin-dashboard/src/app/dashboard/monitoring/toolbar.tsx` | Added demand toggle button | AD-01 toolbar UI |
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-map.tsx` | Added demandData prop, demandFillColor/demandFillOpacity functions, data-driven paint, demand legend overlay | AD-01 map coloring |
| `admin-dashboard/src/app/dashboard/monitoring/page.tsx` | Added demand state, polling effect, demandData prop passing | AD-01 data wiring |
| `admin-dashboard/src/app/dashboard/heatmap/page.tsx` | Added demand imports, AreaDemand/ForecastSlot types, demand fetch effect, unmet-demand cards section, forecast bar chart | AD-03 unmet demand view |
| `admin-dashboard/src/__tests__/dashboard/pages.smoke.test.tsx` | Added heatmap + service-areas smoke tests, next/dynamic mock, heat-map mock, settings-ai/analytics-payouts mocks, missing lucide icons, ReferenceLine recharts mock | Test coverage for AD-* |

## 7. Before / after

### AD-01: Monitoring map area fill (monitoring-map.tsx)

```typescript
// Before — hard-coded area fill
map.setPaintProperty(AREAS_FILL_LAYER_ID, "fill-color", "#3b82f6");
map.setPaintProperty(AREAS_FILL_LAYER_ID, "fill-opacity", 0.08);
```

```typescript
// After — data-driven from GeoJSON properties
features.forEach(f => {
    f.properties!.fillColor = demandFillColor(ratio);
    f.properties!.fillOpacity = demandFillOpacity(ratio);
});
map.setPaintProperty(AREAS_FILL_LAYER_ID, "fill-color", ["get", "fillColor"]);
map.setPaintProperty(AREAS_FILL_LAYER_ID, "fill-opacity", ["get", "fillOpacity"]);
```

## 8. Rollback plan

All four changes are purely additive frontend code with no backend or data changes. Rollback:
- Revert the commits (git revert) and redeploy admin-dashboard. No data-level remediation needed.
- Alternatively, the demand overlay (AD-01) defaults to off (`showDemand: false`), so it has zero impact without user interaction.
- The heatmap config tab (AD-05) writes to `app_settings` via the existing `updateSettings` API — any incorrect config can be corrected by editing the same settings again or via Supabase directly.

## 9. Verification performed

- [x] Automated tests run: vitest smoke tests for heatmap page (renders + unmet demand heading), service-areas page (renders), monitoring page (pre-existing, still passes)
- [ ] Manual repro steps followed in staging — no staging available in this session
- [x] Blast-radius grep performed: searched all importers of `MonitoringFilters` (only toolbar.tsx and page.tsx), `AreaDemandSupply` (only monitoring-map.tsx and page.tsx), service-areas tab array consumers (only page.tsx)
- [x] Reviewed against relevant CLAUDE.md conventions: no money/RLS/state machine changes; pure admin UI
- [x] Production build verified: `npm run build` succeeds for all four commits
- [x] TypeScript check verified: `tsc --noEmit` clean for all four commits

## 10. What was NOT verified

- No visual regression tooling exists in this repo for the admin dashboard — all UI changes were reasoned about from code, not screenshotted. This is a standing gap (see ACTION_ITEMS.md).
- The demand polling interval (120s) was not load-tested against the surge/status endpoint — the endpoint is already used by other admin pages and the surge engine runs every 2 minutes, so 120s polling should be within acceptable load.
- The Recharts `ReferenceLine` label positioning was not visually verified — styled identically to existing Recharts usage elsewhere in the dashboard.
