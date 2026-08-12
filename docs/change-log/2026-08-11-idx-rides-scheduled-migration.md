# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend, migrations |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (this branch) |
| Related issue or gap ID | Scheduled-ride audit (`spinr-dispatch-reviewer`), P1 finding #3 |

## 1. Issue / gap identified

`idx_rides_scheduled` — the index `utils/scheduled_rides.py`'s own code comments and migration 276's header both assert backs the scheduled-ride dispatcher's core per-tick query — was never created by any numbered migration in `backend/migrations/`. It only exists in `backend/supabase_schema.sql`, a standalone bootstrap file outside `backend/scripts/migrate.py`'s tracked chain. Any environment provisioned purely through the migration runner is missing it.

## 2. Root cause

Migration 114 (which added the `scheduled_dispatched`/`reminder_sent` columns) and migration 276 (which added a `scheduled_time` index) both assumed `idx_rides_scheduled` already existed and explicitly declined to add their own supporting index on that assumption. No migration ever actually created it — it was only ever added directly to the `supabase_schema.sql` bootstrap reference file, likely during initial schema setup, and never back-filled into the tracked migration sequence.

## 3. Fix / remediation

New migration `298_rides_scheduled_index.sql` creates `idx_rides_scheduled` via `CREATE INDEX CONCURRENTLY IF NOT EXISTS` (autocommit-routed by the migration runner, so it never blocks live dispatch/booking traffic). Rather than copying `supabase_schema.sql`'s existing (non-partial composite) shape verbatim, the index shape was improved during a `spinr-migration-reviewer` review of this migration: it's now a **partial, covering** index — `ON rides (scheduled_time) WHERE is_scheduled = TRUE AND status = 'scheduled'` — which self-prunes as rides dispatch (rather than permanently retaining every ride that ever passed through `scheduled` status) and serves the dispatcher's whole query (`WHERE ... ORDER BY scheduled_time LIMIT 100`) as a single index-only sorted scan. `backend/supabase_schema.sql`'s definition was updated in the same commit to match, so a fresh bootstrap and a fresh `migrate.py` run produce an identical index.

## 4. Risk & impact on existing functionality

- **Blast radius: read-path only, single table (`rides`).** No write path, RLS policy, or application code depends on this index's existence for *correctness* — only for *performance* of the scheduled-ride dispatcher's candidate query.
- **CONCURRENTLY + IF NOT EXISTS**: safe to apply against live traffic; a no-op on any environment that already has an index of this name (from the old bootstrap path).
- **Known limitation, not a regression**: an environment that already ran the OLD `supabase_schema.sql` bootstrap (non-partial shape) has an index of the same NAME but the OLD shape — `CREATE INDEX ... IF NOT EXISTS` matches by name, not definition, so this migration is a no-op there and that environment simply doesn't get the extra optimization. This is a strict no-worse-than-before outcome (identical to the situation before this migration existed), not a new risk. Reconciling it requires a manual `DROP` + recreate on that specific environment if one is ever found running the old bootstrap path in production.

## 5. User-experience effect

- None directly visible. This closes a latent performance gap (a full-table scan on `rides` every ~45–66s per replica, on any environment missing the index) that would only become user-visible as dispatch latency degradation as scheduled-ride volume grows. No rider/driver/admin-facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/298_rides_scheduled_index.sql` | New migration: creates `idx_rides_scheduled` as a partial covering index | Track the index the dispatcher's own code has always assumed exists |
| `backend/supabase_schema.sql` | Updated `idx_rides_scheduled` definition to match the new partial/covering shape | Keep the bootstrap file and the tracked migration chain producing identical schemas |

## 7. Before / after

```sql
-- Before (supabase_schema.sql only; not in any tracked migration)
CREATE INDEX IF NOT EXISTS idx_rides_scheduled ON rides (is_scheduled, status);
```

```sql
-- After (migration 298 + supabase_schema.sql, kept in sync)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_scheduled
    ON public.rides (scheduled_time)
    WHERE is_scheduled = TRUE AND status = 'scheduled';
```

## 8. Rollback plan

- `DROP INDEX CONCURRENTLY IF EXISTS idx_rides_scheduled;` — safe to run at any time. This is a read-path performance index, not a correctness dependency; `check_scheduled_rides()` would simply fall back to the pre-migration full-scan behavior, not break.
- No data migrated, no feature flag needed.

## 9. Verification performed

- [x] Reviewed by a dedicated `spinr-migration-reviewer` agent pass: confirmed no existing migration creates this index (or a near-duplicate), confirmed `supabase_schema.sql` is genuinely outside the tracked chain, confirmed the `CONCURRENTLY` autocommit routing in `backend/scripts/migrate.py` is correct, confirmed 298 is the next free migration number, and suggested the partial/covering shape improvement adopted here.
- [x] Cross-checked the index shape against `utils/scheduled_rides.py`'s actual query (`WHERE is_scheduled=True, status='scheduled', order='scheduled_time', limit=100`).
- [ ] Not applied against a real Postgres/Supabase instance in this environment — verified by static SQL review and the migration-reviewer agent pass, not by running `EXPLAIN ANALYZE` against real data.

## What was NOT verified

- No real Postgres instance available in this environment — the migration has not been executed, and its performance improvement (full-scan → index-only scan) is reasoned about, not measured with `EXPLAIN ANALYZE`.
- Did not audit whether any other Spinr environment is currently running on the old (non-partial) bootstrap-created index shape; if one exists, it will not automatically pick up the improved shape (see §4 known limitation).
