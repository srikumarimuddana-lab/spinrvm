-- 286_settings_lifecycle_emails_enabled.sql
--
-- Kill switch for the lifecycle-email channel.
--
-- Spinr notifies almost entirely by push. utils/email_notifications.py adds
-- email as a SECOND channel for account and document-expiry notices — events
-- that need a durable record (an application rejection, a suspension, a
-- licence about to expire) and that push silently drops on an uninstalled app
-- or a stale FCM token.
--
-- Adding a channel to live-tested flows raises exactly one operational
-- question: how do we turn it off without a deploy if the copy is wrong or the
-- volume is unexpected? This column is that answer. Setting it false suppresses
-- every send routed through send_lifecycle_email and leaves push, the in-app
-- inbox row, and all existing behaviour byte-for-byte as it was before those
-- emails existed.
--
-- Scope — deliberately narrow. This does NOT gate:
--   • ride receipts        (utils/email_receipt.py)
--   • driver statements    (utils/driver_statement_job.py)
--   • Spinr Pass invoices  (routes/drivers/subscriptions.py)
--   • T4A / DSAR exports   (routes/drivers/tax_exports.py)
--   • corporate invites, KYB decisions, low-balance alerts
--   • marketing            (utils/marketing_email.py — CASL consent gates that)
-- Those are pre-existing paths that never call the policy layer, and several
-- are regulatory obligations that must not be switchable from a settings page.
--
-- NOT NULL DEFAULT true is safe with traffic in flight: no table rewrite on
-- PG 11+, existing rows are backfilled to the current (enabled) behaviour, and
-- old replicas that never write the column keep working. The backend also
-- defaults it to true in schemas.AppSettings, so the feature behaves correctly
-- from the moment the code deploys, whether or not this migration has run —
-- there is no window where the switch reads as off.
--
-- Rollback:
--   ALTER TABLE public.settings DROP COLUMN IF EXISTS lifecycle_emails_enabled;
--   (Safe at any time: the schema default keeps the code reading `true`.)

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS lifecycle_emails_enabled BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN public.settings.lifecycle_emails_enabled IS
    'Master switch for lifecycle emails sent via utils/email_notifications.send_lifecycle_email (driver approval/rejection/suspension/ban, document expiry). false suppresses every one of them without a redeploy; push and the in-app inbox are unaffected. Does NOT gate receipts, statements, invoices, tax/DSAR exports, corporate mail, or marketing.';
