# Change Impact & Risk Log — Driver Heatmap P1

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | `1065cea` (backend HM-10/13), `a316e64` (driver-app HM-11/12) |
| Related issue or gap ID | HM-10, HM-11, HM-12, HM-13 |

## 1. Issue / gap identified

P1 gaps after the P0 privacy/rendering fix:
1. **Single-dimensional demand (HM-10)**: heatmap shows only decayed 7-day aggregate — no separation of live demand, historical patterns, or upcoming scheduled rides.
2. **No surge visualization (HM-11)**: drivers see a numeric surge multiplier in the top bar but the map doesn't reflect surge visually.
3. **No layer control (HM-12)**: drivers can't filter between "busy now" vs "usually busy at this hour" vs "scheduled pickups soon."
4. **No v2 feature flag (HM-13)**: no gating mechanism for progressive v2 rollout or dark-launch to internal drivers.

## 2. Root cause

P0 delivered the privacy-safe aggregation foundation but only a single blended weight. The v2 payload (component-separated demand) and its UI (layers, surge shading) are the next iteration.

## 3. Fix / remediation

**Backend (HM-10 + HM-13):**
- Added `driver_heatmap_v2_enabled` (bool, default false) and `heatmap_internal_driver_ids` (list, default []) to AppSettings schema.
- Extended the heatmap endpoint: when v2 is enabled (global flag or per-driver allowlist), adds `cells` array with `{lat, lng, live, baseline, scheduled}` components plus `surge: {multiplier, active}` mirror from service_area fields.
- `live`: rides in active statuses from last 10 minutes, same definition as surge engine's demand count.
- `baseline`: 28-day same-day-of-week same-hour demand, normalized 0-1.
- `scheduled`: scheduled rides with pickup in next 2 hours.
- Per-component k-anonymity: below-floor components zeroed, cell survives if any component passes.
- Separate cache key (`v2` vs `v1`) at 60s TTL.
- Legacy `points` array always served for backwards compatibility.

**Driver-app (HM-11 + HM-12):**
- Extended `useDemandHeatmap` hook: parses v2 `cells` and `surge`, supports layer state (`blend`/`live`/`baseline`/`scheduled`), re-derives weighted cells from cached v2 data on layer switch (no re-fetch).
- Service-area polygon: surge-tinted fill (ramp step at 0.12 opacity) + stroke when surge ≥ 1.25x.
- Surge multiplier chip: red pill on the map when surge > 1.0.
- `DemandLegend`: segmented control row (All / Now / Usual / Soon) below the ramp when v2 is active.
- i18n keys for layer labels and info modal layer explanation.

## 4. Risk & impact on existing functionality

**Backend:**
- Response shape change is purely additive — `cells` and `surge` only appear when v2 is enabled. Old clients that only read `points` are unaffected.
- Three additional DB queries per v2 build (live 10-min, baseline 28-day, scheduled 2h) — all use the existing `(service_area_id, created_at)` index. Worst case: 3 × 5000-row reads. Cached at 60s, so load is 1 build/min/area.
- No interaction with ride state machine, dispatch, money, or background loops.
- AppSettings changes are purely additive — no existing field touched.
- Blast radius: **isolated** — only heatmap endpoint, driver dashboard.

**Driver-app:**
- `useDemandHeatmap` hook: new state (`v2Cells`, `surge`, `layer`, `isV2`). Existing `cells` and `status` behavior unchanged when v2 is off.
- `DemandLegend`: new props (`isV2`, `layer`, `onLayerChange`) are all optional with defaults matching P0 behavior.
- Service-area polygon: stroke/fill colors now dynamic based on surge multiplier. Default (no surge) is the same teal as before.
- Surge chip is a new overlay — additive, no existing element displaced.
- Other dashboard components untouched.

**Blast-radius grep:**
- `useDemandHeatmap` — imported only by `driver/(tabs)/index.tsx`.
- `DemandLegend` — imported only by `driver/(tabs)/index.tsx`.
- `HeatmapLayer` type — imported only by `DemandLegend.tsx`.
- `surgeMultiplier` state — read by `DriverTopBar` (unchanged), surge chip (new), polygon tint (new).

## 5. User-experience effect

- **Driver sees** (when v2 is enabled): layer selector below the ramp legend (All / Now / Usual / Soon), surge-tinted service-area polygon when surge is active, red multiplier chip on the map.
- **Visible mid-session**: Yes — a driver currently online and idle will see the layer selector and surge tint appear. These are additive UI elements; the underlying heatmap cells and legend ramp are unchanged from P0.
- **Copy change**: New i18n keys for layer labels and info modal text.
- **All gated by `driver_heatmap_v2_enabled` flag** (default off). When the flag is off, the driver sees exactly the P0 experience — no layer selector, no surge tinting from the heatmap endpoint (the top-bar surge badge and polygon are governed by the existing `/service-areas` poll as before).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | Added `driver_heatmap_v2_enabled`, `heatmap_internal_driver_ids` to AppSettings | HM-13 config keys |
| `backend/routes/drivers/profile.py` | v2 payload: live+baseline+scheduled queries, per-component k-floor, surge mirror, separate cache key; extracted helpers | HM-10 |
| `backend/tests/test_drivers_shared_status_profile_coverage.py` | 5 new v2 tests + `timedelta` import + configurable `_heatmap_ctx` | Test coverage |
| `driver-app/hooks/useDemandHeatmap.ts` | v2 cell parsing, surge state, layer selection, `v2CellsToWeighted` helper | HM-11/12 |
| `driver-app/components/dashboard/DemandLegend.tsx` | Segmented control row, v2 info modal text, new props | HM-12 |
| `driver-app/app/driver/(tabs)/index.tsx` | Surge-tinted polygon, multiplier chip, layer props passed to DemandLegend | HM-11 |
| `driver-app/i18n/en.json` | Layer labels, layer info text | HM-12 i18n |

## 7. Before / after

```tsx
// Before — service-area polygon (static teal)
<Polygon
  coordinates={rawPoly.map(p => ({ latitude: p.lat, longitude: p.lng }))}
  strokeColor="rgba(0,212,170,0.65)"
  fillColor="rgba(0,212,170,0.07)"
  strokeWidth={2}
/>
```

```tsx
// After — surge-tinted when active (HM-11)
const sm = heatmapSurge?.active ? (heatmapSurge?.multiplier ?? surgeMultiplier) : surgeMultiplier;
let surgeFill = 'rgba(0,212,170,0.07)';
let surgeStroke = 'rgba(0,212,170,0.65)';
if (sm >= 1.25) {
  const rampIdx = sm >= 2.0 ? 4 : sm >= 1.75 ? 3 : 2;
  const hex = colors.heatmapRamp[rampIdx];
  // ... hex→rgba conversion
  surgeFill = `rgba(${r},${g},${b},0.12)`;
  surgeStroke = `rgba(${r},${g},${b},0.55)`;
}
<Polygon ... strokeColor={surgeStroke} fillColor={surgeFill} />
```

## 8. Rollback plan

- **Primary**: Set `driver_heatmap_v2_enabled` to `false` in `app_settings` (DB, no redeploy). The endpoint returns only v1 `points`, the driver-app shows P0 experience. Layer selector, surge tinting from heatmap data, and multiplier chip all disappear.
- **Per-driver**: Remove the driver's user ID from `heatmap_internal_driver_ids` to revert them individually.
- **Surge chip**: Gated by `surgeMultiplier > 1.0` (existing state from the 2-min poll) — this is independent of v2 and shows whenever surge is active. To disable, set the area's `surge_active` to false in admin.
- No data-level remediation needed — all changes are read-only queries.

## 9. Verification performed

- [x] Backend tests: 13 heatmap tests pass (8 v1 + 5 v2)
- [x] TypeScript: `npx tsc --noEmit` passes clean for driver-app
- [x] Blast-radius grep: all importers of changed modules listed above
- [x] Feature-flagged: `driver_heatmap_v2_enabled` (global) + `heatmap_internal_driver_ids` (per-driver allowlist)
- [x] Reviewed against CLAUDE.md: surge cap respected (display only, cap-clamped at 2.5x as always), contractor-safe copy, PIPEDA (grid centroids only, per-component k-floor)

## 10. What was NOT verified

- No visual regression tooling — surge tinting, multiplier chip, and layer selector were reasoned about, not screenshotted. Standing gap.
- Not tested against live Supabase — v2 queries use `mock_supabase_client` fixtures only.
- Baseline hour-of-week query performance not load-tested — a 28-day window scan could be slow on large areas. The 60s cache bounds real load to 1 build/min/area.
- Layer switching UX (tap responsiveness, re-render smoothness) not measured on a physical device.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated
- [x] No silent behavior change — all v2 UI is behind the v2 flag
