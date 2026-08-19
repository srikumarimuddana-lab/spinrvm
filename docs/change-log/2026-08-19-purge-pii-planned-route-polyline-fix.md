# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent session) |
| Surface(s) | backend / migrations |
| Domain (Sentry tag) | rides (retention/PIPEDA) |
| PR / commit link | (this branch's PR) |
| Related issue or gap ID | ACTION_ITEMS.md A40 ranked blocker #4/#11 (`docs/audit/2026-08-18-full-fleet-whole-app-audit.md`) |

## 1. Issue / gap identified

`purge_pii_retention()`'s Step A anonymizes ride GPS at the regulatory
3-year window — `pickup_lat/lng`, `dropoff_lat/lng`, `route_polyline`,
`phase_polylines`, `route_snapshot_url` — but never touched
`rides.planned_route_polyline` (migration 100, the Google Directions
polyline captured at booking time). A ride older than 3 years still had its
full planned turn-by-turn route live in that column even after
`gps_anonymized_at` was stamped.

## 2. Root cause

`planned_route_polyline` was added by migration 100, after Step A's
original definition (migration 50) was written, and no later purge-function
revision (141/143/187/216/228/285/289/296/321/323/324) ever added it to the
anonymizing `SET` clause — a field simply missed when the column was
introduced, not a deliberate exclusion.

## 3. Fix / remediation

New migration 335 re-forks `purge_pii_retention` verbatim from its current
definition (migration 324) and adds
`planned_route_polyline = '[]'::jsonb` to Step A's `UPDATE ... SET` clause
— the same empty-array sentinel migration 100 uses as the column's own
`DEFAULT`, and the same shape `route_polyline`/`phase_polylines` already
reset to on this step. Every other step (B through N) is byte-for-byte
unchanged — confirmed by a migration-reviewer diff pass against 324.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to Step A of one function.** `purge_pii_retention`
  is called only by the retention-purge background loop
  (`backend/core/lifespan.py`) and manually via `p_dry_run=true` for
  operator preview — no application code path reads/writes
  `planned_route_polyline` after ride creation (it's write-once at booking,
  read-only afterward for route display), so nulling it on rides already
  past the 3-year window has no other consumer to break.
- **What else reads `rides.planned_route_polyline`:** grepped the backend —
  only ride-detail/route-display serialization paths for an active or
  recent ride. A ride old enough to hit this purge step (3+ years) is
  effectively never displayed via those paths in normal product use.
- **Idempotent, additive-only:** `CREATE OR REPLACE FUNCTION`, no schema
  change (column and type already exist from migration 100), no new lock
  beyond the existing 03:00 UTC loop's Redis leader lock. The unchanged
  `gps_anonymized_at IS NULL` guard means a ride already anonymized under
  the old function body is **not** retroactively re-touched by this fix —
  see "What was NOT verified" below.
- **Background loops:** only the existing retention-purge loop is affected;
  no new loop added, no other of the 37 startup loops touched.
- **Money:** none.

## 5. User-experience effect

None. This is a server-side background retention job; no rider, driver, or
admin UI reads or displays the effect of this purge step directly.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/335_purge_pii_retention_step_a_planned_route_polyline.sql` | New migration — re-forks `purge_pii_retention` from 324, adds `planned_route_polyline` to Step A's anonymizing `SET` clause | Close the 3-year GPS retention gap for this column |
| `backend/tests/test_step_a_planned_route_polyline_purge_migration.py` | New regression test suite (textual SQL-contract pinning, same convention as the 321/323/324 test files — CI has no live Postgres) | Prove the fix and guard against future re-forks dropping it or another column |

## 7. Before / after

```sql
-- Before (Step A, migration 324)
UPDATE rides
SET pickup_lat         = NULL,
    pickup_lng         = NULL,
    dropoff_lat        = NULL,
    dropoff_lng        = NULL,
    route_polyline     = '[]'::jsonb,
    phase_polylines    = '{}'::jsonb,
    route_snapshot_url = NULL,
    gps_anonymized_at  = v_started_at
WHERE created_at < v_started_at - c_gps_anon_age
  AND gps_anonymized_at IS NULL;
```

```sql
-- After (Step A, migration 335)
UPDATE rides
SET pickup_lat             = NULL,
    pickup_lng             = NULL,
    dropoff_lat            = NULL,
    dropoff_lng            = NULL,
    route_polyline         = '[]'::jsonb,
    phase_polylines        = '{}'::jsonb,
    route_snapshot_url     = NULL,
    planned_route_polyline = '[]'::jsonb,
    gps_anonymized_at      = v_started_at
WHERE created_at < v_started_at - c_gps_anon_age
  AND gps_anonymized_at IS NULL;
```

## 8. Rollback plan

Re-apply migration 324's `purge_pii_retention` definition verbatim (drops
`planned_route_polyline` from Step A's `SET` clause). Note: rolling back the
function does **not** restore rows this version already cleared — that
matches every prior migration in this chain (321/323/324), none of which
can un-anonymize already-purged data either. This is a documented,
consistent property of the whole `purge_pii_retention` migration chain, not
a new gap introduced here.

## 9. Verification performed

- `pytest tests/test_step_a_planned_route_polyline_purge_migration.py -q --no-cov` → 7 passed.
- `pytest tests/test_retention_purge.py tests/test_retention_purge_coverage.py tests/test_step_f_stripe_events_column_fix_migration.py tests/test_step_d_ride_messages_column_fix_migration.py tests/test_step_h_driver_rides_guard_migration.py tests/test_step_a_planned_route_polyline_purge_migration.py -q --no-cov` → 56 passed (no regression to any prior purge-step fix's own regression suite).
- `spinr-migration-reviewer` agent pass: numbering, append-only, reversibility, forward-compatibility, and money-function-adjacent (`SECURITY DEFINER`/`REVOKE`/`GRANT`) conventions all confirmed clean; diffed programmatically against 324 to confirm every other step is byte-for-byte unchanged. Verdict: SAFE TO APPLY.
- Confirmed `planned_route_polyline`'s type/default (`JSONB DEFAULT '[]'::JSONB`, migration 100) matches the sentinel used here.

## 10. What was NOT verified

- **No live Supabase/production run.** This migration has not been applied
  to the production Supabase project — per this repo's standing convention,
  applying a migration to live data requires explicit confirmation, which
  was not sought this session. It ships as a migration file for the normal
  deploy pipeline / `run_migrations.py`, same as every prior purge-chain fix.
- Because of the unchanged `gps_anonymized_at IS NULL` guard, any ride that
  was already anonymized under the *old* function body (i.e. before this
  migration lands) will **not** be retroactively re-swept to clear its
  `planned_route_polyline` — only rides that hit the 3-year window *after*
  this migration is applied get the new column cleared. Backfilling
  already-anonymized-but-still-carrying-`planned_route_polyline` rows was
  out of scope for this fix and would need its own explicit one-time
  UPDATE, deliberately not bundled here to keep this change purely additive
  and low-risk (per CLAUDE.md's "additive over destructive" preference) —
  flagging as a known residual gap rather than silently leaving it
  unaddressed.
