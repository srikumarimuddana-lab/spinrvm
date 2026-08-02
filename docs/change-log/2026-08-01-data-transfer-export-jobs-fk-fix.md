# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code |
| Surface(s) | backend (database) |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch, PR #2909) |
| Related issue or gap ID | User-reported: Data Transfer export fails with "internal server error" |

## 1. Issue / gap identified

The Data Transfer export button (`POST /api/admin/data-transfer/export`) has never once succeeded in production — confirmed earlier this session via direct query (`data_transfer_export_jobs` had zero rows, ever, for any admin). The user reported this as a generic "internal server error"; the browser console this session showed the real status: **503**, with `Failed to load resource` on `POST .../data-transfer/export`.

## 2. Root cause

`data_transfer_export_jobs.requested_by_admin_id` was declared `REFERENCES users(id)`. Admin identity in this codebase lives in `admin_staff` (or an env-var-creds sentinel like `"admin-001"`/`"break-glass"`) — never in `users` (CLAUDE.md's JWT trust model: admin JWTs are fully trusted, admin identity ≠ `users.id`). Confirmed live via direct query: `SELECT id FROM users WHERE id IN (SELECT id FROM admin_staff)` returns zero rows.

Every real export request therefore violated the FK on `INSERT INTO data_transfer_export_jobs` (`routes/admin/data_transfer_export.py`'s `export_entities`), which is wrapped in:
```python
try:
    await db_supabase.insert_one("data_transfer_export_jobs", job_record)
except Exception:
    logger.error(...)
    raise HTTPException(status_code=503, detail="Could not record export job") from None
```
— exactly matching the observed 503 and the zero-rows-ever finding. This is the *identical* bug class already fixed once this session on a sibling table: migration 270 dropped the same mistaken `REFERENCES users(id)` FK from `admin_export_approval_requests.requested_by`/`decided_by` (see that migration's own docstring, and CLAUDE.md's references to the same pattern on migrations 213/214).

## 3. Fix / remediation

`backend/migrations/274_data_transfer_export_jobs_admin_id_no_fk.sql` — drops `data_transfer_export_jobs_requested_by_admin_id_fkey`. No column type change (already `TEXT`), no data migration needed (the column was always populated correctly, just unable to satisfy an FK that was never satisfiable). Applied directly to production via Supabase MCP with explicit user confirmation this session, verified via a follow-up `pg_constraint` query showing only the primary key remains on the table.

## 4. Risk & impact on existing functionality

- **Blast radius: one column, one table.** Grepped every reference to `data_transfer_export_jobs` in the codebase: only `routes/admin/data_transfer_export.py` (insert on request, update on background-job completion/failure) and `routes/admin/data_transfer_jobs.py` (read-only list/detail/download-link endpoints for the Jobs & History tab). Neither reads or writes `requested_by_admin_id` in a way that assumed the FK's presence (no join through it); it's stored purely for audit/display.
- **This makes a previously-100%-failing write path succeed** — the risk isn't regression, it's newly-live behavior: the export background job (`_run_export_job`) will now actually run for the first time in production. That job gathers PII (profile, documents, ride history, insurance-period audit trail) into a ZIP/CSV/JSON/Excel bundle and uploads it to the `data-transfer-exports` Storage bucket. This is expected/intended behavior (the whole point of the feature), not a side effect — flagging it because "first time this code path has ever actually executed against real production data" is exactly the kind of thing that deserves a close look at the next few real uses, not because the change itself is risky.
- No RLS change: the `service_role`-only INSERT/SELECT/UPDATE/DELETE policies on this table are untouched — only the FK (a referential constraint, not an access-control one) was dropped.

## 5. User-experience effect

**Internal admin only.** The Data Transfer export button, which has always failed, will now work. No rider/driver/corporate-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/274_data_transfer_export_jobs_admin_id_no_fk.sql` | Drops the broken FK | Root-cause fix |

## 7. Before / after

```sql
-- Before
ALTER TABLE data_transfer_export_jobs
    ADD CONSTRAINT data_transfer_export_jobs_requested_by_admin_id_fkey
    FOREIGN KEY (requested_by_admin_id) REFERENCES users(id) ON DELETE SET NULL;
-- Every real admin's insert violates this: admin_staff.id is never a users.id.

-- After
ALTER TABLE data_transfer_export_jobs
    DROP CONSTRAINT IF EXISTS data_transfer_export_jobs_requested_by_admin_id_fkey;
```

## 8. Rollback plan

`git revert` the migration file for the repo history. The live constraint itself is **not** re-addable via a plain revert — dropping it was correct (it could never be satisfied by a real admin), so there is nothing to roll back to; a hypothetical "undo" would mean re-adding a constraint that breaks the feature again, which would only make sense if this root-cause analysis turns out to be wrong. If the export feature misbehaves in some *other* way after this, that's a new investigation, not a reason to restore this FK.

## 9. Verification performed

- [x] Direct query confirming zero overlap between `admin_staff.id` and `users.id` in production (the actual mechanism of failure).
- [x] Direct query confirming the FK is gone post-migration (only `data_transfer_export_jobs_pkey` remains in `pg_constraint`).
- [x] Cross-checked against the browser console error captured this session (`POST .../data-transfer/export 503`), which matches exactly the code path this FK would break.
- [ ] Not yet verified end-to-end against a live "click Export" attempt in the browser after the fix — the user should retry the export now that the constraint is gone; if it succeeds, `data_transfer_export_jobs` will finally show a row.
- [ ] No automated test added for this specific FK-violation path — the existing test suite mocks `db_supabase.insert_one` and would not have caught a real-schema-only bug like this (same gap noted for the sibling `admin_export_approval_requests` fix in migration 270's own history).

## 10. What was NOT verified / deferred

- A live, real end-to-end retry of the export flow (see §9) — recommend the user re-attempt the original repro (select all users, all doc types, valid reason, Export) and confirm a job now appears in Jobs & History and completes.
- Whether other tables in this codebase have the same `REFERENCES users(id)` mistake on an admin-identity column, beyond the two now fixed (`admin_export_approval_requests`, `data_transfer_export_jobs`) — worth a one-time audit (`grep` for `admin_id.*REFERENCES users` across `backend/migrations/`) as a follow-up, not done here.
