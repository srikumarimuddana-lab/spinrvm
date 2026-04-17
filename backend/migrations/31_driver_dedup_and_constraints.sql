-- Migration 31: Deduplicate drivers + add identity constraints (v4)
-- =================================================================
--
-- v3 used TEMP TABLE _doomed_drivers across statements. Supabase's SQL
-- Editor runs statements in separate sessions, so the temp table was
-- gone by the time later statements tried to reference it. v4 inlines
-- the doomed-row selection as a CTE / subquery in each statement.
--
-- For the testing phase: safety aborts (rides / payouts / bank_accounts /
-- activity log / daily stats) are left in place but won't trigger on
-- fresh test data. If you hit an abort, read the message — the row
-- with activity should usually be the keeper, and the other gets deleted.
--
-- Run top-to-bottom in one go (Supabase SQL Editor "Run" button will
-- execute each statement sequentially in a single transaction block).

BEGIN;

-- ── 1. Safety aborts (inlined, no TEMP TABLE needed) ──────────────
DO $$
DECLARE
    doomed_count INT;
    child_count  INT;
BEGIN
    WITH doomed AS (
        SELECT id FROM (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY phone ORDER BY created_at ASC, id ASC) AS rn
            FROM drivers
        ) r WHERE rn > 1
    )
    SELECT COUNT(*) INTO doomed_count FROM doomed;
    RAISE NOTICE 'Doomed driver rows: %', doomed_count;

    SELECT COUNT(*) INTO child_count
    FROM rides WHERE driver_id IN (
        SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY phone ORDER BY created_at ASC, id ASC) AS rn
            FROM drivers
        ) r WHERE rn > 1
    );
    IF child_count > 0 THEN
        RAISE EXCEPTION 'Abort: % rides reference doomed drivers.', child_count;
    END IF;

    SELECT COUNT(*) INTO child_count
    FROM payouts WHERE driver_id IN (
        SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY phone ORDER BY created_at ASC, id ASC) AS rn
            FROM drivers
        ) r WHERE rn > 1
    );
    IF child_count > 0 THEN
        RAISE EXCEPTION 'Abort: % payouts rows reference doomed drivers.', child_count;
    END IF;

    SELECT COUNT(*) INTO child_count
    FROM bank_accounts WHERE driver_id IN (
        SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY phone ORDER BY created_at ASC, id ASC) AS rn
            FROM drivers
        ) r WHERE rn > 1
    );
    IF child_count > 0 THEN
        RAISE EXCEPTION 'Abort: % bank_accounts rows reference doomed drivers.', child_count;
    END IF;

    SELECT COUNT(*) INTO child_count
    FROM driver_activity_log WHERE driver_id IN (
        SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY phone ORDER BY created_at ASC, id ASC) AS rn
            FROM drivers
        ) r WHERE rn > 1
    );
    IF child_count > 0 THEN
        RAISE EXCEPTION 'Abort: % driver_activity_log rows reference doomed drivers.', child_count;
    END IF;

    SELECT COUNT(*) INTO child_count
    FROM driver_daily_stats WHERE driver_id IN (
        SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY phone ORDER BY created_at ASC, id ASC) AS rn
            FROM drivers
        ) r WHERE rn > 1
    );
    IF child_count > 0 THEN
        RAISE EXCEPTION 'Abort: % driver_daily_stats rows reference doomed drivers.', child_count;
    END IF;
END $$;

-- ── 2. Delete doomed drivers (FK CASCADE removes driver_documents) ──
DELETE FROM drivers
WHERE id IN (
    SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (PARTITION BY phone ORDER BY created_at ASC, id ASC) AS rn
        FROM drivers
    ) r WHERE rn > 1
);

-- ── 3. Add UNIQUE constraints ─────────────────────────────────────
ALTER TABLE drivers
    ADD CONSTRAINT drivers_phone_unique UNIQUE (phone);

ALTER TABLE drivers
    ADD CONSTRAINT drivers_user_id_unique UNIQUE (user_id);

-- ── 4. Backfill users.role for legacy drivers with role='rider' ───
-- Note: public.users has no updated_at column (see supabase_schema.sql:12-26).
UPDATE users u
SET role = 'driver',
    is_driver = TRUE
WHERE u.role <> 'driver'
  AND EXISTS (SELECT 1 FROM drivers d WHERE d.user_id = u.id);

COMMIT;

-- After running, reload PostgREST so it sees the new constraints:
--   NOTIFY pgrst, 'reload schema';
