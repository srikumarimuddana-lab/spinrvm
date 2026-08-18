# Deploy Runbook: Migrations 64 & 65 — Driver Insurance Periods

**Scope:** `64_driver_insurance_periods.sql` + `65_backfill_driver_insurance_periods.sql`  
**Regulatory context:** SGI / Saskatchewan Transportation Act — 7-year append-only audit requirement.  
**Estimated prod window:** < 5 s DDL (migration 64) + variable DML (migration 65, proportional to online driver count; typically < 1 s in Saskatchewan launch scale).

---

## 1. Pre-Deploy Checks

### 1a. Confirm staging test completed successfully

Run the dry-run against staging first:

```bash
export DATABASE_URL=<staging-pooler-connection-string>
python -m backend.scripts.run_migrations --dry-run
```

Expected output shows both `64_driver_insurance_periods.sql` and `65_backfill_driver_insurance_periods.sql` as pending.

Then apply for real on staging:

```bash
python -m backend.scripts.run_migrations
```

### 1b. Verify the table and indexes exist on staging

```sql
-- Table exists
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_name = 'driver_insurance_periods';

-- Partial unique index (the idempotency gate for open rows)
SELECT indexname, indexdef FROM pg_indexes
WHERE tablename = 'driver_insurance_periods'
  AND indexname = 'driver_insurance_periods_open';

-- All three indexes
SELECT indexname FROM pg_indexes
WHERE tablename = 'driver_insurance_periods'
ORDER BY indexname;
-- Expected: driver_insurance_periods_driver_started
--           driver_insurance_periods_open
--           driver_insurance_periods_ride

-- RLS enabled
SELECT relrowsecurity FROM pg_class
WHERE relname = 'driver_insurance_periods';
-- Expected: t

-- Tamper-evidence trigger attached
SELECT tgname FROM pg_trigger
WHERE tgrelid = 'driver_insurance_periods'::regclass;
-- Expected: driver_insurance_periods_no_mutate

-- SELECT policy present; no INSERT/UPDATE/DELETE policies (by design)
SELECT policyname, cmd FROM pg_policies
WHERE tablename = 'driver_insurance_periods';
-- Expected: exactly one row, cmd = SELECT
```

### 1c. Expected row counts before and after migration 65

**Before 65 runs** (immediately after 64):

```sql
SELECT COUNT(*) FROM driver_insurance_periods;
-- Expected: 0
```

**After 65 runs** — count must equal the number of currently-online drivers:

```sql
-- Online drivers
SELECT COUNT(*) FROM drivers WHERE is_online = true;

-- Open period rows (ended_at IS NULL)
SELECT COUNT(*) FROM driver_insurance_periods WHERE ended_at IS NULL;

-- The two counts must match.
-- Break down by period to sanity-check:
SELECT period, COUNT(*) FROM driver_insurance_periods
WHERE ended_at IS NULL GROUP BY period ORDER BY period;
-- Period 1: idle-online drivers
-- Period 2: drivers in driver_assigned / driver_accepted / driver_arrived
-- Period 3: drivers in in_progress rides
```

**Period-3 sanity check** — every period-3 row must have a ride_id linking to an in_progress ride:

```sql
SELECT COUNT(*) FROM driver_insurance_periods dip
JOIN rides r ON r.id = dip.ride_id
WHERE dip.period = 3 AND r.status = 'in_progress' AND dip.ended_at IS NULL;
-- Must equal the period-3 count above.
```

### 1d. Verify no duplicate open periods (the partial unique index prevents this, but confirm)

```sql
SELECT driver_id, COUNT(*) FROM driver_insurance_periods
WHERE ended_at IS NULL
GROUP BY driver_id HAVING COUNT(*) > 1;
-- Expected: 0 rows
```

### 1e. Verify the tamper-evidence trigger blocks a DELETE

```sql
-- Run on staging only; should raise an exception:
DELETE FROM driver_insurance_periods LIMIT 1;
-- Expected: ERROR: driver_insurance_periods rows are append-only and cannot be deleted
-- Roll back this test transaction immediately.
```

### 1f. Confirm `insurance_periods.py` is deployed before or with the migration

The runtime helper (`backend/utils/insurance_periods.py`) writes to this table. Confirm:

1. The PR/deploy that ships `insurance_periods.py` is already on production **or** is bundled in the same Railway deploy as the migration.
2. If the table does not exist yet when the helper code loads, it will fail silently (compliance-grade swallow) and emit `spinr_insurance_period_write_failed_total` — acceptable for the brief pre-migration window, but not for longer.

---

## 2. Deploy Order

### Mandatory sequence

```
Migration 64  →  Migration 65  →  (no restart required)
```

Migration 65 depends on the table and partial unique index created by 64. The runner already stops on first failure, so if 64 fails, 65 will not run.

**Service restart:** not required. The table is new; no existing code paths are broken by its absence before the deploy. After the deploy, `insurance_periods.py` will begin inserting rows on the next driver state transition.

### How migrations are applied in this repo

```bash
# From repo root, pointing at production credentials:
export DATABASE_URL=<prod-pooler-connection-string>
python -m backend.scripts.run_migrations
```

The runner applies files in alphanumeric (filename) order, skipping versions already in `schema_migrations`. Both migrations execute inside their own `BEGIN / COMMIT` transaction; a failure rolls back and halts the runner.

### Confirm both are recorded after apply

```sql
SELECT version, applied_at FROM schema_migrations
WHERE version IN (
    '64_driver_insurance_periods.sql',
    '65_backfill_driver_insurance_periods.sql'
)
ORDER BY version;
-- Expected: 2 rows with recent timestamps
```

---

## 3. Rollback Plan

> **Read this section carefully before executing any rollback.**

### Rollback SQL (from migration file comments)

**For migration 64 only** (table never populated by application yet):

```sql
DROP TABLE IF EXISTS driver_insurance_periods;
DROP FUNCTION IF EXISTS _driver_insurance_periods_immutable();
```

**For migration 65 only** (backfill rows only, before application traffic writes new rows):

```sql
TRUNCATE driver_insurance_periods;
-- Then re-apply or skip migration 65 if the backfill is unwanted.
```

### When rollback IS safe

| Condition | Action |
|---|---|
| Migration 64 applied; migration 65 failed; application has not yet started writing rows | `DROP TABLE` — no audit data exists yet; no regulatory data is lost |
| Migration 64 and 65 applied; still within staging deploy; no prod drivers online | `DROP TABLE` — backfill rows are synthetic staging data |

### When rollback IS NOT safe

| Condition | Reason |
|---|---|
| Any production driver has gone through a state transition after 64 was applied | Dropping the table destroys an SGI-required audit record. Saskatchewan Transportation Act mandates 7-year retention. Destroying these rows is a compliance violation. |
| Migration 65 backfill rows exist and production traffic is live | Even truncating the backfill removes the only proof of commercial insurance coverage for drivers who were online at migration time. Do **not** truncate without legal sign-off. |

**Alternative to DROP in production:** if the table is broken but cannot be dropped, use a new migration (66+) to `ALTER TABLE` or patch schema issues. Never reuse or edit a merged migration file.

### Migration runner idempotency

The runner uses the full filename as the idempotency key in `schema_migrations`. If a migration was recorded as applied but the DDL failed mid-run, manually remove the `schema_migrations` row and re-run after fixing the SQL.

---

## 4. Verification After Deploy

Run these queries immediately after production apply.

### 4a. Both migrations recorded

```sql
SELECT version FROM schema_migrations
WHERE version LIKE '6%_driver_insurance%'
ORDER BY version;
-- Expected: exactly 2 rows
```

### 4b. Backfill ran (row count > 0, assuming any drivers were online)

```sql
SELECT COUNT(*) FROM driver_insurance_periods;
-- Expected: > 0 if any driver was online at migration time
-- (0 is acceptable only during off-hours deploys with no active drivers)
```

### 4c. Open-period count matches online driver count

```sql
SELECT
    (SELECT COUNT(*) FROM drivers WHERE is_online = true)          AS online_drivers,
    (SELECT COUNT(*) FROM driver_insurance_periods WHERE ended_at IS NULL) AS open_periods;
-- The two numbers must be equal.
```

### 4d. No duplicate open periods

```sql
SELECT driver_id, COUNT(*) AS cnt
FROM driver_insurance_periods
WHERE ended_at IS NULL
GROUP BY driver_id HAVING COUNT(*) > 1;
-- Expected: 0 rows
```

### 4e. Period-3 rows all have ride_id pointing to in_progress rides

```sql
SELECT COUNT(*) AS unlinked_p3
FROM driver_insurance_periods
WHERE period = 3 AND ended_at IS NULL
  AND (ride_id IS NULL OR NOT EXISTS (
      SELECT 1 FROM rides WHERE id = driver_insurance_periods.ride_id AND status = 'in_progress'
  ));
-- Expected: 0
```

### 4f. Trigger is live — attempt a DELETE (in a transaction you will roll back)

```sql
BEGIN;
DELETE FROM driver_insurance_periods LIMIT 1;
-- Must raise: driver_insurance_periods rows are append-only and cannot be deleted
ROLLBACK;
```

### 4g. First live transition is recorded correctly

Trigger a real driver state transition (go online → accept ride or go offline) on a test account and confirm:

```sql
SELECT * FROM driver_insurance_periods
WHERE driver_id = '<test-driver-id>'
ORDER BY started_at DESC LIMIT 5;
```

---

## 5. Monitoring — First 24 Hours

### Metrics to watch

| Metric | Expected behaviour | Alert threshold |
|---|---|---|
| `spinr_insurance_period_recorded_total` | Incrementing on every driver state transition (go online, ride accept, trip start, trip end, go offline) | Flat for > 10 min during active hours = possible wiring bug |
| `spinr_insurance_period_write_failed_total` | Zero | **Alert immediately if non-zero** — a failed audit write means an unlogged commercial-insurance window |
| `spinr_insurance_period_race_total` | Near-zero; occasional concurrent-dispatch races are normal | Spike (> 5/min) warrants investigation |
| `spinr_insurance_period_noop_total` | Should be near-zero after the backfill | High values indicate callers firing redundant transitions |

### Alert: `spinr_insurance_period_write_failed_total` > 0

1. Check backend logs for `insurance_periods: transition write FAILED` at ERROR level.
2. The full exception (including `exc_info=True`) will be in Sentry under domain=`drivers`, surface=`backend`.
3. The most common causes: Supabase connection failure, RLS misconfiguration, or `db_supabase.supabase` returning `None`.
4. Gaps can be backfilled from the `rides` table using the same logic as migration 65. Schedule a new migration (66+) for the backfill; do not edit migration 65.

### DB errors to watch in logs

Search for these strings in Railway/log aggregator:

```
insurance_periods: transition write FAILED
insurance_periods: supabase client unavailable
driver_insurance_periods rows are append-only
```

Any occurrence of the third string outside a test transaction indicates something is attempting to DELETE rows — investigate immediately and treat as a potential tamper event.

### Verify RLS is not blocking the service role

The service role bypasses RLS by design. If you see `23502` (RLS denial) errors from this table, the Supabase client being used is the anon key, not the service role key — that is a configuration error.

### Open-period drift check (run once at end of first day)

```sql
-- Any driver who is online but has no open period row = missed transition
SELECT d.id AS driver_id, d.is_online
FROM drivers d
LEFT JOIN driver_insurance_periods dip
    ON dip.driver_id = d.id AND dip.ended_at IS NULL
WHERE d.is_online = true AND dip.id IS NULL;
-- Expected: 0 rows. Any results = drift from failed writes; backfill manually.
```

---

## References

- `backend/migrations/64_driver_insurance_periods.sql`
- `backend/migrations/65_backfill_driver_insurance_periods.sql`
- `backend/utils/insurance_periods.py` — runtime transition recorder
- `backend/scripts/run_migrations.py` — migration runner
- CLAUDE.md §Insurance periods — period mapping and retention rules
- `docs/runbooks/data-retention.md` — 7-year retention enforcement
