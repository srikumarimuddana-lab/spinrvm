# Change Impact & Risk Log — financial_events purge delete gate

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-07 |
| Author | Claude Code (agent), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend (SQL only) |
| Domain (Sentry tag) | payments / compliance |
| Related issue or gap ID | Adjacent finding §11.1 in `docs/change-log/2026-08-06-ledger-durability-double-entry.md`; W5 in the error-free plan |

## 1. Issue / gap identified

The 7-year DSAR hard delete cannot work: migration 58's append-only trigger unconditionally RAISEs on any `financial_events` DELETE, and `purge_pii_retention` Step H (migration 216, carried verbatim through all 16 redefinitions to 285) runs exactly such a DELETE. Its per-user handler catches only `foreign_key_violation` (23503); the trigger's P0001 propagates and **aborts the entire purge run** — every later step included. Dormant today only because no account's 7-year footprint has aged out yet.

## 2. Root cause

Migration 58 (tamper evidence) and migration 216 (DSAR hard delete) were written independently; nothing reconciled the trigger with the purge, and the purge's "never aborts" claim was only tested against FK violations.

## 3. Fix / remediation

Migration 289, copying the migration-56 `audit_logs` pattern (the repo's established `spinr.<table>.allow_delete` GUC convention):

1. `_financial_events_immutable()` now permits **DELETE only** when the transaction-local GUC `spinr.financial_events.allow_delete = 'true'`. UPDATE stays unconditionally blocked. `CREATE OR REPLACE` keeps the existing trigger binding — no unprotected window at any point.
2. `purge_pii_retention` redefined (17th definition, verbatim copy of 285) setting/clearing the GUC immediately around Step H's `financial_events` DELETE, including on the error path.

Deliberately **not** done: widening Step H's `foreign_key_violation`-only handler. Catching `raise_exception` there would hide future trigger regressions; the GUC makes the delete *legal* instead of making the failure *silent*.

## 4. Risk & impact on existing functionality

**Blast radius: two SQL functions; no Python, no schema, no data.**

- A scripted diff of the new purge body against 285's confirms the **only executable change is the Step H gate** (all other diff lines are comment rewraps). Steps A–G and I–M are byte-identical in behavior.
- The tamper-evidence posture of `financial_events` is preserved: UPDATE blocked always; direct DELETE outside the purge still raises (now `check_violation` with a purpose-built message, matching the 56 convention — previously P0001 with the generic message).
- The GUC is transaction-local (`set_config(..., true)`) and additionally cleared on both the success and error paths — an aborted purge cannot leave the gate open for later statements in the same session (and a subtransaction abort rolls the GUC back regardless).
- `financial_event_entries` cascade is unobstructed by design: 286's entries trigger is UPDATE-only.
- Pre-289 purge firings keep failing exactly as today — applying the migration is strictly monotonic improvement; there is no Python-side ordering hazard (the purge is invoked by name via RPC from `utils/retention_purge.py`).

**Adjacent landmine found during this work, NOT fixed (needs its own design):** Step B's `DELETE FROM rides` at 7 years has **no** exception handler, and `financial_events.ride_id REFERENCES rides(id)` with default NO ACTION — once rides age past 7 years while their ledger rows are retained, Step B will 23503 and abort the purge before Step H is even reached. Dormant until ~2033 (no ride is near 7y old). Options when addressed: NULL `ride_id` before the ride delete, or an `ON DELETE SET NULL` migration on the FK. Recorded here so it is not re-discovered.

## 5. User-experience effect

Nobody — enables a background compliance job that has never yet had eligible data. PIPEDA/retention posture improves (the deletion right becomes mechanically enforceable at the 7-year mark).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/289_financial_events_purge_delete_gate.sql` | **New.** Trigger-function replacement + purge 17th definition | Make Step H legal |

## 7. Before / after

```sql
-- Before (58): any DELETE raises, purge aborts at Step H
RAISE EXCEPTION 'financial_events rows are append-only and cannot be modified or deleted';
```

```sql
-- After (289): DELETE legal only under the purge's transaction-local flag
IF TG_OP = 'DELETE' AND current_setting('spinr.financial_events.allow_delete', true) = 'true' THEN
    RETURN OLD;
END IF;
-- purge Step H:
PERFORM set_config('spinr.financial_events.allow_delete', 'true', true);
DELETE FROM financial_events WHERE user_id = v_uid;  -- wrapped, cleared after + on error
```

## 8. Rollback plan

Pure `CREATE OR REPLACE` both ways: re-apply 58's trigger body and 285's purge body (both quoted in the migration header). No data or schema to unwind; no deploy needed beyond running the rollback SQL.

## 9. Verification performed

- `pglast` (real Postgres parser): 7 statements, all accepted.
- **Scripted verbatim-diff against 285's function body** — 29 changed lines, all of them the Step H gate or comment rewraps; zero unintended executable changes.
- Reviewed against `backend/migrations/CLAUDE.md` (append-only, reversible-on-paper, SECURITY DEFINER + pinned search_path preserved) and the 56/285 GUC pattern.

## 10. What was NOT verified

- **No runtime execution** — no real Postgres reachable from this environment, and trigger/GUC interplay cannot be exercised by pglast. Staging verification runbook before relying on it: (1) apply 289; (2) `SELECT purge_pii_retention(true)` dry run; (3) real run; (4) in psql, a manual `UPDATE financial_events ...` and a manual `DELETE FROM financial_events ...` must BOTH still raise.
- The purge has never executed against data old enough to reach Step H anywhere; first true exercise is years away by construction.
