# Change Impact & Risk Log — C129: two RLS policies missed by the C107/C123 unreachable-admin-role sweeps, plus a missing append-only trigger

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Claude Code session (proactive edge-case/error-handling sweep, on behalf of ittalenthire.ca@gmail.com) |
| Surface(s) | backend (database, RLS) |
| Domain (Sentry tag) | — (no runtime code changed; DB policy/trigger only) |
| PR / commit link | (this branch, `fix/financial-ledger-rls-unreachable-c129`) |
| Related issue or gap ID | ACTION_ITEMS.md C129 |

## 1. Issue / gap identified

Two RLS policies gate access on `(SELECT role FROM users WHERE id = auth.uid()::text) = 'admin'`:
`financial_event_entries_select` (migration 286) and `reconciliation_discrepancies`'s
`recon_admin_only` (migration 59). Migration 256's `chk_users_role_not_admin` CHECK constraint
makes `users.role` permanently unable to hold `'admin'`/`'super_admin'` — real admin identity
lives in `admin_staff` instead — so this check is structurally unreachable, exactly the pattern
migrations 430 (C107) and 432/433 (C123) already fixed on 11 other tables. These 2 tables
postdate/sit outside migration 142's original sweep and were missed by both later campaigns.

Separately, `subscription_payments` (migration 151) claims "Append-only ledger" in its own
`COMMENT ON TABLE` but has no trigger enforcing it — unlike its sibling `financial_event_entries`,
which has a real UPDATE-blocking trigger.

## 2. Root cause

Found 2026-09-21 by a prior session while writing C49 RLS test coverage, confirmed by
`spinr-security-auditor` against the actual migration SQL, filed as ACTION_ITEMS.md C129 and
left open pending its own migration review (schema changes are out of scope for a
test-coverage-only PR). Picked up now via a proactive edge-case/error-handling sweep.

## 3. Fix / remediation

`backend/migrations/456_financial_ledger_rls_unreachable_and_subscription_payments_append_only.sql`:

- Replaces `financial_event_entries_select` with an explicit `FOR SELECT TO authenticated
  USING (false)` deny, matching migration 430's exact convention for this table shape (anon is
  already REVOKEd at the table-grant layer by migration 286 itself).
- Replaces `recon_admin_only` with `FOR ALL USING (false) WITH CHECK (false)`, no `TO` clause —
  matching the original's own PUBLIC scope, since this table (per this entry's own investigation)
  has no accompanying REVOKE unlike every other table in this family; RLS itself now denies
  anon too, rather than relying on a REVOKE this table never had.
- Adds `subscription_payments_no_mutate`, a `BEFORE UPDATE OR DELETE` trigger blocking both
  operations unconditionally — broader than `financial_event_entries_no_update` (UPDATE-only,
  deliberately allowing DELETE to avoid breaking an `ON DELETE CASCADE` from a parent row).
  `subscription_payments` has no FK/cascade relationship to any parent row (confirmed by
  grepping every migration touching it — 151/186/188 — for a `REFERENCES`/`FOREIGN KEY` on
  `driver_id`: none exists), so blocking DELETE too is safe and makes the table genuinely
  append-only end to end, matching its own `COMMENT ON TABLE` claim.

Test coverage: `backend/tests/rls/conftest.py` now applies migration 456 in the RLS test
fixture (after the base 59/286/151 migrations, so it replaces their original policies).
`backend/tests/rls/test_financial_ledger_extension_rls.py`: 3 existing tests flipped from
"admin CAN access" to "admin CANNOT access" (`test_admin_authenticated_cannot_select`,
`test_recon_admin_authenticated_cannot_select_or_update`), and the documented-gap test
(`test_sub_no_immutability_trigger_service_role_can_mutate_despite_appendonly_comment`) flipped
to two new tests confirming the trigger blocks both UPDATE and DELETE.

## 4. Risk & impact on existing functionality

- **Blast radius: 3 tables, isolated to their RLS policies and one new trigger.** No other
  reader/writer of `financial_event_entries`, `reconciliation_discrepancies`, or
  `subscription_payments` was touched — this is a policy/trigger swap, not a schema or
  application-code change.
- **Why safe:** the backend's only Supabase client always uses `SUPABASE_SERVICE_ROLE_KEY`,
  which bypasses RLS entirely — these two policies have never gated a single real request
  (same reasoning as C107/C108/C123). The new trigger only fires on UPDATE/DELETE, and no
  production code path issues either against `subscription_payments` (confirmed by grepping
  `routes/`, `services/`, `utils/` for every write to this table: INSERT only).
- Not a live security hole either way: fail-closed (denying an already-unreachable admin
  check), not fail-open.

## 5. User-experience effect

None. No rider/driver/admin-facing behavior changes — this is a database-policy correctness
fix with zero application-code changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/456_financial_ledger_rls_unreachable_and_subscription_payments_append_only.sql` | New migration: 2 policy replacements + 1 new trigger | Close ACTION_ITEMS.md C129 |
| `backend/tests/rls/conftest.py` | Applies migration 456 in the RLS test fixture | So the RLS test suite exercises the fixed policies, not the original ones |
| `backend/tests/rls/test_financial_ledger_extension_rls.py` | 3 tests flipped, 2 new tests added, module docstring updated | Pin the fixed behavior; the old tests asserted the gap this migration closes |
| `ACTION_ITEMS.md` | C129 marked closed | Keep backlog in sync with actual completion state |

## 7. Before / after

```sql
-- Before (migration 286)
CREATE POLICY financial_event_entries_select ON financial_event_entries
    FOR SELECT USING (
        (SELECT role FROM users WHERE id = auth.uid()::text) = 'admin'
    );

-- After (migration 456)
CREATE POLICY "financial_event_entries admin RLS unreachable (service role only)"
    ON financial_event_entries
    FOR SELECT TO authenticated USING (false);
```

```sql
-- Before (migration 59)
CREATE POLICY recon_admin_only ON reconciliation_discrepancies
    FOR ALL USING (
        (SELECT role FROM users WHERE id = auth.uid()::text) = 'admin'
    );

-- After (migration 456)
CREATE POLICY "reconciliation_discrepancies admin RLS unreachable (service role only)"
    ON reconciliation_discrepancies
    FOR ALL USING (false) WITH CHECK (false);
```

```sql
-- New (migration 456) — subscription_payments had no trigger at all before this
CREATE TRIGGER subscription_payments_no_mutate
    BEFORE UPDATE OR DELETE ON subscription_payments
    FOR EACH ROW EXECUTE FUNCTION _subscription_payments_immutable();
```

## 8. Rollback plan

`DROP TRIGGER subscription_payments_no_mutate ON subscription_payments;` plus `DROP POLICY` on
both tables and re-running migrations 286's RLS section / 59 restores the original `role='admin'`
policies verbatim. Zero data changes in this migration — pure policy/trigger addition, instantly
reversible, no PITR needed.

## 9. Verification performed

- [x] **Real Postgres, not just mocked** — started a local Postgres 16 instance in this sandbox,
  set `TEST_DATABASE_URL`, and ran `pytest tests/rls/test_financial_ledger_extension_rls.py -c
  /dev/null --confcutdir=tests/rls` (32 passed, including all 3 flipped tests and both new
  trigger tests) and the full `pytest tests/rls` suite (425 passed, 6 failed — all 6 failures
  confirmed pre-existing and unrelated: identical failures reproduced with this diff fully
  reverted via `git stash`, root cause is an environment DB-auth configuration issue in
  `tests/rls/money/*.py`, a different subdirectory using a different connection path than the
  fixture this change touches, not caused by this change).
- [x] Blast-radius grep performed — every migration touching the 3 tables, every application
  code write to `subscription_payments`, every RLS test referencing any of the 3 tables.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — migration conventions (numbering,
  append-only, idempotency, reversibility); dispatched `spinr-migration-reviewer` before commit
  (verdict: SHIP IT AS-IS, independently re-verified the numbering, idempotency, DELETE-safety,
  and rollback-plan claims rather than trusting them).
- [x] Idempotent — `DROP POLICY IF EXISTS` before each `CREATE POLICY`, `pg_trigger` existence
  check before `CREATE TRIGGER`.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — no application code changed

## What was NOT verified

- Not applied to production Supabase — this is a merged-migration-file change only; applying it
  to `spinrmobileapp` production is a separate step outside this session's access, same as every
  other migration produced here.
- The 6 pre-existing `tests/rls/money/*.py` failures were confirmed unrelated to this diff but
  were NOT fixed here — they're an environment DB-authentication issue in this sandbox, out of
  scope for a C129 fix.
