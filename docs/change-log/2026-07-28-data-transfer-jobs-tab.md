# Change Impact & Risk Log — Data Transfer module: Jobs/History tab (follow-up)

## Issue/gap identified
The original phased plan specified five tabs: Search & Select, Export,
Import, SGI Compliance Forms, **and Jobs/History**. `data_transfer_export_jobs`
(migration 262) was built with exactly that in mind — its own table comment
says "so the Jobs tab can render history" — but the tab itself was never
built. Every export batch was tracked (status, entity count, format,
expiry) and then effectively write-only: nothing ever read it back. An
admin who closed the download toast had no way to find that file again
short of re-running the export.

## Root cause
Scope drift between the original plan and execution — caught during a
self-review pass, not by an external report.

## Fix/remediation
- New `backend/routes/admin/data_transfer_jobs.py`:
  - `GET /admin/data-transfer/jobs` — most recent batches (default 50, capped
    200), excluding soft-deleted rows (`deleted_at IS NULL`, matching the
    purge loop's own convention). Returns `entity_count` computed from
    `entity_ids`'s array length rather than making the frontend do it.
  - `GET /admin/data-transfer/jobs/{job_id}/download` — the original signed
    URL from export time is never persisted (intentionally short-lived); this
    mints a fresh one from the tracked `storage_path`, returning 410 Gone if
    the job was already purged and 409 if it's still pending/failed.
- Modified `backend/routes/admin/__init__.py`: registers the router, same
  `bulk_operations` gate as the rest of the module.
- New `admin-dashboard/src/app/dashboard/data-transfer/JobsTab.tsx`: table of
  recent batches (status icon, created/expiry timestamps, record count,
  format, re-download button for completed/unexpired jobs), manual refresh
  button (no polling — this is history, not a live queue).
- Modified `admin-dashboard/src/lib/api.ts`: added `listDataTransferJobs`/
  `regenerateDataTransferJobDownload` wrappers + `DataTransferJob` type.
- Modified `page.tsx`: adds the fifth "Jobs & History" tab.

## Risk & impact on existing functionality
Blast radius: `data_transfer_jobs.py` is a new, read-mostly route
(`GET`s only, aside from no writes at all) — it reads a table that already
has exactly one other writer (`data_transfer_export.py`, unchanged) and one
other consumer (`utils/data_export_purge.py`'s sweep, unchanged — this
endpoint filters out what the purge loop has already soft-deleted, so the
two can't show contradictory state). No existing route, table write path, or
frontend component is modified beyond the additive tab-list/content changes
in `page.tsx` and `api.ts`.

## User experience effect
New capability, no existing behavior changed: an admin can now see export
history and re-download a file without having to remember its original
signed URL or re-run the export from scratch.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/routes/admin/data_transfer_jobs.py` | New: list + regenerate-download-link routes | Read back what `data_transfer_export_jobs` already tracks |
| `backend/routes/admin/__init__.py` | +2 lines: import + `include_router` | Wire in, module-gated |
| `admin-dashboard/src/lib/api.ts` | +2 wrappers + type (additive) | Typed client |
| `admin-dashboard/src/app/dashboard/data-transfer/JobsTab.tsx` | New: Jobs tab UI | History table + re-download |
| `admin-dashboard/src/app/dashboard/data-transfer/page.tsx` | +5th tab | Wire `JobsTab` in |

## Before/after snippet
N/A — purely additive; closes a scope gap rather than changing existing
behavior.

## Rollback plan
Remove the two added lines in `routes/admin/__init__.py`, delete
`data_transfer_jobs.py` and `JobsTab.tsx`, revert the additive blocks in
`api.ts`/`page.tsx`. The `data_transfer_export_jobs` table and its existing
writer/purge-sweep are completely unaffected either way — this is a
read-only addition on top of infrastructure that already existed.

## Verification performed
- `python3 -m py_compile` on the new/modified backend files — passes.
- `npx tsc --noEmit -p tsconfig.json` — zero errors attributable to any file
  this follow-up touched.
- Confirmed the `deleted_at IS NULL` filter convention matches
  `utils/data_export_purge.py`'s own query shape (same soft-delete
  semantics, not a new convention).

## What was NOT verified
- Not exercised against a live Supabase project — no real
  `data_transfer_export_jobs` rows existed to list in this session.
- Not run in a browser — the Jobs tab's rendering, status icons, and
  re-download flow are untested visually.
- No unit test added — same standing coverage gap as the rest of this
  module's route layer, called out in the broader module review.
