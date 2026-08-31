# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard, docs |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Owner-directed follow-up: "add validations/checks to Bulk Operations so I can see what needs to run, in order" |

## 1. Issue / gap identified

17 legacy-migration/import/backfill admin tools exist across the codebase.
No single doc named more than 4 of them in order, and nothing in the admin
UI showed which tools had already been run against production, so the
operator had to cross-check counts by hand (or guess) before running the
next tool in a fresh Mongo export's processing sequence.

## 2. Root cause

Each tool was built independently, in its own session, against its own
narrow scope — none of them were ever asked to also update a shared
ordering doc or status view. Two docs (`legacy-migration-playbook.md`,
`2026-08-27-legacy-data-full-migration-approach.md` §7) each covered a
different, incomplete slice, and both predate more than half the current
tool inventory.

## 3. Fix / remediation

- `backend/services/migration_status_service.py` (new): `get_migration_status()`
  — read-only, one function per tool, returns the tool's real current state
  (`not_started` / `partial` / `done` / `manual_check_required`) computed
  from live Supabase counts. Two tools (`Fix Rider Join Dates`, `Fix
  Backfilled Driver Join Dates`) have no Supabase-only signal — their real
  completion check requires re-comparing against the source CSV — so they
  are reported honestly as `manual_check_required` rather than a fabricated
  percentage.
- `backend/routes/admin/migration_status.py` (new): `GET /api/admin/migration-status`,
  super_admin-gated at the router-mount level (same posture as
  `pre_launch_flag_router`/`tax_id_import_router`), registered in
  `backend/routes/admin/__init__.py`.
- `admin-dashboard/src/lib/api/imports.ts`: added `adminGetMigrationStatus()`
  + types, in the same domain module every other Bulk Operations client
  function already lives in (matches `api.ts`'s own stated convention —
  "add new endpoints to the relevant domain module").
- `admin-dashboard/src/app/dashboard/bulk-operations/_components/MigrationChecklist.tsx`
  (new): renders all 16 tools in dependency order with a live status badge,
  a link to each tool, and a manual refresh button. Wired in at the top of
  `bulk-operations/page.tsx`, above the Stripe Mapping Import section.
- `docs/runbooks/migration-tool-order.md` (new): the canonical,
  verified-against-code tool-by-tool order and dependency reasoning,
  superseding the stale sequencing in
  `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §7
  (which now carries a pointer to this file rather than being rewritten).

## 4. A real, unrelated production-readiness gap found while building this

**Migration 373 (`saved_addresses.legacy_import_metadata`) exists in
`backend/migrations/` but was confirmed NOT applied to production** (checked
directly against `schema_migrations` — migration 374, dated one day later,
IS applied; 373 and 375 are not). This means the already-shipped Legacy
Saved-Address Backfill tool (#10) **would fail if committed against
production today** — it writes to a column that doesn't exist yet. The
migration itself is a small, safe, additive `ALTER TABLE ... ADD COLUMN
... DEFAULT '{}'`, with its own documented rollback.

This module's own `_tool_10_saved_address_backfill()` function is written
defensively against this: it catches the query failure and reports
`manual_check_required` with an explicit "Migration 373 not applied" message
and warning badge, rather than letting one missing column 500 the whole
status endpoint for all 16 tools. **Not fixed here** — applying a migration
to production is outside what this session can do (no `DATABASE_URL`/write
credentials, same constraint as every other tool built this session); the
operator needs to run `python -m backend.scripts.run_migrations` (or confirm
it's already scheduled) before Legacy Saved-Address Backfill can actually be
used.

## 5. Risk & impact on existing functionality

- **Blast radius, checked directly**: `migration_status_service.py` is a
  brand-new module with one external caller (the new route). Every query it
  runs is a `SELECT`, no `.update()`/`.insert()`/`.delete()` anywhere in the
  file — confirmed by reading the whole file back after writing it.
- **Every count query was cross-checked against live production** (read-only
  SQL via the Supabase MCP) before being written into the service, not
  assumed correct from reading the writer's own code. All numbers came back
  internally consistent with everything already verified earlier this
  session (e.g. 310 pre-launch-flagged drivers, 25 rides — exact match).
- **No existing tool's behavior changed.** This panel only reads; every
  actual import/backfill tool's own validate/commit flow is untouched.
- **`saved_addresses` schema-gap defensive handling is new, isolated
  code** — it doesn't touch `saved_address_import_service.py` itself (that
  tool's own behavior against production is unchanged: it would still fail
  to commit until migration 373 is applied, exactly as before this change;
  this session only made the *status panel* not crash because of it).

## 6. User-experience effect

Admin-facing only (Bulk Operations page, super_admin only). A new panel at
the top of the page. No rider/driver-facing change. No change to any
existing admin who doesn't look at this new panel.

## 7. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/migration_status_service.py` | New — 16-tool read-only status computation | Answer "what's run, what's pending" |
| `backend/routes/admin/migration_status.py` | New — `GET /migration-status` | Expose the status computation |
| `backend/routes/admin/__init__.py` | Mounted the new router, `require_super_admin` | Same posture as every other Bulk Operations tool |
| `admin-dashboard/src/lib/api/imports.ts` | New `adminGetMigrationStatus()` + types | Client for the new endpoint |
| `admin-dashboard/src/lib/api.ts` | Re-export the new function/types | Existing barrel-file convention |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/MigrationChecklist.tsx` | New component | The checklist UI |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Wired the component in | Surface it on the page |
| `docs/runbooks/migration-tool-order.md` | New canonical order doc | Single source of truth |
| `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` | §7 marked superseded, pointer added | Don't leave two conflicting "official" orders |
| `backend/tests/test_migration_status_service.py`, `backend/tests/test_admin_migration_status.py` | New — 17 tests total | Lock in state-transition logic + the defensive schema-gap path |

## 8. Rollback plan

`git revert` — every file here is either brand new or a pure addition
(new router mount line, new export lines, one new `<MigrationChecklist />`
JSX line). No data change, no schema change, nothing to undo beyond the
code itself.

## 9. Verification performed

- [x] `pytest tests/test_migration_status_service.py tests/test_admin_migration_status.py tests/test_admin_pre_launch_flag.py tests/test_pre_launch_flag_service.py` — 40 passed, 0 regressions.
- [x] `ruff check` / `ruff format --check` on every touched Python file — clean.
- [x] `npx tsc --noEmit` — clean for every file this change touches (3 pre-existing, unrelated Storybook module-resolution errors remain — confirmed via a clean-baseline stash-and-rebuild that they exist identically without any of this change's files present; not something this change caused or should fix).
- [x] `npm run build` (admin-dashboard) — real production build; Turbopack compile succeeds, the TypeScript step fails only on the same 3 pre-existing Storybook errors above, confirmed pre-existing via the same clean-baseline check.
- [x] Every count query in `migration_status_service.py` was run as raw read-only SQL against production first, and the service's Python logic matched those real numbers before being written.
- [x] Blast-radius grep: `migration_status_service.py` has one caller; no existing tool's code path was touched.

## What was NOT verified

- The live admin-dashboard UI was not screenshotted (no browser access in
  this session) — the component's layout was reasoned about against the
  page's existing Card/badge patterns, not visually confirmed. No visual
  regression tooling exists for admin-dashboard per CLAUDE.md's standing
  note.
- Migration 373 was not applied — see §4. The panel's defensive handling of
  that gap was tested (a mocked exception, `test_saved_address_backfill_reports_missing_column_without_crashing_other_tools`),
  but the real production 500-on-missing-column behavior itself was not
  re-triggered live (confirmed instead via `information_schema.columns` and
  `schema_migrations`, both read-only).
- `Bulk Driver Tax-ID Import`'s admin-dashboard link points at
  `/dashboard/bulk-operations` (no dedicated page exists for it per the
  research this session did) — if one gets built later, the checklist's
  `admin_path` for that one tool should be updated to point at it directly.
