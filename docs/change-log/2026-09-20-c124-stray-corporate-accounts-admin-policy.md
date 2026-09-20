# Change Impact & Risk Log — C124: drop out-of-band `corporate_accounts` admin policy

**Date:** 2026-09-20
**Related:** ACTION_ITEMS.md C107/C124 (this entry), migration 430 (the C107 hardening this was found while verifying)

## Issue/gap identified
Production's `corporate_accounts` table carried a second RLS policy, `"Admin full access for corporate accounts"` (FOR ALL, TO authenticated, `USING (users.role = 'admin')`, no WITH CHECK), that does not correspond to any migration file in this repo's history.

## Root cause
Found while verifying migration 430's rollout to production. `git log --all -S "Admin full access for corporate accounts" -- backend/migrations/` returns zero hits across every branch/commit — no migration ever created this exact policy. Migration 17 (checksum-verified byte-identical to what's currently committed, so it was never edited post-application) creates a *differently*-named policy, `"Admin full access corporate_accounts"` (no "for", underscore not space), guarded by its own `IF NOT EXISTS` check on that exact name. Migration 416 later correctly `DROP`ped that exact policy — its DROP target matched migration 17's committed name precisely; 416 was not buggy. The policy this change removes is a separate, out-of-band artifact — most likely created manually via Supabase's dashboard/SQL editor at some point outside the tracked migration history for this table — that no migration ever knew existed and so was never targeted by anything.

## Fix/remediation
New migration `431_drop_stray_corporate_accounts_admin_policy.sql`: `DROP POLICY IF EXISTS "Admin full access for corporate accounts" ON corporate_accounts;`. The `USING` clause captured in the migration's own rollback-plan comment was verified byte-exact against the live policy via `pg_get_expr(polqual, polrelid)` before the drop ran, not reconstructed from memory.

## Risk & impact on existing functionality
**Isolated, tightens access, doesn't loosen anything.** Grepped `backend/` for every `create_client`/Supabase client construction: `backend/supabase_client.py` is the only one, always instantiated with `SUPABASE_SERVICE_ROLE_KEY` (bypasses RLS entirely) — no backend code path relied on RLS-level admin access to `corporate_accounts` via this or any other policy. The stray policy's write half (INSERT/UPDATE/DELETE/TRUNCATE) was already independently blocked at the table-grant layer by migration 416's `REVOKE` (confirmed via `information_schema.role_table_grants`: `authenticated` holds only SELECT/REFERENCES/TRIGGER). Its read half (SELECT, permissive, ORs with migration 430's `USING (false)` policy on the same table) could only ever be satisfied by a `users` row with `role = 'admin'` — permanently blocked by migration 256's `chk_users_role_not_admin` CHECK constraint, and confirmed zero such rows exist. Checked the other 10 tables migration 430 touched for the same kind of untracked drift — `corporate_accounts` is the only one affected.

## User experience effect
None. No rider/driver/corporate-admin/internal-admin-facing behavior changes.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/migrations/431_drop_stray_corporate_accounts_admin_policy.sql` | New migration: drops the untracked stray policy | Core fix |
| `backend/tests/rls/conftest.py` | Applies migration 431 after 430 (a documented no-op in this harness, since the harness's schema is built by replaying migration files and never had the drifted policy) | Keep the harness applying the real, full migration sequence |
| `backend/tests/rls/test_corporate_accounts_super_admin_fix.py` | New test `test_stray_admin_policy_removed_by_431`: manufactures the exact drifted policy, proves it was a real access hole (an admin-role JWT gains SELECT through it despite migration 430's deny policy — RLS ORs permissive policies), re-applies the DROP, proves the hole closes; restores state in a `finally` block | Direct regression coverage for a bug the harness can't otherwise reproduce |

## Before/after snippet

```sql
-- Before: two policies on corporate_accounts.
-- 1. "corporate_accounts admin RLS unreachable (service role only)" (migration 430, USING false)
-- 2. "Admin full access for corporate accounts" (untracked, FOR ALL, USING role = 'admin')
--    -> permissive policies OR together, so an admin-role JWT still got SELECT via #2
--       despite #1's explicit deny.

-- After (migration 431):
DROP POLICY IF EXISTS "Admin full access for corporate accounts" ON corporate_accounts;
-- Only policy #1 remains. No permissive path left for any authenticated role.
```

## Rollback plan
`CREATE POLICY "Admin full access for corporate accounts" ON corporate_accounts FOR ALL TO authenticated USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text AND users.role = 'admin'));` — full command in migration 431's own header comment, verified byte-exact against the pre-drop live policy. Zero data changes, instantly reversible, no PITR needed.

## Verification performed
- Ran the full `backend/tests/rls` suite (real Postgres 16, local): **377/377 passed**, including the new regression test.
- `ruff check` on both changed Python files: clean.
- Two independent adversarial reviews (`spinr-migration-reviewer`, `spinr-security-auditor`) against the actual diff: both returned SAFE TO MERGE/APPLY, no blockers. The migration reviewer's one note (rollback-plan text should be verified byte-exact against production rather than reconstructed) was addressed before applying — see Fix/remediation above.
- **Applied directly to production** (Supabase project `soavhtdhefowwvforzwb`) via a verified, direct SQL execution path, with explicit user sign-off obtained via `AskUserQuestion` first (this repo's normal `run_migrations.py` runner needs `DATABASE_URL`, which isn't set in this session's sandbox — see "What was NOT verified" below). The `schema_migrations` tracking row was inserted manually, replicating exactly what the official runner records (`filename`, `checksum` — `applied_at`/`applied_by` use the table's own `DEFAULT now()`/`DEFAULT current_user`). Verified post-apply: `corporate_accounts` now shows exactly one policy (migration 430's), zero stray policies remain.
- **Note on apply ordering** (flagged by the security auditor's review): this migration's SQL was applied to production, then committed to git — the reverse of the normal review-then-apply order — because the fix was small, already dual-reviewed, and the user explicitly asked for both the production apply and the PR to happen together. Recording this here per the auditor's recommendation so it's auditable; the change itself (a single `DROP POLICY IF EXISTS`, no data mutation) carries no additional risk from that ordering, since the review happened before the apply either way.

## What was NOT verified
- This session's sandbox does not have `DATABASE_URL` set, so the official `backend/scripts/run_migrations.py` runner could not be used directly — the migration was applied via a direct, verified SQL execution path instead (Supabase MCP), with the tracking row inserted manually to match. A future run of `run_migrations.py --status` against production should show both 430 and 431 as applied, not pending — worth a human spot-check once `DATABASE_URL` access exists in whatever environment runs it next.
- No production build/deploy run — backend-only migration + RLS test change, not an admin-dashboard/rider-app/driver-app change.
- Whether this stray policy was ever actually exploited (vs. merely present-but-unreachable) was not investigated — out of scope here; the CHECK constraint (migration 256) means it's been unreachable for as long as that constraint has existed, and there's no evidence of it being reachable before that either (the only role value that could satisfy it, `role='admin'`, was reset to `'rider'` on the one legacy row that ever had it, per C107's own 2026-09-13 closure).
