-- Migration: 102_push_tokens_client_type.sql
-- Rollback: ALTER TABLE users DROP COLUMN IF EXISTS fcm_token_rider;
--           ALTER TABLE users DROP COLUMN IF EXISTS fcm_token_driver;

-- Per-app FCM token columns for dual-role users. Cloud messaging reads
-- fcm_token_rider for customer pushes, fcm_token_driver for driver pushes,
-- falling back to the legacy fcm_token when the per-app column is NULL.
--
-- Columns are populated when each app registers with client_type='rider'
-- or client_type='driver'. Until then they stay NULL and the legacy
-- fcm_token column is used for all pushes.

ALTER TABLE users ADD COLUMN IF NOT EXISTS fcm_token_rider TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS fcm_token_driver TEXT;

-- No backfill — columns stay NULL until each app explicitly registers.
-- This prevents the driver app's token from being copied into
-- fcm_token_rider (which would route rider notifications to the driver app).
