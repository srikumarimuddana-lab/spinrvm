-- 436_notifications_audience.sql
--
-- Dual-role users (one phone number → one `users` row with both is_rider and
-- is_driver set — see routes/auth.py's get_user_by_phone reuse on OTP verify)
-- see every notification twice: once in the rider app and once in the driver
-- app. The in-app inbox has no app scoping at any layer —
-- `notifications` (migration 48) has no audience column, features.py's
-- _record_inbox_notification writes user_id only, and GET /notifications
-- filters on {"user_id": ...} alone. Both apps then render that same list
-- through the shared useNotifications hook, defaulting to an "All" tab that
-- matches every type.
--
-- This adds the missing dimension. The push path already knows the answer —
-- send_push_notification's `target_app` ('rider' | 'driver' | None) selects
-- the per-app FCM token column added by migration 102 — so audience is
-- derived from that same signal at the single choke point every push flows
-- through, rather than invented separately per call site.
--
-- Additive by design (root CLAUDE.md pre-merge gate 2): existing rows and any
-- push whose call site does not declare a target_app default to 'both', which
-- is exactly today's behavior — visible in both apps. Nothing disappears from
-- anyone's inbox when this lands, including for a rider mid-ride or a driver
-- mid-shift. Scoping only tightens as call sites are swept to declare their
-- target_app.
--
-- Deliberately NOT adding an index. The inbox query becomes
--   WHERE user_id = ? AND audience IN (?, 'both') ORDER BY created_at DESC
-- and migration 48's existing idx_notifications_user_created
-- (user_id, created_at DESC) already satisfies it: user_id is highly
-- selective (a per-user inbox is tens-to-low-hundreds of rows), the ordering
-- is served directly, and `audience` is a cheap residual filter over that
-- narrow set. Leading with user_id then audience would force a BitmapOr or
-- MergeAppend across the two IN values and lose the ordered scan, while
-- adding write amplification to an append-hot table every push writes to.
-- Revisit only if EXPLAIN on real inbox volume says otherwise.
--
-- Rollback plan (no second deploy needed — the reader treats a missing
-- column as "no scoping" only if it is also reverted, so revert the app code
-- first, then optionally):
--   ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_audience_check;
--   ALTER TABLE notifications DROP COLUMN IF EXISTS audience;
-- Dropping the column is safe at any time: it carries no FK, nothing else
-- reads it, and every row is reconstructible as 'both'.

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS audience TEXT NOT NULL DEFAULT 'both';

COMMENT ON COLUMN notifications.audience IS
    'Which app surface this notification belongs to: rider | driver | both. '
    'Derived from send_push_notification''s target_app at write time; ''both'' '
    'for account-level notices (suspension, reactivation) and for any call '
    'site that has not yet declared a target_app. GET /notifications filters '
    'audience IN (X-App-Platform, ''both'').';

-- NOT VALID first, then VALIDATE: the validating scan takes only a SHARE
-- UPDATE EXCLUSIVE lock rather than blocking writes to a table every push
-- inserts into. Every existing row holds the 'both' default, so validation
-- cannot fail.
ALTER TABLE notifications
    DROP CONSTRAINT IF EXISTS notifications_audience_check;
ALTER TABLE notifications
    ADD CONSTRAINT notifications_audience_check
    CHECK (audience IN ('rider', 'driver', 'both')) NOT VALID;
ALTER TABLE notifications
    VALIDATE CONSTRAINT notifications_audience_check;
