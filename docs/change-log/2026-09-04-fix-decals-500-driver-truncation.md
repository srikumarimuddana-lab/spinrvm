# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (session 01Sspqro7zzjKdTbUh6D61wQ), design-audit follow-up |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin, drivers |
| PR / commit link | branch `claude/fix-decals-500-truncation` |
| Related issue or gap ID | Found during PR #4947's pagination investigation, filed as a separate follow-up task |

## 1. Issue / gap identified

`admin-dashboard/src/app/dashboard/drivers/decals/page.tsx` ("Welcome Letters" — the tool ops uses to generate/track welcome-letter decals for drivers) fetched drivers with `getDrivers({ limit: 500, ...opts })` — a single, hard-capped request. The backend (`routes/admin/drivers.py`'s `admin_get_drivers`) caps a single page at `le=500` and fully supports real `offset`-based pagination, but the frontend never asked for a second page. If the driver count matching the current Status/Area filter exceeded 500, drivers past the 500th were silently invisible — no error, no warning, no indication anything was missing.

This wasn't just a display gap: the page's client-side "PAGE_SIZE=25" pagination, CSV export (`handleExport`), and bulk welcome-letter generation ("select all → Download PDF (N)") all operate on the same `drivers`/`filtered` array — so CSV exports and bulk-generate runs were silently incomplete too, not just the visible table.

## 2. Root cause

The 500-cap was presumably a "good enough for now" default from whenever this page was first written, before Spinr's driver count grew large enough for any single filter combination (especially "All Statuses" + "All Areas") to plausibly exceed it. The backend already had proper offset pagination (`Query(0, ge=0)`) built for exactly this case — the frontend just never looped to use it.

## 3. Fix / remediation

Replaced the single capped fetch with a loop that pages through the backend using its existing `offset` support, 500 rows per request (the backend's own per-request maximum), until a short page confirms no more rows exist. A safety ceiling of 20 pages (10,000 drivers per filter combination) prevents a runaway loop against a misbehaving backend; if that ceiling is ever hit, the admin now sees an explicit destructive-variant toast ("Driver list may be incomplete... narrow the Status or Area filter") instead of silent data loss — the same "detect and disclose truncation" idea the task's option 2 described, kept as a backstop even though option 1 (full server-side pagination) is the primary fix.

No backend change was needed — `admin_get_drivers` already supported everything required.

## 4. Risk & impact on existing functionality

- Blast radius: `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx` only. No other file imports this page's internals (Next.js route file); `getDrivers` itself (in `src/lib/api/drivers.ts`) is unchanged — this fix only changes how this one page's `load()` calls it.
- For any filter combination currently under 500 drivers (the common case today), behavior is identical: the loop's first fetch returns fewer than 500 rows, the loop exits immediately, one request total — same as before.
- For a filter combination at or above 500 drivers, the page now correctly fetches all of them (up to the 10,000-row safety ceiling) instead of silently dropping the rest. This is the intended fix, not a regression risk.
- Extra network requests only occur when a filter combination genuinely has ≥500 matching drivers — bounded by the 20-page ceiling, so no unbounded request storm is possible even in a pathological case.

## 5. User-experience effect

Admin-facing (ops staff generating/tracking welcome letters), not rider/driver-facing. Previously: an admin querying "All Statuses" + "All Areas" (or any large-enough filter combination) would see an apparently-complete list, CSV export, and bulk-generate run that was silently missing drivers past the 500th — with zero indication anything was wrong. Now: the full matching set loads (or, in the extreme edge case, the admin gets an explicit warning instead of silent gaps). This is a real, visible-when-it-matters change from "silently wrong" to "correct or explicitly caveated" — worth calling out as more than cosmetic, since decals are a physical/compliance artifact tied to real drivers.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx` | `load()`'s single capped `getDrivers({ limit: 500 })` call replaced with an offset-paginated fetch loop (500/page, up to 20 pages) plus a truncation-warning toast as a backstop | The single-page fetch silently dropped drivers past the 500th; CSV export and bulk-generate shared the same truncated data |

## 7. Before / after

```tsx
// before
const load = useCallback(async () => {
    try {
        setRefreshing(true);
        const opts: Record<string, any> = { limit: 500 };
        if (driverStatus !== "all") opts.status = driverStatus;
        if (serviceAreaId !== "all") opts.service_area_id = serviceAreaId;
        const res = await getDrivers(opts);
        const list = Array.isArray(res) ? res : (res as any)?.drivers || [];
        setDrivers(list);
    } catch (e: any) {
        toast({ title: "Failed to load drivers", description: e?.message, variant: "destructive" });
    } finally {
        setLoading(false);
        setRefreshing(false);
    }
}, [toast, driverStatus, serviceAreaId]);

// after
const load = useCallback(async () => {
    try {
        setRefreshing(true);
        const opts: Record<string, any> = {};
        if (driverStatus !== "all") opts.status = driverStatus;
        if (serviceAreaId !== "all") opts.service_area_id = serviceAreaId;

        const FETCH_PAGE_SIZE = 500;
        const MAX_FETCH_PAGES = 20;
        let all: any[] = [];
        let offset = 0;
        let truncated = false;
        for (let i = 0; i < MAX_FETCH_PAGES; i++) {
            const res = await getDrivers({ ...opts, limit: FETCH_PAGE_SIZE, offset });
            const batch = Array.isArray(res) ? res : (res as any)?.drivers || [];
            all = all.concat(batch);
            if (batch.length < FETCH_PAGE_SIZE) break;
            offset += FETCH_PAGE_SIZE;
            if (i === MAX_FETCH_PAGES - 1) truncated = true;
        }
        setDrivers(all);
        if (truncated) {
            toast({
                title: "Driver list may be incomplete",
                description: `Loaded the first ${all.length.toLocaleString()} drivers matching your filters — narrow the Status or Area filter to see the rest.`,
                variant: "destructive",
            });
        }
    } catch (e: any) {
        toast({ title: "Failed to load drivers", description: e?.message, variant: "destructive" });
    } finally {
        setLoading(false);
        setRefreshing(false);
    }
}, [toast, driverStatus, serviceAreaId]);
```

## 8. Concrete before/after scenario

- **300 drivers matching "Active" + "Regina"** (well under 500): before and after are identical — one `getDrivers({ limit: 500, offset: 0, status: 'active', service_area_id: 'regina-id' })` call, all 300 returned, loop exits after page 1 either way. No behavior change for the common case.
- **600 drivers matching "All Statuses" + "Saskatoon"** (over the old 500 cap): **before**, `getDrivers({ limit: 500, status omitted, service_area_id: 'saskatoon-id' })` returns exactly 500 drivers; drivers 501–600 are silently absent from the table, CSV export, and any "select all → bulk generate" run — with no toast, no indicator, nothing. **After**, the loop's first call returns 500 (a full page, so it continues), the second call (`offset: 500`) returns the remaining 100 (a short page, so it stops) — all 600 drivers now present in `drivers`, correctly reflected everywhere the array is used.
- **Pathological 12,000-driver match** (only plausible with a badly broad filter far beyond realistic fleet size): the loop hits `MAX_FETCH_PAGES` (20 × 500 = 10,000) and stops, setting `truncated = true` — the admin sees the destructive-variant toast naming the exact count loaded and telling them to narrow the filter, rather than silent gaps.

## 9. Rollback plan

`git revert` — pure frontend fetch-logic change, no data touched, no migration, no backend change, no Stripe/wallet/ride-state involved.

## 10. Verification performed

- [x] `npx tsc --noEmit` — clean.
- [x] **Real production build**: `npm run build` — succeeded, `/dashboard/drivers/decals` route unaffected in the route list (this specific route isn't in the trimmed log tail shown, but the build's overall success covers it — no route-specific error was raised).
- [x] Full test suite: `npm run test` (vitest) — 59 files / 562 tests, all passing. No existing test file covers this specific page (confirmed via grep for "decals" across `*.test.tsx`/`*.test.ts`), so this is a regression-safety check on the rest of the app, not direct coverage of the fix itself (see §11).
- [x] Confirmed the backend (`routes/admin/drivers.py`'s `admin_get_drivers`) genuinely supports `offset`-based pagination (`Query(0, ge=0)`, `limit` capped at `le=500`) by reading the route signature and its docstring ("Search, filtering, sorting and pagination all happen at the DB so the admin UI operates over the ENTIRE drivers table") — confirming option 1 (real pagination) was achievable rather than assuming and falling back to option 2 (warning-only).
- [x] Traced the three concrete before/after scenarios in §8 by hand against the new loop logic.

## 11. What was NOT verified

- No test file exists for `drivers/decals/page.tsx` at all (before or after this change) — this fix was not accompanied by a new test, which is a real gap. A future pass should add at least one test mocking `getDrivers` to return two pages (500 + partial) and asserting the full merged set lands in state, plus a test for the `MAX_FETCH_PAGES` truncation-warning path. Not added here to keep this fix minimal and focused on the bug itself; flagging honestly rather than skipping the disclosure.
- Did not run this against a real Supabase dataset with genuinely >500 matching drivers (no such dataset exists in this environment) — verified via code tracing (§8) and the backend route's own pagination contract, not empirically against live data.
- No visual-regression baseline exists for `dashboard-drivers-decals` (not one of admin-dashboard's 5 seeded pages) — not applicable regardless, since this is a data-fetching change with no rendered-output difference for the common (<500) case.

## 12. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data involved)
- [x] Blast radius is stated, not assumed (1 file; `getDrivers`/backend route confirmed unchanged and already supporting what this fix needs)
- [x] No silent behavior change to an already-shipped flow — the opposite: this fix removes a *pre-existing* silent behavior (truncation) and either fixes it outright or replaces the silence with an explicit warning
