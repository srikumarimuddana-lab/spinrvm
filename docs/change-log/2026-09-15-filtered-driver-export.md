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
