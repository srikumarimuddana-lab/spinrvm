# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (B11/R-C follow-up on the Data Transfer PIA) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B11 / R-C (`docs/privacy/2026-07-28-pia-data-transfer-export.md`) |

## 1. Issue / gap identified

The Data Transfer export request had no field capturing *why* a given export was happening. If an export bundle were later found to have been misused, there was no contemporaneous record of the stated business reason to compare against (PIPEDA Accountability principle).

## 2. Root cause

Not applicable — this is a new field, not a fix for broken existing behavior.

## 3. Fix / remediation

Added a required `reason` field (10-200 chars, enforced by Pydantic `Field(..., min_length=10, max_length=200)`) to `ExportRequest`. Stored on the `data_transfer_export_jobs` row (new nullable `reason` column, migration 264 — nullable at the schema level so existing historical rows aren't broken; "required" is enforced at the application layer for new requests, not via a `NOT NULL` constraint that would need a backfill). Surfaced in the Jobs & History tab's job list (`_LIST_COLUMNS`) and admin-dashboard table, and included in the `log_admin_action` audit metadata.

## 4. Risk & impact on existing functionality

- **What else reads/writes `data_transfer_export_jobs`?** `data_transfer_jobs.py` (list/get/download, unchanged logic, only added `reason` to the selected columns), the purge loop (`utils/data_export_purge.py`, doesn't touch `reason`, unaffected).
- **Could this regress a working flow?** Yes, deliberately: any export request that omits `reason` (or supplies one outside 10-200 chars) now gets a `422` where it previously succeeded. This is a breaking API change for the export endpoint's request contract — see "User-experience effect" below.
- **Blast radius:** isolated to the Data Transfer export flow — no other route reads `ExportRequest` or writes to this table's `reason` column.
- **Migration:** additive, nullable column, no lock/rewrite risk (see migration reviewer's report — SAFE TO APPLY, no blockers).

## 5. User-experience effect

- **Who sees a difference:** internal admin only (super_admin, per the R-A fix earlier in this same B11 work).
- **Mid-session visible?** The Export tab now requires a reason (10-200 chars) before the Export button enables — an admin mid-export-flow who hasn't used this feature since the deploy will see a new required field. Not a silent behavior change: the field is visible, labeled "required," with a live character counter, and the button is disabled (not just erroring after submit) until valid.
- **API contract change:** breaking, additive-with-requirement — `POST /admin/data-transfer/export` now requires `reason` in the request body; any external caller of this endpoint (there are none outside the admin-dashboard UI, confirmed by grep) would need to add it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/264_data_transfer_export_reason.sql` (new) | Adds nullable `reason TEXT` column to `data_transfer_export_jobs` | Storage for the new field; reviewed and approved by the migration-safety subagent (SAFE TO APPLY) |
| `backend/routes/admin/data_transfer_export.py` | `ExportRequest.reason` required field; threaded through `_run_export_job` and into `job_record`/`log_admin_action` | Enforce + persist + audit the business justification |
| `backend/routes/admin/data_transfer_jobs.py` | Added `reason` to `_LIST_COLUMNS` | Surface it in the Jobs & History tab |
| `backend/tests/test_data_transfer_export_route.py` (new) | 4 tests: missing/too-short/too-long reason → 422; valid reason persisted on the job row | Cover the new validation + persistence behavior |
| `admin-dashboard/src/lib/api.ts` | `exportDataTransferEntities` takes a required `reason` param; `DataTransferJob.reason` field added | Wire the new field through the typed API client |
| `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx` | New reason `<textarea>` with client-side 10-200 char validation; Export button disabled until valid | Give admins the field to fill in, matching the backend's validation |
| `admin-dashboard/src/app/dashboard/data-transfer/JobsTab.tsx` | New "Reason" column in the jobs table | Make the recorded justification actually visible for accountability — the whole point of R-C |

## 7. Before / after

```python
# Before
class ExportRequest(BaseModel):
    entities: list[ExportEntityRef]
    doc_types: Optional[list[str]] = None
    format: str = Field("zip", pattern="^(zip|csv|json|excel)$")
```

```python
# After
class ExportRequest(BaseModel):
    entities: list[ExportEntityRef]
    doc_types: Optional[list[str]] = None
    format: str = Field("zip", pattern="^(zip|csv|json|excel)$")
    reason: str = Field(..., min_length=10, max_length=200)
```

## 8. Rollback plan

Code: `git-revert-safe`. Migration: `ALTER TABLE data_transfer_export_jobs DROP COLUMN IF EXISTS reason;` (stated in the migration's own top comment) — safe, no dependents. If reverting the code without reverting the migration, the extra nullable column is simply unused going forward, harmless either way.

## 9. Verification performed

- [x] Automated tests: `tests/test_data_transfer_export_route.py` (4/4), full `pytest -k data_transfer` (29 passed, 1 skipped, 0 failed).
- [x] `ruff check` clean on all changed/new backend files.
- [x] Migration reviewed by the `spinr-migration-reviewer` subagent: **SAFE TO APPLY**, no blockers, no warnings.
- [x] `npm run build` (real production build, not just `tsc --noEmit`) completed successfully including the `/dashboard/data-transfer` route — per CLAUDE.md's requirement that a passing dev server or `tsc --noEmit` alone isn't equivalent for admin-dashboard changes.
- [x] `tsc --noEmit` confirmed 0 new errors (22 pre-existing errors in unrelated `__tests__` files, none touching the 3 changed frontend files).

## 10. What was NOT verified

- Not tested against a real Supabase/staging environment — only mocked `db_supabase.insert_one` and FastAPI `TestClient`.
- Did not visually screenshot the new reason `<textarea>`/Jobs-table column in a running browser — reasoned about via code review + successful production build, not screenshotted (no visual regression tooling exists in this repo for admin-dashboard, a standing gap noted elsewhere in ACTION_ITEMS.md).

## 11. Sign-off

- [x] Rollback plan is concrete and testable (migration's own stated `DROP COLUMN`, code `git revert`).
- [x] Blast radius is stated, not assumed (§4).
- [x] User-experience effect stated explicitly, including that this is a deliberate breaking API-contract change for the export endpoint (§5) — not silent.
