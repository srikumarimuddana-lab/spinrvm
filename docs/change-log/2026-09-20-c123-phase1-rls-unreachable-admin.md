# Change Impact & Risk Log — C123 phase 1: unreachable admin-role RLS on 4 more tables

**Date:** 2026-09-20
**Author:** Claude Code
**Surface(s):** backend
**Domain (Sentry tag):** admin / security
**Related issue or gap ID:** ACTION_ITEMS.md C123 (phase 1 of 2; phase 2 — `safety_incidents`,
`driver_insurance_periods` — deliberately deferred, see below), follow-up to C107 (migration 430)

## 1. Issue/gap identified
Four tables' (`audit_logs`, `push_tokens`, `cloud_messages`, `document_requirements`) admin-read/
admin-write RLS policies check `users.role IN ('admin', 'super_admin')` (or, on
`document_requirements`, the older `role = 'admin'`-only form) — a value migration 256's
`chk_users_role_not_admin` CHECK constraint makes permanently impossible to hold. Same pattern
migration 430 (C107) already fixed on 11 other tables; found during that review by grepping every
migration for the same pattern rather than trusting C107's original "10 tables" scope as complete.

## 2. Root cause
Same as C107: `users.role`-based admin auth was retired in favor of the `admin_staff` +
custom-JWT model, and migration 256 closed off the role value going forward, but the RLS policies
gating these 4 tables were never updated to match — they still read as though they grant
admin/super_admin access via Supabase Auth's `auth.uid()`, which no admin session ever produces
(admin auth is a custom JWT verified entirely inside FastAPI; it never creates a Supabase Auth
session, so `auth.uid()` can never equal an admin's identity regardless of `users.role`).

## 3. Fix/remediation
New migration `432_admin_role_rls_unreachable_phase1.sql`, applying C107's fix pattern:
- `audit_logs`, `push_tokens`: both `SELECT`-only, same shape as migration 430's 11 tables —
  replaced with an explicit `USING (false)` deny.
- `cloud_messages`, `document_requirements`: both `FOR ALL` with no `WITH CHECK` — the same
  missing-`WITH CHECK` shape migration 416 fixed on `corporate_accounts`. Replaced with
  `FOR ALL ... USING (false)`, which Postgres also applies as the (absent) `WITH CHECK` for
  INSERT/UPDATE. Precision note: since migration 256 already makes the admin-role predicate
  permanently false, the write path was already just as unreachable as the read path before this
  migration — there wasn't a live write-side gap distinct from the read-side unreachability. The
  explicit `false` is still an improvement (clearer/more honest than an implicit fallback through
  a broken `EXISTS`), just not literally "closing a gap that was open."

Verified against live production `pg_policies` for all 4 tables immediately before writing the
migration — text matches each table's migration file exactly, no out-of-band drift (unlike C124's
`corporate_accounts` finding).

**Genuine finding, not part of the fix's correctness (but it does affect the documented rollback —
see §8):** `document_requirements`'s original "Admin full access for requirements" policy compares
`public.users.id = auth.uid()` with no cast — `users.id` is `text`, `auth.uid()` returns `uuid`.
Reproduced directly (both against a local Postgres 16 sandbox and against production's own
Postgres 17.6 via a bare `SELECT 'x'::text = gen_random_uuid()`) that this comparison has no valid
operator and a fresh `CREATE POLICY` with this exact text fails outright (`operator does not
exist: text = uuid`).

Production's live `pg_policies` has this exact text stored, and an initial `SET LOCAL ROLE
authenticated` + `SELECT` test against production returned rows without error — but the mandatory
`spinr-migration-reviewer` pass that reviewed this change showed that test doesn't actually prove
the clause itself is sound: `EXPLAIN (VERBOSE, COSTS OFF)` confirms Postgres constant-folds
`(true) OR (EXISTS(<this clause>))` down to no filter at all, because `document_requirements`'
other policy ("Public read access for requirements") is an unconditional `USING (true)` — so a
plain `SELECT` never actually reaches the broken clause. Isolating it would need an authenticated
UPDATE/DELETE/INSERT attempt instead, which the Public-read policy doesn't cover; that wasn't run
against production (out of caution — no destructive test against production's live table).

An initial working theory ("Postgres binds a policy's operators once at `CREATE POLICY` time and
doesn't re-typecheck the stored expression tree against a column's later-changed type") was also
tested by the reviewer and ruled out in its most literal form: both `ALTER TABLE users ALTER
COLUMN id TYPE text` and `DROP TABLE users` (against a reproduction with a dependent policy in
place) are refused outright by Postgres, which names the dependent policy in the error. So the
divergence did not happen via ordinary ALTER/DROP DDL; the actual mechanism (a Supabase-side
restore/fork/`pg_upgrade`-style catalog carryover, or direct catalog surgery, are the remaining
candidates) is unresolved and not traced further here — the finding is flagged as unresolved, not
guessed at. None of this changes the fix's correctness: `DROP POLICY IF EXISTS` doesn't require
re-parsing the stored qual, only re-creating it does, so dropping this policy works regardless of
which explanation is right. It does mean the *original* policy cannot be replayed verbatim in the
RLS test harness, and cannot be used as a literal rollback step — see the harness changes below
and §8.

**Deliberately not touched in this PR:** `safety_incidents` and `driver_insurance_periods` (C123's
other 2 tables) — both safety/regulatory-sensitive per the owner's explicit choice (2026-09-20,
via `AskUserQuestion`) to split C123 into two phases. `driver_insurance_periods` additionally needs
a different, non-blanket-deny fix: its one `SELECT` policy ORs the driver's own legitimate
self-read access together with the broken admin check (`driver_id = own OR <broken admin check>`),
so migration 430/432's "replace with `USING (false)`" pattern would also break the driver's real,
currently-working ability to read their own insurance-period history — a functional regression on
a regulatory audit surface, not just closing a dead path. Tracked as C123 phase 2.

## 4. Risk & impact on existing functionality
**Isolated, no production behavior change.** Grepped `backend/` for every reader of these 4 tables
(same method as C107): the only Supabase client (`backend/supabase_client.py`) always uses
`SUPABASE_SERVICE_ROLE_KEY`, which bypasses RLS unconditionally — these policies have never gated a
single real backend request. Defense-in-depth correctness fix, not a live-traffic fix.

- `audit_logs`: no other RLS policy or grant on this table is touched. Migration 51's REVOKE
  narrowing, append-only triggers (51/56/57), and the 7y retention purge step are all unaffected.
- `push_tokens`: "Users manage own push tokens" (the real, working access path) is untouched —
  only the separate "Admin read push_tokens" policy is replaced.
- `cloud_messages`: no owner-row policy exists on this table at all (admin-broadcast only) — the
  `FOR ALL ... USING (false)` replacement denies every command for `authenticated`, same net effect
  as before (unreachable), now honestly stated and with the write gap also closed.
- `document_requirements`: "Public read access for requirements" (the driver app's actual read
  path, `USING (true)` for `anon` + `authenticated`) is untouched — only "Admin full access for
  requirements" is replaced.

**Accepted risk, explicit rather than an oversight:** unlike `audit_logs` (migration 51) and
`corporate_accounts` (migration 416), which each pair their RLS narrowing with a grant-layer
`REVOKE INSERT, UPDATE, DELETE, TRUNCATE ... FROM authenticated` as a second layer of protection,
`cloud_messages` and `document_requirements` get no such REVOKE in this migration —
`authenticated` still holds the full baseline INSERT/UPDATE/DELETE grant, and the RLS policy is
the *only* thing stopping a write. This is a deliberate scope decision (C123 is about the broken
role-check pattern, not a grant-narrowing pass across every table that lacks one) flagged here so
a future reader knows it was considered, not missed — a reasonable phase-2-or-later candidate if
the added defense-in-depth layer is wanted.

No interaction with the ride state machine or money/wallet deltas. No background loop reads or
writes any of these 4 tables' RLS policies directly.

**Blast radius on the shared RLS test fixture** (`backend/tests/rls/conftest.py`, touched by 3
other concurrent-session test files in the last change per C107's own log): this PR *adds* new
fixture setup (building `cloud_messages`, `push_tokens`, `document_requirements`, none of which
existed in the harness before) rather than modifying any existing table's fixture setup, so it
carries none of C107's "migration 256 broke 49 unrelated tests" risk. Ran the full
`backend/tests/rls` suite after the change: 393/393 passing (377 pre-existing + 16 new — 12 from
the initial pass, plus 4 UPDATE/DELETE tests for `cloud_messages`/`document_requirements` added
after the mandatory review flagged that gap), including every file added by other concurrent
sessions.

## 5. User-experience effect
None. No rider/driver/corporate-admin/internal-admin-facing behavior change — these are all
backend-only Supabase RLS policies never reached by real traffic (service-role bypass, as above).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/432_admin_role_rls_unreachable_phase1.sql` | New migration: replaces `audit_logs`/`push_tokens`'s `SELECT`-only and `cloud_messages`/`document_requirements`'s `FOR ALL` unreachable admin policies with an explicit `USING (false)` deny | Core fix |
| `backend/tests/rls/conftest.py` | Adds fixture setup for `cloud_messages`, `push_tokens`, `document_requirements` (none built by the harness before); adds the `uuid-ossp` extension (needed by `push_tokens`/`document_requirements`'s `uuid_generate_v4()` default, never previously created by the harness); applies migration 432. Deliberately does **not** build `document_requirements`'s original admin policy (see the type-mismatch finding above) | These tables need real RLS coverage for the first time; migration 432 needs them to exist to apply |
| `backend/tests/rls/test_audit_and_insurance_correction_rls.py` | Renamed `test_admin_authenticated_can_select_audit_logs` → `_cannot_select_`, rewritten to assert an empty result instead of a returned row; module docstring updated | Migration 432 makes the old "admin can select" assertion false, same as C107's precedent for `corporate_accounts` |
| `backend/tests/rls/test_notifications_and_docs_admin_rls.py` | New file: first-ever RLS coverage for `cloud_messages` (admin denied SELECT+INSERT+UPDATE+DELETE, rider denied, service-role bypass), `push_tokens` (own-token access preserved, admin denied, service-role bypass), `document_requirements` (public read preserved for anon+authenticated, admin denied INSERT+UPDATE+DELETE, service-role bypass) | These 3 tables had zero RLS test coverage before this change; UPDATE/DELETE coverage added after the mandatory review flagged INSERT-only as an incomplete pin of the FOR-ALL write-path claim |
| `ACTION_ITEMS.md` | C123 marked phase-1-resolved / phase-2-open; corrected the item's own "7 tables" vs. 6-item list ambiguity (document_requirements has 2 independent problems, not 2 tables); phase 2's scope (including the `driver_insurance_periods` tailored-fix requirement) spelled out explicitly for whoever picks it up | Keep the backlog item accurate and actionable rather than stale once phase 1 lands |

## 7. Before/after

```sql
-- Before (migration 06, cloud_messages — FOR ALL, no WITH CHECK):
CREATE POLICY "Admin full access cloud_messages"
ON cloud_messages FOR ALL TO authenticated
USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text
                 AND users.role IN ('admin', 'super_admin')));
    -- Looks like it grants admin/super_admin full access. Cannot actually
    -- ever match, since no users row can hold that role value -- and even
    -- if it could, no WITH CHECK means writes would bypass row content
    -- validation entirely.

-- After (migration 432):
CREATE POLICY "cloud_messages admin RLS unreachable (service role only)"
    ON cloud_messages FOR ALL TO authenticated USING (false);
    -- Same effective behavior (always denies authenticated), now honestly
    -- named, and the missing-WITH-CHECK gap is closed as a side effect
    -- (Postgres uses USING as the implicit WITH CHECK when none is given).
```

## 8. Rollback plan
For `audit_logs`, `push_tokens`, `cloud_messages`: `DROP POLICY "<table> admin RLS unreachable
(service role only)"` and re-create the original policy verbatim — exact text (confirmed against
production's live `pg_policies`) is in migration 432's own header comment. Verified executable.

For `document_requirements`: the original policy's text cannot be replayed — see §3's finding
above. Its rollback is `DROP POLICY "document_requirements admin RLS unreachable (service role
only)" ON document_requirements;` alone, which leaves the table on RLS's implicit default-deny for
that policy slot. This is functionally identical to the original (the original could never grant
access either, whatever the mechanism that let its mismatched text persist), and
`document_requirements`' separate "Public read access for requirements" policy is untouched by
either the migration or this rollback step, so the driver app's actual read path is unaffected
either way. **Correction from an earlier draft of this document:** that draft described all 4
rollback statements as "confirmed byte-exact ... instantly reversible via psql" — true for 3 of
the 4, but the `document_requirements` restore statement, taken literally, does not execute (the
same type-mismatch bug §3 describes bites here too; caught by the mandatory
`spinr-migration-reviewer` pass, which reproduced the failure directly). Corrected above rather
than left standing.

Zero data changes in either case — pure RLS policy swap, no PITR or second deploy needed.

## 9. Verification performed
- [x] Automated tests run: full `backend/tests/rls` suite against a real local Postgres 16
      (`TEST_DATABASE_URL` pointed at localhost) — **393/393 passed**.
- [x] Blast-radius grep performed: every backend reader of `audit_logs`/`push_tokens`/
      `cloud_messages`/`document_requirements` (all go through the service-role Supabase client);
      every RLS test file for existing seeding of `role="admin"`/`"super_admin"` that might be
      affected (only `test_admin_authenticated_can_select_audit_logs`, updated above).
- [x] Verified against live production `pg_policies` (read-only) for all 4 tables before writing
      the migration — no drift from the migration files.
- [x] Reviewed against relevant CLAUDE.md conventions: RLS pattern (migration rules), append-only
      migration convention, `backend/migrations/CLAUDE.md`'s numbering rule (432 is the next free
      slot as of this PR).
- [x] `ruff check` / `ruff format --check` on all 3 changed/added Python files: clean.
- [ ] Feature-flagged: not applicable — RLS policy change with zero live-traffic impact (service
      role bypasses RLS unconditionally), same reasoning as migration 430.
- Ran the mandatory adversarial review (`spinr-migration-reviewer` agent) against the actual diff
      before treating this as final — see PR for its findings and how each was resolved.
- No production build/deploy run — backend-only DB migration + backend test change, not an
      admin-dashboard/rider-app/driver-app change, so `npm run build` is not applicable.

## 10. Sign-off
- [x] Rollback plan is concrete and testable (3 of 4 tables: exact original policy text confirmed
      byte-exact against production and independently verified executable; `document_requirements`:
      a corrected, verified-executable single-DROP rollback — see §8).
- [x] Blast radius is stated, not assumed (grep results above, not "checked, looks fine").
- [x] No silent behavior change to an already-shipped flow (none of these policies gate live
      traffic; explicitly confirmed via the service-role grep, not assumed).

## What was NOT verified
- This migration has **not** been applied to production yet — committed to the repo, pending the
  normal migration-apply process (or a direct apply via Supabase MCP with explicit sign-off, as
  migrations 430/431 received).
- The historical mechanism behind `document_requirements`'s type-mismatch finding (which migration
  originally changed `users.id`'s type, and exactly when) was not traced — the finding is real and
  reproduced, but its origin story is not. Does not affect the fix's correctness.
- Real-world confirmation that no external tool or script directly queries these 4 tables with the
  anon/publishable key (the scenario this policy defends against) — reasoned about via the
  "backend always uses service-role" grep, not observed live.
- C123 phase 2 (`safety_incidents`, `driver_insurance_periods`) is explicitly out of scope for this
  change and remains open — see ACTION_ITEMS.md C123 for the tailored fix `driver_insurance_periods`
  needs.
