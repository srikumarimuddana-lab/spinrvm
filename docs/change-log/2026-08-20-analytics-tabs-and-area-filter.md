# Change Impact & Risk Log — Analytics tabs + shared service-area filter

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-dashboard-analytics-review-xsjyuk` |
| Related issue or gap ID | Operational Analytics review, P3-area + user request to consolidate tabs |

## 1. Issue / gap identified

Three related gaps on `/dashboard/analytics`:

1. **No service-area filter.** Both backend endpoints the page used already
   accepted `service_area_id` and the API client already carried the
   parameter — the page simply never passed it, so Saskatoon and Regina were
   permanently blended into one number.
2. **Dispatch data scattered.** The real offer-ledger accept/decline/ignore
   rates lived on a separate page (`/dashboard/driver-offers`), and demand
   forecasting on another (`/dashboard/forecast`), each with its own
   independent filter bar. An operator comparing "why are we cancelling" with
   "were drivers even offered these rides" had to change area and date range
   in three places and keep them in sync by hand.
3. **Latent bug found during extraction:** the forecast page did
   `if (fc?.forecast) setForecast(fc.forecast)`. When a fetch failed or an
   area returned no forecast, the *previous* area's forecast stayed on
   screen, attributed to the newly-selected area.

## 2. Root cause

(1) and (2) are omissions, not defects — the page was built before the
service-area dimension and the two sibling pages existed, and was never
revisited. (3) is a guard written to avoid flashing an empty chart that
accidentally made stale data sticky across a filter change.

## 3. Fix / remediation

- One shared filter bar (service area + date range + refresh) on
  `/dashboard/analytics`, threaded into every query on the page. Subtitle
  states the active scope so a filtered view is never mistaken for the whole
  business.
- Extracted the bodies of both sibling pages into shared components,
  `src/components/analytics/driver-offers-panel.tsx` and
  `demand-forecast-panel.tsx`, and added them as Analytics tabs
  ("Dispatch Offers", "Demand Forecast"). The standalone routes now render
  the same components with their own filter bar — one implementation, two
  mount points, so the two views cannot drift.
- Hour/day axis labels now state the bucketing zone (`(Regina time)`,
  `(Regina days)`), read from the `timezone` field migration 350's endpoints
  return.
- Fixed (3): the forecast panel now replaces state unconditionally
  (`setForecast(fc?.forecast ?? [])`).
- Tab row wrapped in `overflow-x-auto` so it scrolls rather than wrapping
  into the content on narrow screens.

## 4. Risk & impact on existing functionality

**Blast radius: admin-dashboard only, three routes.**

Grepped every importer before moving code:
- `getDriverOfferStats` / `getDriverOfferTrends` — only `/dashboard/driver-offers`, now also the panel.
- `getDemandForecast` / `getDemandForecastSummary` — `/dashboard/forecast` and `/dashboard/heatmap`. **`heatmap` was left untouched** — it calls the API directly and does not import the page body, so extracting the forecast page does not affect it.
- `getAnalyticsOverview` — `/dashboard/analytics` only. Signature gained an optional second argument; existing single-argument behavior is unchanged.

No backend change in this commit. No ride, payment, auth, corporate, or safety
path is touched. No shared UI primitive was modified — the panels consume
`Card`/`Table`/`Select`/`Pagination` as-is, so no other page inherits a change.

**Risk accepted:** the two standalone pages were rewritten as thin wrappers.
Their rendered markup is byte-identical to before except for the fixes noted
above, but this is a real rewrite of two live-tested screens, verified by
type-check and production build rather than by visual diff (see §10).

**Two sidebar entries now duplicate content available as tabs.** Deliberate —
removing them would break existing bookmarks and muscle memory, and the
underlying component is shared, so there is no divergence risk. Whether to
retire them is a follow-up product decision, not a code one.

## 5. User-experience effect

**Internal admin only.** Nothing rider-, driver-, or corporate-facing; not
visible mid-session to anyone using the apps.

Admins get: a service-area filter on Analytics (default "All service areas",
i.e. today's behavior); four tabs instead of two; timezone-labelled axes; and
on the Forecast page, an empty state instead of a stale other-area forecast
when a fetch returns nothing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/analytics/driver-offers-panel.tsx` | New. Body of the driver-offers page, filters lifted to props | One implementation for page + tab |
| `admin-dashboard/src/components/analytics/demand-forecast-panel.tsx` | New. Body of the forecast page; stale-state bug fixed | One implementation for page + tab |
| `admin-dashboard/src/app/dashboard/driver-offers/page.tsx` | Reduced to filter bar + panel | Remove duplication |
| `admin-dashboard/src/app/dashboard/forecast/page.tsx` | Reduced to filter bar + panel | Remove duplication |
| `admin-dashboard/src/app/dashboard/analytics/page.tsx` | Shared area filter; two new tabs; tz axis labels; scrollable tab row | Consolidate + scope |
| `admin-dashboard/src/lib/api/analytics-payouts.ts` | `getAnalyticsOverview` takes optional `serviceAreaId` | Reach migration 350's new scope |

## 7. Before / after

```tsx
// Before — a failed or empty fetch left the previous area's forecast on screen
if (fc?.forecast) setForecast(fc.forecast);
if (summ) setSummary(summ);
```

```tsx
// After — replace unconditionally; an area with no forecast shows empty,
// not the last area's numbers
setForecast(fc?.forecast ?? []);
setSummary(summ ?? null);
```

## 8. Rollback plan

`git revert` is sufficient and complete. Frontend-only, no migration, no
schema change, no write path, no persisted state, no live data touched. The
two extracted panels are new files; reverting restores the original
self-contained pages.

No feature flag: the change is internal-admin-only, and the default filter
state ("All service areas") reproduces exactly the pre-change numbers, so the
risky part of the diff is already inert until an admin actively selects an area.

## 9. Verification performed

- [x] **Real production build run** — `npm run build`, exit 0, full route table emitted. Not `tsc --noEmit` alone (that was also run separately, exit 0, after each step).
- [x] `npm run lint` — **0 errors**. New warnings are the `react-hooks/set-state-in-effect` class already pervasive in this repo (327 pre-existing warnings; the same pattern appears in `src/components/referral-analytics.tsx` and `src/components/sidebar.tsx`). The `setDriverPage(0)`-on-filter-change effect was kept deliberately: resetting inside each individual handler risks missing a path (notably the debounced search), and the effect is what guarantees page 3 of a 2-page result cannot render empty.
- [x] Blast-radius grep performed — searched `getDriverOfferStats`, `getDriverOfferTrends`, `getDemandForecast`, `getDemandForecastSummary`, `getAnalyticsOverview` across `src/`. Consumers listed in §4; `heatmap` confirmed unaffected.
- [x] Reviewed against CLAUDE.md: no money/state-machine/RLS/PIPEDA surface touched; no PII added to any payload or log.

## 10. What was NOT verified

- **Nothing was rendered or clicked.** No dev server was run and no browser was opened in this session. Two live-tested screens (`/dashboard/driver-offers`, `/dashboard/forecast`) were rewritten as wrappers and are verified only by type-check and a successful production build. **A manual pass over both, plus the two new tabs, is required before merge** — a visually broken layout would compile cleanly.
- **This repo has no visual/snapshot regression tooling for admin-dashboard** (standing gap). The claim that the extracted markup renders identically is a reading of the diff, not a screenshot comparison.
- **No test covers the extraction.** `admin-dashboard/__tests__` has no analytics tests; none were added here. The stale-forecast fix in §7 is therefore unverified by any automated check.
- **The service-area filter was not exercised against real data** — no backend was running. Whether `service_area_id` values from `getServiceAreas()` match the `rides.service_area_id` values the RPC filters on is unverified end-to-end.
- The `is_active !== false && !parent_service_area_id` filter on the area dropdown mirrors the forecast page's existing convention; whether it is the right rule for *this* page's data was assumed, not confirmed with the user.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change — §5 documents the visible changes; default filter state reproduces prior numbers
- [ ] **Open gate: manual render pass over the 2 rewritten pages + 2 new tabs before merge** (see §10)
