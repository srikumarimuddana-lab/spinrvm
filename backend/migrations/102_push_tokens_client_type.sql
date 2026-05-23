-- Migration: 102_push_tokens_client_type.sql
-- Rollback: ALTER TABLE users DROP COLUMN IF EXISTS fcm_token_rider;
--           ALTER TABLE users DROP COLUMN IF EXISTS fcm_token_driver;

-- Cloud messaging sends to "customers" or "drivers" but dual-role users only
-- have one fcm_token — whichever app registered last wins. Adding per-app
-- token columns lets cloud messaging target the correct app surface.
--
-- fcm_token        — legacy single column, still used by dispatch/ride pushes
-- fcm_token_rider  — set by rider app on register-token
-- fcm_token_driver — set by driver app on register-token

ALTER TABLE users ADD COLUMN IF NOT EXISTS fcm_token_rider TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS fcm_token_driver TEXT;

-- Backfill: copy current fcm_token into both columns as a safe default.
-- Once both apps re-register, each column will hold the correct token.
UPDATE users
SET    fcm_token_rider  = fcm_token,
       fcm_token_driver = fcm_token
WHERE  fcm_token IS NOT NULL
  AND  fcm_token_rider IS NULL;
