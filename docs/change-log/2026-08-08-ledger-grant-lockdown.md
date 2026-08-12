# Change Impact & Risk Log — Ledger table grant lockdown (audit blocker)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-08 |
| Author | Claude Code (agent), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend (SQL + observability) |
| Domain (Sentry tag) | payments / security |
| Related issue or gap ID | BLOCKER from the `spinr-security-auditor` and `spinr-migration-reviewer` passes on PR #3464 |

## 1. Issue / gap identified

Two independent audits of PR #3464 found the same blocker, and one of them found it on a table this PR did **not** create.

**Migration 286 (introduced by this PR):** `financial_event_entries` shipped with
`CREATE POLICY ... FOR INSERT WITH CHECK (true)` — no `TO` clause, so it applies to `PUBLIC` — and no accompanying `REVOKE`. Supabase grants `anon`/`authenticated` default CRUD on new public-schema tables; an RLS policy only *narrows* a grant, it does not remove it. A permissive policy plus a default grant is an open door.

**Migration 58 (pre-existing, live since the ledger was created):** `financial_events` — the actual 7-year CRA/SK tax ledger — has the identical permissive INSERT policy and has never been locked down. Migration 142 swept this exact bug class across `disputes` and nine corporate money tables, and 151 did it for `subscription_payments`; neither sweep listed `financial_events`.

**Impact:** anyone holding the publishable anon key — which ships inside `rider-app` and `driver-app` — could `POST /rest/v1/financial_events` and forge a ledger row. `event_type` is CHECK-constrained, but `ride_id` is nullable, `metadata` is free-form jsonb, `delta_cents` is unconstrained, and the `user_id` FK only requires the target user to *exist*, so the forged row can be attributed to any account. Combined with the child-table hole, an attacker could mint a fake header **and** a self-balancing pair of legs — which would pass `financial_event_entries_unbalanced` silently, defeating the exact tamper-evidence control this PR built.

The security audit put it plainly: nobody needs to defeat the migration-289 DELETE gate when INSERT is wide open.

## 2. Root cause

Migration 286 followed migration 58's RLS as its template — and 58 is where the bug lives. The money-auditor pass independently marked the same RLS "clean, matches the established `financial_events` precedent (migration 58) exactly," which is the failure mode in one sentence: matching a broken precedent reads as correct. The two audits that checked the *grants* rather than the *policies* caught it.

Neither hole was reachable by the test suite: grants cannot be exercised against a mocked Supabase client, and (per the change-log entries on this branch) no migration on this PR had executed against a real Postgres.

## 3. Fix / remediation

- **Migration 286** (still unmerged, so edited in place): added `REVOKE ALL ... FROM anon`, `REVOKE INSERT/UPDATE/DELETE/TRUNCATE ... FROM authenticated`, `GRANT SELECT ... TO authenticated`, plus an explicit `REVOKE ALL ON financial_event_entries_unbalanced FROM anon, authenticated` — a view executes with its *owner's* privileges for RLS purposes unless created `WITH (security_invoker)`, so the base table's RLS does not protect it.
- **Migration 290 (new):** the same lockdown for `financial_events`. Verbatim pattern from 142/151.
- **Migration 291 (new):** `CREATE INDEX CONCURRENTLY financial_events_legs_pending` — the migration reviewer showed migration 287's anti-join has no supporting index, so the planner must materialize and sort the entire qualifying backlog on every 15-minute tick. Worst exactly during the initial backfill.
- **Observability (3 sites):** `payment_service.py` (ambiguous-RPC branch) and `ledger_projection.py` (×2) logged `err` with a bare `{}`. `run_sync` wraps DB failures in `DatabaseError`, whose `__str__` is the constant `"Database operation failed"` — so the Sentry page for the highest-stakes "did the charge commit?" path carried no traceback and no Postgres error code. Now `logger.opt(exception=err)` plus `details["original"]`, per CLAUDE.md.
- **New alert tag** `settlement_state_unverifiable`: the branch that tells the rider "our team has been notified" now raises a *taggable* escalation instead of relying on the generic logger→Sentry bridge, so on-call can page on it without grepping message text.
- **`to_cents()` standardization:** `_finalize_card_settlement` reimplemented dollars→cents as `int(_round(x * 100))`, which truncates where `to_cents()` rounds half-up. Harmless with today's pre-rounded inputs; removed the second formula anyway.
- **`.github/labeler.yml`:** the `area:money` globs still pointed at `backend/routes/rides.py` and `drivers.py`, which became packages in the god-object decomposition. A PR touching settlement, the ledger, and four money migrations matched **zero** money globs. Added the packages and the new ledger modules.

## 4. Risk & impact on existing functionality

**Blast radius: table-level GRANTs on two tables, plus three log lines and one cents conversion.**

- **The backend is unaffected.** Every write path (`ledger_service.record_event`, `settle_ride_card_payment`, `purge_pii_retention`) runs as `service_role`, which bypasses both RLS and these grants.
- **`SELECT` is deliberately preserved for `authenticated`** on `financial_events`, because migration 58/70's SELECT policy already scopes it correctly (own rows, or admin). Revoking it would break a legitimate read. The verification script asserts this in both directions — that writes are gone *and* that reads still work.
- **No client reads these tables directly** — grepped `rider-app`, `driver-app`, `admin-dashboard`, `shared`: zero hits. Admin reads go through the backend.
- **Migration 291 uses `CONCURRENTLY`**, so it takes no blocking lock; `scripts/migrate.py` detects that keyword and runs the file outside a transaction, as Postgres requires.
- The three logging changes are strictly additive information. The `to_cents()` swap produces identical output for every value the callers can currently pass.

## 5. User-experience effect

Nobody. No rider, driver, corporate-admin or internal-admin surface changes. The grants being revoked are ones no legitimate client was using.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/286_financial_event_entries.sql` | REVOKE on table + view | Blocker: forged legs |
| `backend/migrations/290_financial_events_grant_lockdown.sql` | **New.** REVOKE on the parent tax ledger | Blocker: forged headers (pre-existing) |
| `backend/migrations/291_financial_events_legs_pending_index.sql` | **New.** Partial index, CONCURRENTLY | Work queue would full-sort the backlog each tick |
| `backend/services/payment_service.py` | `opt(exception=)` + `details["original"]`; tagged escalation; `to_cents()` | Swallowed error detail; untaggable page |
| `backend/services/ledger_service.py` | `ALERT_SETTLEMENT_UNVERIFIABLE` | New tag constant |
| `backend/utils/ledger_projection.py` | `opt(exception=)` + `details["original"]` ×2 | Same |
| `backend/scripts/verify_migrations_286_291.sql` | Renamed from `_286_289`; +5 grant assertions | Prove the fix on a live DB |
| `.github/labeler.yml` | `area:money` globs repaired + extended | Money PRs were unlabelled |
| `ACTION_ITEMS.md` | B19, B20 | Two audit findings deferred with reasons |

## 7. Before / after

```sql
-- Before (286 and 58): permissive policy, no REVOKE → anon key can INSERT
CREATE POLICY financial_events_insert ON financial_events
    FOR INSERT WITH CHECK (true);
```

```sql
-- After: the GRANT is what actually gates writes
REVOKE ALL ON financial_events FROM anon;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON financial_events FROM authenticated;
GRANT  SELECT ON financial_events TO authenticated;   -- RLS still scopes to own rows
```

## 8. Rollback plan

Migrations 290/291 are grant/index changes with the rollback SQL in each header; 286's REVOKEs revert the same way. None touches data. Note that rolling back 290 **restores a live security hole** — it should only ever be done if the revoke provably breaks a legitimate reader, and the verification script's "authenticated retains SELECT" assertion exists to catch that case before it reaches production.

## 9. Verification performed

- Verified every audit claim against the code before acting: confirmed 142's sweep enumerates nine tables and omits `financial_events`; confirmed no REVOKE on it exists anywhere in `backend/migrations/`; confirmed 151's own comment states the exploit verbatim; confirmed no client bundle queries either table.
- All six migrations re-parsed with `pglast` (14/5/5/7/5/2 statements).
- Affected battery — **154 passed** (`test_atomic_settle`, `test_ledger_service`, `test_ledger_projection`, `test_ledger_pii`, `test_loguru_call_conventions`, `test_log_guard`, `test_coverage_payments`, `test_process_payment_card`, `test_settle_card_capture`).
- New assertion in `test_atomic_settle.py` pinning the `settlement_state_unverifiable` escalation.
- `ruff check` / `format --check` clean on all 17 files this branch touches (the 36 errors `ruff check backend/` reports are pre-existing elsewhere and untouched here).
- `.github/labeler.yml` re-validated as YAML.
- Full backend suite run before push.

## 10. What was NOT verified

> **UPDATE 2026-08-08 — the database layer of this gap is now CLOSED.** The repo owner applied migrations 286–291 to a real Postgres and ran `backend/scripts/verify_migrations_286_291.sql`; **all checks passed**. See `docs/change-log/2026-08-08-migration-verification-result.md` for exactly what that proved and what it did not. The items below are corrected in place; anything still outstanding is called out there.


- ~~The grants themselves have still never been applied.~~ **Applied and asserted 2026-08-08 — the blocker is genuinely closed.** `anon`/`authenticated` hold no INSERT/UPDATE/DELETE/TRUNCATE on either ledger table, `anon` cannot SELECT either, the unbalanced view is unreadable by both JWT roles, and — proving the lockdown did not over-revoke — `authenticated` **retains** SELECT on `financial_events` so riders can still read their own rows. `service_role` retains EXECUTE on all three RPCs, confirming the `REVOKE ... FROM PUBLIC` did not silently strip its inherited rights.
- Migration 291's index has not been built, so its effect on the work-queue plan is reasoned from the query shape, not measured. The script prints the `EXPLAIN` output as advisory info when run.
- Whether `anon` currently holds the default grants at all is environment-dependent (it is inferred from migrations 142/151 having needed the same fix). If the verification script reports the grants were already absent, the fix is a no-op — which is a fine outcome and worth knowing either way.
