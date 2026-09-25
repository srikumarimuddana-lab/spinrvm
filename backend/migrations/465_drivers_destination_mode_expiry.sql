-- 465: destination ("heading home") mode auto-expiry — C136.
--
-- Purpose: destination mode (migration 219) had no expiry, so a driver who set
-- "heading home" and forgot it kept filtering dispatch offers indefinitely —
-- across shifts, since going offline did not clear it either. This adds:
--   destination_set_at     — when the driver last set a destination
--   destination_expires_at — set_at + DESTINATION_MODE_TTL (2h, a code
--                            constant in services/dispatch_service.py, NOT a
--                            settings column)
--
-- Semantics: dispatch applies the destination filter ONLY while
-- destination_expires_at > now(). NULL expiry = expired = filter OFF. So on
-- deploy, every existing destination_mode=true row (which has NULL expiry)
-- stops filtering. That is INTENDED — those rows are the stale ones this fixes.
-- No backfill.
--
-- Index: none. Dispatch never WHEREs or ORDER BYs on these columns; it reads
-- destination_expires_at per candidate row (already selected by id/geo) and
-- evaluates it in Python (dispatch_service.is_destination_mode_active).
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS, nullable, no default) — a
-- metadata-only change, no table rewrite, safe under live traffic.
--
-- ROLLOUT ORDER: apply this migration BEFORE deploying the C136 backend. The
-- dispatch candidate select names destination_expires_at explicitly; PostgREST
-- 400s the whole select if the column is missing, which would fail every
-- dispatch attempt.
--
-- Rollback (ONLY after reverting the C136 backend code, which selects/writes
-- these columns):
--   ALTER TABLE public.drivers
--     DROP COLUMN IF EXISTS destination_expires_at,
--     DROP COLUMN IF EXISTS destination_set_at;

BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS destination_set_at timestamptz NULL,
    ADD COLUMN IF NOT EXISTS destination_expires_at timestamptz NULL;

COMMENT ON COLUMN public.drivers.destination_set_at IS
    'C136: when the driver last set destination (heading-home) mode.';
COMMENT ON COLUMN public.drivers.destination_expires_at IS
    'C136: destination filter applies only while > now(); NULL = expired/off.';

COMMIT;
