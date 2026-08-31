# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 (incident + investigation); fix committed 2026-08-30 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Found while answering an admin-dashboard UI question about "Unnamed Legacy Driver" records; root-caused via direct production SQL |

## 1. Issue / gap identified

692 real production `users` rows are flagged `is_driver=true` (via the legacy Mongo driver-import's "link a new driver to an existing account" path) with a placeholder `"Unnamed Legacy Driver <id>"` name, but have **no corresponding `drivers` table row at all**. They can never appear on the admin Drivers page, get verified, or go online — they're inert "phantom drivers" that only show up (confusingly) in the Users list with a Driver badge and nothing behind it.

## 2. Root cause

`backend/services/driver_import_service.py::commit_mongo_driver_import_plan()` wrote in two separate, sequential phases with no shared transaction and no rollback between them:

1. `plan.users_to_update` (sets `is_driver=true` + appends `mongo_driver_history`) — committed first, each row an independent, already-durable Supabase UPDATE.
2. `plan.drivers_to_insert` — one bulk INSERT statement, built after step 1.

If step 2 failed for any reason (a single bad row in the bulk insert, an encryption-RPC error, anything), Postgres/PostgREST rejects the whole INSERT and the exception aborts the function — but step 1's writes were already committed and nothing rolls them back.

**Confirmed via direct production evidence, not guessed:**
- Two batches (`20260828205641`, `20260828205731`) wrote all 697 `users_to_update` rows at 20:56–20:57 UTC on 2026-08-28.
- Zero `audit_logs` rows exist for either batch — expected, since the calling route (`routes/admin/legacy_driver_import.py`) only calls `log_admin_action` *after* `commit_mongo_driver_import_plan()` returns; the function threw before reaching that line.
- The three runs that *do* have audit-log rows (20:59:19, 21:04:31, 21:06:42 UTC) show a clean 5-for-5 match between `linked_accounts` and `new_drivers` — proving the plan-building logic itself is correct; this was a write-order/atomicity bug, not a logic bug.
- A same-day commit (`387b73a`, "commit timeout fix") had already found and fixed a related but distinct issue (sequential round-trips tripping a proxy timeout) on the same function, without addressing this separate all-or-nothing-Phase-2 gap.

## 3. Fix / remediation

**Code fix** (`commit_mongo_driver_import_plan`): reordered so no user is ever flagged `is_driver=true` until its matching `drivers` row is durably written.
- Brand-new users are inserted with `is_driver=false` first.
- The `drivers_to_insert` bulk insert runs next.
- Only after that succeeds does the function (a) bulk-`UPDATE` the new users' `is_driver` to `true`, and (b) run `plan.users_to_update` (the existing-account-link path, which already carries `is_driver=true` where applicable).

If the drivers insert fails, the function now raises **before** any user is ever flagged as a driver — a failed commit leaves nothing to clean up and is safe to simply retry.

**Data repair** (one-time, not part of the normal import flow): a new admin-only route + a "Fix Orphaned Legacy-Linked Accounts" section on the Legacy Driver Import page finds every user matching this exact orphan shape and creates the missing `drivers` row from that user's own surviving `mongo_driver_history[0]` entry (the only data that survived the original partial write). `license_number`/`rating` were never persisted anywhere outside the original CSV and cannot be recovered — backfilled rows come back with neither, same as any other Phase 1 row with no vehicle/license data on file. Every row this import has ever written used the Saskatoon service area (confirmed via production query), used as the default.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one import's commit function and a new, additive repair route.** Grepped for other callers of `commit_mongo_driver_import_plan` — only the one route (`routes/admin/legacy_driver_import.py`) calls it.
- **Behavior-preserving for the success path**: 25 pre-existing tests for `build_mongo_driver_import_plan`/`commit_mongo_driver_import_plan` all still pass unmodified — the plan's *output shape* never changed, only the commit function's internal write order. The 3 existing commit-path tests (happy path, no-license, existing-account-link) assert end-state, not intermediate ordering, so they're unaffected.
- **New regression tests prove the fix**: 2 new tests simulate the drivers-insert step failing (for both the brand-new-user and existing-account populations) and assert `is_driver` is never left `true` with no driver row — confirmed to fail without the fix (reverted, re-ran, saw the exact orphan state reproduce), pass with it restored.
- **Backfill is idempotent and additive-only**: `find_orphaned_legacy_driver_users()` only returns users still missing a driver row, so re-running the fix after a partial apply only touches what's still missing — never a duplicate. It only ever `INSERT`s new `drivers` rows; no existing row (user or driver) is mutated.
- **Backfilled rows carry the same safety floor as every other row this import writes**: `status: needs_review`, `is_verified/is_online/is_available: false` — cannot be dispatched, matched, or shown as verified until an admin reviews them, same as a normal import row.
- **Backfilled rows are explicitly marked** (`legacy_import_metadata.backfilled_at` / `backfill_reason`) so they're distinguishable from a normal at-import-time row if anyone needs to audit this later.
- 45 tests pass across the two affected test files (`test_legacy_mongo_driver_import_service.py`, `test_admin_legacy_driver_import.py`); `ruff check`/`format` clean.
- `npx tsc --noEmit` clean; `npm run build` succeeded — `/dashboard/drivers/legacy-import` compiled.

## 5. User-experience effect

- **Internal admin only**, and none of it is user-visible mid-session (no rider or driver app is affected — orphaned records were never online-eligible in the first place). Once the backfill runs, the 692 formerly-orphaned accounts become normal `needs_review` driver rows an admin can review on the Drivers page, same as any other legacy-imported driver.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | `commit_mongo_driver_import_plan()` reordered so `is_driver=true` is never written before its driver row; new `find_orphaned_legacy_driver_users()` / `backfill_orphaned_legacy_driver_rows()` | Root-cause fix + one-time repair for existing bad data |
| `backend/tests/test_legacy_mongo_driver_import_service.py` | 2 new atomicity regression tests + 4 new backfill-function tests | Lock in both the fix and the repair tool |
| `backend/routes/admin/legacy_driver_import.py` | New `POST /legacy-drivers/backfill-orphaned` route (apply=false default, dry-run first) | Sanctioned production-write path (admin API, not raw SQL) |
| `backend/tests/test_admin_legacy_driver_import.py` | 4 new HTTP-level tests for the new route | Lock in the route's contract |
| `admin-dashboard/src/lib/api/imports.ts` | New `adminBackfillOrphanedLegacyDrivers()` client + `OrphanedDriverBackfillResult` type | Frontend client for the new route |
| `admin-dashboard/src/lib/api.ts` | Re-export the two new symbols | Keep the barrel export in sync |
| `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx` | New "Fix Orphaned Legacy-Linked Accounts" section (preview → apply, mirrors this session's other dry-run-first backfill tools) | Give the operator a button to run the repair |

## 7. Before / after

```python
# Before — is_driver flipped BEFORE the driver row exists
if plan.users_to_insert:
    supabase.table("users").insert(plan.users_to_insert).execute()  # is_driver=True baked in
_run_concurrently(plan.users_to_update, _update_user_row)            # is_driver=True flipped here too
...
if drivers:
    supabase.table("drivers").insert(drivers).execute()              # if THIS fails, users above are already orphaned
```

```python
# After — is_driver only ever set once the driver row is durably written
if plan.users_to_insert:
    new_user_rows = [{**row, "is_driver": False} for row in plan.users_to_insert]
    supabase.table("users").insert(new_user_rows).execute()
...
if drivers:
    supabase.table("drivers").insert(drivers).execute()               # if this raises, nothing above is ever flagged
new_user_ids = [row["id"] for row in plan.users_to_insert]
if new_user_ids:
    supabase.table("users").update({"is_driver": True}).in_("id", new_user_ids).execute()
_run_concurrently(plan.users_to_update, _update_user_row)
```

## 8. Rollback plan

**Code fix**: `git-revert-safe` — same end-state on the success path (proven by all 25 pre-existing tests passing unmodified), only the write order changed.

**Backfill route/UI**: `git-revert-safe` — new, additive route + UI section. If a backfilled `drivers` row ever needs to be undone, it's identifiable by `legacy_import_metadata.backfill_reason == "orphaned_by_2026-08-29_commit_atomicity_bug"` and can be deleted directly (it's `needs_review`/offline, never dispatched, so deleting one has no live-ride impact).

## 9. Verification performed

- [x] Root-caused via direct production SQL (batch timestamps, audit-log cross-reference, git-history correlation of the fix commit's own timestamp) — not guessed.
- [x] `pytest tests/test_legacy_mongo_driver_import_service.py tests/test_admin_legacy_driver_import.py` — 45 passed.
- [x] New regression tests confirmed to fail without the fix (reverted the service-file change, re-ran, saw the exact orphan state reproduce in both populations), pass with it restored.
- [x] `ruff check` / `ruff format --check` — clean.
- [x] `npx tsc --noEmit` — clean.
- [x] `npm run build` (admin-dashboard) — real production build, succeeded.
- [x] Blast-radius grep: `commit_mongo_driver_import_plan`'s only caller is the one admin route.
- [x] Service-area default (Saskatoon) confirmed against production, not assumed — every prior successful row from this import used the same `service_area_id`.

## What was NOT verified

- Not yet run against real production — the backfill itself (previewing, then applying, for the actual 692 orphaned rows) is the operator's next step once this deploys; production will be re-checked directly via SQL afterward, same rigor as every other verification this session.
- No live OSRM/Google/Supabase network access in this environment to exercise the route end-to-end; the new tests use the same in-memory fake-Supabase pattern already established in this test file.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; backfilled rows are individually identifiable and deletable)
- [x] Blast radius is stated, not assumed (one caller of the fixed function; new route/UI are purely additive)
- [x] No silent behavior change to the import's success-path contract — proven by all pre-existing tests passing unmodified against the new write order
