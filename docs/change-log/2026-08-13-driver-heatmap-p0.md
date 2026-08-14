# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code |
| Surface(s) | backend, driver-app, shared |
| Domain (Sentry tag) | drivers |
| PR / commit link | `355e10d` (backend), `7496259` (driver-app) |
| Related issue or gap ID | HM-01 through HM-06 (driver heatmap parity plan) |

## 1. Issue / gap identified

Six gaps in the driver heatmap feature:

1. **Privacy (HM-01)**: Raw rider pickup lat/lng exposed directly to drivers — no spatial aggregation or k-anonymity floor.
2. **Noise (HM-02)**: No recency decay — ancient rides weighted equally. Rider-cancelled rides counted as demand.
3. **Staleness (HM-03)**: Single fetch on mount, no refresh lifecycle — data goes stale within minutes.
4. **Brand (HM-04)**: No legend component, no loading/empty/error/stale states, off-brand color ramp.
5. **Platform parity (HM-05)**: Google-only `<Heatmap>` component silently no-ops on iOS Apple Maps.
6. **Performance (HM-06)**: No caching — every driver polls Supabase directly, no observability metrics.

## 2. Root cause

Initial heatmap was an MVP pass-through of raw ride coordinates. No grid aggregation layer, no privacy controls, no cross-platform rendering strategy, and no refresh lifecycle.

## 3. Fix / remediation

**Backend (HM-01/02/06):**
- Grid-based spatial aggregation: floor coordinates to ~445m×414m cells, aggregate ride counts per cell.
- k-anonymity floor: suppress cells with fewer than `heatmap_k_floor` (default 3) contributing rides.
- Recency decay: `weight = Σ 0.5^(age_days / 3.0)` — 3-day half-life, excludes `cancelled` rides.
- Redis cache: 60s TTL per area, in-memory fallback when Redis unavailable.
- Prometheus metrics: request counter (hit/miss), build duration histogram, cells-suppressed counter.
- Migration 307: composite index on `rides(service_area_id, created_at DESC)`.

**Driver-app (HM-03/04/05):**
- `useDemandHeatmap` hook: 90s refresh interval (server-driven, ±10% jitter), pauses on non-idle/offline, resumes on return, AppState foreground re-fetch, stale detection at 5min, 3-consecutive-error threshold.
- `DemandLegend` component: 5 states (loading shimmer, empty, error, stale, ready) with brand ramp swatches, Quiet/Busy labels, info modal with contractor-safe copy.
- `HeatmapCells` component: `<Polygon>` from react-native-maps (works on both iOS+Android), viewport culling, 200-polygon cap, weight-sorted rendering.
- Brand-red sequential 5-step ramp in light and dark theme.
- i18n keys for all heatmap strings.

## 4. Risk & impact on existing functionality

**Backend endpoint (`GET /api/drivers/demand-heatmap`):**
- Response shape unchanged: `{enabled, points, total_rides, refresh_seconds, generated_at}`. Existing driver-app versions that only read `points` and `enabled` are fully compatible.
- `refresh_seconds` is new but additive — old clients ignore it.
- Reads `rides` table (read-only query, no writes) with new index — no interaction with ride state machine or dispatch.
- Redis cache is opportunistic — falls back to in-memory on Redis failure.
- No interaction with background loops, money/wallet, or Stripe.
- Blast radius: **isolated** — only the heatmap endpoint and driver dashboard consume this.

**Driver-app dashboard:**
- Removed `Heatmap` import from react-native-maps (Google-only, was silently no-op on iOS).
- Added `HeatmapCells` (Polygon-based) and `DemandLegend` in the same render position.
- `useDemandHeatmap` replaces the old single-fetch `useEffect` — same endpoint, same auth, but now with polling.
- Other dashboard components (`ActiveRidePanel`, `TripCompletedPanel`, `DriverIdlePanel`, `MapControls`, `DriverTopBar`) are untouched.
- `quests.tsx` updated to use `ThemeColorKey` (filters `heatmapRamp` tuple from string color union) — no behavioral change, only type narrowing.

**Blast-radius grep results:**
- `heatmapRamp` — only consumed by `HeatmapCells.tsx` and `DemandLegend.tsx` (new files).
- `ThemeColorKey` — consumed by `quests.tsx` (2 occurrences, replacing `keyof ThemeColors`).
- `useDemandHeatmap` — consumed only by `driver/(tabs)/index.tsx`.
- `DemandLegend` / `HeatmapCells` — exported from `components/dashboard/index.ts`, consumed only by dashboard.

## 5. User-experience effect

- **Driver sees**: demand heatmap now renders on iOS (was blank), refreshes every ~90s while idle, has a legend pill showing Quiet↔Busy gradient, and info button explaining the data. Stale/error states show warning indicators instead of stale data.
- **Visible mid-session**: Yes — a driver currently online and idle will see the legend appear and the heatmap refresh periodically. This is additive UX, not a change to existing behavior (the old heatmap was broken on iOS and stale everywhere).
- **Copy change**: New i18n strings added (`heatmap.legend.*`, `heatmap.info.*`, `heatmap.stale`, `heatmap.empty`, `heatmap.unavailable`). All contractor-safe ("Where you drive is always your choice").
- **Rider / corporate admin / internal admin**: No visible change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/profile.py` | Rewrote `get_demand_heatmap()` with grid aggregation, k-floor, decay, cache, metrics | HM-01/02/06 |
| `backend/migrations/307_rides_area_created_idx.sql` | New composite index | Query performance for heatmap |
| `backend/tests/test_drivers_shared_status_profile_coverage.py` | 6 new heatmap tests + helpers | Coverage for aggregation, k-floor, decay, cache |
| `shared/theme/index.ts` | Added `heatmapRamp` to ThemeColors, `ThemeColorKey` utility type | HM-04 brand ramp + type safety |
| `driver-app/hooks/useDemandHeatmap.ts` | New hook: refresh lifecycle, stale detection, error resilience | HM-03 |
| `driver-app/components/dashboard/DemandLegend.tsx` | New legend pill with 5 states | HM-04 |
| `driver-app/components/dashboard/HeatmapCells.tsx` | New Polygon-based cell renderer | HM-05 |
| `driver-app/components/dashboard/index.ts` | Added exports for DemandLegend, HeatmapCells | Barrel file |
| `driver-app/i18n/en.json` | Added heatmap i18n keys | HM-04 |
| `driver-app/app/driver/(tabs)/index.tsx` | Replaced Heatmap+single-fetch with HeatmapCells+useDemandHeatmap+DemandLegend | HM-03/04/05 integration |
| `driver-app/app/driver/quests.tsx` | `keyof ThemeColors` → `ThemeColorKey` (2 occurrences) | Type fix after heatmapRamp addition |

## 7. Before / after

```tsx
// Before — driver/(tabs)/index.tsx
import { Heatmap } from 'react-native-maps';
// Single fetch on mount, no refresh, Google-only
const [heatmapPoints, setHeatmapPoints] = useState([]);
useEffect(() => { fetchHeatmap().then(setHeatmapPoints); }, []);
// ...
{heatmapPoints.length > 0 && <Heatmap points={heatmapPoints} radius={40} />}
```

```tsx
// After — driver/(tabs)/index.tsx
import { HeatmapCells, DemandLegend } from '../../components/dashboard';
import { useDemandHeatmap } from '../../hooks/useDemandHeatmap';
// Hook manages refresh lifecycle, stale detection, error resilience
const { cells: heatmapCells, status: heatmapStatus, visible: heatmapVisible } =
  useDemandHeatmap(rideState, isOnline);
// ...
<HeatmapCells cells={heatmapCells} region={null} />
{rideState === 'idle' && heatmapVisible && <DemandLegend status={heatmapStatus} />}
```

## 8. Rollback plan

- **Feature flag**: Set `heatmap_enabled` to `false` in `app_settings` DB table — endpoint returns `{enabled: false}` immediately, driver-app hides legend and cells. No redeploy needed.
- **Backend**: The aggregation logic only reads the `rides` table (no writes). Rolling back the code is safe — no data-level remediation needed.
- **Migration 307**: The index is additive (`CREATE INDEX CONCURRENTLY IF NOT EXISTS`). Rollback: `DROP INDEX IF EXISTS idx_rides_area_created;` — harmless, only affects query performance.
- **Driver-app**: If the OTA update causes issues, the `heatmap_enabled` flag disables the feature server-side. Old app versions that cached the previous bundle will still work (endpoint shape unchanged).

## 9. Verification performed

- [x] Automated tests run: 8 backend heatmap unit tests pass (aggregation, k-floor, suppression, missing coords, recency decay, cache hit, empty area, disabled)
- [x] TypeScript type check: `npx tsc --noEmit` passes clean for driver-app
- [x] Blast-radius grep: searched all importers of `ThemeColors`, `heatmapRamp`, `Heatmap` from react-native-maps, `useDemandHeatmap`, `DemandLegend`, `HeatmapCells`
- [x] Reviewed against CLAUDE.md: PIPEDA (no raw coordinates exposed — grid centroids only), observability (Prometheus metrics added), contractor-safe copy (descriptive not imperative)
- [x] Feature-flagged: `heatmap_enabled` in `app_settings` controls the entire feature

## 10. What was NOT verified

- No visual regression tooling exists in this repo — DemandLegend and HeatmapCells rendering were reasoned about, not screenshotted. Standing gap.
- Not tested against live Supabase — backend tests use `mock_supabase_client` fixtures only.
- Not tested on a physical iOS device — Polygon cross-platform claim is based on react-native-maps documentation, not runtime verification.
- i18n keys added for English only — no French/other locale strings added (no existing l10n pipeline in this repo beyond the en.json file).
- No integration/e2e test for the full refresh lifecycle (hook + network + re-render cycle).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`heatmap_enabled` flag + index drop SQL)
- [x] Blast radius is stated (isolated — heatmap endpoint + driver dashboard only)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
