# Admin Dashboard Audit — Phase 6: Performance, UX & Accessibility

**Date:** 2026-04-26

---

## 1. Backend Performance — N+1 and Unbounded Queries

### N+1: `GET /analytics/driver-acceptance` (analytics.py:128)

```python
for driver in drivers:          # up to 500 drivers
    period_rides = await db.get_rows("rides", {"driver_id": driver_id, ...}, limit=500)
    user = await db.find_one("users", {"id": driver.get("user_id")})
```

This loop issues **2 sequential Supabase queries per driver** — up to 1,000 round trips for a 500-driver fleet. At the target P95 fare-calc latency of 300 ms and a per-query baseline of ~10 ms (PostgREST), this endpoint takes **~10 s** before any network variance. It would breach the analytics dashboard's usability threshold before the fleet reaches ~100 drivers.

**Finding F-48:** `GET /analytics/driver-acceptance` has an N+1 query pattern (2 DB calls per driver × 500 drivers = 1,000 DB round trips). Should be replaced with a single aggregating Postgres query or a join-and-group in PostgREST.

### Stats endpoint fires 14 sequential DB calls (`rides.py` admin stats handler)

The daily-chart section of the stats endpoint calls `get_ride_count_by_date_range()` **14 times in a loop** (once per day for the last 14 days), followed by two `limit=10000` full-row fetches. On a busy day, the two `limit=10000` fetches alone pull up to 20,000 rows into memory just to compute a revenue sum.

**Finding F-49:** Admin stats endpoint makes 14 sequential DB calls for the daily chart, plus 2 unbounded `limit=10000` row fetches for revenue aggregation. Should use a `date_trunc` GROUP BY query or Postgres function. Revenue totals should be computed with a `SUM()` aggregate, not a Python-side `sum(float(...))` loop.

### Unbounded fetches across admin routes

| File | Query | Limit |
|---|---|---|
| `maintenance.py:47, 59` | GPS history fetch for cleanup | 100,000 |
| `maintenance.py:135, 171` | Rollup stats, GPS join | 100,000 |
| `rides.py:562, 620` | Heatmap, driver earnings map | 10,000 |
| `rides.py:738` | All payouts (no date filter) | 10,000 |
| `support.py:71` | All disputes | 10,000 |
| `faqs.py:108, 113, 118` | All users / riders / drivers (for notify target) | 10,000 each |
| `analytics.py:219` | Overview rides | 10,000 |

The CLAUDE.md SLA target for fare calc is 300 ms. None of these admin endpoints have SLA targets defined, but unbounded fetches grow linearly with data volume. Several (especially `GET /payouts` and `GET /disputes`) have no date-range filter at all.

---

## 2. Missing Response Caching on Analytics Endpoints

`GET /analytics/overview`, `GET /analytics/driver-acceptance`, and `GET /analytics/cancellation-reasons` each recompute expensive aggregations on every request. The surge pricing data already uses Redis for real-time values, but no analytics result is cached.

At 10 concurrent dashboard sessions all loading the analytics page, each session fires 3–4 uncached analytics calls, creating 30–40 parallel multi-second DB queries.

**Finding F-50:** Analytics endpoints (`/analytics/overview`, `/analytics/driver-acceptance`, `/analytics/cancellation-reasons`) have no response caching. A Redis TTL of 60–300 s would eliminate the N+1 and reduce peak DB load to one aggregation per window.

---

## 3. Frontend — Large Datasets Loaded Into Browser

The `rides` page calls `GET /rides/list` with `limit=10000` — the full paginated list loads 10,000 ride rows into the browser's JavaScript heap before any client-side filter is applied. With the 47-field ride schema, this transfers several MB per load.

Similarly the analytics `/overview` endpoint fetches up to 10,000 ride rows server-side, processes them in Python, then returns a much smaller JSON response — the Python-side aggregation is correct, but the upstream DB fetch is the bottleneck.

The `ride-list.tsx` component has filter state (area, status, date range, search) applied entirely client-side after the full dataset loads. For large deployments the page will be slow to interact with even after load.

---

## 4. Float Arithmetic in Display Paths

(Cross-reference from F-34) Confirmed in Phase 6 context: `rides.py:334–343` uses `sum(float(r.get("total_fare") or 0) for r in ...)` in the stats endpoint. This produces float-accumulated sums that are then rounded with `round(..., 2)` and returned to the frontend. Although this is a display path (not a settlement path), the inconsistency with the Decimal-only convention means copy-paste into a settlement path would silently introduce rounding error.

---

## 5. Accessibility (WCAG 2.1 AA)

### ARIA attribute coverage

A full scan of dashboard pages found:
- 95 aria-label / aria-describedby / role / htmlFor / alt occurrences across ~40 files — averaging ~2.4 per file.
- 139 `<Input>` / `<input>` elements across dashboard pages.

The ratio suggests most form inputs lack associated `<label>` or `aria-label` attributes. For example, filter inputs in rides, users, drivers, and promotions pages use `<Input placeholder="Search...">` without `aria-label`.

**Finding F-51:** Most dashboard form inputs lack explicit ARIA labels — `placeholder` text is used as the only descriptor. Screen readers cannot identify these fields without visible labels. Dashboard does not meet WCAG 2.1 AA SC 1.3.1 (Info and Relationships) for these inputs.

### Color-only status indicators

Status badges and driver/user state indicators rely on color alone to convey meaning:

```tsx
// users/page.tsx:388
user.status === "banned" ? "bg-red-500/15 text-red-600"   // red badge
                         : "bg-green-500/15 text-green-600" // green badge
```

```tsx
// monitoring/toolbar.tsx:61
<span className="h-2 w-2 rounded-full bg-green-500" />  // no text label
```

Badges use text labels in some places (e.g. "Banned" in drivers page) but the colored dot in the monitoring toolbar conveys "connected / disconnected" through color alone.

**Finding F-52:** Several status indicators (monitoring toolbar connection dot, audit log action color dots) use color as the sole differentiator, violating WCAG 2.1 AA SC 1.4.1 (Use of Color). These should include text labels or icons with `aria-label`.

### No `loading.tsx` files — no Suspense boundaries

No `loading.tsx` files exist under `src/app/dashboard/`. The 105 loading state variables in dashboard pages trigger skeleton or spinner UI via component state — but this means the initial page render on navigation is a hard flash (blank page → data). Next.js App Router Suspense loading boundaries would improve perceived performance without requiring component changes.

**Finding F-53:** No `loading.tsx` Suspense boundary files in the dashboard route tree. Page-level data fetches block rendering, causing flash-of-empty-content on navigation.

### Global error boundary

`src/app/error.tsx` exists and displays a "Something Went Wrong" card with reset/navigate options. This is correct. However, it renders `error.message` directly which may surface internal stack traces or database error messages to the admin user.

**Finding F-54:** `error.tsx:22` renders `error.message` directly — internal error messages (DB errors, auth errors) are surfaced to the browser. Should use a generic fallback message in production and log the error to Sentry instead.

---

## 6. Internationalisation (i18n)

The admin dashboard has no i18n framework (`react-i18next`, `next-intl`, `formatjs`, or similar). All UI strings are hardcoded in English. Date formatting uses `new Date().toISOString()` or custom `formatDate()` helpers — no locale-aware `Intl.DateTimeFormat` usage was found.

For the current Saskatchewan market (English-primary), this is an acceptable scope decision. If the platform expands to Francophone Saskatchewan or other Canadian markets, a full i18n refactor would be required.

**Observation:** No i18n framework. Acceptable for the current market scope but documented here for future reference.

---

## 7. Phase 6 New Findings

| ID | Finding | Severity |
|---|---|---|
| F-48 | `GET /analytics/driver-acceptance` N+1: up to 1,000 DB round trips for 500-driver fleet | MEDIUM |
| F-49 | Admin stats endpoint: 14 sequential DB calls for daily chart + 2 unbounded limit=10000 revenue fetches | MEDIUM |
| F-50 | Analytics endpoints have no response caching — concurrent sessions create multiplicative DB load | LOW |
| F-51 | Most form inputs lack `aria-label` / `<label>` — WCAG 2.1 AA SC 1.3.1 violation | LOW |
| F-52 | Color-only status indicators (monitoring toolbar dot, audit log action dots) — WCAG 2.1 AA SC 1.4.1 | LOW |
| F-53 | No `loading.tsx` Suspense boundaries — flash-of-empty-content on page navigation | LOW |
| F-54 | `error.tsx` renders `error.message` directly — may surface internal error details in browser | LOW |
