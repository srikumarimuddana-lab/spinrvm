# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend (database) |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | Follow-up audit requested by user after two prior fixes of the same bug class this session (migrations 270, 274) |

## 1. Issue / gap identified

A one-time repo-wide audit for the `REFERENCES users(id)` admin-identity FK mistake (recommended as a follow-up in `docs/change-log/2026-08-01-data-transfer-export-jobs-fk-fix.md` §10) found a third, previously-unfixed instance: `compliance_export_events.admin_user_id`. This is the audit-log table every Compliance report endpoint (`backend/routes/admin/compliance.py`'s `_log_compliance_export`) writes to on every successful export — GST/PST remittance, SGI/Knight Archer insurance billing, Driver Roster, T4A filer handoff, Airport Trips, all built/touched this session.

## 2. Root cause

Same as migrations 270 and 274: `admin_user_id` was declared `REFERENCES users(id)`, but admin identity lives in `admin_staff` (or an env-var-creds sentinel) — never in `users` (CLAUDE.md's JWT trust model). Confirmed live: `SELECT count(*) FROM compliance_export_events` returns **zero rows, ever**, despite the table existing since migration 263 and every Compliance report export calling `_log_compliance_export()` on success. The write is wrapped in try/except (best-effort per its own docstring), so no report generation ever failed or errored visibly — the audit trail has simply never existed, silently.

## 3. Fix / remediation

`backend/migrations/278_compliance_export_events_admin_id_no_fk.sql` — drops `compliance_export_events_admin_user_id_fkey` (verified exact constraint name live via `pg_constraint` before writing the migration). No column type change, no data migration. Applied directly to production via Supabase MCP, verified via a follow-up `pg_constraint` query showing only the primary key remains.

## 4. Risk & impact on existing functionality

- **Blast radius: one column, one table.** Grepped `compliance_export_events` across the codebase: only `_log_compliance_export()` (insert) in `routes/admin/compliance.py`, and the RLS `SELECT` policy created in migration 263, reference it. No other reader/writer.
- **This makes a previously-100%-failing write path succeed** — same category as the data_transfer_export_jobs fix: the risk isn't regression, it's that this audit log will finally start populating for the first time. That's the intended behavior (CLAUDE.md requires admin-action audit logging), not a side effect.
- **Secondary observation, not fixed here**: `compliance_export_events`'s RLS `SELECT` policy (`(SELECT role FROM users WHERE id = auth.uid()::text) IN ('admin','super_admin')`) has the same admin-identity-vs-`users` assumption baked in. Not fixed in this migration — backend reads/writes go through the service role, which bypasses RLS entirely, so this doesn't block anything currently working. Flagged for a future audit of RLS policies using the same pattern, not addressed now to keep this change scoped to the one confirmed-broken FK.
- Repo-wide follow-up grep for the same pattern on other admin-identity-shaped columns (`reviewed_by`, `approved_by`, `decided_by`, `created_by`, `actor_id`, `staff_id`, `performed_by`, `initiated_by`) found only `admin_export_approval_requests.decided_by`, already fixed in migration 270. No further unfixed instances found.

## 5. User-experience effect

**Internal admin only, and indirect.** No visible change to any report's output. The compliance-audit trail (who exported what, when) will start being populated for the first time going forward — relevant to a future SGI/CRA audit, not to any admin's day-to-day report usage.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/278_compliance_export_events_admin_id_no_fk.sql` | Drops the broken FK | Root-cause fix, same pattern as migrations 270/274 |

## 7. Before / after

```sql
-- Before
admin_user_id text NOT NULL REFERENCES users(id),
-- Every real admin's insert violates this: admin_staff.id is never a users.id.

-- After
ALTER TABLE compliance_export_events
    DROP CONSTRAINT IF EXISTS compliance_export_events_admin_user_id_fkey;
```

## 8. Rollback plan

`git revert` for repo history. The live constraint drop itself is not meaningfully "undoable" — re-adding it would just reintroduce the always-failing-write bug. If this root-cause analysis turns out to be wrong, that's a new investigation, not a reason to restore the FK.

## 9. Verification performed

- [x] Direct query confirming zero rows in `compliance_export_events` in production pre-fix (the actual mechanism of failure — matches the same silent-since-creation pattern as the two prior fixes).
- [x] Direct query confirming the exact live constraint name (`compliance_export_events_admin_user_id_fkey`) before writing the migration, rather than assuming Postgres's default naming convention.
- [x] Direct query confirming the FK is gone post-migration (only `compliance_export_events_pkey` remains in `pg_constraint`).
- [x] Repo-wide grep for the same `REFERENCES users(id)` pattern on admin-identity-shaped columns (`admin*_id`, `reviewed_by`, `approved_by`, `decided_by`, `created_by`, `actor_id`, `staff_id`, `performed_by`, `initiated_by`) confirmed no further unfixed instances.
- [ ] Not yet verified end-to-end against a live Compliance report export in the browser — the next successful export of any Compliance report should produce a real `compliance_export_events` row; not confirmed this session (no browser available).
- [ ] No automated test added for this specific FK-violation path — same gap noted for the two prior fixes of this bug class; the existing test suite mocks `db_supabase.insert_one` and would not have caught a real-schema-only bug like this.

## 10. What was NOT verified / deferred

- A live, real export of any Compliance report to confirm `compliance_export_events` now actually receives a row.
- The RLS `SELECT` policy's own admin-identity assumption (see §4) — flagged, not fixed.
