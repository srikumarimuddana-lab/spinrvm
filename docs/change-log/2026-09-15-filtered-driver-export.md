# Export the selected driver list

## Scope and design

The Drivers page currently ignores its selected filters when exporting. Export
must use the same search, status, area, vehicle, onboarding, legacy, pre-launch,
photo and dormancy criteria as the list, across pages, with the same sort.
The date controls currently affect statistics only and remain statistics-only.
No database migration is required.

Implementation is split into commits of at most three files:
1. Extract shared raw driver selection; verify existing filter tests.
2. Apply shared selection to the export with paging and tests.
3. Send one shared filter object from list and export; verify client behavior.
4. Record final build, regression and review results here.

Sharing the existing query wins over duplicating filter construction: it keeps
cross-table search and special legacy/photo rules consistent. Exporting only
the browser's current rows would omit other matching pages.

## Step 1: shared query

`backend/routes/admin/drivers.py` moves selection into `_query_driver_rows`,
leaving the public list signature, enrichment, deduplication and filters intact.
Only the list calls it in this commit. The optional projection lets export
request its existing safe columns later. The POST driver search also reaches
this selection through `admin_get_drivers`; no other query caller is changed.
`backend/tests/test_admin_extended.py` corrects a stale sort test to use the real
`total_rides` column; `total_earnings` was already removed from the sort map.

Before: list builds filters, fetches, enriches. After: list delegates the same
filter/fetch block to the helper, then performs unchanged enrichment. No user
experience change in this extraction. Rollback is a backend code revert and
redeploy; there are no schema/data mutations. Test/build results for subsequent
steps will be recorded below before the PR update.

Step 1 verification: 144 existing search/list/legacy/dormancy/batching tests pass;
independent review found no blocker. No production or browser check in this step.

## Step 2: filtered export

`backend/routes/admin/rides.py` accepts the same list criteria and calls shared
selection in pages ordered by unique driver ID. It then sorts by the selected
display column. The default export cap rises from 1,000 to 10,000. An explicit
`limit` is now a maximum allowed match count: exceeding it returns 413 with a
request to narrow filters, rather than producing a truncated file. No matches
returns an empty export. CSV fields, admin auth, masking and audit remain intact.

Tests in `backend/tests/test_admin_rides_read_endpoints_coverage.py` cover status
plus service area, vehicle/online, search, photo, legacy/onboarding/pre-launch,
dormancy, display sort, no matches, multiple pages and exact/overflow caps.
The 11 initial new cases failed against the previous export. After implementation,
38 focused export/helper/list tests pass; the paging contract was also checked.
Independent review found no blocker. Offset paging is not a snapshot: concurrent
inserts/deletes can affect results. The list's existing capped photo-user lookup
and potentially large search/flag ID filters are inherited limitations.

## Step 3: client contract

`admin-dashboard/src/lib/api/content-area.ts` accepts filter options derived from
the existing list API type; it preserves false values and URL-encodes search.
It strips list `limit`/`offset` so a visible page cannot truncate an export.
`admin-dashboard/src/lib/__tests__/api.test.ts` tests the actual fetch URL.
The new contract test failed before implementation, then all 15 API tests passed.
The page remains unchanged in this commit, so its selected filters are not yet
sent. Final build and page-level checks follow in the next step.

## Step 4: page integration and final verification

`admin-dashboard/src/app/dashboard/drivers/page.tsx` builds one memoized filter
object for the list and Export. Only the list adds pagination. Export replaces
the debounced search with the current trimmed input, so immediate typing or
clearing cannot download the previous search. Clearing other filters also
updates the next export. Internal admins see the change on their next click;
the layout, CSV fields and other application surfaces do not change.

Before/after behavior:
```typescript
// Before: export ignores selected filters.
await exportDrivers();
// After: same filters as the list, with current search at click time.
await exportDrivers(exportFilters);
```

`page.export.test.tsx` exercises the real page callbacks with a stubbed table:
Active + Saskatoon, page navigation, clearing filters, and immediate search
typing/clearing. Both the missing filter wiring and stale-search tests failed
before their fixes. It verifies parent behavior, not layout or real dropdowns.
`admin-dashboard/e2e/drivers.spec.ts` now checks actual filter controls, export
query parameters and CSV contents instead of merely clicking Export.

Final checks:
- 164 backend search/list/legacy/dormancy/batching/export tests passed.
- 16 client API/page-component tests passed.
- `npm run build` completed successfully on the final page code.
- Ruff/diff checks passed. Targeted ESLint completed with zero errors and 13
  existing warnings; existing Vite/Starlette/Next/Sentry warnings remain.
- Independent review found no remaining blockers after the stale-search fix.

Not verified: production/staging download, full test suites, or browser/visual
regression. The Chromium download repeatedly timed out, so Playwright failed
before launching a browser. The improved browser test is ready for CI.
The repository has active admin visual-regression tooling including Drivers;
it was not run locally. No visual layout change or baseline update is intended.

Environment limit: `npm ci` failed because the existing lockfile is missing
`magic-string@1.4.1`. Local validation used `npm install --package-lock=false
--ignore-scripts`; manifests/lockfiles were not changed. CI clean-install
reproducibility is therefore not established by this local build.

Rollout: deploy the backend before the dashboard. The old backend ignores
unknown filter query parameters, so deploying the new dashboard first would
still export unfiltered data until the backend update lands. This is an
explicit correction requested by the user; no feature flag is introduced.
Rollback the dashboard first, then the backend, by reverting these commits and
redeploying prior images. There are no schema or live-data mutations to undo.
