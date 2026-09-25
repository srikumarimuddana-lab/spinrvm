-- 485_saved_addresses_unique_home_work.sql
--
-- At most one Home and one Work saved address per user, enforced by the DB.
--
-- PR #5815 (2026-09-25) made POST/PATCH /addresses replace a rider's existing
-- Home/Work instead of adding a second one, but only in application code
-- (read, delete the others, then write). Two concurrent saves could still
-- both insert a first Home, or both promote a row to Home, leaving a
-- duplicate until the next save collapsed it. This partial unique index makes
-- a duplicate impossible: the losing request gets a 23505 unique violation,
-- which routes/addresses.py maps to a retryable HTTP 409.
--
-- Only rows whose icon is exactly 'home' or 'work' are covered. Every other
-- icon ('location', 'gym', 'school', 'other', NULL) can repeat freely. Driver
-- app requests are stored as 'location' by routes/addresses.py (old driver
-- builds send 'home' for every address), so drivers can keep saving several
-- addresses.
--
-- Pre-check: production had 0 users with a duplicate Home and 0 with a
-- duplicate Work on 2026-09-25 (264 rows). The DO block below aborts the
-- whole migration, with the counts, if another environment has duplicates,
-- so it fails loudly instead of half-applying. Fix the duplicates by hand
-- (keep one row per user and type) and re-run.
--
-- Not CONCURRENTLY: the table is tiny (264 rows), so the brief lock of a
-- plain CREATE INDEX is fine and the whole file runs in one transaction.
--
-- Change log: docs/change-log/2026-09-25-saved-address-unique-home-work.md
--
-- Rollback:
--   DROP INDEX IF EXISTS public.uq_saved_addresses_user_home_work;
--   (routes/addresses.py keeps working without the index: the delete-before-
--   write replace logic from PR #5815 is unchanged, only the race returns.)

DO $$
DECLARE
    dup_home INTEGER;
    dup_work INTEGER;
BEGIN
    SELECT COUNT(*) INTO dup_home FROM (
        SELECT user_id FROM public.saved_addresses
        WHERE icon = 'home' GROUP BY user_id HAVING COUNT(*) > 1
    ) d;
    SELECT COUNT(*) INTO dup_work FROM (
        SELECT user_id FROM public.saved_addresses
        WHERE icon = 'work' GROUP BY user_id HAVING COUNT(*) > 1
    ) d;
    IF dup_home > 0 OR dup_work > 0 THEN
        RAISE EXCEPTION
            'migration 485 aborted: % user(s) with more than one home and % with more than one work saved address; de-duplicate first',
            dup_home, dup_work;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_saved_addresses_user_home_work
    ON public.saved_addresses (user_id, icon)
    WHERE icon IN ('home', 'work');

COMMENT ON INDEX public.uq_saved_addresses_user_home_work IS
    'One Home and one Work saved address per user. A violating write returns 23505, mapped to HTTP 409 by routes/addresses.py. Migration 485.';
