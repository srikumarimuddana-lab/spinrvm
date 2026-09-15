# Driver export: bound fleet-sized PostgREST requests

Follow-up: [filtered driver export](2026-09-15-filtered-driver-export.md) extends
this initial fix with shared list filters, complete matching-page retrieval,
and the updated validation/release boundaries.

| Field | Value |
|---|---|
| Date / author | 2026-09-15 / Codex |
| Surface / domain | Backend admin export / admin |
| Issue | Admin driver CSV download fails for a large fleet |

## Issue and root cause

The export returns 503 after Supabase reports HTTP 400 with a plain `Bad Request`
body, wrapped as `JSON could not be generated`. The user enrichment sends all
driver user IDs in one URL. This matches the proxy-limit failure already
documented in `get_rows_batched_in`. A synthetic 927-driver regression constructs
a 36,177-character users URL. The exact outgoing request from the reported
incident was unavailable, so attribution to the users lookup remains inference.

## Fix and alternative

Reuse `get_rows_batched_in` for user and subscription enrichment (150 IDs per
request). Read all subscription pages and reduce to the newest timestamp per
driver; timestamps are normalized to UTC. A POST SQL RPC could avoid URL limits,
but the existing tested helper avoids a migration and changes to shared callers.

## Risk, blast radius, and user experience

Only `admin_export_drivers` changes behavior. Its consumer is
`admin-dashboard/src/app/dashboard/drivers/page.tsx` through `exportDrivers` in
`src/lib/api/content-area.ts`, re-exported by `src/lib/api.ts`. `docs/known-forks.md`
has no sibling for this endpoint. The shared helper, driver listing, ride export,
dispatch, auth, data writes, money flows, and background loops are unchanged.
The admin receives the same CSV fields and order; user enrichment errors still
abort the export. Existing subscription/earnings fallback behavior is unchanged.
VIN/licence masking and the successful-export audit entry remain in place.
No flag is added for this isolated request-sizing crash fix. More small reads
replace two large reads. Subscription history is now fully fetched, so unusually
large histories cost more reads. The existing helper uses offset paging without
a snapshot or explicit order; concurrent history changes remain a consistency
limitation; concurrent history changes were not integration-tested.

| File | Change | Why |
|---|---|---|
| backend/routes/admin/rides.py | Batch export enrichment and reduce latest subscriptions | Avoid oversized URLs and truncated enrichment |
| backend/tests/test_admin_rides_read_endpoints_coverage.py | Fleet-size, pagination, and failed-batch regressions | Protect complete exports and failure behavior |
| This file | Impact and verification record | Document release boundaries |

```python
# Before
await db_supabase.get_rows("users", {"id": {"$in": user_ids}}, ...)
# After
await db_supabase.get_rows_batched_in("users", "id", user_ids, ...)
```

## Verification and rollback

The new fleet tests failed before the fix; the 927-driver case emitted an
oversized URL. After the fix, 16 focused export/helper tests pass, including
151/927-driver fixtures, multi-page subscription history, latest-row selection
across timezone offsets, empty/one-driver exports, and failure of a later user
batch returning 503 without an export-success audit. Ruff and diff checks pass.
One existing Starlette/httpx deprecation warning is emitted by the test harness.
Independent edge-case review performed before commit. No production export,
staging/browser download, full backend suite, or production build was run; no
frontend code changed. Live checks did not exercise the export HTTP path.

Rollback requires reverting this isolated backend commit and redeploying the
previous image; no existing flag controls request batching. There is no schema
or live-data mutation to undo. Keep the PR unmerged until the normal release
review; after deployment, verify the dashboard download and exported row count.
