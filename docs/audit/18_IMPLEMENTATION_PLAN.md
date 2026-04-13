# Phase 1 Implementation Plan — Surge Pricing + Driver Earnings Dashboard

## Context

All 55 security audit issues are resolved (Sprints 1-9 complete). The platform is now ready for feature development. Section 19 of `docs/audit/00_SPINR_FULL_OVERVIEW.md` identifies two **High priority** next features:

1. **Automated Surge Pricing** — Real-time demand/supply based fare multipliers (revenue optimization)
2. **Enhanced Driver Earnings Dashboard** — Native chart-based earnings visualization (driver retention)

This plan covers Phase 1 (MVP) implementations of both features.

---

## Feature 1: Automated Surge Pricing

### What Already Works
- `service_areas.surge_multiplier` field exists and is applied to `distance_fare` + `time_fare` in ride estimates and creation (`backend/routes/rides.py:459-464, 535-537`)
- Admin can manually toggle surge via UI (`admin-dashboard/src/app/dashboard/pricing/_tabs/surge.tsx`)
- Rider app shows "Surge (X.Xx) Applied" in red when `surge_multiplier > 1.0` (`rider-app/app/payment-confirm.tsx:167-172`)
- `surge_pricing` table exists but is unused (`backend/routes/admin/service_areas.py:130-146`)
- Nearby driver query exists in `backend/routes/rides.py:416-444` (in-Python distance calc)
- Fare endpoint `backend/routes/fares.py:80` reads `surge_multiplier` from matched service area

### What's Missing
- No automatic demand/supply calculation
- No background surge recalculation
- No surge history/audit trail
- `RideEstimate` TypeScript interface missing `surge_multiplier` field (`rider-app/store/rideStore.ts:21-33`)
- No rider-facing surge explanation (just "Applied")
- Admin can't distinguish auto vs manual surge

### Implementation

#### Task 1: Backend — Surge Calculation Engine
**New file: `backend/services/surge.py`**

Create a surge calculation service:
- `calculate_surge_for_area(area_id)` — Computes demand/supply ratio
  - **Demand**: Count of rides with `status IN ('searching', 'driver_assigned')` in service area in last 10 minutes
  - **Supply**: Count of drivers with `is_online=True, is_available=True` within service area polygon
  - **Ratio**: `demand / max(supply, 1)`
  - **Tier mapping**: ratio → multiplier (configurable thresholds)
    - `< 0.5` → 1.0x (normal)
    - `0.5 - 0.8` → 1.25x
    - `0.8 - 1.2` → 1.5x
    - `1.2 - 2.0` → 1.75x
    - `2.0 - 3.0` → 2.0x
    - `> 3.0` → 2.5x (cap)
- `update_all_surges()` — Iterates active service areas, computes surge, updates `service_areas.surge_multiplier` only when `surge_active=True` and no manual override
- `get_surge_status(area_id)` — Returns current surge level + metadata (demand, supply, is_auto)

Uses existing `point_in_polygon` from `geo_utils.py` to check if drivers are within area.
Uses existing `db.rides` and `db.drivers` queries.
All monetary values stay as Decimal through the `_fd()` helper in `fares.py`.

#### Task 2: Backend — Background Surge Update Task
**Modified file: `backend/core/lifespan.py`**

Add background task in `lifespan()`:
```python
from services.surge import update_all_surges
asyncio.create_task(periodic_surge_update())  # every 2 minutes
```

The `periodic_surge_update()` function:
- Runs `update_all_surges()` every 120 seconds
- Logs surge changes via `audit_logger`
- Catches all exceptions to prevent task death

#### Task 3: Backend — Surge History Tracking
**Modified file: `backend/routes/admin/service_areas.py`**

Update `admin_update_surge_pricing` to:
- Set a `manual_override=True` flag on service_area when admin manually sets surge
- Log to `surge_pricing` table with `{area_id, multiplier, source: 'auto'|'manual', demand, supply, timestamp}`

**New endpoint: `GET /admin/surge/status`**
- Returns all service areas with current surge info: `{area_id, name, multiplier, source, demand_count, supply_count, last_updated}`

#### Task 4: Frontend — Add `surge_multiplier` to RideEstimate type
**Modified file: `rider-app/store/rideStore.ts`**

Add to `RideEstimate` interface:
```typescript
surge_multiplier?: number;
```

This eliminates the `(selectedEstimate as any).surge_multiplier` casts in `payment-confirm.tsx`.

#### Task 5: Rider App — Surge Indicator on Ride Options
**Modified file: `rider-app/app/ride-options.tsx`**

When `estimate.surge_multiplier > 1.0`:
- Show a surge badge on the vehicle card (e.g., "1.5x Surge" in orange/red)
- Show a brief explanation: "Fares are higher due to increased demand"

#### Task 6: Admin Dashboard — Auto Surge Visibility
**Modified file: `admin-dashboard/src/app/dashboard/pricing/_tabs/surge.tsx`**

- Show "Auto" or "Manual" badge next to each area's surge multiplier
- Show demand/supply counts when auto surge is active
- Add toggle for "Enable auto surge" (sets `surge_active=True` without manual override)
- Show surge history chart (last 24h) per area

### Files Changed (Surge Pricing)
| File | Action | Purpose |
|------|--------|---------|
| `backend/services/surge.py` | **NEW** | Surge calculation engine |
| `backend/core/lifespan.py` | MODIFY | Add periodic surge update task |
| `backend/routes/admin/service_areas.py` | MODIFY | Surge history + manual override flag + status endpoint |
| `rider-app/store/rideStore.ts` | MODIFY | Add `surge_multiplier` to `RideEstimate` |
| `rider-app/app/ride-options.tsx` | MODIFY | Surge badge on vehicle cards |
| `admin-dashboard/src/app/dashboard/pricing/_tabs/surge.tsx` | MODIFY | Auto/manual badge, demand/supply display |

---

## Feature 2: Enhanced Driver Earnings Dashboard

### What Already Works
- Backend: `GET /drivers/earnings` (period summary), `/earnings/daily` (daily breakdown), `/earnings/trips` (trip list), `/balance`
- `driver_daily_stats` table exists with daily aggregates (`backend/migrations/16_driver_daily_stats.sql`) but NO endpoints expose it
- Current `earnings.tsx` has: hero header, period tabs, 4-stat grid, manual bar chart (7 days), trip list
- driverStore has `EarningsSummary`, `DailyEarning`, `TripEarning` types and fetch actions
- Export and T4A endpoints exist

### What's Missing
- No weekly/monthly aggregation endpoints
- No chart library (current chart is manual `View` + `LinearGradient` bars)
- No comparison with previous period
- No online hours tracking in earnings
- `driver_daily_stats` not used by any endpoint

### Implementation

#### Task 7: Backend — Weekly & Monthly Aggregation Endpoints
**Modified file: `backend/routes/drivers.py`**

**New endpoint: `GET /drivers/earnings/weekly?weeks=4`**
- Query `driver_daily_stats` table grouped by ISO week
- Returns: `[{week_start, week_end, earnings, tips, rides, online_hours, distance_km}]`
- Falls back to computing from `rides` table if `driver_daily_stats` is empty

**New endpoint: `GET /drivers/earnings/monthly?months=6`**
- Query `driver_daily_stats` grouped by month
- Returns: `[{month, year, earnings, tips, rides, online_hours, distance_km}]`
- Same fallback pattern

**New endpoint: `GET /drivers/earnings/comparison`**
- Compares current period vs previous period
- Returns: `{current: {earnings, rides, tips}, previous: {earnings, rides, tips}, change_pct: {earnings, rides, tips}}`
- Period param: `week` (this vs last week), `month` (this vs last month)

#### Task 8: Driver App — Install `react-native-svg`
**Modified file: `driver-app/package.json`**

`react-native-svg` is Expo-compatible (no prebuild needed). Required for proper chart rendering.
```
npx expo install react-native-svg
```

No `react-native-chart-kit` — we'll build lightweight custom SVG charts to keep bundle size small and avoid third-party coupling.

#### Task 9: Driver App — Custom Chart Components
**New file: `driver-app/components/charts/EarningsLineChart.tsx`**

A lightweight SVG line chart component:
- Props: `data: {label: string, value: number}[]`, `height`, `color`, `showArea`
- Renders: SVG path for line, optional gradient fill, axis labels
- Handles zero-data gracefully (shows empty state)

**New file: `driver-app/components/charts/EarningsBarChart.tsx`**

Replaces the manual bar chart in earnings.tsx:
- Props: `data: {label: string, value: number, secondary?: number}[]`, `height`
- SVG-based bars with rounded corners
- Optional secondary series (tips overlay)
- Animated entrance

#### Task 10: Driver App — Enhanced Earnings Screen
**Modified file: `driver-app/app/driver/earnings.tsx`**

Enhancements:
1. **Chart mode toggle** — "Daily" | "Weekly" | "Monthly" tabs above chart
2. **Line chart** for weekly/monthly views (shows trend over time)
3. **Bar chart** remains for daily view (with tips overlay in secondary color)
4. **Comparison banner** — "Up 15% from last week" or "Down 8% from last month" with green/red indicator
5. **Online hours** stat card replacing current "Online Time" (use actual data when available)

#### Task 11: Driver Store — New Actions
**Modified file: `driver-app/store/driverStore.ts`**

New types:
```typescript
interface WeeklyEarning {
  week_start: string;
  week_end: string;
  earnings: number;
  tips: number;
  rides: number;
  online_hours: number;
  distance_km: number;
}

interface MonthlyEarning {
  month: string;
  year: number;
  earnings: number;
  tips: number;
  rides: number;
  online_hours: number;
  distance_km: number;
}

interface EarningsComparison {
  current: { earnings: number; rides: number; tips: number };
  previous: { earnings: number; rides: number; tips: number };
  change_pct: { earnings: number; rides: number; tips: number };
}
```

New state + actions:
- `weeklyEarnings: WeeklyEarning[]` + `fetchWeeklyEarnings(weeks?)`
- `monthlyEarnings: MonthlyEarning[]` + `fetchMonthlyEarnings(months?)`
- `earningsComparison: EarningsComparison | null` + `fetchEarningsComparison(period)`

### Files Changed (Earnings Dashboard)
| File | Action | Purpose |
|------|--------|---------|
| `backend/routes/drivers.py` | MODIFY | Add weekly, monthly, comparison endpoints |
| `driver-app/package.json` | MODIFY | Add `react-native-svg` |
| `driver-app/components/charts/EarningsLineChart.tsx` | **NEW** | SVG line chart component |
| `driver-app/components/charts/EarningsBarChart.tsx` | **NEW** | SVG bar chart component |
| `driver-app/app/driver/earnings.tsx` | MODIFY | Enhanced UI with chart mode toggle, comparison |
| `driver-app/store/driverStore.ts` | MODIFY | New types + weekly/monthly/comparison actions |

---

## Implementation Order

1. **Backend surge engine** (Task 1) — no dependencies
2. **Backend earnings endpoints** (Task 7) — no dependencies
3. **Surge background task** (Task 2) — depends on Task 1
4. **Surge history + admin endpoint** (Task 3) — depends on Task 1
5. **RideEstimate type fix** (Task 4) — no dependencies
6. **Rider surge badge** (Task 5) — depends on Task 4
7. **Install react-native-svg** (Task 8) — no dependencies
8. **Chart components** (Task 9) — depends on Task 8
9. **Driver store extensions** (Task 11) — depends on Task 7
10. **Enhanced earnings screen** (Task 10) — depends on Tasks 8, 9, 11
11. **Admin surge visibility** (Task 6) — depends on Task 3

---

## Verification

### Surge Pricing
1. Start backend (`cd backend && python -m uvicorn server:app --reload`)
2. Verify `GET /fares/fares?lat=...&lng=...` returns `surge_multiplier` in response
3. Verify background task logs show surge calculations every 2 minutes
4. Verify `GET /api/admin/surge/status` returns area surge metadata
5. Verify admin dashboard shows auto/manual badge
6. Verify rider app shows surge badge on ride options when multiplier > 1.0

### Earnings Dashboard
1. Start backend, verify `GET /drivers/earnings/weekly?weeks=4` returns weekly data
2. Verify `GET /drivers/earnings/monthly?months=6` returns monthly data
3. Verify `GET /drivers/earnings/comparison?period=week` returns comparison
4. Start driver app (`cd driver-app && npx expo start`)
5. Navigate to Earnings screen
6. Verify chart mode toggle switches between daily bars / weekly line / monthly line
7. Verify comparison banner shows correct % change
8. Test with zero data — charts should show empty state gracefully

### Regression
- Existing ride estimate flow still works (no surge regression)
- Existing earnings screen still loads (period tabs, trip list)
- Admin surge manual override still works
- Pre-commit hooks pass on all changed files

---

## Key Reusable Functions
- `geo_utils.point_in_polygon()` — check if driver/ride is within service area (`backend/geo_utils.py`)
- `geo_utils.get_service_area_polygon()` — extract polygon from area doc
- `geo_utils.calculate_distance()` — haversine distance
- `fares._fd()` — Decimal rounding to 2dp (`backend/routes/fares.py:18-20`)
- `rides._d(), _round(), _f()` — Decimal helpers (`backend/routes/rides.py`)
- `audit_logger.log_security_event()` — structured audit logging (`backend/utils/audit_logger.py`)
- `db.get_rows()` — Supabase query with filters (`backend/db_supabase.py`)
- `LinearGradient` from `expo-linear-gradient` — already used in earnings.tsx
- `SpinrConfig.theme.colors` — theme constants (`shared/config/spinr.config.ts`)
