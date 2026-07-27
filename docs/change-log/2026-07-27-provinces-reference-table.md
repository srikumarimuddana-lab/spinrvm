# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-27 |
| Author | Claude (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate (feeds corporate/reporting/bulk-upload modules) |
| PR / commit link | (fill in on PR creation) |
| Related issue or gap ID | Phase 1 of Corporate/Reporting/Bulk-Upload design docs |

## 1. Issue / gap identified

Not a bug fix — this is new-feature scaffolding. No `provinces` reference table exists today; `service_areas.province` is a bare free-text column, and per-service-area regulator info (`regulatory_authority`, `regulatory_region`) is duplicated per row instead of centralized. This blocks the province/service-area multi-tenancy work planned for the Corporate, Reporting, and Bulk Upload modules.

## 2. Root cause

N/A — greenfield addition, not a fix.

## 3. Fix / remediation

Added `backend/migrations/259_provinces_reference_table.sql`: creates a new `public.provinces` table (`code` PK, `name`, `default_regulatory_authority`, `default_regulatory_requirements_url`, `default_regulatory_notes`, `default_timezone`, `regulatory_config` JSONB, timestamps), enables RLS with no anon/authenticated policies (default-deny; service-role/backend bypasses RLS by design), and seeds one row for Saskatchewan (the only live province).

## 4. Risk & impact on existing functionality

- **Blast radius: zero.** This migration does not `ALTER` any existing table (`service_areas`, `drivers`, `users`, etc.) and creates no foreign keys into or out of existing tables. Confirmed via `spinr-migration-reviewer` agent review — verdict SAFE TO APPLY.
- No existing code path reads or writes `provinces` — the table has no active consumer yet. It is intentionally inert until the follow-up migration (`260_provinces_backfill_and_fk.sql`, not yet written) adds `service_areas.province_code` as an FK and backfills it.
- No interaction with background loops, the ride state machine, or money/wallet deltas.
- Grep performed: searched for existing usages of `provinces` (table name) across `backend/` — zero matches, confirming no naming collision and no pre-existing partial implementation.

## 5. User-experience effect

None. No rider, driver, corporate admin, or internal admin sees any difference — the table isn't read by any endpoint or UI yet.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/259_provinces_reference_table.sql` | New file — creates `provinces` table, RLS, seed row | Foundation for province/service-area multi-tenancy across Corporate, Reporting, and Bulk Upload modules |

## 7. Before / after

N/A — pure additive, no existing behavior changed.

## 8. Rollback plan

`DROP TABLE IF EXISTS public.provinces;` — stated in the migration's own top-of-file comment. Safe at any point before the follow-up migration (260) adds an FK from `service_areas`, since nothing else references this table yet.

## 9. Verification performed

- [x] Reviewed by `spinr-migration-reviewer` subagent against `backend/migrations/CLAUDE.md` conventions — verdict SAFE TO APPLY, no blockers.
- [x] Blast-radius grep performed: searched `backend/` for existing `provinces` references (none found) and for existing RLS-policy precedent on the comparable `service_areas` table (none found in migrations — used as justification to default-deny rather than speculatively grant anon read).
- [ ] Not yet applied to any live/staging database — this PR ships the migration file only; `backend/migrate.py` applies it on next deploy per normal process.
- [x] Reviewed against relevant CLAUDE.md conventions: RLS (explicit, no `FOR ALL`), migration naming/numbering (259 confirmed free via `ls backend/migrations | sort -V | tail -1`), append-only (new file, no edits to existing migrations), forward-compatible (idempotent `CREATE TABLE IF NOT EXISTS` + `ON CONFLICT DO NOTHING` seed, no lock risk).
- [x] Feature-flag: not applicable — no user-visible behavior, nothing to flag.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`DROP TABLE IF EXISTS`)
- [x] Blast radius is stated, not assumed (zero — confirmed via grep + migration reviewer)
- [x] No silent behavior change to an already-shipped flow — nothing reads this table yet
