-- 155_email_suppressions_and_send_log.sql
--
-- Email deliverability tracking for the SES-primary / Resend-fallback sender
-- (utils/email_provider.py). Two append-style tables:
--
--   • email_suppressions — addresses we must NOT email because SES reported a
--     hard bounce or a spam complaint (or an operator suppressed manually).
--     send_transactional_email checks this before every send. Keeping a local
--     copy of SES's suppression decisions stops us re-sending to dead/hostile
--     addresses, which is what protects the SES account from being throttled
--     or suspended for a high bounce/complaint rate.
--
--   • email_send_log — one row per send attempt for support + an ops
--     deliverability view ("did the rider get their receipt?"). PIPEDA: we
--     store recipient_user_id, NEVER the email address, in this table.
--
-- Populated by:
--   • email_suppressions ← POST /api/v1/webhooks/ses (SNS Bounce/Complaint)
--   • email_send_log     ← email_provider.send_transactional_email
--
-- RLS: both tables hold user-linked data, so RLS is ENABLED with NO policies —
-- that denies all anon/authenticated access; only the backend service role
-- (which bypasses RLS by design) can read/write them. The frontend anon key
-- must never touch these directly.
--
-- Forward-compatible: pure CREATE TABLE / CREATE INDEX, safe under live
-- traffic. No money columns.
--
-- Rollback:
--   DROP TABLE IF EXISTS public.email_send_log;
--   DROP TABLE IF EXISTS public.email_suppressions;

-- ── Suppression list ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.email_suppressions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT        NOT NULL,                 -- stored normalized (lower/trim)
    reason      TEXT        NOT NULL,                 -- 'bounce' | 'complaint' | 'manual'
    detail      TEXT,                                 -- bounce subtype / diagnostic, optional
    source      TEXT        NOT NULL DEFAULT 'ses',   -- 'ses' | 'manual'
    message_id  TEXT,                                 -- SES MessageId that triggered it, if any
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One suppression row per address; the SES webhook is idempotent against this
-- (checks before insert) so SNS redeliveries don't duplicate. Unique on the
-- normalized address — callers must lowercase/trim before insert.
CREATE UNIQUE INDEX IF NOT EXISTS email_suppressions_email_key
    ON public.email_suppressions (email);

ALTER TABLE public.email_suppressions ENABLE ROW LEVEL SECURITY;
-- Intentionally no policies: service-role-only (backend). Locked to anon/auth.

COMMENT ON TABLE public.email_suppressions IS
    'Addresses excluded from transactional email after a hard bounce/complaint (or manual). Checked by email_provider.send_transactional_email; populated by the SES SNS webhook. Service-role-only (RLS enabled, no policies).';

-- ── Send log ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.email_send_log (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id         TEXT,                              -- provider message id (SES MessageId / Resend id)
    email_type         TEXT,                              -- 'receipt' | 'safety_alert' | 'dsar' | 'corporate_low_balance' | 'transactional'
    recipient_user_id  UUID,                              -- PIPEDA: user id only, never the email address; NULL for non-user recipients
    provider           TEXT,                              -- 'ses' | 'resend' | 'none'
    status             TEXT        NOT NULL,              -- 'sent' | 'failed' | 'suppressed'
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "Recent emails for this user" support lookups, newest first.
CREATE INDEX IF NOT EXISTS email_send_log_recipient_idx
    ON public.email_send_log (recipient_user_id, created_at DESC);
-- Correlate a provider message id back to its send (e.g. from an SES event).
CREATE INDEX IF NOT EXISTS email_send_log_message_id_idx
    ON public.email_send_log (message_id);

ALTER TABLE public.email_send_log ENABLE ROW LEVEL SECURITY;
-- Intentionally no policies: service-role-only (backend). Locked to anon/auth.

COMMENT ON TABLE public.email_send_log IS
    'Append-only audit of transactional email sends (provider, status, message id, recipient_user_id). PIPEDA: never stores the email address. Service-role-only (RLS enabled, no policies).';
