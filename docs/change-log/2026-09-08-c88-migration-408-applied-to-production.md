# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude (session addressing ACTION_ITEMS.md C88/C89), human-directed |
| Surface(s) | Production database (`spinrmobileapp`, project `soavhtdhefowwvforzwb`) — schema only, no application code |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/pr-5085-5079-hardening-c88-close` |
| Related issue or gap ID | C88 (action items 1 and 3 of 3, closing the item); C89 (superseded, decision recorded) |

## 1. Issue / gap identified

`backend/migrations/408_rides_refund_amount.sql` (merged via PR #5105) had only been added to the repo — it had not been run against the real production database, and PR #5088's F1 fix (`charge.refunded` compare-and-swap on `rides.refund_amount`) had never had its live round-trip behavior verified against a database that actually has the column, because the column was confirmed missing on `spinrmobileapp` at the time.

A separate, prior misunderstanding (documented and corrected in PR #5106) had this session treating `spinrmobileapp` as a pre-production stand-in, with the "real" production database assumed to be an unreachable `Spinr-Prod` project. The user corrected this directly: `spinrmobileapp` **is** production, confirmed by project ID (`soavhtdhefowwvforzwb`). That correction is what made this action both possible and necessary — the missing column was on production all along, not a lower environment.

## 2. Root cause

The column was originally added to production via untracked, ad-hoc DDL (see migration 408's own top comment and C88 in `ACTION_ITEMS.md`), so no migration file ever tracked it. Once 408 was written to formalize it, applying it required a write to a now-confirmed production database — something this session does not do unilaterally. It required, and received, explicit user authorization.

## 3. Fix / remediation

With the user's explicit go-ahead ("Apply it, and for now dont have to narrow to read-only cause we need to write as well. so till october 31 it can be read and write and other permission."):

1. Applied `backend/migrations/408_rides_refund_amount.sql` to `spinrmobileapp` via `mcp__Supabase__apply_migration`. Re-verified via a live `information_schema.columns` query that `rides.refund_amount` (`NUMERIC(8,2)`, default `0`) now exists.
2. Inserted a matching tracking row into that project's own `schema_migrations` table (`filename = '408_rides_refund_amount.sql'`, `checksum = sha256(file bytes) = c8b4ce05355e7ee15bce0442b5cad24045323f219268bd6a4d5dd615e7fb033b`) — this repo's own `backend/scripts/run_migrations.py` and Supabase's native `apply_migration`/`supabase_migrations` tracking are two separate mechanisms; applying only through the MCP tool without this insert would have left `run_migrations.py --status` showing 408 as perpetually "pending" against a project that already has the column, which is exactly the tracking mismatch `backend/migrations/CLAUDE.md`'s `NEVER_APPLY`/`CONTRADICTION` handling exists to catch — not something to introduce deliberately.
3. Performed the live round-trip verification C88 required, safely: ran `BEGIN;` then an `UPDATE rides SET refund_amount = <test value> WHERE id = '261cd5f1-7c67-44bf-84bd-09a06206123a'`, then a `SELECT` re-filtering on that exact just-written value (mirroring `routes/webhooks.py`'s `update_one({"id": ride_id, "refund_amount": prev_refund_amount_raw}, ...)` compare-and-swap pattern), confirmed it matched the same row, then `ROLLBACK;`. A follow-up `SELECT` after the rollback confirmed `refund_amount` was back at its default `0.00` — the transaction left no trace on production data.

## 4. Risk & impact on existing functionality

- **Blast radius of the migration itself: isolated**, as already assessed by `spinr-migration-reviewer` in PR #5105 — `ADD COLUMN IF NOT EXISTS ... DEFAULT 0` is metadata-only on Postgres 11+, no table rewrite, no lock escalation, no clobbering of any pre-existing value (there was none — the column didn't exist).
- **Who else reads/writes `rides.refund_amount`:** only `backend/routes/webhooks.py`'s `charge.refunded` handler (read via the CAS filter, write via `update_one`). Grepped for any other reference — none found outside that handler and the migration/comment references to it. No other consumer to coordinate with.
- **Risk of the verification step itself:** a real `UPDATE` was issued against a live production row inside an explicit transaction, never committed. The only way this could have left a trace is if the connection dropped between `UPDATE` and `ROLLBACK` (Postgres would then roll back the open transaction automatically on disconnect) or if `ROLLBACK` itself failed silently — neither happened; the post-rollback `SELECT` directly confirmed the row's `refund_amount` was back at `0.00`, not inferred from the `ROLLBACK` command succeeding.
- **No RLS change, no index change** — same reasoning as PR #5105's Change Impact Log entry; nothing here adds to that.
- **Connector-permission risk, explicitly accepted, not overlooked:** the Supabase connector used for this stays at full read/write/other permissions (not narrowed to read-only) through 2026-10-31, per the user's explicit decision — recorded with a revisit trigger in `.claude/context/connector-scoping.md` so it doesn't silently become permanent. This is a real, accepted increase in blast radius for any session using this connector between now and that date, not a risk-free action.

## 5. User-experience effect

None directly from this change — the column now exists with the same default (`0`) it would have needed anyway, and the one code path that reads/writes it (`charge.refunded` webhook processing) was already assuming the column existed. If any real refund webhook had previously failed against production due to the missing column (not yet investigated — see "What was NOT verified"), that failure mode is now closed going forward; no new user-facing behavior is introduced.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `spinrmobileapp` production database (not a repo file) | `rides.refund_amount NUMERIC(8,2) DEFAULT 0` column created; `schema_migrations` tracking row inserted | Close the gap between the merged migration file and the real production schema |
| `ACTION_ITEMS.md` | C88 marked CLOSED with the final verification/apply details; C89 marked resolved with the Oct 31 decision | Track completion accurately |
| `.claude/context/connector-scoping.md` | Supabase row updated with the Oct 31 read/write exception and its revisit trigger | Keep the connector-scoping policy doc in sync with the actual, time-boxed decision |

## 7. Before / after

```
Before: rides.refund_amount does not exist on spinrmobileapp (production).
        Any real charge.refunded webhook attempting
        db_supabase.update_one(..., {"refund_amount": ...}) against this
        column would fail at the DB layer (500, surfaced loudly per this
        repo's error-handling convention — not silently swallowed).
```

```
After: rides.refund_amount NUMERIC(8,2) DEFAULT 0 exists on spinrmobileapp.
       Live round-trip verified (write -> CAS-filter read -> match) via a
       ROLLBACK'd transaction against a real row; no data left changed.
       schema_migrations row inserted so run_migrations.py --status shows
       408 as applied, not pending, against this project going forward.
```

## 8. Rollback plan

Same as migration 408's own stated rollback (`ALTER TABLE rides DROP COLUMN refund_amount;` in a new, forward-only migration — never edit or delete 408 itself per the append-only rule) — unchanged by this action, since applying the migration didn't introduce anything beyond what 408 already described. The verification step needed no rollback plan of its own: it was already inside a transaction that was rolled back, by design, before this entry was written.

## 9. Verification performed

- [x] `information_schema.columns` query against `spinrmobileapp` confirmed `rides.refund_amount` exists post-apply (`NUMERIC(8,2)`, default `0`).
- [x] `schema_migrations` row for `408_rides_refund_amount.sql` confirmed present with the correct sha256 checksum, matching the file as merged in PR #5105.
- [x] Live round-trip check: `BEGIN; UPDATE rides SET refund_amount = <value> WHERE id = '261cd5f1-...'; SELECT ... WHERE id = ... AND refund_amount = <same value>;` returned the expected row; `ROLLBACK;` issued; a subsequent plain `SELECT` confirmed `refund_amount` back at `0.00` on that row, proving no residual change.
- [x] User explicitly authorized both the migration apply and the connector-permission decision in this session, in writing, before either was executed.

## What was NOT verified

- **Whether any real `charge.refunded` webhook ever hit production before today while the column was missing**, and if so what happened to it (500 surfaced to Stripe's retry queue? swallowed somewhere upstream?). This would require a Stripe dashboard / Sentry log review spanning however long the column has been missing — out of scope for this entry, and not something this session's Supabase connector can answer on its own. Flagged as a possible follow-up in `ACTION_ITEMS.md` C88, not opened as a new item since it's speculative until someone checks.
- **No load/concurrency test was run** against the now-existing column under real traffic — the verification here was a single-row transactional check, not a production traffic replay. This is consistent with how every other migration in this repo is verified (schema-level correctness + reviewer sign-off, not live load testing) and isn't a new gap this change introduces.
- **The Oct 31 connector-narrowing deadline is not automated** — nothing in this repo or session enforces it; it depends on a human (or a future session prompted to check `connector-scoping.md`) actually acting before that date. This is stated in the doc itself as the revisit trigger, not left implicit.
