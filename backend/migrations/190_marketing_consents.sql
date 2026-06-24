-- 190_marketing_consents.sql
--
-- CASL (Canada's Anti-Spam Law) express-consent tracking for marketing
-- messages across all three channels: email, SMS, and push. A marketing
-- "commercial electronic message" (CEM) — email OR SMS — may only be sent to a
-- recipient who has given EXPRESS consent. Push promotions follow the same
-- opt-in model for consistency. CASL also requires us to PROVE consent on
-- demand, so this ships two tables:
--
--   • marketing_preferences — current opt-in state, one row per user, one
--     boolean per channel. Defaults are FALSE: silence is never consent under
--     CASL, so a user is non-consenting until they actively opt in.
--
--   • marketing_consent_events — append-only audit of every opt-in / opt-out,
--     with the source (rider_app, admin, unsubscribe_link, sms_stop, bounce,
--     complaint …) and the consent-language version in effect at the time.
--     This is the evidentiary record for a CASL consent challenge. Append-only:
--     never UPDATE or DELETE a row here.
--
-- The send path (utils/email_provider.send_marketing_email and the SMS/push
-- marketing helpers) reads marketing_preferences AND the marketing_suppressions
-- list (migration 191) and sends only when the channel is opted in AND not
-- suppressed.
--
-- PIPEDA: neither table stores an email address or phone number — only the
-- user_id. The address/phone live on the users row already; duplicating them
-- here would widen the PII surface for no benefit.
--
-- No FOREIGN KEY on user_id (deliberate): marketing_consent_events is a CASL
-- evidentiary record that must SURVIVE user deletion — a deleted user can still
-- raise a consent complaint, and an FK with ON DELETE CASCADE would erase the
-- proof. Since the only user-linked column is user_id (no other PII per the
-- note above), retaining orphaned rows is PIPEDA-safe. marketing_preferences
-- likewise omits the FK so a hard user delete never fails on it; the retention
-- purge job nulls/removes rows as policy dictates, not the database.
--
-- updated_at on marketing_preferences is stamped by the APPLICATION on every
-- write (the consent service sets it explicitly) — there is intentionally no
-- BEFORE UPDATE trigger, matching how the other Spinr preference tables behave.
--
-- RLS: both tables hold user-linked data → RLS ENABLED with NO policies, which
-- denies all anon/authenticated access; only the backend service role (which
-- bypasses RLS by design) reads/writes them. The frontend anon key must never
-- touch these directly.
--
-- Forward-compatible: pure CREATE TABLE / CREATE INDEX, safe under live
-- traffic. No money columns.
--
-- Rollback:
--   DROP TABLE IF EXISTS public.marketing_consent_events;
--   DROP TABLE IF EXISTS public.marketing_preferences;

-- ── Current opt-in state ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.marketing_preferences (
    user_id            UUID        PRIMARY KEY,
    -- Per-channel express consent. DEFAULT false — never send marketing until
    -- the user actively opts in (CASL: implied consent is not the default).
    email_opt_in       BOOLEAN     NOT NULL DEFAULT false,
    sms_opt_in         BOOLEAN     NOT NULL DEFAULT false,
    push_opt_in        BOOLEAN     NOT NULL DEFAULT false,
    -- When each channel was most recently opted in (NULL once opted out / never).
    email_opted_in_at  TIMESTAMPTZ,
    sms_opted_in_at    TIMESTAMPTZ,
    push_opted_in_at   TIMESTAMPTZ,
    -- Version of the consent language the user agreed to (latest opt-in wins).
    consent_version    TEXT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.marketing_preferences ENABLE ROW LEVEL SECURITY;
-- Intentionally no policies: service-role-only (backend). Locked to anon/auth.

COMMENT ON TABLE public.marketing_preferences IS
    'Current CASL express-consent state per user, one boolean per channel (email/sms/push). Defaults false. Read by the marketing send path; written via consent UI / admin / unsubscribe. Service-role-only (RLS enabled, no policies).';

-- ── Append-only consent audit (CASL proof-of-consent) ──────────────────────
CREATE TABLE IF NOT EXISTS public.marketing_consent_events (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID        NOT NULL,
    channel          TEXT        NOT NULL CHECK (channel IN ('email', 'sms', 'push')),
    action           TEXT        NOT NULL CHECK (action IN ('opt_in', 'opt_out')),
    -- Where the change came from. Free-form is avoided: a fixed set keeps the
    -- audit queryable and blocks typos that would hide a consent source.
    source           TEXT        NOT NULL CHECK (source IN (
                         'rider_app', 'driver_app', 'admin',
                         'unsubscribe_link', 'sms_stop', 'bounce', 'complaint'
                     )),
    consent_version  TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "Show me this user's consent history, newest first" — the CASL evidence query.
CREATE INDEX IF NOT EXISTS marketing_consent_events_user_idx
    ON public.marketing_consent_events (user_id, created_at DESC);

ALTER TABLE public.marketing_consent_events ENABLE ROW LEVEL SECURITY;
-- Intentionally no policies: service-role-only (backend). Locked to anon/auth.

COMMENT ON TABLE public.marketing_consent_events IS
    'Append-only CASL consent audit: every opt-in/opt-out with channel, source and consent_version. Never UPDATE/DELETE. Service-role-only (RLS enabled, no policies).';
