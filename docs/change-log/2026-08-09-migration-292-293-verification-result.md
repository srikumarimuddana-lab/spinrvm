# Verification result — migrations 292 & 293 applied and asserted against a real Postgres

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-09 |
| Author | Repo owner (ran it) / Claude Code (wrote the script), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend (SQL) |
| Domain (Sentry tag) | payments |
| Related | `2026-08-08-unbalanced-check-date-scoped.md`; companion to `2026-08-08-migration-verification-result.md` (286–291) |

The repo owner applied migrations 292–293 and ran
`backend/scripts/verify_migrations_292_293.sql`. **All checks passed, no skips.**

This is the record of what that run proved and — just as importantly — what it did
not. The relevant "What was NOT verified" sections in the earlier entries are corrected
in place; anything still outstanding is listed here.

## What is now PROVEN

**The type match — the thing most likely to be wrong.** `CREATE FUNCTION` succeeding at
all proves `RETURNS SETOF financial_event_entries_unbalanced` matches the view's column
types. The reasoning behind it was that `SUM(bigint)` yields `numeric` in Postgres,
which is why the function deliberately does **not** cast its sums to `bigint`. Had that
been wrong the migration would have failed outright at apply time. It did not.

**The objects, and one failure mode that would have looked like success.** Both exist,
and `financial_event_entries_created_at` is asserted `indisvalid = true` — an
interrupted `CREATE INDEX CONCURRENTLY` leaves an INVALID index behind that the planner
silently ignores, so the fix would have looked applied while the nightly check kept
sequential-scanning.

**The grants.** `anon` and `authenticated` hold no EXECUTE on the SECURITY DEFINER
function, and `service_role` **retains** it — confirming the migration-205 form's
`REVOKE ... FROM PUBLIC` did not strip the inherited right it then grants back. Getting
that wrong would have left the nightly check dead in production and silent about it.

**The behaviour, in both directions:**
- a balanced journal is not reported;
- a lopsided one is, with `debit_cents` / `credit_cents` / `imbalance_cents` matching
  the inserted legs;
- the scoped function **agrees with the unscoped view** on the same event, so an
  operator querying by hand and the nightly job cannot tell different stories;
- an event whose legs fall before the window is excluded — *and* is found once the
  window is widened to cover it, which is what proves the exclusion was scoping rather
  than the row being invisible for some unrelated reason;
- `p_end` is exclusive and `p_start` is inclusive. Both matter and in opposite
  directions: a closed `p_end` double-reports a journal across consecutive daily runs,
  and an exclusive `p_start` drops it into the gap between them where it is never
  checked at all.

**`promo_expense` is accepted by migration 286's CHECK constraint.** Incidental to this
script's purpose but it closes a gap flagged in the promo change-log: check 3a inserts a
five-leg journal including a `promo_expense` debit, and it was accepted. That was
previously "verified by reading the applied migration, not by inserting a row with it."
The promo fix's chart-of-accounts assumption is now proven, not read.

## What is still NOT proven

- **The performance claim.** The script prints an `EXPLAIN` as advisory output and
  deliberately does **not** score it: on a small or empty table Postgres correctly
  prefers a sequential scan, so a seq scan there is not a failure and a proof of
  improvement is not available from it either. "Bounded by one day's legs rather than
  the whole table" still rests on the query shape, not on an observed plan at scale.
  Measuring it needs a populated table, which needs the double-entry flag on.
- **The Python ↔ PostgREST round trip.** The script calls the function over SQL. It has
  **not** been called the way `_check_entry_balance` calls it — `supabase-py`'s `rpc()`
  with ISO-8601 strings for two `timestamptz` params. That string→timestamptz coercion
  through PostgREST is assumed, not observed. Same class of remaining gap as migration
  288's `p_metadata` JSONB encoding.
- **A real nightly run.** `_check_entry_balance` has never executed inside a live
  `_run_reconciliation` against a real database; only in isolation against mocks.
- **Everything downstream of the flags.** Neither `ledger_double_entry_enabled` nor
  `ledger_atomic_settle_enabled` has been on end-to-end, so the table this function
  reads is still empty in every environment. It is proven correct on rows the
  verification script inserted and rolled back, not on rows the projection produced.

## Verification performed

`psql -v ON_ERROR_STOP=1 -f backend/scripts/verify_migrations_292_293.sql` against a
real Postgres with 292–293 applied. All assertions PASS, zero SKIP. The script runs in
one transaction ending in `ROLLBACK`, so nothing it inserted was committed.

Kept separate from `verify_migrations_286_291.sql` on purpose: that script is a recorded
artifact whose passing run on 2026-08-08 is cited in
`2026-08-08-migration-verification-result.md`, and editing it would break the provenance
of that result.

## Rollback plan

Unchanged, and both objects remain additive with their DROP statements in the migration
headers. Worth noting what this run changes about rollback risk: nothing. Neither
`DROP INDEX CONCURRENTLY financial_event_entries_created_at` nor
`DROP FUNCTION financial_event_entries_unbalanced_between(timestamptz, timestamptz)`
touches a row, and `_check_entry_balance` degrades to a logged skip when the function is
absent — so unlike migration 290, rolling these back restores no security hole and loses
no data.
