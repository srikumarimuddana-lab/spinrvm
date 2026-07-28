# Change Impact & Risk Log — Data Transfer module: export core (Phase 1.1)

## Issue/gap identified
No export path exists for moving a full driver/rider record (profile, documents,
ride history, insurance-period audit trail) between Spinr's own admin environments.
Existing `driver_import.py`/`rider_import.py` are import-only, CSV-metadata-only.

## Root cause
The bulk-operations tooling was built for one-way legacy-CSV onboarding; no one
built the export half or document/history bundling because the original need was
"bring drivers in," not "move drivers between environments."

## Fix/remediation
New `backend/services/data_transfer/` package:
- `entity_export_service.py` — gathers a full-fidelity (unredacted) bundle for a
  user/driver: profile, notification prefs, rides, `driver_documents` (with raw
  bytes fetched from the `driver-documents` Storage bucket), and
  `driver_insurance_periods`. Supports multi-entity batch gather with per-entity
  failure isolation (`gather_entity_bundles`, `return_exceptions=True`).
- `bundle_zip_builder.py` — builds a ZIP with one subfolder per entity (CSV +
  JSON + original-format document files + README), modeled on the existing
  `tax_exports.py::_build_export_zip` shape.
- `migrations/262_data_transfer_export_jobs.sql` — new table tracking export
  batches (for the purge loop and a future Jobs tab), RLS enabled with no public
  policies (service-role-only), reviewed by the spinr-migration-reviewer agent:
  SAFE TO APPLY, no blockers.

This is Phase 1.1 of a 6-phase plan; no route or UI wired yet, so this commit is
**inert in production** — new files/table, zero existing call sites changed.

## Risk & impact on existing functionality
Blast radius: zero existing consumers. Grepped for all callers of
`db_supabase.get_rows`/`insert_one` on `drivers`, `users`, `rides`,
`driver_documents`, `driver_insurance_periods`, `notification_preferences` —
this commit only *reads* those tables via the same helper functions every other
route already uses (no new query patterns, no writes to existing tables). The
new `data_transfer_export_jobs` table has no other readers/writers anywhere in
the codebase yet (Phase 1.2 wires the only writer). No existing migration was
edited (append-only respected). No route is registered in `server.py` yet, so
no new HTTP surface exists until Phase 1.2.

## User experience effect
None. No UI or endpoint is reachable yet.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/__init__.py` | New empty package init | New service subpackage |
| `backend/services/data_transfer/entity_export_service.py` | New: gather full entity bundle (profile+docs+rides+insurance periods) | Core data-gathering for export |
| `backend/services/data_transfer/bundle_zip_builder.py` | New: build multi-entity ZIP | Bundle packaging for export |
| `backend/migrations/262_data_transfer_export_jobs.sql` | New table `data_transfer_export_jobs` | Track export batches for purge/Jobs tab |

## Before/after snippet
N/A — purely additive; no existing behavior-changing diff.

## Rollback plan
Delete the three new service files and drop the migration
(`DROP TABLE IF EXISTS data_transfer_export_jobs;`) — safe because no other code
references either yet (confirmed by grep, zero existing call sites). No feature
flag needed at this stage since nothing is wired to a route.

## Verification performed
- `python3 -m py_compile` on all three new `.py` files — passes.
- `spinr-migration-reviewer` subagent review of migration 262 against
  `backend/migrations/CLAUDE.md` conventions and the closest analog
  (`200_data_export_objects.sql`) — verdict: SAFE TO APPLY, no blockers.
- Confirmed 262 is the next free migration number (`ls backend/migrations | sort -V | tail`).

## What was NOT verified
- No integration test run yet — `db_supabase.get_rows`/`insert_one` calls are
  unexercised against a real or mocked Supabase client (no unit test file added
  in this subtask; `test_entity_export_service.py` is planned for a later
  subtask once the route exists to test against).
- Migration not applied to any live or staging Supabase project — SQL reviewed
  but not executed.
- Document byte-fetch path (`_fetch_document_bytes`) not exercised against a
  real `driver-documents` bucket object.
