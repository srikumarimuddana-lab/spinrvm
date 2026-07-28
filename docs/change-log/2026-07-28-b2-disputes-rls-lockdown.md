# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code |
| Surface(s) | backend (migrations) |
| Domain (Sentry tag) | payments (disputes are a refund/complaint record) |
| PR / commit link | (filled in on PR) |
| Related issue or gap ID | B2 (`ACTION_ITEMS.md`) |

## 1. Issue / gap identified

**Correction, made before merge**: an earlier draft of this change assumed the `disputes` table's RLS policy was still migration 10's `"Admin full access disputes" FOR ALL TO authenticated`, and attempted to replace it with enumerated SELECT/UPDATE. A `spinr-migration-reviewer` subagent review caught that this was stale: migration 142 (`142_fix_rls_financial_tables.sql`) already superseded migration 10 months ago — it dropped the `FOR ALL` policy, replaced it with SELECT-only policies (`"Admin read disputes"` role-checked, `"Rider read own disputes"` own-row), and revoked all `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` grants from `authenticated`. It also already scrubbed the `user_name` column (set to `''`, stopped writing it — PIPEDA). The draft's SELECT/UPDATE policies would have failed to apply (duplicate policy name collision with 142's `"Admin read disputes"`) and, if renamed to avoid the collision, would have *reopened* UPDATE for `authenticated` admins — a regression from 142's intentionally tighter baseline.

The one gap 142 genuinely left: it only revoked/restricted `anon` and `authenticated`. `service_role` still bypasses RLS by design (correctly — the backend needs INSERT on create, UPDATE on resolve), and nothing stops a `service_role` caller (i.e. a future backend bug) from calling DELETE on `disputes`.

## 2. Root cause

Migration 142 (months prior) already closed the over-broad-policy gap this backlog item (B2) originally described. B2's own text in `ACTION_ITEMS.md` was stale — written against an older snapshot of the schema that predates 142. The actual remaining gap is narrower: no defense-in-depth block against DELETE for `service_role`, matching the pattern already shipped for `audit_logs` in migration 51 (which blocks UPDATE for every role including `service_role`).

## 3. Fix / remediation

`backend/migrations/262_disputes_rls_lockdown.sql` now does exactly one thing: adds a `BEFORE DELETE` trigger (`disputes_no_delete`) that blocks deletion for every role, including `service_role`. It does not touch RLS policies or grants — migration 142's SELECT-only lockdown for `authenticated` is left completely alone, since it's already correct and more restrictive than what the withdrawn draft would have produced.

No application code changed — this is a database-migration-only fix.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — single table (`disputes`), a single new trigger + comment. No RLS policy, grant, or column is touched.
- **Who else reads/writes `disputes`?** Grepped the full backend: `routes/disputes.py` (`insert_one` on create, `update_one` on resolve — both via `service_role`), `services/zoho_desk_integration.py` (`update_one` to backfill `zoho_ticket_id`, also via `service_role`). Neither is affected by a DELETE-only trigger. The only `DELETE` call anywhere in the codebase (`routes/admin/support.py`'s `delete_many("disputes", ...)`) is in a file never imported or mounted by `server.py` or `features.py` — dead code, confirmed via grep for any import of that module.
- **Could this regress a currently-working flow?** No live flow calls DELETE on `disputes`; SELECT and UPDATE are entirely untouched by this migration (they remain exactly as migration 142 left them).
- **Interaction with background loops / ride state machine / money deltas?** None.

## 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin facing behavior changes. The only observable effect is that a DELETE attempt (which no live code path performs) now fails loudly instead of silently succeeding via `service_role`.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/262_disputes_rls_lockdown.sql` | New migration: adds `BEFORE DELETE` blocking trigger + function; updates `COMMENT ON TABLE disputes` to document the full current policy set (142's SELECT policies + this migration's DELETE block) | Closes the one gap migration 142 left — defense-in-depth against `service_role` deletion — without touching or duplicating 142's already-correct RLS/grant lockdown |

## 7. Before / after

```sql
-- Before (as of migration 142, still the live state today)
-- RLS: "Admin read disputes" (SELECT, role-checked), "Rider read own disputes"
-- (SELECT, own-row). Grants: authenticated has SELECT only; anon has nothing.
-- service_role bypasses RLS (migration 10's "Service role bypass disputes").
-- No DELETE protection beyond application code discipline.
```

```sql
-- After (this migration, additive only)
CREATE OR REPLACE FUNCTION disputes_block_delete()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'disputes is append-only — DELETE is not permitted (attempted on row %)',
        OLD.id USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER disputes_no_delete
    BEFORE DELETE ON disputes
    FOR EACH ROW
    EXECUTE FUNCTION disputes_block_delete();

-- 142's SELECT policies and grants are completely untouched.
```

## 8. Rollback plan

`DROP TRIGGER disputes_no_delete ON disputes; DROP FUNCTION disputes_block_delete();` — fully restores the pre-262 state (migration 142's SELECT-only lockdown, which this migration never modifies). No data is touched, so rollback is a pure trigger/function drop with no data-level remediation needed.

## 9. Verification performed

- [x] **Blast-radius grep performed** — searched the full backend for every read/write of the `disputes` table; confirmed which callers are live (`service_role`, unaffected by a DELETE-only trigger) vs. dead code (the one DELETE caller, never imported/mounted).
- [x] **Caught via independent review before merge** — a `spinr-migration-reviewer` subagent review flagged that the original draft (enumerated SELECT/UPDATE policies) collided with migration 142's already-existing policy of the same name and would have failed to apply; also flagged that the draft's header narrative was stale (describing migration 10 as the current baseline when 142 had already superseded it). This corrected version was written in response to that review, not merged blind.
- [x] **Reviewed against relevant `CLAUDE.md` conventions** — matches the append-only defense-in-depth pattern from migration 51 (`audit_logs`); does not violate the "never `FOR ALL`" convention since it adds no RLS policy at all, only a trigger.
- [ ] Automated tests run — not applicable; RLS/triggers are enforced by Postgres, not application code; migration 51 also shipped without a dedicated Python test for the same reason.
- [ ] Manual repro steps followed in staging — **not performed**, no staging or live Supabase access in this session. The SQL was reviewed for syntactic and semantic correctness (twice — once by me, once by an independent subagent review that caught the collision) but not executed against a real Postgres instance.
- [ ] Feature-flagged — not applicable; backend-only migration, no user-visible surface to flag.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — a two-statement trigger/function drop, documented above and in the migration's own header comment
- [x] Blast radius is stated, not assumed — isolated to `disputes`, confirmed via grep of every caller, and additive-only (no RLS/grant change)
- [x] No silent behavior change to an already-shipped flow — SELECT/UPDATE are completely untouched (still exactly migration 142's state); the only behavior change (DELETE now blocked for `service_role`) has zero live callers
