-- Migration: 117_push_retry_target_app.sql
-- Rollback: ALTER TABLE push_retry_queue DROP COLUMN IF EXISTS target_app;

-- Queue rows can now preserve which app surface should receive the push.
-- This matters for dual-role users who can have separate rider and driver
-- FCM tokens; dispatch ride offers must target the driver app token.
ALTER TABLE push_retry_queue
    ADD COLUMN IF NOT EXISTS target_app text
        CHECK (target_app IS NULL OR target_app IN ('rider', 'driver'));
