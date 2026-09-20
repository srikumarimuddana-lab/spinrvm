# Change Impact & Risk Log — C107: admin-role RLS policies formalized as service-role-only

**Date:** 2026-09-20
**Related:** ACTION_ITEMS.md C107 (already closed upstream by a different, non-policy fix — see "Reconciliation with upstream" below; this change layers additional hardening on top, per explicit owner decision), C121 (new, filed by this change)

## Reconciliation with upstream C107

While preparing this change, a separate concurrent session had already closed C107 (2026-09-13) via a different remediation: a production `users` data cleanup (zero rows with `role IN ('admin', 'super_admin')` as of that cleanup) plus migration 256's `chk_users_role_not_admin` CHECK constraint, which blocks that role value from ever being written again. That fix is a data/schema guarantee, not an RLS policy rewrite — the "Admin read `<table>`" policies on the 11 affected tables still literally read `users.role IN ('admin', 'super_admin')`, just against a column that can no longer hold either value.

This change was developed independently before that closure was discovered, and takes a different, complementary approach: it rewrites the policies themselves to `USING (false)`, so the denial no longer depends on the CHECK constraint holding. Presented side-by-side to the repo owner (upstream fix: data cleanup + CHECK constraint, no policy change, no new test coverage; this fix: explicit policy rewrite + first-ever direct test coverage for all 11 tables' admin-denial behavior), the owner's decision was **"Ship both"** — the two are not in tension (this migration only tightens an already-correct-in-practice state) and both stay in place. C107's existing 2026-09-13 closure text in ACTION_ITEMS.md is kept intact; this migration is recorded there as additional hardening layered on top, not a re-decision.

## Issue/gap identified
10 tables' (per C107's ticket) plus `disputes`' admin-read RLS policies check `users.role IN ('admin', 'super_admin')` — a value migration 256 makes permanently impossible to hold, and which a 2026-09-13 production cleanup already confirmed holds no rows. The policies themselves, however, still read as though they grant admin/super_admin access.

## Root cause
Migration 256 (`chk_users_role_not_admin`) added a CHECK constraint blocking `users.role` from ever holding `'admin'`/`'super_admin'` again, as part of retiring `users.role`-based admin auth in favor of the `admin_staff` + custom-JWT model. Migrations 142/416's RLS policies on 11 tables were never updated to match — they still gate SELECT via the now-impossible role value. Confirmed empirically (2026-09-20): zero rows in production `users` have `role IN ('admin', 'super_admin')`. Investigated whether "check `admin_staff` instead" would fix it: it would not — `admin_staff` (migration 18) has no column linking it to a Supabase Auth user, and admin authentication (`_verify_admin_payload`, `backend/dependencies/__init__.py`) is a custom JWT verified entirely inside the FastAPI app, which never creates a Supabase Auth session. There is no path for `auth.uid()` to ever equal an `admin_staff.id`.

## Fix/remediation
New migration `430_admin_role_rls_unreachable_service_role_only.sql`: replaces the `"Admin read <table>"` policy on all 11 affected tables with an explicit `USING (false)` policy, honestly documenting that no anon/authenticated role can ever satisfy it — service-role (which bypasses RLS entirely) is the only real access path, matching how these tables are actually read/written today. Does not touch the separate, still-fully-reachable "owner reads own row" policies (`auth.uid() = user_id`) on `disputes`/`corporate_members`/`corporate_member_allowances`/`corporate_allowance_requests`, nor any GRANT statements (those "own row" policies depend on the existing base GRANT to run at all).

## Risk & impact on existing functionality
**Isolated, no production behavior change.** Grepped `backend/` for every reader of these 11 tables: the only Supabase client (`backend/supabase_client.py`) always uses `SUPABASE_SERVICE_ROLE_KEY`, which bypasses RLS unconditionally — these policies have never gated a single real backend request. This is a defense-in-depth correctness fix (what happens if the anon/publishable key, shipped in mobile app bundles, is ever used to query Supabase directly), not a live-traffic fix. Before this change, that scenario was already denied (fail-closed, since no role could satisfy the broken check, and separately since no `users` row can hold that role value at all after the 2026-09-13 cleanup); after, it's denied for the same reason but the RLS policy set now says so explicitly instead of appearing to grant access it never could.

**Blast radius found and corrected during this change:** an earlier draft of this fix also added migration 256 (the CHECK constraint) to the shared RLS test fixture (`backend/tests/rls/conftest.py`), reasoning that tests should run against "the real current schema." That draft broke **49 tests** across several files added by other concurrent sessions during this work (`test_admin_export_audit_rls.py`, `test_audit_and_insurance_correction_rls.py`, `test_otp_and_safety_rls.py`, `test_ride_distance_integrity_rls.py`, plus new `ride_payment_sources` tests merged into `test_corporate_billing_rls.py`) — all of which seed `role="admin"`/`"super_admin"` for their own, unrelated scenarios on tables migration 430 doesn't touch. Root cause: migration 430's `USING (false)` policy doesn't need migration 256 present to be correct — it denies unconditionally regardless of whether the role value could ever be seeded. **Migration 256 was removed from the shared fixture entirely** (it stays applied to production directly, per the upstream C107 fix, but is not exercised by this harness) and the affected test files were rewritten to seed `role="admin"`/`"super_admin"` directly and assert the SELECT returns zero rows, pinning migration 430's actual policy behavior rather than a CHECK-constraint side effect. Full suite re-run confirmed **376/376 passing** with this narrower fixture change.

## User experience effect
None. No rider/driver/corporate-admin/internal-admin-facing behavior changes — internal admin dashboard reads go through the backend's service-role-authenticated REST API, never through Supabase RLS directly.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/migrations/430_admin_role_rls_unreachable_service_role_only.sql` | New migration: replaces 11 tables' unreachable admin-read policy with an explicit `USING (false)` | Core fix |
| `backend/tests/rls/conftest.py` | Added `disputes` table (migration 10) + its migration-142 §1 narrowing, previously never built by this harness at all; applies migration 430. Deliberately does **not** apply migration 256 (see blast-radius note above) | migration 430 needs `disputes` to exist to apply; 256 stays out to avoid blocking every other test file's ability to seed `role="admin"` for unrelated scenarios |
| `backend/tests/rls/test_corporate_billing_rls.py` | Renamed `test_admin_can_select`/`test_super_admin_can_select` (9 tables, parametrized) and the two `corporate_accounts`/`ride_payment_sources` equivalents to `test_*_cannot_select`, rewritten to assert an empty result instead of a CHECK violation. Restored `test_admin_cannot_write_corporate_account` (redundant coverage, kept for minimal diff against upstream `main`) | Tests must pin migration 430's actual policy behavior directly, without depending on migration 256 being in the fixture |
| `backend/tests/rls/test_corporate_accounts_super_admin_fix.py` | Rewrote the two admin/super-admin-can-select tests to assert denial instead of removing them; kept rider/anon/service-role/insert-denial tests unchanged | Same as above |
| `backend/tests/rls/test_money_and_safety_rls.py` | **Fully reverted to upstream `main`** — `driver_insurance_periods` is untouched by migration 430 and migration 256 is no longer in the fixture, so this file needed no change at all | An earlier draft touched this file for a fixture-consequence reason that no longer applies |
| `backend/tests/rls/test_disputes_rls.py` | New file: first-ever RLS test coverage for `disputes` (rider-own-row, stranger-denied, anon-denied, insert-denied, service-role-bypass, admin-denied, super-admin-denied) | `disputes` was newly built into the fixture by this change and is one of migration 430's 11 tables |
| `ACTION_ITEMS.md` | Appended this work as a new paragraph under C107's existing 2026-09-13 closure (kept intact, not overwritten); filed the 7-additional-table finding as new item **C121** (renumbered twice: an initial "C108" collided with the real, pre-existing C108 about `auth.users`; the next pick, "C116", then collided with a second, unrelated, concurrently-filed C116 about `driver-map.tsx`) | Layered hardening, not a re-decision of the existing closure; avoid colliding with concurrently-filed items of the same number |

## Before/after snippet

```sql
-- Before (migrations 142 / 416, per table):
CREATE POLICY "Admin read corporate_wallets"
    ON corporate_wallets FOR SELECT TO authenticated
    USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text
                     AND users.role IN ('admin', 'super_admin')));
    -- Looks like it grants admin/super_admin read access. Cannot actually
    -- ever match a row, since no users row can hold that role value.

-- After (migration 430):
CREATE POLICY "corporate_wallets admin RLS unreachable (service role only)"
    ON corporate_wallets FOR SELECT TO authenticated USING (false);
    -- Same effective behavior (always denies authenticated), now honestly named.
```

## Rollback plan
`DROP POLICY "<table> admin RLS unreachable (service role only)"` on each of the 11 tables and re-run migrations 142 §2 / 416 to restore the original policies verbatim (full commands in migration 430's own header comment). Zero data changes in this migration — pure RLS policy swap, instantly reversible via `psql`, no PITR or second deploy needed.

## Verification performed
- Ran the full `backend/tests/rls` suite against a real local Postgres 16 instance (`TEST_DATABASE_URL` pointed at localhost with a throwaway local password, per this repo's documented RLS testing convention): **376/376 passed**, re-run fresh against the current state of `origin/main` (including test files added by other concurrent sessions during this work) after correcting the migration-256-in-fixture blast radius described above.
- Directly queried production (`soavhtdhefowwvforzwb`, read-only `SELECT count(*)`) to confirm zero `users` rows currently hold `role IN ('admin', 'super_admin')` — the empirical basis for "this policy is unreachable today," not just theoretical.
- Grepped every migration file for the same `role IN ('admin', 'super_admin')` / `role = 'admin'` pattern to find the complete affected-table list, not just the ticket's named 10.
- Grepped every RLS test file under `backend/tests/rls/` for `role="admin"`/`role="super_admin"` seeding to confirm no file outside the 11 tables migration 430 touches is affected by this change.
- `ruff check` on all 5 changed/added Python files: clean.
- No production build/deploy run — this is a backend-only DB migration + backend test change; not an admin-dashboard/rider-app/driver-app frontend change, so `npm run build` is not applicable.

## What was NOT verified
- This migration has **not** been applied to production — it is committed to the repo only, pending the normal migration-apply process (`python -m backend.scripts.run_migrations`).
- Real-world confirmation that no external tool or script directly queries Supabase with the anon/publishable key against these 11 tables (the scenario this policy defends against) — reasoned about via the "backend always uses service-role" grep, not observed live.
- The 7 additional tables found with the identical pattern (`audit_logs`, `safety_incidents`, `driver_insurance_periods`, `cloud_messages`, `push_tokens`, `document_requirements`) are explicitly **not** fixed by this change — filed as **C121**, deliberately deferred given `safety_incidents`/`driver_insurance_periods`' regulatory sensitivity warrants its own review, not folding into this change.
