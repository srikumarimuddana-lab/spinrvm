# Migration 286–291 runtime verification — RESULT: all checks passed

Closes the single largest verification gap on PR #3464. Every change-log entry on this
branch carried a "What was NOT verified" line saying the migrations had only been parsed
with `pglast`, never executed. They have now been applied and exercised against a real
Postgres by the repo owner, via `backend/scripts/verify_migrations_286_291.sql`.

**Result: all checks passed.** Reported by the repo owner, 2026-08-08.

## What this now proves

Everything below moved from "reviewed" to "executed and asserted". These are the things a
mocked-Supabase unit suite structurally cannot test — which is precisely why the RLS/grant
blocker survived code review until the security and migration audits found it.

**The migrations apply.** `scripts/migrate.py` ran 286→291 successfully; the script requires
them present before it will assert anything.

**Grants — the audit blocker is genuinely closed.**
- `anon` / `authenticated` cannot EXECUTE `financial_events_missing_legs`,
  `settle_ride_card_payment`, or `purge_pii_retention`.
- `service_role` *can* still execute them — confirming the `REVOKE ... FROM PUBLIC` did not
  strip its inherited rights without the explicit re-`GRANT` (the migration-205 trap).
- `anon` / `authenticated` hold **no** INSERT/UPDATE/DELETE/TRUNCATE on either
  `financial_events` or `financial_event_entries`. The forged-ledger-row vector is shut.
- `anon` cannot SELECT either table; `authenticated` **retains** SELECT on
  `financial_events` — so the lockdown did not over-revoke and break riders reading their
  own ledger rows.
- `financial_event_entries_unbalanced` is not readable by either JWT role.

**Migration 286 constraints all enforce.** Balanced legs insert; a duplicate leg is rejected
by `UNIQUE (event_id, account, side)` (this is what makes projection retries idempotent);
`amount_cents = 0`, an unknown account, and an invalid `side` are each rejected; UPDATE is
blocked; the unbalanced view ignores a balanced entry and catches a lopsided one; FK
`ON DELETE CASCADE` takes legs with their header.

**Migration 289's delete gate holds under all four attacks.** UPDATE stays blocked *even
with the gate open*; DELETE is blocked when the gate is shut; DELETE is blocked when the GUC
holds a truthy-looking non-`'true'` value; DELETE succeeds only with the gate open; and the
GUC rolls back with an aborted subtransaction — so a purge that fails midway cannot leave
the tax ledger deletable.

**Migration 287's work queue filters correctly.** It includes an old leg-less charge and
excludes all four disqualifying cases: inside the 30-minute grace window (the tip-race
guard), `delta_cents = 0`, non-projectable `event_type`, and already-projected rows. Limit
clamps at both ends.

**Migration 288's atomic settle behaves.** It flips the ride to paid and returns the event
id; the ledger header lands in the same transaction; the tip is written; a replay hits the
paid-gate and returns NULL **without writing a second header**; a same-event-id replay does
not raise; an unknown ride raises; a negative amount is rejected; a downward tip correction
claws back `driver_earnings`; and earnings clamp at zero.

The last group matters most: it is the first execution of the RPC whose money-correctness
claim (byte-for-byte parity with `payment_service._tip_ride_update`) previously rested
entirely on code review. The money audit called that out as the one piece of new logic
carrying that exposure.

## What is still NOT verified

Being explicit, because "all migration checks passed" is narrower than "the feature works":

- **No end-to-end app run with either flag on.** The SQL layer is proven; the Python↔RPC
  round trip is not. Specifically unproven: `p_metadata` JSONB encoding through supabase-py,
  and `ledger_repo`'s error translation against a real PostgREST error (the
  `SettleRpcUnavailable` path is only exercised against mocks).
- **The projection loop has never run against real data.** Migration 287's RPC is verified;
  `utils/ledger_projection.py` driving it on a real backlog is not — including how many
  historical events project *degraded* (expected non-zero for cancellation fees predating
  the fee-split metadata).
- **No performance measurement.** Migration 291's index was created, but its effect on the
  work-queue plan was not captured — the script prints `EXPLAIN` output as an advisory
  NOTICE only. P95 settlement latency is still unmeasured.
- **Any check reported as SKIP did not run.** The script prints `SKIP:` with a reason when a
  precondition is missing (e.g. no non-paid ride to borrow for the 288 tests) and a SKIP is
  not a pass. If the run produced SKIP lines, the corresponding assertions above should be
  treated as still-unverified.

## Consequence for the rollout

The staging-verification precondition stated in the PR body and in every change-log entry on
this branch is **satisfied for the database layer**. The remaining gate before flipping
either flag in production is an end-to-end exercise with the flag on in staging —
`ledger_double_entry_enabled` first (watch `ledger_legs_degraded` volume), then
`ledger_atomic_settle_enabled` (watch `atomic_settle_fallback` and P95).
