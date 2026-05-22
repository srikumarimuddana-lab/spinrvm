-- Migration: 100_users_add_fcm_token.sql
-- Rollback: ALTER TABLE users DROP COLUMN IF EXISTS fcm_token;
--
-- The `fcm_token` column on `users` was originally defined in sql/03_features.sql
-- which is not run by the migration runner, so it was never applied to production.
-- Every call to notifications/register-token silently caught a "column does not
-- exist" error and returned success=True with no token actually saved.
-- send_push_notification() reads users.fcm_token → NULL → drops every push.
--
-- This migration:
--   1. Adds the column
--   2. Backfills from push_tokens (most-recently-updated token wins per user)

ALTER TABLE users ADD COLUMN IF NOT EXISTS fcm_token TEXT;

-- Backfill: for each user pick their most recently updated token from push_tokens
UPDATE users u
SET    fcm_token = pt.token
FROM   (
    SELECT DISTINCT ON (user_id)
           user_id,
           token
    FROM   push_tokens
    ORDER  BY user_id, updated_at DESC NULLS LAST
) pt
WHERE  u.id = pt.user_id
  AND  u.fcm_token IS NULL;
