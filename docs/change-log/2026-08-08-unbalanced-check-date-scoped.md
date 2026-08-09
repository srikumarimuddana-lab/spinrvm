# Change Impact & Risk Log — Date-scope the nightly trial-balance check

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-08 |
| Author | Claude Code (agent), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend (SQL + one reconciliation check) |
| Domain (Sentry tag) | payments |
| Related issue or gap ID | Finding 4 of the code review of PR #3464 |

## 1. Issue / gap identified

`utils/reconciliation.py::_check_entry_balance` asks "are any double-entry journals
unbalanced for this day?" It did so by filtering the
`financial_event_entries_unbalanced` view (migration 286) on `created_at`:

```python
supabase.table("financial_event_entries_unbalanced")
    .select(...).gte("created_at", day_start).lt("created_at", day_end)
```

That column is `MIN(created_at)` — an **aggregate output**, not a grouping key.
Postgres cannot push a predicate on an aggregate result below the `GROUP BY`, so
every nightly run computed the full `GROUP BY event_id` + `HAVING` over the **entire
table** and only then discarded everything outside the day.

At the projected volume (~19k events/day × ~3 legs) that reaches **~20M rows within
a year**, re-aggregated every night to return the zero rows it is expected to return.
The cost grows monotonically with the ledger and never comes back down.

(`event_id` *is* a grouping key, so a caller filtering the view by `event_id` would
get pushdown. That is not the shape a daily check needs.)

## 2. Root cause

Migration 286 put `MIN(created_at)` on the view so a human could see roughly when an
unbalanced entry was written. That is a reasonable display column and a bad filter
column, and the distinction is invisible at the call site — the query reads exactly
like an indexed range scan. Nothing failed, nothing alerted; it just got slower every
day.

Secondarily, `financial_event_entries` had no index that could serve a bare
`created_at` range: its indexes are the `UNIQUE (event_id, account, side)` constraint
and `(account, created_at DESC)`. The composite leads with `account`, and Postgres has
no index skip scan, so even a correctly-shaped query would have sequential-scanned.

## 3. Fix / remediation

- **Migration 292** — `CREATE INDEX CONCURRENTLY financial_event_entries_created_at`.
  Without it the new query path is still a sequential scan and the fix buys nothing.
- **Migration 293** — `financial_event_entries_unbalanced_between(p_start, p_end)`,
  which applies the date bound *inside* the aggregate where it can be pushed down.
  `RETURNS SETOF financial_event_entries_unbalanced` (the same `SETOF <relation>`
  idiom as migration 287) so the scoped and unscoped paths are column-identical —
  an operator querying the view by hand and the nightly job see the same shape.
- **The window is applied via an `IN` subquery**, not a bare `WHERE` on the outer
  aggregate. `write_legs` stamps every leg of one event with a single
  Python-computed timestamp and inserts them in one batch, so legs cannot straddle a
  day boundary *today* — but a bare `WHERE ... GROUP BY event_id` silently becomes
  **wrong** if that ever changes: a split batch would be aggregated in halves and
  each half would look unbalanced. That is a false alarm on the one control that is
  supposed to mean "something is genuinely broken". The subquery costs one extra
  index lookup per event and is correct either way.
- **`_check_entry_balance`** now calls the RPC instead of the view.
- **First tests for `_check_entry_balance`** — it had none.

## 4. Risk & impact on existing functionality

**Blast radius: one reconciliation check, one new index, one new function.**

- **`_check_entry_balance` has exactly one caller** — `_run_reconciliation`, which
  awaits it after the Stripe-vs-ledger comparison. That comparison, `_record_discrepancy`,
  `_sum_stripe_intents`, and `_sum_financial_events` are untouched.
- **The view is NOT redefined or dropped.** Migration 286 is already applied to a
  real Postgres; changing it now would be a destructive edit to a live object. It
  stays as the ad-hoc/human surface, and migration 293's function is the scoped path
  for the automated job. The verification script asserts the two agree.
- **No behavioural change to what gets alerted on.** Same predicate, same day
  window, same ERROR line. Only the execution plan changes — plus one genuine
  semantic hardening (the straddle case above), which can only *remove* false alarms.
- **Partial deploy is safe:** if 293 is not applied the RPC 404s, the existing
  `except` logs at info ("migrations 286/292/293 not applied?") and returns. The
  daily Stripe-vs-ledger reconciliation is unaffected either way. Pinned by a test.
- **Write cost:** one B-tree entry per leg. `financial_event_entries` is written only
  by the `ledger_projection` background loop (single-writer invariant), never in the
  settlement request path, so this adds nothing to the P95 fare-settlement SLA.
- **Migration 292 uses `CONCURRENTLY`**, so it takes no blocking lock;
  `scripts/migrate.py` detects the keyword and runs the file outside a transaction,
  as Postgres requires (`test_migration_concurrently_splitting.py` covers the
  runner's side and passes).
- **Everything here is behind `ledger_double_entry_enabled`**, which is off in
  production: with the flag off `financial_event_entries` is empty, so both the old
  and new query cost nothing. This is a fix for the state the feature is heading
  into, not for today.

## 5. User-experience effect

Nobody. No rider, driver, corporate-admin or internal-admin surface changes. Not
even an on-call-visible change — same alert, same threshold, same message.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/292_financial_event_entries_created_at_index.sql` | **New.** `created_at` index, CONCURRENTLY | Nothing could serve a bare date range |
| `backend/migrations/293_financial_event_entries_unbalanced_scoped.sql` | **New.** Date-scoped RPC, `SETOF` the view | Push the date predicate below the GROUP BY |
| `backend/utils/reconciliation.py` | `_check_entry_balance` calls the RPC | The fix's call site |
| `backend/tests/test_reconciliation.py` | +6: window params, month-end, balanced, unbalanced, missing-RPC, never-raises | Function had zero coverage |
| `backend/tests/test_unbalanced_scoped_migration.py` | **New.** 8 SQL-contract tests | Pin the properties that make this a fix, not a rename |
| `backend/scripts/verify_migrations_292_293.sql` | **New.** 13 runtime assertions | pglast proves syntax only |

## 7. Before / after

```python
# Before — the predicate is on MIN(created_at), an aggregate output.
# Postgres aggregates the WHOLE table, then discards everything outside the day.
supabase.table("financial_event_entries_unbalanced")
    .select("event_id,debit_cents,credit_cents,imbalance_cents")
    .gte("created_at", day_start).lt("created_at", day_end)
```

```python
# After — the date bound crosses as RPC params and is applied inside the aggregate.
await rpc("financial_event_entries_unbalanced_between",
          {"p_start": day_start, "p_end": day_end})
```

```sql
-- The pushdown, plus the straddle-proof event scoping
SELECT e.event_id, SUM(...), SUM(...), SUM(...), MIN(e.created_at)
FROM financial_event_entries e
WHERE e.event_id IN (SELECT w.event_id FROM financial_event_entries w
                      WHERE w.created_at >= p_start AND w.created_at < p_end)
GROUP BY e.event_id
HAVING SUM(CASE WHEN e.side='debit' THEN e.amount_cents ELSE -e.amount_cents END) <> 0;
```

## 8. Rollback plan

No data to unwind; both objects are additive.

1. `DROP FUNCTION IF EXISTS financial_event_entries_unbalanced_between(timestamptz, timestamptz);`
   — `_check_entry_balance` degrades to a logged skip (partial-deploy guard, tested).
   The daily Stripe-vs-ledger reconciliation keeps running.
2. `DROP INDEX CONCURRENTLY IF EXISTS financial_event_entries_created_at;` — nothing
   depends on it for correctness, only for speed.
3. Or, without touching the DB at all: `ledger_double_entry_enabled = false` in
   `app_settings` (no deploy). The legs table stops growing and the check has nothing
   to scan either way.

Both DROPs are in the migration headers. Neither restores a security hole (contrast
migration 290's rollback) and neither touches a row.

## 9. Verification performed

- Both migrations re-parsed with `pglast` (the real PostgreSQL parser): 2 and 5
  statements accepted. The verification script parses too (11 statements).
- Migration convention gates: `test_migration_ordering`,
  `test_migration_concurrently_splitting`, `test_migrate_autocommit_chunks`,
  `test_migration_fk_column_types` — **59 passed**. 292/293 are the next free
  numbers (`ls backend/migrations | sort -V | tail -1` → 291).
- Blast-radius grep before writing the fix: `_check_entry_balance` has one caller;
  `financial_event_entries_unbalanced` is referenced only by the old call site and
  the 286-291 verification script.
- Targeted battery (`test_reconciliation`, `test_ledger_service`,
  `test_ledger_projection`, `test_atomic_settle`, `test_replay_safety_payment_loops`)
  — **106 passed**, plus 8 in the new SQL-contract file.
- One of those contract tests guards the link no SQL test can see: that
  `reconciliation.py` calls the exact name migration 293 defines. A rename on either
  side would turn the nightly check into a **permanent silent no-op**, because a
  missing RPC is deliberately treated as "not deployed yet" rather than an error.
- `ruff check` + `ruff format --check` clean.
- **Full backend suite run to completion BEFORE the push** — result in §11.

## 10. What was NOT verified

> **UPDATE 2026-08-09 — the database layer of this gap is now CLOSED.** The repo owner
> applied migrations 292–293 and ran `backend/scripts/verify_migrations_292_293.sql`;
> **all checks passed, no skips**. See
> `docs/change-log/2026-08-09-migration-292-293-verification-result.md` for exactly what
> that proved. The items below are corrected in place; anything still outstanding is
> called out there.

- ~~Neither migration has executed against a real Postgres.~~ **Applied and asserted
  2026-08-09.**
  - ~~that `RETURNS SETOF ...` matches the view's column types — the single most likely
    thing to be wrong in this change.~~ **Proven by `CREATE FUNCTION` succeeding at
    all**: the `SUM(bigint)` → `numeric` reasoning holds, and had it not, the migration
    would have failed outright at apply time.
  - ~~that the window bounds are half-open in the direction intended.~~ **Asserted in
    both directions**: `p_end` exclusive (a closed one double-reports across
    consecutive daily runs) and `p_start` inclusive (an exclusive one drops journals
    into the gap between runs, never checked at all).
  - ~~that the migration-205 grant form left `service_role` with EXECUTE.~~ **Asserted**,
    along with `anon`/`authenticated` holding none.
  - Additionally asserted, and not something the original list thought to ask for: the
    index is `indisvalid = true`. An interrupted `CREATE INDEX CONCURRENTLY` leaves an
    INVALID index the planner silently ignores — the fix would have *looked* applied
    while the nightly check kept sequential-scanning.
- **The performance claim is STILL reasoned, not measured.** Unchanged by the
  verification run. No `EXPLAIN ANALYZE` against a populated table, no before/after
  timing. The script prints an `EXPLAIN` as advisory output, but on a small or empty
  staging table Postgres will correctly choose a sequential scan, so it is deliberately
  **not scored** — a seq scan there is not a failure, and it is not a proof of
  improvement either. The claim "this is now bounded by one day's legs rather than the
  whole table" rests on the query shape, not on an observed plan at scale. Measuring it
  needs a populated table, which needs the double-entry flag on.
- **The Python ↔ PostgREST round trip is still unproven.** The verification script calls
  the function over SQL, not the way `_check_entry_balance` calls it (`supabase-py`
  `rpc()` with ISO-8601 strings for two `timestamptz` params). Same class of gap as
  migration 288's `p_metadata` JSONB encoding.
- **The `~20M rows/year` figure is derived from the projection's own design
  throughput** (200 events/tick × 96 ticks/day × ~3 legs), not from measured
  production volume. The direction of the problem does not depend on the figure being
  right; the urgency does.
- `_check_leg_completeness` and `_check_entry_balance` are now both tested in
  isolation, but **neither has been exercised inside a real `_run_reconciliation`
  run against a live DB.**
- Findings 5–7 of the review (non-atomic `extra_ride_fields` follow-up on the
  flag-on settle path, cross-module use of `ledger_service._escalate`, and
  `insert_many` reporting success when Supabase is unconfigured) remain **unfixed**.

## 11. Full suite result

`pytest backend/tests` run to completion **before** the push.

```
10050 passed, 8 skipped, 1 xfailed, 20 warnings in 631.76s (0:10:31)
```

Exit code 0, zero `FAILED`/`ERROR` lines. Baseline before this change was
**10,035 passed**, so the delta is **+15** against 14 hand-written tests. The extra
one is not a mystery and is worth recording: `test_migration_concurrently_splitting.py::test_no_prose_or_body_fragment_leaks_out`
is parameterized over every migration containing `CONCURRENTLY`, so migration 292
enrolled itself in the gate that checks `scripts/migrate.py` can split the file into
statements without handing Postgres a fragment of prose. Migration 293 has no
`CONCURRENTLY` and adds no case. (An unexplained delta would have meant a
pre-existing test changed state; this one is fully attributed.)
