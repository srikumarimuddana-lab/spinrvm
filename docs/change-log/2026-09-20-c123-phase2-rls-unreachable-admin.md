# Change Impact & Risk Log — C123 phase 2: unreachable admin-role RLS on safety/regulatory tables

**Date:** 2026-09-20
**Author:** Claude Code
**Surface(s):** backend
**Domain (Sentry tag):** admin / security / safety
**Related issue or gap ID:** ACTION_ITEMS.md C123 (phase 2 of 2 — phase 1, migration 432, merged
earlier the same day), follow-up to C107 (migration 430)

## 1. Issue/gap identified
`safety_incidents`, `driver_insurance_periods`, and 2 direct siblings found during this change's
own investigation (`driver_insurance_period_corrections`, `driver_period_distances`) all gate
admin access on `users.role IN ('admin', 'super_admin')` — a value migration 256's
`chk_users_role_not_admin` CHECK constraint makes permanently impossible to hold. Same pattern
already fixed on 15 other tables across migrations 430 and 432; deliberately deferred from those
passes because these 4 tables are safety/regulatory-sensitive and needed their own review.

## 2. Root cause
Same as C107/phase 1: `users.role`-based admin auth was retired in favor of the `admin_staff` +
custom-JWT model, and migration 256 closed off the role value going forward, but these 4 tables'
RLS policies were never updated to match.

`driver_insurance_periods`, `driver_insurance_period_corrections`, and `driver_period_distances`
have a second, more consequential wrinkle: each table's single SELECT policy combines the driver's
own legitimate self-read access with the broken admin check in one `USING` clause via `OR`
(`driver_id = own OR <broken admin check>`, or the equivalent chained-through-original-period-id
form for the corrections table). This is why phase 1's "replace with `USING (false)`" pattern could
not be applied here unmodified — it would have also denied the driver's own, currently-working read
access, a functional regression on a regulatory audit trail (SGI / Saskatchewan Transportation Act,
7-year retention).

## 3. Fix/remediation
New migration `433_admin_role_rls_unreachable_phase2_safety_insurance.sql`:

- `safety_incidents`: its two broken admin policies ("Admin read/update safety_incidents" FOR
  SELECT, "Admin update safety_incidents" FOR UPDATE) are each standalone — no other policy's
  legitimate access shares their USING clause — so each is replaced with an explicit `USING
  (false)` deny, same shape as migration 430/432. "Reporter can read own safety_incidents" and the
  service-role bypass are untouched.
- `driver_insurance_periods`, `driver_insurance_period_corrections`, `driver_period_distances`: NOT
  a blanket deny. Each policy is rewritten to drop only the `OR <broken admin check>` disjunct,
  keeping the driver-owned-row predicate exactly as it was (verified character-by-character against
  each original by the `spinr-insurance-period-auditor` review, not just by me). Each policy keeps
  its original name and implicit PUBLIC role scope — only the USING expression changes.

**Scope note — 2 tables not in C123's original named list:** `driver_insurance_period_corrections`
(migration 355) and `driver_period_distances` (migration 249) were found while writing this
migration, not before. Both migrations' own header comments say they deliberately mirror
`driver_insurance_periods`' RLS shape ("confirm the new table's RLS/immutability pattern matches
theirs, don't diverge silently" — 355's own words), and a check against production `pg_policies`
confirmed both carry the identical entangled-OR pattern, unmodified from their original migration
text (no drift). Folded into this migration rather than filed as a separate follow-up: same fix,
same table family, same migration this change already touches, and leaving them broken while fixing
their sibling would have been inconsistent. Flagged explicitly here and in `ACTION_ITEMS.md` rather
than silently expanding scope.

Verified against live production `pg_policies` for all 4 tables immediately before writing the
migration — no out-of-band drift from the migration files.

## 4. Risk & impact on existing functionality
**Isolated, no production behavior change.** Grepped `backend/` for every reader of these 4 tables
(same method as C107/phase 1): the only Supabase client (`backend/supabase_client.py`) always uses
`SUPABASE_SERVICE_ROLE_KEY`, which bypasses RLS unconditionally — these policies have never gated a
single real backend request. The admin safety-triage dashboard (`backend/routes/admin/safety.py`)
and the regulator-export read path (`backend/routes/admin/compliance.py`, for
`driver_period_distances`) both read through this same service-role-mediated backend layer, not
direct Supabase RLS — confirmed by the `spinr-safety-sos-reviewer` and `spinr-insurance-period-auditor`
reviews respectively, not just asserted.

**Critical regression avoided by design, not by luck:** a driver's own ability to read their own
`driver_insurance_periods` / `driver_insurance_period_corrections` / `driver_period_distances` rows
is the one real, load-bearing access path in this whole change (unlike every other table this C123
work has touched, which had zero legitimate authenticated-role access to begin with). Verified this
specific path 3 ways: (1) the rewritten policy text change is a pure subtraction of the `OR <admin
check>` clause, confirmed character-by-character; (2) every pre-existing "driver can select own"
test for all 3 tables re-run and confirmed passing unchanged after the fix; (3) the append-only
immutability triggers on all 3 tables (which enforce the regulatory audit-trail guarantee
independent of RLS) are untouched — migration 433 contains zero `ALTER TABLE`/`DROP TRIGGER`/`DROP
FUNCTION` statements.

**Test-harness note:** the rewritten `test_admin_cannot_select_*` tests still seed
`role="admin"`/`"super_admin"` via `_seed_user`, which would violate migration 256's CHECK
constraint if 256 were applied to this harness — it deliberately isn't (documented in
`conftest.py`, kept out specifically so other RLS test files can still seed that role value for
their own unrelated scenarios). This means these tests prove something *strictly stronger* than
"unreachable because 256 also blocks it" — they prove the policy text itself denies an admin-role
row even in a hypothetical environment where that role value could exist (a legacy row, a restored
snapshot, 256 not yet applied) — a genuine defense-in-depth improvement, not a test artifact papering
over a gap.

No interaction with the ride state machine or money/wallet deltas. No background loop reads or
writes any of these 4 tables' RLS policies directly.

**Blast radius on the shared RLS test fixture:** this change adds one new `cur.execute(...)` call
applying migration 433 (placed after all 4 target tables are built — required a later insertion
point than an initial attempt, since `driver_insurance_period_corrections`/`driver_period_distances`
aren't built until later in `conftest.py`) plus rewrites of 4 existing tests across 3 files. No new
tables built, no fixture structure changed. Full suite re-run after merging phase 1's own changes
into this branch: 393/393 passing.

## 5. User-experience effect
None. No rider/driver/corporate-admin/internal-admin-facing behavior change — these are all
backend-only Supabase RLS policies never reached by real traffic (service-role bypass, as above).
The real SOS submission flow, the safety check-in escalation loop, the admin safety triage
dashboard, and the regulator-export path are all structurally immune to this change (confirmed by
the `spinr-safety-sos-reviewer`/`spinr-insurance-period-auditor` reviews reading the actual route
and service files, not just reasoning from the RLS layer).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/433_admin_role_rls_unreachable_phase2_safety_insurance.sql` | New migration: `safety_incidents` gets migration 430/432's blanket-deny pattern; `driver_insurance_periods`/`driver_insurance_period_corrections`/`driver_period_distances` get a tailored rewrite dropping only the broken admin OR-clause | Core fix |
| `backend/tests/rls/conftest.py` | Applies migration 433 after all 4 target tables are built | Migration 433 needs the tables to exist first |
| `backend/tests/rls/test_otp_and_safety_rls.py` | Renamed `test_admin_roles_can_select_any_incident` → `_cannot_select_`, `test_admin_can_update_incident_status` → `_cannot_update_`, both rewritten to assert denial | Migration 433 makes the old "admin can" assertions false |
| `backend/tests/rls/test_money_and_safety_rls.py` | Renamed `test_admin_can_select_any_insurance_period` → `_cannot_select_`, rewritten to assert denial; every driver-self-access test re-verified unchanged | Same as above, for `driver_insurance_periods` |
| `backend/tests/rls/test_audit_and_insurance_correction_rls.py` | Renamed and rewrote `test_admin_can_select_any_correction` and `test_admin_can_select_any_distance` to `_cannot_select_`, asserting denial | Same fix applied to the 2 siblings found during this change |
| `ACTION_ITEMS.md` | C123 marked fully resolved (both phases); the 2 siblings not in the original named scope documented explicitly | Keep the backlog item accurate |

## 7. Before/after

```sql
-- Before (migration 64, driver_insurance_periods):
CREATE POLICY driver_insurance_periods_select ON driver_insurance_periods
    FOR SELECT USING (
        driver_id = (
            SELECT id FROM drivers WHERE user_id = auth.uid()::text
        )
        OR (SELECT role FROM users WHERE id = auth.uid()::text) IN ('admin', 'super_admin')
    );
    -- The OR clause looks like it grants admin/super_admin read access to
    -- every driver's insurance-period history. Cannot actually ever match,
    -- since no users row can hold that role value.

-- After (migration 433):
CREATE POLICY driver_insurance_periods_select ON driver_insurance_periods
    FOR SELECT USING (
        driver_id = (SELECT id FROM drivers WHERE user_id = auth.uid()::text)
    );
    -- Same real-world access (driver reads own rows only), now honestly
    -- stated -- no dead disjunct implying broader access than actually
    -- exists. The driver's own access path is byte-identical to before.
```

## 8. Rollback plan
For all 4 tables: `DROP POLICY "<name>"` on the replacement and re-create the original verbatim —
exact text (confirmed against production's live `pg_policies`) is in migration 433's own header
comment. Unlike phase 1's `document_requirements` exception, all 4 of this migration's rollback
statements are verified executable — no type-mismatch or similar issue on any of these 4 tables.

Zero data changes — pure RLS policy swap, instantly reversible via `psql`, no PITR or second deploy
needed.

## 9. Verification performed
- [x] Automated tests run: full `backend/tests/rls` suite against a real local Postgres 16 —
      **393/393 passed**, re-run after merging phase 1's now-merged changes into this branch.
- [x] Blast-radius grep performed: every backend reader of the 4 affected tables (all go through
      the service-role Supabase client); the admin safety triage dashboard and regulator-export
      path specifically checked (both service-role-mediated, confirmed by reading the actual route
      files, not assumed).
- [x] Verified against live production `pg_policies` (read-only) for all 4 tables before writing
      the migration — no drift from the migration files.
- [x] Reviewed against relevant CLAUDE.md conventions: RLS pattern, append-only migration
      convention, insurance-period rules (`regulatory-sk.md`), migration numbering.
- [x] `ruff check` / `ruff format --check` on all changed Python files: clean.
- [x] Ran three adversarial reviews before treating this as final, given the higher stakes than
      phase 1: `spinr-migration-reviewer` (general correctness), `spinr-insurance-period-auditor`
      (regulatory/audit-trail risk), `spinr-safety-sos-reviewer` (SOS/emergency-flow risk). All
      three returned **no blockers, no warnings specific to this diff** — see each agent's full
      report in the PR.
- No production build/deploy run — backend-only DB migration + backend test change, not an
  admin-dashboard/rider-app/driver-app change, so `npm run build` is not applicable.

## 10. Sign-off
- [x] Rollback plan is concrete and testable (all 4 statements verified executable, unlike phase
      1's one exception).
- [x] Blast radius is stated, not assumed — including the one path that actually matters here (a
      driver's own access to their own regulatory records), verified 3 independent ways rather than
      just asserted.
- [x] No silent behavior change to an already-shipped flow — the one real access path in this whole
      change (driver's own-row read) is provably unchanged, not just "should be fine."

## What was NOT verified
- This migration has **not** been applied to production yet — committed to the repo, pending the
  normal migration-apply process (or a direct apply via Supabase MCP with explicit sign-off, as
  migrations 430/431/432 received).
- Real-world confirmation that no external tool or script directly queries these 4 tables with the
  anon/publishable key — reasoned about via the "backend always uses service-role" grep and the
  three reviewer agents' independent confirmation, not observed live.
- The historical mechanism behind phase 1's `document_requirements` type-mismatch finding remains
  unresolved (not this migration's concern — flagged in that phase's own log).
