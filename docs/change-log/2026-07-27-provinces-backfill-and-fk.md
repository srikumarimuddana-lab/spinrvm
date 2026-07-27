# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-27 |
| Author | Claude (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate (feeds corporate/reporting/bulk-upload modules) |
| PR / commit link | (fill in on PR creation) |
| Related issue or gap ID | Phase 1 of Corporate/Reporting/Bulk-Upload design docs, subtask 2 |

## 1. Issue / gap identified

Not a bug fix — new-feature scaffolding, subtask 2 of 2 for the `provinces` foundation (subtask 1 was migration 259, already committed). `service_areas.province` is free-text with no FK; `regulatory_authority`/`regulatory_region` are duplicated per row instead of centralized in the new `provinces` table.

## 2. Root cause

N/A — greenfield addition.

## 3. Fix / remediation

Added `backend/migrations/260_provinces_backfill_and_fk.sql`: pre-flight checks abort if any province has conflicting `regulatory_authority` or `timezone` values across its service areas (case/whitespace-normalized); backfills `provinces` from distinct `service_areas.province` values; adds `service_areas.province_code` as a nullable column, backfills it, then adds the FK via `NOT VALID` + separate `VALIDATE CONSTRAINT` (low-lock pattern) plus a partial index.

## 4. Risk & impact on existing functionality

- **Blast radius: `service_areas` only**, and only additively — no existing column (`province`, `regulatory_authority`, `regulatory_region`, `timezone`) is dropped, renamed, or has its semantics changed. They're kept as nullable per-area overrides.
- `service_areas` is read by fare calculation, dispatch, and driver-document-requirement logic (per earlier codebase audit). This migration adds a new column those code paths don't yet read — confirmed zero behavior change since no backend code in this PR reads `province_code`.
- Two-stage abort safety: the migration will refuse to apply (transaction rolls back, nothing partially changes) if it finds a province with genuinely conflicting regulator or timezone data across service areas, rather than silently guessing via an arbitrary `LIMIT 1`. Reviewed and confirmed correct by `spinr-migration-reviewer` (two warnings raised in first pass — both fixed: added the missing timezone pre-flight check, and normalized province-code casing/whitespace to prevent near-duplicate rows like `'SK'` vs `'sk'`).
- `NOT VALID` + `VALIDATE CONSTRAINT` avoids a blocking table lock even though `service_areas` is a small table — defensive, not strictly required at current scale.
- Interaction with background loops / ride state machine / money: none.

## 5. User-experience effect

None. No rider, driver, corporate admin, or internal admin sees any difference — `province_code` isn't read by any endpoint or UI yet. Ships dark by design.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/260_provinces_backfill_and_fk.sql` | New file — backfills `provinces`, adds `service_areas.province_code` FK + index | Completes the province/service-area foundation started in migration 259 |

## 7. Before / after

N/A — additive column + FK, no existing column's behavior changes.

## 8. Rollback plan

Stated in the migration's own top comment:
```sql
ALTER TABLE public.service_areas DROP CONSTRAINT IF EXISTS service_areas_province_code_fkey;
DROP INDEX IF EXISTS idx_service_areas_province_code;
ALTER TABLE public.service_areas DROP COLUMN IF EXISTS province_code;
```
`provinces` rows from the backfill can be left in place (harmless reference data) or removed manually if rolling back both 259 and 260 together.

## 9. Verification performed

- [x] Reviewed by `spinr-migration-reviewer` subagent — first pass found 2 non-blocking warnings (missing timezone pre-flight conflict check; unnormalized province-code casing), both fixed and re-verified structurally (balanced `DO $$...END $$;` blocks, statement count sane).
- [x] Blast-radius stated: `service_areas` only, additive-only, no other table touched.
- [ ] Not yet applied to any live/staging database — ships with migration 259 on next deploy per normal `backend/migrate.py` process.
- [x] Reviewed against relevant CLAUDE.md conventions: RLS N/A (not a new table), forward-compatible (`NOT VALID`+`VALIDATE` low-lock pattern, nullable column, idempotent `ON CONFLICT`/`IS DISTINCT FROM`), index added for the new query pattern it enables, append-only (new file only).
- [x] Feature-flag: not applicable — no user-visible behavior.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — nothing reads `province_code` yet
