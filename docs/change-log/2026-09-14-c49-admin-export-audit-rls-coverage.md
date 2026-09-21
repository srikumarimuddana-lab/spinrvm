# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (session `session_01173usfHtfdzMMzYpWeWmVm`) |
| Surface(s) | backend (test-only) |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `mvapps/c49-admin-export-audit-rls` |
| Related issue or gap ID | ACTION_ITEMS.md C49 (progressed, not closed) |

## 1. Issue / gap identified

Three tables had zero DB-role-level RLS test coverage: `data_transfer_export_jobs`,
`compliance_export_events`, and `admin_export_approval_requests` — the admin
PII-export audit trail. The mocked test suite can never exercise real
Postgres RLS.

## 2. Root cause

Not a bug — a coverage gap, the next slice of the long-running C49 backlog
item. Picked this trio deliberately: all three back the same dual-approval/
export-audit hardening already done at the app layer earlier this same
session (B1 — gated the SIN/DOB backfill router to super_admin; W2a-c —
added per-handler super_admin rechecks to the data_transfer export/import/
search, data_transfer_jobs, sgi_forms, export_approvals, and
migration_status handlers). This round adds the DB-level backstop under
that same surface.

## 3. Fix / remediation

Added `backend/tests/rls/test_admin_export_audit_rls.py` (31 tests) and
extended `conftest.py` to apply the real shipped SQL, in filename-sort
order (matching how `run_migrations.py` would actually apply them):

- `data_transfer_export_jobs`: migration 262 (create + 4 service-role-only
  policies) + 264 (additive `reason` column).
- `compliance_export_events`: migration 263 (create + admin/super_admin-
  only SELECT policy + append-only trigger), plus an `_extract_section()`
  slice of 285 — just the trigger function's redefinition (adds a
  session-flag-gated DELETE exception for the retention purge), not its
  much larger `purge_pii_retention()` body, which reaches tables outside
  this harness's scope (`driver_location_history`, `ride_routes`,
  `price_searches`, etc.).
- `admin_export_approval_requests`: migration 268 (create + 4 service-
  role-only policies + a `no_self_approval` CHECK constraint + a
  `settings.dual_approval_exports_enabled` flag column).
- 270/274/278: each drops an admin-identity FK (`requested_by`/
  `decided_by`/`requested_by_admin_id`/`admin_user_id` → `users(id)`) that
  could never be satisfied by a real admin caller — admin identity lives
  in `admin_staff` or an env-var-creds sentinel like `"admin-001"`, never
  in `users`. Confirmed live in each migration's own header: zero rows
  were ever written to any of the three tables before its fix. Applied
  here (not skipped) so the harness doesn't silently paper over the
  removed constraint — see the FK-regression tests below.

**Alternative considered:** folding these 3 tables into the existing
`test_stripe_admin_tables_rls.py` (already an "admin-only-read" themed
file). **Rejected** — every prior round keeps one file per theme, and
these are a distinct theme (export/compliance audit trail, not Stripe);
mixing would make that file's own docstring inaccurate for future rounds.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, test-only.** No production code changed. Two
  files: `backend/tests/rls/conftest.py` (extended) and the new test file.
  Grepped every other file in `backend/tests/rls/` for a reference to any
  of the 3 new tables — none found.
- **Review used:** `spinr-security-auditor` via the Agent tool (available
  in this session). Independently re-derived every asserted policy/GRANT/
  trigger from the actual migration SQL rather than trusting this file's
  own docstring claims, ran the suite itself against a real Postgres, and
  confirmed all 3 tables' real production read/write paths go through the
  service-role client only (grepped `routes/admin/`, `services/`,
  `db_supabase.py`/`supabase_client.py` — no anon/authenticated Supabase
  client exists anywhere in the backend).
- **One real finding, fixed before merge:** this file's own docstring
  claimed `data_transfer_export_jobs` and `admin_export_approval_requests`
  are "identical shape," but only the former had a DELETE-denial test —
  `admin_export_approval_requests`' `service_delete` policy (migration
  268) went unexercised by any negative case, a real gap relative to the
  file's own parity claim. Fixed: added
  `test_authenticated_cannot_delete_approval_request`.
- **Noted, not a bug:** neither table's `FOR DELETE TO service_role`
  policy is exercised by a *positive* test either, because no production
  code path issues a hard DELETE on any of the 3 tables today
  (`data_transfer_export_jobs` only ever soft-deletes via a `deleted_at`
  UPDATE in `utils/data_export_purge.py`; no `.delete(`/`delete_many` call
  exists anywhere for the other two) — genuinely dead DB-layer capability
  today, not a coverage gap.
- **No new production bug surfaced this round** (unlike the prior
  2026-09-14 round, which found C112) — `compliance_export_events`'
  flag-gated DELETE path was checked end-to-end and confirmed to actually
  work, a real positive contrast to C112's finding for `audit_logs` (a
  *second*, flag-unaware trigger there silently defeats the first's
  exception; `compliance_export_events` has only the one trigger, whose
  function body was replaced in place by migration 285, so there's nothing
  for a second trigger to conflict with).

## 5. User-experience effect

None. Test-only change; no production code, no deploy artifact.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/rls/conftest.py` | Applies migrations 262, 263, 264, 268, 270, 274, 278 in order, plus an extracted slice of 285; adds baseline GRANTs for the 3 new tables; adds them to the TRUNCATE list; updates the module docstring's coverage-scope list | Wires the harness to the 3 tables' real shipped schema + policies |
| `backend/tests/rls/test_admin_export_audit_rls.py` | New file, 31 tests | DB-role-level RLS coverage, a CHECK-constraint test, and FK-removal regression tests |
| `ACTION_ITEMS.md` | C49 status updated with this round's progress and a first-ever published remaining-table list | Keep the backlog record accurate |

## 7. Before / after

Not applicable — pure test addition, no behavior change to any existing
passing test (confirmed: full suite went from 295/295 to 326/326 passing).

## 8. Rollback plan

`git-revert-safe` — a plain revert removes the new test file and reverts
`conftest.py`/`ACTION_ITEMS.md`. No data, no migration, no config involved
either direction.

## 9. Verification performed

- [x] Automated tests run: new file standalone (31 passed, after fixing
      the one missing-parity test the review found) and the full
      `backend/tests/rls/` suite (**326 passed, 0 failed**) against a real
      local Postgres 16 instance (the same `rlstest` cluster this
      session's earlier C49 round stood up, on port 5544).
- [x] `spinr-security-auditor` ran independently, re-deriving every
      assertion from the migration SQL directly and re-running the suite
      itself rather than trusting this diff's own claims — verdict: safe
      to merge, one finding fixed (see §4).
- [x] Blast-radius grep performed: every other RLS test file checked for a
      reference to the 3 new tables (none found); every backend route/
      service file checked for a non-service-role read/write path to any
      of the 3 tables (none found).
- [x] Concurrent-work check performed before starting: `git fetch origin
      main` + an open-PR search for anything touching `backend/tests/rls/`
      or `ACTION_ITEMS.md` — none found, confirmed via a fresh Agent-tool
      search of live PR state, not assumed from an earlier check.
- [ ] Not run against CI's actual `postgres:15` service container — run
      against a real local Postgres 16 instead, same caveat every prior
      round of this backlog item has carried; no version-specific syntax
      is used by any of the applied migrations.

## What was NOT verified

- Not run against CI's own `postgres:15` container (see above).
- The corrected "39 of ~70 tables" running total depends on a repo-wide
  regex sweep, not a byte-for-byte audit of every migration file — flagged
  explicitly in the ACTION_ITEMS.md entry as corrected once already this
  round (an earlier round's single-line regex undercounted); a future
  round could still find more drift.
- Whether migrations 262/263/264/268/270/274/278/285 have actually been
  applied to the live `schema_migrations` table in production is not
  confirmed here — no production DB access from this environment. The
  policy/trigger *logic* itself is confirmed by direct reproduction
  against real Postgres, not assumed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`,
      test-only.
- [x] Blast radius stated: two files (plus the ACTION_ITEMS.md doc-only
      change), test-only, zero other consumers found.
- [x] No silent behavior change to a working flow — nothing in production
      changed.
