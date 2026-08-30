# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Live error reported by operator: "Scan failed: an unexpected error occurred" clicking Preview on `/dashboard/drivers/legacy-import`'s "Fix Orphaned Legacy-Linked Accounts" section, after the 2026-08-30 service-area fix (PR #4706) deployed |

## 1. Issue / gap identified

`find_orphaned_legacy_driver_users()` (`backend/services/driver_import_service.py`) issued one raw, unchunked `.in_("user_id", candidate_ids)` query against the `drivers` table with all ~697 candidate UUIDs at once, instead of this same file's own established `_select_in(chunk=200)` convention (used by every other `.in_()` lookup here — `_prefetch_existing`, the driver-history backfill, etc.). This code path had never actually run against real production data before: the operator's only two prior attempts both failed earlier — first at `get_service_area()` (fixed in PR #4706), then this one, the first time execution ever reached this query at real ~700-row scale.

## 2. Root cause

An `.in_()` filter with ~700 UUID values (36 chars each, ~25KB+ of query string) risks rejection by the PostgREST layer's URL-length handling. When that request fails, the backend raises inside a synchronous handler; the frontend's generic error path (`admin-dashboard/src/lib/api/client.ts`) falls back to a non-descriptive message when the response isn't valid JSON — surfacing to the operator as "an unexpected error occurred" with no actionable detail. This is the same class of scale bug (an unchunked `.in_()`/per-row loop only tested at small scale in unit tests, never at real production row counts) that's recurred multiple times across this migration effort.

## 3. Fix / remediation

Replaced the raw `.in_()` call with this file's own `_select_in()` helper (already used everywhere else in the file), which batches in chunks of 200. No behavior change to the function's output — same orphan set, same filtering logic — only how the underlying query is issued.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one function, one call site.** Grepped `driver_import_service.py` — `find_orphaned_legacy_driver_users()` has exactly one caller (`backfill_orphaned_legacy_driver_rows()`), which itself has exactly one caller (`routes/admin/legacy_driver_import.py`'s `POST /legacy-drivers/backfill-orphaned` route). Nothing else touches this function.
- **Purely a query-batching change** — `_select_in()` is proven, already used by 5+ other call sites in this same file (`_prefetch_existing`, vehicle-history backfill, etc.), and returns the same shape (`list[dict]`) the raw `.execute().data` did.
- **New regression test proves the fix**: `test_find_orphaned_batches_in_query_at_production_scale` uses a fake Supabase that rejects any `.in_()` call carrying more than 200 values (simulating the real URL-length risk) against 250 candidates — confirmed to fail without the fix (reverted, re-ran, reproduced the exact `RuntimeError`), passes with it restored.
- 46 tests pass across the two affected test files; `ruff check` / `ruff format --check` clean.

## 5. User-experience effect

- **Internal admin only**, and the change is invisible to any rider/driver — this endpoint only reads `users`/`drivers` for a preview report; nothing is written until "Apply fix" is separately clicked (`apply=True`), which is unaffected by this change (it doesn't call this query). Before: Preview failed outright at real production scale. After: Preview should return `scanned: 697` (or whatever the current count is) as expected.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | `find_orphaned_legacy_driver_users()`'s drivers lookup now goes through `_select_in()` instead of a raw unchunked `.in_()` | Avoid a URL-length failure against PostgREST at real (~700-row) production scale |
| `backend/tests/test_legacy_mongo_driver_import_service.py` | New regression test `test_find_orphaned_batches_in_query_at_production_scale` | Lock in the fix; confirmed to fail without it |

## 7. Before / after

```python
# Before — one raw .in_() call with up to ~700 UUIDs
linked = supabase.table("drivers").select("user_id").in_("user_id", candidate_ids).execute().data or []
```

```python
# After — batched via this file's own established convention
linked = _select_in("drivers", "user_id", "user_id", candidate_ids)
```

## 8. Rollback plan

`git-revert-safe` — no data written by this function (read-only query), no schema change, no behavior change to output shape. A revert simply restores the unchunked query.

## 9. Verification performed

- [x] Confirmed the real production candidate count is 697 via direct SQL (`execute_sql`, read-only) — same order of magnitude the operator's failed Preview click was working against.
- [x] Grepped for every caller of `find_orphaned_legacy_driver_users()` and `backfill_orphaned_legacy_driver_rows()` — one caller each, confirmed isolated.
- [x] New regression test confirmed to fail without the fix (reverted the service-file change, re-ran, reproduced the simulated rejection), pass with it restored.
- [x] `pytest tests/test_legacy_mongo_driver_import_service.py tests/test_admin_legacy_driver_import.py` — 46 passed.
- [x] `ruff check` / `ruff format --check` — clean.

## What was NOT verified

- Not reproduced against the real production PostgREST/Supabase endpoint directly (no route-level credentials available in this session, by design — writes and live endpoint calls go through the deployed admin dashboard, not raw credentials in chat). The URL-length failure mode is inferred from the exact deviation from this file's own established `_select_in` convention, the fact this is the first time this exact code path ran at real ~700-row scale, and the generic non-JSON-shaped error message the operator reported — not confirmed via a captured backend stack trace or Sentry event (Sentry MCP access is not authorized in this session).
- Operator has not yet re-clicked Preview against the deployed fix — next step is to verify `scanned: 697` (or current count) renders cleanly once this deploys.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (single caller chain, read-only query)
- [x] No silent behavior change — same orphan-detection logic and output shape, only the query batching changed
