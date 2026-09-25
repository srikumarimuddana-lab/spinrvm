-- 482_settings_destination_mode_enabled.sql
--
-- Feature switch for driver destination ("heading home") mode.
--
-- C136 (2026-09-24): destination mode is a HARD dispatch filter — a driver in
-- the mode is offered only rides whose dropoff is >= 5% closer to their
-- destination (services/dispatch_service.py). A flag left on with a NULL
-- expiry (pre-migration-465 row) excluded the driver from every nearby ride.
-- Owner decision 2026-09-25: hide the feature behind this switch, default
-- OFF, until its defects are fixed and it is deliberately re-enabled.
--
-- While false: dispatch skips the destination filter for every driver,
-- POST /drivers/destination refuses with 409, GET reports active=false and
-- enabled=false, DELETE (clear) still works. Code reads the key with
-- .get(..., False), so the feature is also off before this migration is
-- applied.
--
-- Additive only: one new column with a constant default; no existing row or
-- column is read differently.
--
-- Change log: docs/change-log/2026-09-25-destination-mode-flag.md
--
-- Rollback:
--   UPDATE settings SET destination_mode_enabled = true WHERE id = 'app_settings';  -- behavioural rollback (re-enable), no deploy
--   ALTER TABLE settings DROP COLUMN IF EXISTS destination_mode_enabled;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS destination_mode_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.settings.destination_mode_enabled IS
    'When false (default), driver destination mode is hidden: dispatch ignores stored destinations, drivers cannot set one (409), and the driver app hides the entry points. Clearing a destination always works. C136.';
