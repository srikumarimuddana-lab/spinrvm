# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | Found live while verifying production after PRs #5087/#5090-#5095 (Migration Checklist UX work); same bug class as `backend/tests/test_oversized_in_batching.py` (production incidents 2026-08-31, 2026-09-03) |

## 1. Issue / gap identified

The admin Bulk Operations → Migration Checklist panel showed **5 of 19 tools** ("Legacy SIN/DOB Backfill", "Legacy Vehicle-History Backfill", "Fix Orphaned Legacy-Linked Accounts", "Bulk Driver Tax-ID Import", "Legacy ID Crosswalk Backfill") stuck on a "Status check error — see backend logs" badge in production, confirmed via a live screenshot from the user.

## 2. Root cause

`backend/services/migration_status_service.py` computes each tool's status with direct, synchronous `supabase.table(...).select(...).in_(...)` calls against the raw postgrest-py client. Four of the five affected tools (`sin_dob_backfill`, `vehicle_history_backfill`, `tax_id_import`, `id_crosswalk_backfill`) issue a single unbatched `.in_()` keyed on the shared eligible-driver population (currently ~737 driver UUIDs per the checklist's own "712 new + 25 linked" count); the fifth (`orphaned_accounts`) issues one keyed on the full `is_driver=true` `users` population (~924).

`.in_(column, values)` compiles to a PostgREST `column=in.(v1,v2,...)` **URL query parameter** — the id list travels in the request line, not a body. At this fleet size that line is in the tens of KB, which the edge proxy in front of PostgREST rejects before PostgREST (or its logs) ever see the request. The client only sees an opaque error, which `_safe_status()`'s existing isolation wrapper correctly caught and surfaced as "Status check error" per tool — so no tool's failure took down the panel, but 5 rows never got a real status.

This is not a new failure mode: `backend/tests/test_oversized_in_batching.py` documents the identical shape hitting `/rest/v1/users`, `/rest/v1/driver_documents`, and `/rest/v1/rides` in production on 2026-08-31 and 2026-09-03, with an established fix (`repositories/_base.py`'s `get_rows_batched_in`, batch size 150) for the async `db_supabase` client binding. `migration_status_service.py` uses a *different* client binding (`supabase_client.supabase`, sync) that predates/wasn't covered by that sweep, so it never got the same batching and only started failing once the driver population crossed the threshold — which happened as a direct result of the Legacy Driver Import work this session's Migration Checklist UX PRs were built around.

## 3. Fix / remediation

Added a local, synchronous batching helper (`_select_in_batched`, `_IN_BATCH_SIZE = 150`, same constant value as `_base.py`'s for consistency) to `migration_status_service.py`, and routed all five affected `.in_()` call sites through it (four via the shared helper directly; the crosswalk tool's call has an extra `.eq("entity_type", "driver")` filter the shared helper doesn't support, so it batches inline with the same loop shape). Every batch is issued as a separate request and results are concatenated before aggregating — no change to any tool's counting/state logic.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `get_migration_status()` has exactly one caller in the whole backend — `backend/routes/admin/migration_status.py`'s single read-only `GET` admin route (confirmed via repo-wide grep). No other route, background loop, or service imports this module's functions.
- Read-only: this module still never writes (per its own module docstring, unchanged).
- No interaction with ride state, money/wallet deltas, or insurance periods.
- Existing tests use a fake in-memory Supabase store that evaluates predicates against real fixture data rather than a fixed mock return, so multi-batch calls merge correctly against it without any test-harness changes.

## 5. User-experience effect

Internal-admin-facing only. Before: 5 of 19 checklist rows permanently stuck on "Status check error" once the driver population passed ~150 rows, regardless of actual migration progress underneath. After: all 19 rows report their real computed state. Not visible to riders/drivers/corporate admins.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/migration_status_service.py` | Added `_IN_BATCH_SIZE`/`_select_in_batched`; routed tools #4, #5, #6, #9, #19's `.in_()` calls through batching | Fix the oversized-URL rejection causing 5 tools to report "Status check error" in production |
| `backend/tests/test_migration_status_service.py` | Added 2 regression tests proving batching occurs (chunk size ≤150) and that batched results still merge to the correct aggregate, for both the eligible-driver-gated tools and the orphaned-accounts tool | Lock in the fix; this exact bug class has recurred 3 times in this repo (2026-08-31, 2026-09-03, now) without a regression test at this call site |

## 7. Before / after

```python
# Before
rows = supabase.table("drivers").select("id,sin,date_of_birth").in_("id", eligible_ids).execute().data or []
```

```python
# After
rows = _select_in_batched("drivers", "id,sin,date_of_birth", "id", eligible_ids)
```

## 8. Rollback plan

Pure code change, no data migration, no flag. Revert this commit — `git revert` is a complete rollback here since nothing writes to persisted state; the endpoint returns to its prior (broken-at-scale) behavior with zero data-level cleanup needed.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_migration_status_service.py backend/tests/test_admin_migration_status.py` — 33 passed. `ruff check` clean on both changed files.
- [ ] Manual repro steps followed in staging — not performed; reasoned from the production screenshot + code read, not reproduced against a live Supabase instance in this session.
- [x] Blast-radius grep performed: repo-wide grep for `get_migration_status` confirms the single caller in `routes/admin/migration_status.py`.
- [x] Reviewed against relevant CLAUDE.md convention: this is exactly the "N+1 / unbatched `.in_()`" anti-pattern class already called out in the Performance SLAs section and previously fixed elsewhere per `test_oversized_in_batching.py`.
- [ ] Feature-flagged — not applicable; this is a bug fix to a read-only admin status computation with no behavior change for anyone except restoring correct status reporting.

## What was NOT verified

- Not tested against the real production Supabase instance — the regression tests use this repo's existing fake in-memory Supabase harness, not a live query against a table with the real (~35KB-URL) fleet size.
- No production log access in this session to directly confirm the exact rejection error text (proxy `Bad Request` vs. a Supabase-side error) — the root cause is inferred with high confidence from (a) the exact matching failure shape already documented and fixed elsewhere in this repo for the same client library, and (b) each affected tool's query shape independently matching the "`.in_()` over the whole/eligible fleet" pattern, but not confirmed via a raw log line from this incident.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
