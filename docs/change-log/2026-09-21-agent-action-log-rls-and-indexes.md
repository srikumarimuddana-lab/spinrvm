# Change Impact & Risk Log — `agent_action_log` Dead RLS Policy + Missing Indexes

**Date:** 2026-09-21
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** backend (migration only)
**Domain:** infra/observability (agent action audit log)

## Issue/gap identified
Two non-blocking findings from the 2026-09-20 `/full-audit` fleet review of migration `429_agent_action_log.sql` (`spinr-migration-reviewer`):
1. The table's `agent_action_log_admin_read` SELECT policy gates on `(auth.jwt() ->> 'role') = 'admin'`, which is structurally unreachable — real admin auth never produces a Supabase Auth session JWT.
2. Two query-pattern indexes are missing: `action_type` (a real filter on `GET /agent-actions`) and a plain `created_at DESC` index for the unfiltered/no-param case.

## Root cause
- **RLS:** identical root cause to `ACTION_ITEMS.md` C107/C123 (fixed for 15 other tables via migrations 430 and 432) — admin auth in this codebase is a custom `JWT_SECRET`-signed token verified inside FastAPI (`admin_staff` + `_verify_admin_payload`), which never creates or touches a Supabase Auth session. `auth.jwt()` reads the *Supabase-issued* session JWT, which a real admin request never has. Migration 429 shipped a fresh instance of a pattern two other migrations already exist to clean up elsewhere — not caught at the time because 429 landed the same day as (and slightly ahead of) this specific audit finding it.
- **Indexes:** `backend/routes/admin/maintenance.py`'s `GET /agent-actions` filters on `agent_name`, `action_type`, `target_surface`, and `outcome`, ordered by `created_at DESC`. Migration 429 indexed three of those four filters (plus `risk_domain`, not a route filter) but missed `action_type`, and never added a plain `created_at`-only index for the common no-filter call.

## Fix/remediation
New migration `434_agent_action_log_rls_and_indexes.sql`:
- Drops the dead `agent_action_log_admin_read` policy and replaces it with the same explicit `"agent_action_log admin RLS unreachable (service role only)" ... USING (false)` marker policy migrations 430/432 already use — documents the state as intentional rather than leaving a policy someone might later assume is live.
- Adds `idx_agent_action_log_action_type (action_type, created_at DESC)` and `idx_agent_action_log_created_at (created_at DESC)`.

No production code changed — the actual admin read path (`db_supabase.get_rows` on the service-role client) already bypasses RLS and was never affected by the dead policy.

## Risk & impact on existing functionality
- **Blast radius:** isolated to the `agent_action_log` table, added one day ago (migration 429) with a service-role-only real read path. Grepped the full repo for `agent_action_log_admin_read` and every `idx_agent_action_log_*` name — no other file references them.
- No other consumer of this table exists besides `backend/utils/agent_action_logger.py` (writer, service-role, unaffected by RLS either way) and `backend/routes/admin/maintenance.py`'s `GET /agent-actions` (reader, also service-role, unaffected by RLS either way).
- Zero data changes — pure RLS policy swap plus two new indexes on a near-empty table.

## User experience effect
None — this table has no rider/driver/corporate-facing surface. The only reader is an internal admin endpoint gated behind `require_module("audit")`, whose behavior is unchanged by this migration (it never depended on the dead RLS policy).

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/migrations/434_agent_action_log_rls_and_indexes.sql` | New migration: replaces the dead `agent_action_log_admin_read` policy with an explicit unreachable-marker policy (matching migrations 430/432's established pattern); adds `action_type` and `created_at` indexes | Close the two non-blocking findings from the 2026-09-20 `/full-audit` review of migration 429 |

## Before/after snippet
Before (migration 429, merged):
```sql
CREATE POLICY agent_action_log_admin_read ON agent_action_log
    FOR SELECT
    USING (
        (auth.jwt() ->> 'role') = 'admin'
    );
-- never reachable: admin JWTs here are never Supabase Auth session JWTs
```

After (migration 434):
```sql
DROP POLICY IF EXISTS agent_action_log_admin_read ON agent_action_log;
CREATE POLICY "agent_action_log admin RLS unreachable (service role only)"
    ON agent_action_log FOR SELECT TO authenticated USING (false);

CREATE INDEX IF NOT EXISTS idx_agent_action_log_action_type
    ON agent_action_log (action_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_action_log_created_at
    ON agent_action_log (created_at DESC);
```

## Rollback plan
Given in the migration file's own header comment:
```sql
DROP POLICY IF EXISTS "agent_action_log admin RLS unreachable (service role only)" ON agent_action_log;
CREATE POLICY agent_action_log_admin_read ON agent_action_log
    FOR SELECT USING ((auth.jwt() ->> 'role') = 'admin');
DROP INDEX IF EXISTS idx_agent_action_log_action_type;
DROP INDEX IF EXISTS idx_agent_action_log_created_at;
```
Zero data changes — pure RLS policy swap plus two new indexes, no PITR needed.

## Verification performed
- Confirmed via `git show`/grep that migration 429 is the only other place referencing this table's schema, and that no application code references the dropped policy name or the new index names.
- Cross-checked migrations 430 and 432 to match their established "unreachable admin RLS" fix pattern exactly (policy naming convention, `TO authenticated ... USING (false)` shape).
- Confirmed `backend/routes/admin/maintenance.py`'s actual filter set (`agent_name`, `action_type`, `target_surface`, `outcome`) against the new indexes to verify `action_type` is genuinely a gap being closed, not a redundant addition.
- Dispatched `spinr-migration-reviewer` (the same agent that originally found this) to adversarially verify the fix before committing.

## What was NOT verified
- Not run against a real Supabase/Postgres instance — this environment has no live DB connection; verified by code/pattern review against the two prior migrations (430, 432) that already applied this exact fix shape to production successfully.
- Did not re-verify the CHECK constraint (`chk_users_role_not_admin`, migration 256) that makes the original policy's premise permanently false — that fact was already established by migrations 430/432's own investigation and is not re-derived here.
