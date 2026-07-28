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

The `disputes` table's RLS policy (migration 10) is `"Admin full access disputes" FOR ALL TO authenticated` — that permits any authenticated admin JWT (via PostgREST) to UPDATE *or DELETE* dispute rows, not just read/resolve them.

## 2. Root cause

Migration 10 (early in the project) predates `backend/migrations/CLAUDE.md`'s explicit convention — "INSERT / UPDATE / DELETE explicitly enumerated — never `FOR ALL` on user-writable tables" — and was never revisited when that convention was documented. Disputes are a regulated financial/complaint record (refund decisions, resolution notes); an overly-broad policy means a stolen/misused admin token (or a future backend bug) could silently delete the record of what was disputed and how it was resolved.

## 3. Fix / remediation

`backend/migrations/262_disputes_rls_lockdown.sql` replaces the `FOR ALL` policy with:
- Enumerated `SELECT` and `UPDATE` policies for `admin`/`super_admin` roles (mirrors the existing role-check pattern from migration 10).
- A `BEFORE DELETE` trigger (`disputes_no_delete`) that blocks deletion for **every** role, including `service_role` — defense-in-depth against a future backend bug, matching the pattern already shipped for `audit_logs` in migration 51.
- PostgREST grant narrowing (`REVOKE`/`GRANT`) so `authenticated` only has SELECT+UPDATE at the grant layer too, not just RLS.

No application code changed — this is a database-migration-only fix.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — single table (`disputes`), single migration. No other table's RLS, triggers, or grants are touched.
- **Who else reads/writes `disputes`?** Grepped the full backend: `routes/disputes.py` (`insert_one` on create, `update_one` on resolve — both via `service_role`, which bypasses RLS by design and is unaffected), `services/zoho_desk_integration.py` (`update_one` to backfill `zoho_ticket_id`, also via `service_role`). The only `DELETE` call anywhere in the codebase (`routes/admin/support.py`'s `delete_many("disputes", ...)`) is in a file that is **never imported or mounted** by `server.py` or `features.py` — confirmed via grep for any import of that module — so it is dead code with zero live callers today. Blocking DELETE cannot regress it.
- **Could this regress a currently-working flow?** No live flow calls DELETE on `disputes`. UPDATE and SELECT remain fully allowed for admins (both application code paths and, now, the RLS policy itself), so `admin_resolve_dispute` and the admin dispute-list/detail endpoints are unaffected.
- **Interaction with background loops / ride state machine / money deltas?** None — this table is not touched by any of the 16 background loops, and the migration doesn't change Stripe refund logic (`dollars_to_cents`/refund creation in `routes/disputes.py` is untouched).

## 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin facing behavior changes — admin dispute resolution (SELECT + UPDATE) continues to work exactly as before. The only observable change is that a DELETE attempt (which no live code path performs) would now fail loudly instead of silently succeeding.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/262_disputes_rls_lockdown.sql` | New migration: drops `FOR ALL` policy, adds enumerated SELECT/UPDATE policies, adds `BEFORE DELETE` blocking trigger, narrows PostgREST grants | Brings `disputes` into line with `backend/migrations/CLAUDE.md`'s enumerated-RLS convention; closes B2's RLS sub-issue |

## 7. Before / after

```sql
-- Before (migration 10)
CREATE POLICY "Admin full access disputes"
ON disputes FOR ALL TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM users
    WHERE users.id = auth.uid()::text
    AND users.role IN ('admin', 'super_admin')
  )
);
```

```sql
-- After (migration 262)
CREATE POLICY "Admin read disputes"
    ON disputes FOR SELECT TO authenticated
    USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text AND users.role IN ('admin', 'super_admin')));

CREATE POLICY "Admin update disputes"
    ON disputes FOR UPDATE TO authenticated
    USING (...)
    WITH CHECK (...);

-- No INSERT/DELETE policy for `authenticated` — INSERT goes through service_role
-- only; DELETE is additionally blocked by trigger disputes_no_delete for every
-- role including service_role.
```

## 8. Rollback plan

Drop trigger `disputes_no_delete`, `DROP POLICY` the two new enumerated policies, `CREATE POLICY "Admin full access disputes" ON disputes FOR ALL TO authenticated USING (...)` to restore migration 10's original policy verbatim, and restore the previous GRANT surface (`GRANT ALL ... TO authenticated`, implied by the original `FOR ALL` policy). No data is touched by this migration (RLS/grants/trigger only), so rollback is a straightforward policy/trigger swap — no data-level remediation needed, unlike a change that had already mutated live rows.

## 9. Verification performed

- [x] **Blast-radius grep performed** — searched the full backend for every read/write of the `disputes` table (`routes/disputes.py`, `services/zoho_desk_integration.py`, `routes/admin/support.py`) and confirmed which are live (service_role, unaffected) vs. dead code (the one DELETE caller, never imported/mounted).
- [x] **Reviewed against relevant `CLAUDE.md` conventions** — this migration brings the table into compliance with `backend/migrations/CLAUDE.md`'s enumerated-RLS rule; append-only pattern matches migration 51 (`audit_logs`).
- [ ] Automated tests run — not applicable; RLS is enforced by Postgres, not application code, and no dedicated Python test exercises RLS policies directly (migration 51 also shipped without one, since unit tests mock the DB entirely per `backend/CLAUDE.md`'s testing conventions).
- [ ] Manual repro steps followed in staging — **not performed**, no staging or live Supabase access in this session. The SQL was reviewed for syntactic and semantic correctness but not executed against a real Postgres instance.
- [ ] Feature-flagged — not applicable; this is a backend-only RLS/schema change with no user-visible surface to flag.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain policy/trigger swap, documented above and in the migration's own header comment
- [x] Blast radius is stated, not assumed — isolated to `disputes`, confirmed via grep of every caller
- [x] No silent behavior change to an already-shipped flow — SELECT/UPDATE unaffected; the only behavior change (DELETE now blocked) has zero live callers
