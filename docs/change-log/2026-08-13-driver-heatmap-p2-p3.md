# Change Impact & Risk Log — Driver Heatmap P2 + P3

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | Branch `claude/driver-app-heatmap-planning-o7v5ic` |
| Related issue or gap ID | HM-20 through HM-23 (P2), HM-30 (P3) |

## Tickets implemented

| Ticket | Summary | Status |
|---|---|---|
| HM-20 | Geofence nudge ("you're near a busy zone") | **Blocked** — needs business decision on nudge copy/frequency |
| HM-21 | Airport sub-zone status for drivers | Done |
| HM-22 | Hotspot chips — top-3 busy cells | Done |
| HM-23 | Predictive demand layer (forecast strip) | Done |
| HM-30 | Heatmap cells on Android Auto car surface | Done |
| HM-31 | QA hardware validation | Blocked — requires EAS dev build + Android Auto DHU |
| HM-32 | CarPlay heatmap | Blocked — Apple entitlement required |

## 1. Issue / gap identified

The driver idle map lacked (a) a forecast of upcoming demand, (b) quick-nav chips to nearby hotspots, (c) airport sub-zone awareness, and (d) any heatmap on the Android Auto car surface. Drivers had to rely on phone-only, current-moment demand info with no forward look and no airport context.

## 2. Root cause

These were planned P2/P3 features from the heatmap parity plan. The backend already had `demand_forecast.py` (admin-only) and the `service_areas` table already modeled airport sub-zones, but neither was exposed to drivers. The Android Auto car surface had no demand visualization at all.

## 3. Fix / remediation

### HM-23 — Forecast strip (backend + driver-app)
- Backend (`routes/drivers/profile.py`): inside the v2 heatmap branch, calls `forecast_demand()` for the next 6 hours, normalizes predictions to 0–1 by dividing by the max value in the window. Added to the v2 response as `forecast` array.
- Driver app: new `ForecastStrip` component renders a compact horizontal bar chart with bars colored from the heatmap ramp. Peak hours are highlighted. Shows "Peak at Xp" hint.
- Graceful degradation: forecast failure is caught and logged; the v2 response still works without it.

### HM-22 — Hotspot chips (driver-app only)
- Client-side derivation: `useDemandHeatmap` now computes top-3 cells by weight via `useMemo`.
- New `HotspotChips` component renders tappable flame-icon chips. First chip: "High demand" with border highlight; others: "Busy area" in dimmer style.
- `onPress` animates the map to the hotspot location via `mapRef.current?.animateToRegion`.

### HM-21 — Airport sub-zones (backend + driver-app)
- Backend (`routes/service_areas.py`): new `GET /service-areas/{area_id}/airport-zones` endpoint returns active airport child zones with polygon, name, and fee data. Only exposes `id`, `name`, `is_airport`, `airport_fee`, `polygon` — no admin fields leak.
- Driver app: new `useAirportZones` hook fetches zones when driver is online, includes a client-side ray-casting point-in-polygon check. Dashboard renders blue dashed `<Polygon>` overlays for all airport zones and shows an airplane-icon "Airport Zone" chip when the driver is within one.

### HM-30 — Android Auto heatmap (driver-app only)
- `CarMapSurface` now calls `useDemandHeatmap` independently and renders polygon cells on the car head-unit map during idle state. Uses hardcoded dark-theme ramp (car surface is always dark) and a lower polygon cap (80 vs 200 on phone) for head-unit performance.

## 4. Risk & impact on existing functionality

### Blast radius: low — additive features gated behind v2 flag

- **Forecast** (`profile.py`): Only runs inside the `driver_heatmap_v2_enabled` branch. Failure is caught. Other readers of `profile.py`'s heatmap endpoint see no change unless v2 is enabled. The `demand_forecast` module was admin-only; it's now also called from the driver endpoint, but is a pure read with no side effects.
- **Hotspots**: Derived entirely client-side from existing `cells` array. No new API call. No store mutation.
- **Airport zones**: New endpoint only. Reads `service_areas` table with a filter on `parent_service_area_id + is_airport + is_active` — same query pattern the admin dashboard already uses. No writes. The `useAirportZones` hook runs on a separate fetch cycle from the heatmap; fetches clear on offline/unmount.
- **Android Auto**: `CarMapSurface` adds a second `useDemandHeatmap` instance. Backend Redis cache serves the second fetch nearly free. The `useAuthStore` import is new to carSurface but is a standard shared store with no provider dependency.

### Interaction with background loops / state machine / money

None. All changes are read-only visualization. No ride state transitions, no wallet deltas, no Stripe interactions. The forecast reads from `demand_forecast.py` which queries historical ride counts — pure reads.

### Other consumers of modified files

| File | Other consumers | Impact |
|---|---|---|
| `routes/drivers/profile.py` | Driver status, driver profile — separate endpoints in same file | None — forecast code is inside the v2 heatmap branch only |
| `routes/service_areas.py` | Rider app service-area picker, admin CRUD | None — new endpoint added, existing endpoints unchanged |
| `hooks/useDemandHeatmap.ts` | Phone dashboard (index.tsx) | Return shape extended with `forecast` and `hotspots` — backwards compatible (new optional fields) |
| `lib/androidAuto/carSurface.tsx` | Android Auto register.ts | Added heatmap cells; existing route/marker/card rendering unchanged |

## 5. User-experience effect

- **Driver (phone, idle)**: Sees forecast strip at top of idle map showing next 6 hours of demand. Sees hotspot chips at bottom with top-3 busy areas (tappable to navigate). Sees blue dashed airport zone polygons and an "Airport Zone" chip with zone name when inside one.
- **Driver (Android Auto, idle)**: Sees demand heatmap cells on the car head-unit map matching the phone's visual treatment. Cells disappear when a ride starts.
- **Rider / Admin / Corporate**: No change.
- **Visibility mid-session**: All features only render during idle state. A driver mid-ride sees no change. A driver who goes online with v2 enabled sees new UI elements appear on their idle map.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/routes/drivers/profile.py` | Added forecast integration in v2 branch | HM-23 backend |
| `backend/routes/service_areas.py` | Added `_AIRPORT_FIELDS`, `_project_airport()`, `get_airport_zones()` endpoint | HM-21 backend |
| `backend/tests/test_service_areas_public.py` | Added `TestAirportZones` class with 5 tests | HM-21 test coverage |
| `backend/tests/test_drivers_shared_status_profile_coverage.py` | Added 2 forecast tests to `TestGetDemandHeatmapV2` | HM-23 test coverage |
| `driver-app/hooks/useDemandHeatmap.ts` | Added `ForecastEntry`, `Hotspot` types; `forecast` state; `hotspots` useMemo | HM-23 + HM-22 client |
| `driver-app/hooks/useAirportZones.ts` | New hook: fetch airport zones + point-in-polygon check | HM-21 client |
| `driver-app/components/dashboard/ForecastStrip.tsx` | New component: 6-bar demand forecast chart | HM-23 UI |
| `driver-app/components/dashboard/HotspotChips.tsx` | New component: tappable hotspot chip row | HM-22 UI |
| `driver-app/components/dashboard/index.ts` | Added exports for ForecastStrip, HotspotChips | Barrel file |
| `driver-app/app/driver/(tabs)/index.tsx` | Wired forecast, hotspots, airport zones into dashboard | HM-21/22/23 integration |
| `driver-app/lib/androidAuto/carSurface.tsx` | Added heatmap cell rendering with dark-theme ramp | HM-30 |
| `driver-app/i18n/en.json` | Added forecast, hotspot, airport i18n keys | Localization |

## 7. Rollback plan

All features are gated behind the existing `driver_heatmap_v2_enabled` app_settings flag (default off). To roll back:
- **Forecast + Hotspots + Airport zones on phone**: Disable `driver_heatmap_v2_enabled` in admin dashboard → all v2 features disappear immediately, no redeploy needed.
- **Android Auto heatmap cells**: The `useDemandHeatmap` hook returns empty cells when heatmap is disabled, so the same flag gates the car surface too.
- **Airport zones endpoint**: The endpoint exists unconditionally but the driver app only calls it when online. Removing the endpoint would require a code revert, but the endpoint is a pure read with no side effects.

## 8. Verification performed

- `tsc --noEmit` passes for driver-app (zero errors)
- 16/16 `test_service_areas_public.py` tests pass (11 existing + 5 new airport zone tests)
- 15/15 heatmap tests pass in `test_drivers_shared_status_profile_coverage.py` (including 2 new forecast tests)
- 59/59 Android Auto tests pass (`lib/androidAuto/__tests__/`)
- Pre-commit security hook passes on all commits

## 9. What was NOT verified

- **No visual regression tooling exists in this repo** — all UI changes (ForecastStrip, HotspotChips, airport zone polygons, car surface heatmap) were reasoned about and type-checked, not screenshotted. Standing gap per ACTION_ITEMS.md.
- **Android Auto car surface rendering is unproven on hardware** — validated at JS level only. Requires EAS dev build + Android Auto DHU for on-surface confirmation (HM-31).
- **Airport zone point-in-polygon accuracy** not tested with real airport polygons — the ray-casting algorithm is standard but edge cases (vertices on boundary, very complex polygons) haven't been exercised with production data.
- **Dual useDemandHeatmap instance** (phone + car surface): no integration test verifying that both fetch independently without interference. Relies on hook isolation guarantees.
- **Not tested against live Supabase** — all backend tests use mocked `db_supabase`.
