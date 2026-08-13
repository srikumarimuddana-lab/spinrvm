# Driver Heatmap Parity — Design Spec

- **Date:** 2026-08-13 · **Status:** Proposed
- **Paired plan:** `docs/superpowers/plans/2026-08-13-driver-heatmap-parity.md` (competitive analysis, roadmap, ticket IDs referenced below)
- Scope: backend endpoint redesign, driver-app rendering/UX, Android Auto layer, and the per-service-area admin track. Planning only — nothing here changes behavior until its ticket ships with its own Change Impact entry.

## 1. Backend

### 1.1 HM-01 — privacy-safe aggregation, shape-compatible

Today `GET /drivers/demand-heatmap` (`backend/routes/drivers/profile.py:317-363`) returns up to 2 000 exact rider pickup coordinates as `points: [[lat, lng, 1], ...]`. The fix aggregates server-side and keeps the response shape, so **already-shipped app versions keep working untouched**:

- **Grid:** fixed lat/lng grid, no new dependency. `CELL_LAT = 0.004°` (~445 m) and `CELL_LNG = 0.006°` (~414 m at Saskatchewan's ~52° N; lng cell chosen so cells are near-square there). Cell key = `(floor(lat/CELL_LAT), floor(lng/CELL_LNG))`; emitted point = cell centroid. H3 was considered and rejected for now: a C-extension dependency buys nothing at two-city scale that a fixed grid doesn't; revisit if we ever need multi-resolution tiling.
- **k-anonymity floor:** cells with fewer than `heatmap_k_floor` (default **3**) contributing rides are suppressed entirely. A lone pickup can never be singled out; centroids are fixed grid points, so no jitter is needed.
- **Weight:** `points` become `[[cell_lat, cell_lng, weight]]` where weight = decayed count (§1.2). The native heatmap and the future cell renderer both consume weights as-is.
- `total_rides` keeps meaning "rides considered" (pre-suppression), so the number a driver might see doesn't mysteriously shrink.
- **No rollback flag for the privacy floor itself** — deliberately. Rolling back to leaking coordinates is not an acceptable state; the tunables (`heatmap_k_floor`, cell size) are the pressure valve if the map looks too coarse. This is called out in the Change Impact draft (§8).

### 1.2 HM-02 — what counts as demand

- All rider-requested rides in the window count (a cancelled request was still demand), except admin/test cancellations.
- **Recency decay:** weight per ride = `0.5 ** (age_days / 3)` (3-day half-life) so Friday's rush doesn't dominate Monday's map. Pure counts made a 7-day-old concert look like live demand.
- Window stays 7 days for the baseline component; live component is separate (§1.3).

### 1.3 HM-10 — v2 payload: live + baseline + scheduled

Additive fields on the same endpoint, gated by `app_settings.driver_heatmap_v2_enabled`; legacy `points` continues to be served either way.

```jsonc
{
  "enabled": true,
  "points": [[52.132, -106.664, 4.2]],          // legacy, aggregated per §1.1
  "cells": [                                     // v2
    { "lat": 52.132, "lng": -106.664, "live": 2, "baseline": 0.7, "scheduled": 1 }
  ],
  "surge": { "multiplier": 1.5, "active": true }, // mirror of /service-areas fields
  "generated_at": "2026-08-13T18:04:00Z",
  "refresh_seconds": 90                           // server-driven client cadence
}
```

- **live** — rides in `searching` + active statuses requested in the last 10 min, per cell. Same definition as `surge_engine._count_demand_in_area` (`backend/utils/surge_engine.py:83`), but bucketed by `pickup_lat/lng`.
- **baseline** — hour-of-week demand from the last 28 days, normalized 0–1 per area. First implementation: direct aggregation over `rides`, cached 15 min. `utils/demand_forecast.py` already exists (admin-only surface) — HM-23 decides whether to converge on it; the payload field is agnostic to which produces it.
- **scheduled** — scheduled rides with pickup in the next 120 min, per cell. **k floor applies per component** — this one matters most, since one scheduled pickup is one identifiable rider's plan.
- k floor + suppression run after component merge: a cell survives if *any* component clears the floor; components below floor are zeroed, not the cell.

### 1.4 Caching & cost (HM-06)

- Redis: `spinr:heatmap:{area_id}:{v1|v2}` TTL 60 s; build on miss. In-memory fallback dict is fine per `utils/redis_client.py` conventions (worst case: per-replica rebuilds).
- Query cost at Sask scale: one indexed `rides` read per build (7-day window, one area, `columns="pickup_lat,pickup_lng,status,created_at"`), one 10-min read for live, one scheduled read. **Verify an index serves `(service_area_id, created_at)`** — if absent, migration `307_rides_area_created_idx.sql` adds it (next free number is 307; index-with-query-pattern rule from `backend/migrations/CLAUDE.md`).
- **No new table, no new background loop for P0/P1.** With 18 loops already running on every replica, on-demand + cache is strictly simpler and replay-safe by construction. If build cost ever matters, the escape hatch is an hourly rollup table + loop registered per `core/lifespan.py` conventions (`_spawn`, `_WATCHDOG_LOOP_NAMES`, `record_heartbeat`, Redis leader lock) — that's a Phase-2+ decision, not this one.

### 1.5 Flags & config (HM-13)

All via the existing `get_app_settings()` single-row pattern (`backend/settings_loader.py:26`), editable in the admin settings UI without redeploy:

| Key | Default | Meaning |
|---|---|---|
| `driver_heatmap_v2_enabled` | `false` | v2 `cells`/`surge` fields on the endpoint |
| `heatmap_k_floor` | `3` | min contributing rides per cell per component |
| `heatmap_cell_lat_deg` / `heatmap_cell_lng_deg` | `0.004` / `0.006` | grid size |
| `heatmap_refresh_seconds` | `90` | client poll cadence (server-driven) |
| `heatmap_decay_half_life_days` | `3` | §1.2 decay |
| `heatmap_internal_driver_ids` | `[]` | allowlist for the dark-launch stage (§7) |

Per-area gate stays `service_areas.show_demand_heatmap` (existing, admin-toggleable).

### 1.6 Observability

- Metrics (`utils/metrics.py` helpers, naming per convention): `spinr_drivers_heatmap_requests_total{cache="hit|miss"}`, `spinr_drivers_heatmap_build_duration_ms`, `spinr_drivers_heatmap_cells_suppressed_total`.
- Logs: cell indices/counts only — **never raw coordinates** (standing PIPEDA rule). Errors on the build path are `logger.error` + 503, never warn-and-continue (house error rules).
- Sentry tags: `domain=drivers`, `surface=backend`.

## 2. Driver app

### 2.1 HM-05 — rendering: cells, cross-platform

Replace the Google-only `<Heatmap>` (`driver-app/app/driver/(tabs)/index.tsx:772-782`) with `<Polygon>` cells from react-native-maps — supported on **both** the Android Google provider and iOS Apple Maps, which fixes the silent iOS no-op without adopting the Google iOS SDK.

- Each cell = 4-corner rectangle from the grid; `fillColor` = ramp step (§2.3) at 0.40 opacity, no stroke except a subtle 1 px border on the top step.
- **Budget: ≤ 200 polygons rendered.** Viewport-cull against the current region; when zoomed out past a threshold, merge 2×2 cells client-side so density stays readable and the count stays bounded. Memoize the polygon array keyed on `(data.generated_at, zoom bucket)` — never rebuild per pan frame.
- The idle-map layer order stays: service-area polygon → heatmap cells → route/pins/car marker.

### 2.2 HM-03 — refresh lifecycle

- Fetch on idle entry (today's behavior) **plus** an interval at `refresh_seconds` (server-driven, default 90 s ± 10 % jitter) while `rideState === 'idle'` && driver online && app foregrounded.
- Pause and clear the timer on offer/active ride/backgrounded (offers must never contend with a fetch); resume on return to idle.
- On fetch error: keep last data, retry next tick; after 5 min of failures show a quiet "demand info unavailable" pill — no blocking alert. On `enabled: false`, remove layer + legend entirely.

### 2.3 HM-04 — visual design (validated)

The shipped teal→gold→red gradient is a multi-hue ramp — wrong for magnitude encoding and deuteranopia-hostile, and teal is off-brand (`brand-spinr.md`: derive from the brand palette). Replace with a **single-hue brand-red sequential ramp**, 5 steps, quiet → busy:

| Mode | Steps (quiet → busy) | Validation (dataviz validator, 2026-08-13) |
|---|---|---|
| Light | `#FFE3E0 #FFB3AC #FF7A6E #FF3B30 #B71C1C` | lightness monotonic 0.94→dark ✓; adjacent CVD ΔE ≥ 8.2 ✓ |
| Dark | `#4E211E #7F2D26 #B2382E #FF453A #FF8A80` | lightness monotonic 0.31→0.76 ✓; adjacent CVD ΔE ≥ 8.0 ✓ |

- The low steps sit under 3:1 contrast against the basemap **by design** (quiet should be quiet); the required relief is the **always-present labeled legend** — color is never the only encoding.
- Define the ramp once in `shared/theme/index.ts` next to the existing color tokens; admin mirrors it into `globals.css` the same way the brand-token port did (`docs/change-log/2026-07-29-admin-dashboard-brand-token-port.md`), so phone and admin render demand identically.
- Legend: compact pill above the idle panel — 5 swatches, "Quiet → Busy" end labels, ⓘ opens an explainer sheet (reuse the `CancelReasonSheet` pattern; no bottom-sheet lib exists). Hidden whenever the layer is hidden.
- Dark mode flows from `useTheme()` (`shared/theme/ThemeContext.tsx`) like every other surface; the map already switches `userInterfaceStyle`.

### 2.4 States, i18n, and copy rules

- New `driver-app/i18n/en.json` keys (none exist today): `heatmap.legend.quiet`, `heatmap.legend.busy`, `heatmap.info.title`, `heatmap.info.body`, `heatmap.stale`, `heatmap.empty`, `heatmap.layer.now`, `heatmap.layer.usually`, `heatmap.layer.scheduled`.
- States: **loading** — map renders without the layer, legend shows a shimmer; **empty** (enabled, all suppressed) — legend collapses to "No busy areas right now"; **disabled** — no layer, no legend; **error** — §2.2; **stale** — pill after 5 min.
- **Copy rules (contractor-safe, reviewed strings only):** descriptive, never imperative. "Downtown is busy" ✅ / "Go downtown" ❌. The explainer body must include the independence line, e.g. *"Based on recent rider activity in your area. Where you drive is always your choice."* No earnings estimates anywhere; dollar figures only on P2 guaranteed incentives.

### 2.5 HM-11/HM-12 — surge and layers on the map

- Surge: tint the service-area polygon by tier (1.25×+) using the ramp's step 2–4 at 0.12 opacity, plus a multiplier chip on-map (reuses the existing 2-min `/service-areas` poll and top-bar state — no new endpoint). Display value is cap-clamped at 2.5× exactly as the pill is today.
- v2 layers: segmented control in the legend sheet — **Busy now** (live), **Usually busy** (baseline, the default in sparse hours), **Scheduled soon**. Default view = blend (max of normalized components) so the map is never empty at 2 pm in Regina; the segmented control is for drivers who want one signal.

## 3. Android Auto & CarPlay (Phase 3)

- **HM-30:** add the same memoized `<Polygon>` cell array to `CarMapSurface` (`driver-app/lib/androidAuto/carSurface.tsx`) — **idle state only**, hidden the moment `card.leg !== 'idle'`. No legend, no interaction (the surface is non-interactive by platform rule) — glanceable ambience, same posture as Uber's. JS-only change; the iternio template stack needs no new native work.
- **HM-31 hardware validation checklist** (currently the whole surface is "UNPROVEN ON HARDWARE" per the file header): DHU render of idle map / offer alert / trip card; template zoom buttons; night-mode switch; heatmap layer on/off with flag; nav handoff intents (`carRoute.ts`); memory over a 2-h session; reconnect after cable drop.
- **HM-32:** CarPlay code paths are dormant pending the Apple entitlement — file the request at phase start (weeks of lead time; see `docs/carplay-android-auto.md`).

## 4. Admin dashboard — per service area

All pages follow house patterns: `useRequireModule("heatmap")` (module already exists in the sidebar registry), API modules under `src/lib/api/`, MapLibre via `lib/map/maplibre-base.ts`, charts copy `drivers/_components/driver-charts.tsx`'s `ChartCard`.

### AD-01 — live demand/supply on Monitoring

- New admin endpoint `GET /admin/service-areas/{id}/demand-cells?window=10m` sharing the §1.3 aggregation core. **Admin payloads apply no k floor** (ops legitimately needs finer grain; admins already see exact pickups on ride pages) but the route is module-gated and each call writes an `audit_logger` entry, per the security-event convention.
- Rendered as a MapLibre GeoJSON fill layer (`polygonPointsToGeoJSON` helper exists) with the shared ramp; toggle + legend in the existing monitoring toolbar; available-driver count per area from the data the page already streams.

### AD-02 — per-area trends (the free win)

`surge_pricing` already records `{multiplier, demand_count, supply_count, ratio}` every 2 min per area (`surge_engine.py`), and `GET /admin/surge-history` already serves it windowed (`backend/routes/admin/analytics.py:489-524`). Nothing charts it. Add a **Trends** tab to the service-area accordion:

- Chart 1: demand_count + supply_count lines (same unit → one axis).
- Chart 2: multiplier as a step line (different scale → **its own chart, never a dual axis**).
- Range picker 24 h / 7 d (168 h endpoint max).

### AD-03 — unmet-demand map

Cells of pickups from rides that ended `cancelled` via the no-drivers auto-cancel, per area + hour filter, as a new tab on `/dashboard/heatmap`. **Verify the exact discriminator** for auto-cancel (cancellation reason value) before building — not confirmed in this planning pass (§9). This is the "where do we lose rides" view that decides where incentives (HM-20) would actually pay.

### AD-04 — cross-area comparison (sequenced with C11/ADR-010)

Table: area × {match rate, rides, unmet %, surge-active share, utilization}. Match rate and unmet % are computable from `rides` today; **utilization has a clean source in `driver_insurance_periods`** (Period 3 time ÷ Periods 1+2+3 time — the append-only regulatory log doubles as the KPI source). Build this inside the ADR-010 metrics implementation, not as a bespoke sidecar.

### AD-05 — config UI

Expose §1.5 knobs in admin settings + a "what drivers see" preview (calls the driver endpoint shape with the admin's chosen area) on the service-areas General tab next to the existing `show_demand_heatmap` toggle.

## 5. Performance & SLA

- Endpoint: P95 < 150 ms cached / < 500 ms on build (compare: driver-location write SLA 150 ms — this is a lighter read). Payload ≤ ~30 KB (200 cells × ~60 B v2).
- No dispatch-path coupling: heatmap reads never touch offer/accept flows; the 90 s poll is idle-only so it can't contend with offer traffic.
- App: 60 fps pan with 200 polygons on a low-end Android device is the render gate (HM-05 acceptance); memoization requirements in §2.1.
- Anti-patterns to keep out (house list): no N+1 per-cell queries (single windowed read, bucket in Python); no unpaginated full-table reads; no per-driver WS fan-out for heatmap data (poll + cache is the design — area-based WS rooms don't exist in `socket_manager.py` and this feature doesn't justify building them).

## 6. Testing plan

| Layer | Tests (ticket acceptance) |
|---|---|
| Backend unit (`pytest -m unit`, `mock_supabase_client`) | cell bucketing incl. negative-lng edges; k-floor suppression (k−1 hidden, k shown); decay math; per-component floor in v2; `show_demand_heatmap=false` → `enabled:false`; v2 flag off → no `cells`; cache hit/miss metrics; empty area |
| Backend integration | endpoint against throwaway schema with seeded rides; response-shape snapshot for **v1 compatibility** (the contract HM-01 must not break) |
| Driver app (`__tests__/`) | legend renders 5 steps + labels; polygon array derived from cells (count cap, merge-on-zoom); refresh timer only in idle+online+foreground; error/stale/empty states; **iOS path renders polygons (no `Heatmap` import remains)** |
| Android Auto | extend `lib/androidAuto/__tests__/` — cells present when idle, absent when `card.leg !== 'idle'` |
| Admin | **`npm run build` (real production build — mandatory per house rules, dev server/`tsc` alone doesn't count)**; chart components with fixture history; module-gate redirect test |
| Manual matrix | low-end Android + iPhone SE, light/dark, 2-h idle battery observation, DHU checklist (§3) |
| Load | 200 concurrent drivers polling the cached path |

**Standing gap, stated per house rules:** this repo has no visual-regression tooling, so ramp/legend/cell rendering are verified by manual screenshots (both themes) attached to each PR, not by automation.

## 7. Rollout & rollback

1. Ship dark: flags off, per-area toggles off. All P0–P2 app work is **JS-only → OTA-updatable** (no runtimeVersion bump; anything adding native deps would force an EAS store build `[build]` and must say so in its PR).
2. `heatmap_internal_driver_ids` allowlist → staff drivers in one city.
3. Enable one service area (`show_demand_heatmap`) → watch metrics + guardrail KPIs (plan §8) for a week → all areas.
4. Rollback at every stage = flag/toggle off in admin, no redeploy (`app_settings` pattern). The one deliberate exception: the HM-01 privacy floor has no "off" (§1.1).

## 8. Change Impact & Risk — pre-draft for HM-01 (complete at implementation)

| Field | Content |
|---|---|
| Issue/gap | Driver-facing endpoint ships raw rider pickup coordinates (up to 2 000 exact points) to any driver in the area |
| Root cause | v1 heatmap passed ride rows through with no aggregation layer; native heatmap blur hid the payload's precision |
| Fix | Server-side grid aggregation + k-floor (§1.1), response shape unchanged |
| Risk & blast radius | Endpoint consumers: driver-app idle map (`app/driver/(tabs)/index.tsx:278-307`) — only caller found repo-wide; admin heatmap uses a different route (`lib/api/heatmap.ts` → admin endpoints) — unaffected. Weight semantics change (1 → decayed counts): native heatmap renders relative weights, so display intensity shifts, not breaks |
| UX effect | Driver map becomes blockier/coarser on next fetch — visible mid-session to online drivers; acceptable and intended (it also becomes *more* readable). No rider/admin-visible change |
| Rollback | Tunables (`heatmap_k_floor`, cell size) loosen granularity without redeploy; **no flag restores raw coordinates — deliberate** (reverting a privacy fix is not a rollback path). If the endpoint itself breaks: `show_demand_heatmap` off per area kills the feature cleanly for shipped clients |
| Verification | Unit + integration per §6, incl. v1 shape snapshot; manual before/after screenshots both themes; state what wasn't verified (below) |

## 9. Not verified in this planning pass (honest boundary)

- `utils/demand_forecast.py` internals (referenced for reuse in HM-23; algorithm unread).
- Whether an index already serves `rides (service_area_id, created_at)` — check before HM-01; add migration 307 if not.
- The exact `cancellation_reason` discriminator for no-driver auto-cancels (AD-03 precondition).
- Android Auto behavior on real hardware — explicitly flagged `UNPROVEN` in the code; HM-31 exists to close this.
- Battery cost of the 90 s idle poll — asserted negligible, measured in HM-03 verification.
- Competitor facts are as of 2026-08-13 public sources; Uber/Lyft ship changes continuously.
