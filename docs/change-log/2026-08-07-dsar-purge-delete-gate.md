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

**Adjacent landmine found during this work, NOT fixed (needs its own design) — now tracked as `ACTION_ITEMS.md` B17.** Step B's `DELETE FROM rides` at 7 years has **no** exception handler, and `financial_events.ride_id REFERENCES rides(id)` with default NO ACTION — once rides age past 7 years while their ledger rows are retained, Step B will 23503 and abort the purge.

The 2026-08-07 regulatory audit of this PR corrected my initial framing twice, both times making it **worse** than first written here:

- It does not abort "before Step H is reached" in any contained way — it aborts the **entire transaction**, rolling back Step A and never reaching Steps C–M. GPS anonymization, chat/token/stripe-event cleanup and every other regulatory window stop too, repeating daily.
- It is *more* likely to fire than the Step H bug this migration fixes, not less. Step H needs a deletion request **plus** 7 years; Step B needs only the passage of time on any paid ride — and no purge step ever deletes non-DSAR `financial_events` rows, so essentially every aged paid ride carries a referencing row. Step B also has **no** per-row isolation, where Step H at least had a per-account handler.

Timing: 7 years after the first paid ride. The "~2033" originally written here was an unverified guess and is withdrawn — the real date depends on the earliest retained paid ride. Options when addressed: NULL `ride_id` before the ride delete, migrate the FK to `ON DELETE SET NULL`, or give Step B per-batch exception isolation — each with a different consequence for the tax record's ability to link a charge to its trip, so it is a retention-policy call, not just schema.

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
- CI **"Migration safety check" failed on first push** and was fixed, not bypassed by
  reflex: the gate flags any new migration that redefines an existing `CREATE OR REPLACE`
  target. Both targets here genuinely cannot be renamed — migration 58 already bound
  `financial_events_no_mutate` to `_financial_events_immutable` (renaming needs a
  DROP/CREATE TRIGGER, opening a window where the table is mutable), and
  `utils/retention_purge.py` invokes `purge_pii_retention` by name over RPC. The
  `-- migration-override-ok:` annotation the gate itself offers is therefore the correct
  resolution, with both justifications written into the migration header.

## 10. What was NOT verified

- **No runtime execution** — no real Postgres reachable from this environment, and trigger/GUC interplay cannot be exercised by pglast. Staging verification runbook before relying on it: (1) apply 289; (2) `SELECT purge_pii_retention(true)` dry run; (3) real run; (4) in psql, a manual `UPDATE financial_events ...` and a manual `DELETE FROM financial_events ...` must BOTH still raise.
- The purge has never executed against data old enough to reach Step H anywhere; first true exercise is years away by construction.
