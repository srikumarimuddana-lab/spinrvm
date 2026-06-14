-- Migration 152: collapse duplicate pending checkouts (prep for the unique index)
--
-- Repair step for migration 153's partial unique index `WHERE status='pending'`.
-- Demotes all-but-the-newest pending row per driver to 'superseded' so the
-- CONCURRENTLY index build in 153 can't fail on pre-existing duplicates. Split
-- from the index build (separate file) because 153 must run CONCURRENTLY in
-- autocommit, whereas this repair runs transactionally — same pattern as
-- migrations 142 (repair) → 143 (CONCURRENTLY index).
--
-- Tie-break on id after created_at so two pending rows sharing a created_at
-- still pick a single deterministic winner.
--
-- Rollback: none needed (data-only repair; superseded rows are terminal).

UPDATE public.driver_subscriptions
SET status = 'superseded'
WHERE status = 'pending'
  AND id NOT IN (
      SELECT DISTINCT ON (driver_id) id
      FROM public.driver_subscriptions
      WHERE status = 'pending'
      ORDER BY driver_id, created_at DESC NULLS LAST, id DESC
  );
