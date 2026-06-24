-- 191_marketing_suppressions.sql
--
-- Per-channel marketing suppression list — the "do NOT send MARKETING to this
-- target" record. This is DELIBERATELY SEPARATE from email_suppressions
-- (migration 155):
--
--   • email_suppressions blocks ALL email (transactional included) and is
--     written only on a PERMANENT bounce or a spam complaint — because a
--     legally-required receipt or DSAR export must still reach a soft-bouncing
--     address once it recovers.
--
--   • marketing_suppressions blocks MARKETING ONLY and is written far more
--     eagerly: ANY bounce (transient OR permanent), any spam complaint, any
--     unsubscribe, or an SMS STOP. Per the product rule "if an email bounces
--     once, never use it for marketing again" — one bounce is enough to
--     permanently mark the address marketing-ineligible, while transactional
--     mail to that address keeps flowing until a hard bounce.
--
-- Channel-scoped: an SMS STOP suppresses 'sms' but not 'email', and vice versa.
-- Target is the email address (channel 'email') or phone (channel 'sms'); push
-- has no external suppression — push marketing is gated purely by the
-- push_opt_in flag in marketing_preferences. We also store user_id when known
-- so the admin suppression view can attribute a row without exposing the raw
-- contact value.
--
-- PIPEDA: target (email/phone) is stored in PLAINTEXT for the same reason as
-- email_suppressions — the suppression check is on the hot path of every send
-- and needs an exact-match index; vault encryption is non-deterministic and
-- can't be indexed for equality. The table is service-role-only (RLS enabled,
-- no policies) and the target never appears in logs.
--
-- RLS: ENABLED with NO policies — service-role-only (backend). Anon/auth locked
-- out.
--
-- Forward-compatible: pure CREATE TABLE / CREATE INDEX, safe under live
-- traffic. No money columns.
--
-- Rollback:
--   DROP TABLE IF EXISTS public.marketing_suppressions;

CREATE TABLE IF NOT EXISTS public.marketing_suppressions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    channel     TEXT        NOT NULL CHECK (channel IN ('email', 'sms')),
    -- Normalized contact value: lower/trim email, or E.164 phone. Plaintext by
    -- design (see note above). Callers MUST normalize before insert.
    target      TEXT        NOT NULL,
    reason      TEXT        NOT NULL CHECK (reason IN (
                    'bounce', 'complaint', 'unsubscribe', 'sms_stop', 'manual'
                )),
    detail      TEXT,                                 -- bounce subtype / diagnostic, optional
    source      TEXT        NOT NULL DEFAULT 'ses' CHECK (source IN (
                    'ses', 'twilio', 'unsubscribe_link', 'admin'
                )),
    user_id     UUID,                                 -- attributed user when known; NULL otherwise
    message_id  TEXT,                                 -- provider message id that triggered it, if any
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One suppression row per (channel, target); the webhooks and unsubscribe
-- endpoint are idempotent against this (check before insert) so provider
-- redeliveries don't duplicate. Unique on the normalized (channel, target).
CREATE UNIQUE INDEX IF NOT EXISTS marketing_suppressions_channel_target_key
    ON public.marketing_suppressions (channel, target);

-- Admin "suppression list for this user" lookups.
CREATE INDEX IF NOT EXISTS marketing_suppressions_user_idx
    ON public.marketing_suppressions (user_id);

ALTER TABLE public.marketing_suppressions ENABLE ROW LEVEL SECURITY;
-- Intentionally no policies: service-role-only (backend). Locked to anon/auth.

COMMENT ON TABLE public.marketing_suppressions IS
    'Per-channel marketing-only suppression (email/sms). Written eagerly on ANY bounce, complaint, unsubscribe, or SMS STOP — one bounce permanently blocks marketing. Separate from email_suppressions (which also blocks transactional). Service-role-only (RLS enabled, no policies).';
