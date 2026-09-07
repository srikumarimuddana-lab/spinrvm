# Change Impact & Risk Log — Legacy ID Crosswalk Backfill

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | See branch `claude/mongo-supabase-migration-audit-4nnrgl`, commits `7ab8633`, `7e3efce`, `6190e1b`, `1904b42` |
| Related issue or gap ID | Migration 328 (`legacy_id_crosswalk`) shipped schema-only, "NO BACKFILL"; this closes that gap |

## 1. Issue / gap identified

Migration 328 created the `legacy_id_crosswalk` table (old Mongo ObjectId / old Saskatoon numeric driver ID → Spinr UUID) but shipped with no backfill — the table has been empty since it merged, so support/audit staff have had no way to resolve an old-system ID to a current Spinr user without re-deriving the phone-match by hand.

## 2. Root cause

Migration 328 was intentionally schema-only (its own header comment says "SCHEMA ONLY — NO BACKFILL", CR-2026-4106) — the backfill was deferred as separate follow-up work, not forgotten. This PR is that follow-up.

## 3. Fix / remediation

Added a 19th Migration Checklist tool, "Legacy ID Crosswalk Backfill", following the existing no-CSV preview/commit pattern (`migration_data_quality.py` / `migration_driver_repair_service.py`):

- **Drivers**: reads `drivers.legacy_import_metadata` for both known source markers (`legacy_saskatoon_driver_import` → numeric ID; `legacy_mongo_driver_import`, top-level or newest `mongo_driver_history[]` entry → Mongo ObjectId). A driver enriched from both sources gets both IDs in one crosswalk row.
- **Riders**: groups `rides.legacy_import_metadata.old_customer_id` by `rider_id`. A rider whose own rides agree gets one crosswalk row; a rider whose rides disagree is reported as "ambiguous" and skipped — never guessed.
- Idempotent: re-reads `legacy_id_crosswalk` first and only proposes ids not already recorded, so a partial prior run (or a future CSV-sourced plan builder, per the hybrid design the user selected) can top up the rest without duplicating rows.
- Per the user's explicit design decision (presented via `AskUserQuestion`, "Both: Supabase now, CSV-enrichment later"), this PR ships only the Supabase-sourced half. The apply path (`apply_crosswalk_backfill`) is a plain additive insert with per-row try/except, designed to be reusable by a later CSV-sourced plan builder without redesign.
- New admin route `POST /api/admin/legacy/id-crosswalk-backfill/{preview,commit}`, gated `require_super_admin`, rate-limited (30/hr preview, 10/hr commit).
- New Migration Checklist tool #19 status computation (`done` / `partial` / `not_started` based on eligible-driver coverage; rider coverage reported separately in the tool's `detail` text, not folded into the same ratio).
- New admin-dashboard component `LegacyIdCrosswalkBackfill.tsx` on the Bulk Operations page's Phase 6 section.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, single-surface (backend + admin-dashboard).** `legacy_id_crosswalk` is a brand-new table (migration 328) with no other reader or writer anywhere in the codebase today — grepped `legacy_id_crosswalk` across `backend/` and `admin-dashboard/` before this change; the only hits were the migration file itself and its own docstring/comments. This PR is the first code to read or write it.
- No existing table, endpoint, or background loop is modified. `drivers` and `rides` are read-only in this change (no writes to either).
- No interaction with the ride state machine, insurance periods, or any money/wallet path.
- Does not gate any other Migration Checklist tool (#1-#18 unaffected; #19 depends on #1/#2/#11 having already run, per the updated `docs/runbooks/migration-tool-order.md`, but nothing downstream depends on #19).
- Accepted risk, documented in the service module's docstring: `legacy_id_crosswalk` has no unique constraint on either old-id column (migration 328's own design), so a bad double-commit could in theory insert duplicate rows for the same old ID. Mitigated by: (a) the plan builder re-reads existing rows and only proposes new ones, making a normal re-run a no-op; (b) the frontend disables the commit button while a commit is in flight; (c) this mirrors the exact same accepted-risk precedent already in production for `driver_import.py`, not a new pattern.

## 5. User-experience effect

- Internal admin only (Migration Checklist / Bulk Operations page). No rider, driver, or corporate-admin-facing change.
- Not visible mid-session to anyone using the rider or driver apps — this is a backend data table with no runtime read path yet (a future support-lookup UI would read it, but none exists in this PR).
- No new copy/notification outside the admin dashboard.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/legacy_id_crosswalk_service.py` | New: `build_driver_crosswalk_plan`, `build_rider_crosswalk_plan`, `apply_crosswalk_backfill` | Core backfill logic |
| `backend/tests/test_legacy_id_crosswalk_service.py` | New: 14 unit tests | Coverage for both plan builders and the apply path |
| `backend/routes/admin/legacy_id_crosswalk.py` | New: preview/commit routes | Admin-facing entry point |
| `backend/utils/rate_limiter.py` | Added two rate limiters | Match existing per-tool rate-limit convention |
| `backend/routes/admin/__init__.py` | Mounted new router under `require_super_admin` | Wire the route in |
| `backend/services/migration_status_service.py` | Added tool #19 status computation | Surface state on the Migration Checklist panel |
| `backend/routes/admin/migration_status.py` | Doc-comment count 18 → 19 | Keep docstring accurate |
| `backend/tests/test_migration_status_service.py` | Updated tool-count assertions; added 4 new tests | Cover tool #19's status states |
| `admin-dashboard/src/lib/api/imports.ts` | Added crosswalk types + `adminPreviewIdCrosswalkBackfill`/`adminCommitIdCrosswalkBackfill` | API client |
| `admin-dashboard/src/lib/api.ts` | Re-exported the above | Central API barrel |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx` | New component | Admin UI |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Wired new component into Phase 6 | Surface the tool |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/MigrationChecklist.tsx` | "18" → "19" tools copy | Keep copy accurate |
| `docs/runbooks/migration-tool-order.md` | Added row #19 + verification note | Document dependency order |

## 7. Before / after

Purely additive — no existing behavior changed. Skipping per template guidance ("skip for pure additive code with no existing caller").

## 8. Rollback plan

- Code: standard `git revert` of the four commits is sufficient — no other code path depends on this router, service, or component.
- Data: if a bad batch is committed, migration 328's own header comment documents the rollback: `DELETE FROM legacy_id_crosswalk WHERE batch = '<batch>'` (the `batch` column exists specifically for this). No Stripe charges, wallet deltas, or ride-state changes are involved, so no data-level remediation beyond that delete is needed.
- No feature flag was added — deemed unnecessary because the tool is fully additive-insert-only, gated behind `require_super_admin`, and a bad run is a single scoped `DELETE` away from clean, per the pre-merge release gate's own allowance for "explicitly state why a flag doesn't apply" on genuinely isolated, low-risk changes.

## 9. Verification performed

- [x] Automated tests run — unit only: `pytest backend/tests/test_legacy_id_crosswalk_service.py` (14/14 passed), `pytest backend/tests/test_migration_status_service.py` (28/28 passed after updates). `ruff check` / `ruff format --check` clean on all touched Python files. Frontend: `npx tsc --noEmit -p .` clean; `npx eslint` on the 5 touched files — 0 new errors, 1 pre-existing unrelated warning on a line not touched by this change.
- [ ] Manual repro steps followed in staging — **not performed**. No live Supabase or staging admin-dashboard access in this environment; the route was verified by structural pattern-matching against proven-working sibling routes (`migration_data_quality.py`) and successful import with no exceptions, not by an actual HTTP call or browser click-through.
- [x] Blast-radius grep performed — grepped `legacy_id_crosswalk` repo-wide; only the migration file and this PR's own new files reference it. Grepped both driver-import source constants (`IMPORT_SOURCE`, `MONGO_IMPORT_SOURCE`) to confirm the exact JSONB shape this backfill reads.
- [x] Reviewed against relevant CLAUDE.md conventions — dual-import pattern followed in the new service/route files; task decomposed into 4 ≤200-line commits; loud-error-logging convention followed (`apply_crosswalk_backfill` logs each row failure, never swallows).
- [x] Feature-flagged if user-visible and non-trivial (or justify why not) — see Rollback plan above for the explicit justification.

## What was NOT verified

- No live Supabase connection was available in this environment — every test uses the mocked `_FakeSupabase` harness (matching this repo's existing `mock_supabase_client` convention), not a real database. Real-world edge cases in `legacy_import_metadata`'s actual shape (malformed JSON, unexpected key casing) are only as well-covered as the existing driver-import tests already establish that shape to be.
- The new admin-dashboard page was not opened in a browser — no dev server / staging environment was reachable from this session. Verified via `tsc`/`eslint` only, matching the "reasoned about, not screenshotted" disclosure this repo's CLAUDE.md requires when no visual-regression tooling exists for a surface (Bulk Operations is not one of the 6 seeded visual-regression pages).
- Rider-side ambiguous-detection logic was tested against synthetic fixtures only; it has not been run against the real Mongo-import rider population, so the real-world ambiguous-rider count is unknown until this is actually run in production.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (scoped `DELETE ... WHERE batch = '<batch>'`)
- [x] Blast radius is stated, not assumed (isolated, new table, no other readers/writers)
- [x] No silent behavior change to an already-shipped flow — this is new, additive functionality only
