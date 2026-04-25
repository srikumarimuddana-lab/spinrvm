-- Add the in-app notifications + preferences tables read by routes/notifications.py.
-- Missing in production caused PGRST205 "Could not find the table 'public.notifications'"
-- and "...'public.notification_preferences'" → 503s on /api/v1/notifications and
-- /api/v1/notifications/preferences (with the DB circuit breaker tripping after
-- repeated failures).

-- ── notifications ───────────────────────────────────────────────────────────
-- Per-user inbox rows. Created by routes.notifications.create_notification and
-- by various ride / payout / document / subscription flows.
CREATE TABLE IF NOT EXISTS notifications (
    id          UUID PRIMARY KEY,
    -- users.id in this project is TEXT (a stringified UUID, not a Postgres
    -- uuid column) — matches the convention in refresh_tokens, drivers,
    -- ride_messages etc. Declaring this UUID would fail with
    -- "incompatible types: uuid and text" when the FK constraint is built.
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    -- Free-form bucket: ride_update | promotion | safety | general | ride_offer |
    -- document_expiry | payout_processed | payout_failed | quest_earned |
    -- subscription_expiry | … (see NOTIFICATION_DEEPLINKS in notifications.py).
    type        TEXT NOT NULL DEFAULT 'general',
    -- Arbitrary structured payload — deeplink, ride_id, amount, etc.
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_read     BOOLEAN NOT NULL DEFAULT FALSE,
    read_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Inbox listing query: WHERE user_id=? AND (is_read=false)? ORDER BY created_at DESC.
CREATE INDEX IF NOT EXISTS idx_notifications_user_created
    ON notifications (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
    ON notifications (user_id, is_read)
    WHERE is_read = FALSE;

-- ── notification_preferences ───────────────────────────────────────────────
-- One row per user; absence of a row means "use defaults" (handled in route).
-- One UNIQUE constraint on user_id makes the route's "find then insert/update"
-- pattern safe against double-insert races.
CREATE TABLE IF NOT EXISTS notification_preferences (
    id              UUID PRIMARY KEY,
    -- TEXT to match users.id (see comment on notifications.user_id above).
    user_id         TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    push_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    email_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
    sms_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    ride_updates    BOOLEAN NOT NULL DEFAULT TRUE,
    promotions      BOOLEAN NOT NULL DEFAULT TRUE,
    safety_alerts   BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Force PostgREST to reload its schema cache so the new tables are visible
-- without waiting for the periodic auto-reload.
NOTIFY pgrst, 'reload schema';
