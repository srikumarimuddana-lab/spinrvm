-- 483: service_areas.instant_payout_enabled defaults to FALSE.
--
-- Why: owner decision 2026-09-25 -- Spinr pays drivers weekly only (the
-- Sunday auto-payout, utils/auto_payout.py). There is no instant payout
-- option: POST /api/drivers/payouts/instant and its quote endpoint now answer
-- 410, and the admin service-area API no longer accepts this column. Migration
-- 314 added it NOT NULL DEFAULT TRUE, so any new service area would come up
-- "instant enabled". This flips the default and normalises existing rows so
-- the column states the policy even though no code reads it any more.
--
-- Production already has instant_payout_enabled = false in all 6 service
-- areas (approved data change made on 2026-09-25), so the UPDATE is
-- expected to touch 0 rows there; it exists for staging/dev/fresh databases.
--
-- The column is NOT dropped: it is kept for history and so a rollback needs
-- no schema change. Safe under live traffic: SET DEFAULT is metadata-only,
-- and the UPDATE touches at most one row per service area (a handful of rows).
-- Idempotent: re-running changes nothing.
--
-- Rollback: ALTER TABLE public.service_areas ALTER COLUMN instant_payout_enabled SET DEFAULT true;
--   (restores migration 314's default; row values are left as they are --
--   re-enable an area deliberately with an UPDATE if ever needed.)

ALTER TABLE public.service_areas ALTER COLUMN instant_payout_enabled SET DEFAULT false;

UPDATE public.service_areas
SET instant_payout_enabled = false
WHERE instant_payout_enabled IS DISTINCT FROM false;
