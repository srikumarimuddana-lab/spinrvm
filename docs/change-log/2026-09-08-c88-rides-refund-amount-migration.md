# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude (session addressing ACTION_ITEMS.md C88/C89) |
| Surface(s) | backend (migrations only — no application code) |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/pr-5085-5079-hardening-c88-migration` |
| Related issue or gap ID | C88 (action item 2 of 3); C89 (documentation only, no code) |

## 1. Issue / gap identified

`ACTION_ITEMS.md` C88 found that `backend/routes/webhooks.py`'s `charge.refunded` handler has read and written `rides.refund_amount` since before this session, but no migration file anywhere in this repo ever created that column. It exists on production only via untracked, ad-hoc DDL, and is missing entirely on the `spinrmobileapp` Supabase project (confirmed via a live `information_schema.columns` query during C88's investigation).

## 2. Root cause

The column was added directly against the database at some point in the past, outside the migration system — exactly the class of drift `backend/migrations/CLAUDE.md`'s append-only convention exists to prevent, predating full coverage of that convention.

## 3. Fix / remediation

Added `backend/migrations/408_rides_refund_amount.sql`: `ALTER TABLE rides ADD COLUMN IF NOT EXISTS refund_amount NUMERIC(8,2) DEFAULT 0;` plus a `COMMENT ON COLUMN` documenting its purpose and a `NOTIFY pgrst, 'reload schema';`. `IF NOT EXISTS` makes this a safe no-op on any environment (production) that already has the column via the ad-hoc DDL, while actually creating it on `spinrmobileapp` and any future environment bootstrapped from `backend/migrations/` alone.

This closes action item (2) of C88's three-part remediation. Items (1) (a live round-trip verification against the real production column) and (3) (re-scoping the Supabase connector to reach it) remain open — both require access this session doesn't have. C89's entry was updated with a concrete, ready-to-execute recommendation (a second, read-only, narrowly-scoped Supabase connector) for whoever owns the Supabase account to act on.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Single new migration file; no application code changed. `routes/webhooks.py`'s existing read/write of `rides.refund_amount` is completely unaffected — it already assumed the column exists (and does, on every environment that matters in production), so this migration changes nothing about runtime behavior there.
- **Metadata-only operation, no table rewrite**: `ADD COLUMN ... DEFAULT <constant>` is a metadata-only change on Postgres 11+ (confirmed by `spinr-migration-reviewer`) — no lock escalation or rewrite of the `rides` table, safe against in-flight traffic.
- **`IF NOT EXISTS` cannot clobber existing data**: on an environment where the column already exists (production), Postgres skips the entire `ADD COLUMN` clause — it does not re-apply `DEFAULT 0` to existing rows, does not touch the existing column's real default, and cannot overwrite any already-recorded refund amount.
- **No index added**: grepped every read of `refund_amount` outside `webhooks.py`'s own CAS filter (which is already scoped by primary key `id`) — none exist. The admin dashboard's "refund_amount" KPI is a different column entirely (`disputes.refund_amount`, via migration 163's `admin_earnings_refunds()`).
- **No RLS change needed**: this is a new column on an existing table (`rides`), not a new table — the "every new table ships with RLS" rule doesn't apply, and `rides`' existing row-level policies already cover all columns.
- **`spinr-migration-reviewer` review**: verdict SAFE TO APPLY, no blockers. One non-blocking style suggestion (document the "no index needed" reasoning inline, matching migration 407's precedent) — incorporated into the final file.

## 5. User-experience effect

None — this migration only formalizes a column that already exists and is already in active use on production; no behavior change for any rider/driver/admin.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/408_rides_refund_amount.sql` | New migration: adds `rides.refund_amount NUMERIC(8,2) DEFAULT 0` (idempotent) | Close the untracked-DDL gap C88 found; make `spinrmobileapp` and any future fresh bootstrap match production's actual schema |
| `ACTION_ITEMS.md` | C88 action item (2) marked done; C89 given a concrete recommendation with exact execution steps | Track completion; turn an open decision into an actionable plan |
| `.claude/context/connector-scoping.md` | Supabase row updated to point at C89's recommendation | Keep the connector-scoping policy doc in sync with the tracked decision |

## 7. Before / after

```sql
-- Before: no migration file defines this column anywhere in the repo.
-- Production has it via undocumented, untracked manual DDL.
-- spinrmobileapp does not have it at all.
```

```sql
-- After: backend/migrations/408_rides_refund_amount.sql
ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS refund_amount NUMERIC(8,2) DEFAULT 0;

COMMENT ON COLUMN rides.refund_amount IS '...';

NOTIFY pgrst, 'reload schema';
```

| | Production (already has the column) | `spinrmobileapp` / a fresh bootstrap (didn't) |
|---|---|---|
| Before this migration | Column exists via undocumented DDL; `webhooks.py` reads/writes it correctly | Column missing entirely; any `charge.refunded` webhook there would fail or silently no-op on this column |
| After this migration runs | No-op (`IF NOT EXISTS` skips the whole clause) — nothing changes | Column created with default `0`, matching what production has had all along |

## 8. Rollback plan

`git revert` reverts the migration file itself, but per the append-only convention this migration is never edited or deleted once merged. If the column genuinely needs to be removed (unlikely — it backs a live, in-use CAS check), a **new** migration would run `ALTER TABLE rides DROP COLUMN refund_amount;` — explicitly called out as destructive in the migration's own top comment, conditional on confirming no in-flight refund CAS check depends on it first. No feature flag applies to a schema migration.

## 9. Verification performed

- [x] Confirmed 408 is the next free migration number via `git ls-tree -r origin/main --name-only -- backend/migrations` (highest existing: 407) — no collision.
- [x] `spinr-migration-reviewer` subagent review: **SAFE TO APPLY, no blockers.** Confirmed numbering, append-only compliance, forward-compatibility (metadata-only op, no rewrite/lock escalation), `IF NOT EXISTS` no-clobber behavior, no index needed (verified via grep of every `refund_amount` read site), and N/A RLS (existing table, column-add only). One non-blocking style suggestion (inline "no index needed" reasoning) was incorporated.
- [x] Cross-checked `NUMERIC(8,2)`'s 2-decimal scale against `backend/utils/money.py`'s `_TWO_PLACES` — exact match, no precision mismatch with the `dollars_to_cents()`/`cents_to_dollars()` round-trip `webhooks.py` already performs on this column.
- [x] Confirmed the column type matches the only other `refund_amount` column in this schema (`disputes.refund_amount`, migration 10) — same `NUMERIC(8,2)`.

## What was NOT verified

**Not applied against any real database, deliberately.** This session's Supabase connector can reach `spinrmobileapp` and does expose an `apply_migration` tool, but using it here would apply the change through Supabase's own migration-tracking mechanism (`supabase_migrations`), not this repo's `backend/scripts/run_migrations.py` / `schema_migrations` pipeline — the two are different systems, and applying via the MCP tool would leave this file showing as "pending" in `run_migrations.py --status` forever, even though the column already exists, exactly the kind of tracking mismatch `backend/migrations/CLAUDE.md`'s `NEVER_APPLY`/`CONTRADICTION` handling exists to catch, not something to introduce deliberately. The correct application path is `python -m backend.scripts.run_migrations` (per the root `CLAUDE.md`), run by whoever operates that pipeline against each environment — this file will be picked up there the next time it runs. C88's action item (1) (the live round-trip verification against the real production column) remains genuinely unverified and requires a human with `Spinr-Prod` access, as stated in that item.
