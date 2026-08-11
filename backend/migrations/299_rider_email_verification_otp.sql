-- 299_rider_email_verification_otp.sql
-- Purpose: Store short-lived hashed email verification codes for the
-- rider-facing "verify your email" self-serve flow (N14, ACTION_ITEMS.md).
--
-- Mirrors 220_corporate_email_otp_records.sql's shape exactly, but keyed by
-- user_id rather than a bare email column: the corporate flow issues a code
-- to an email that may not have a user row yet (it creates one on verify),
-- while this flow verifies the email already on an existing, authenticated
-- rider's `users` row. Keeping it a separate table (rather than repurposing
-- corporate_email_otp_records) avoids adding a nullable user_id to a table
-- another workstream owns, and keeps each table's RLS/retention story simple.
--
-- Rollback plan:
--   DROP TABLE IF EXISTS public.rider_email_verification_otp;

CREATE TABLE IF NOT EXISTS public.rider_email_verification_otp (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    code_hash   TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    verified    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rider_email_verify_otp_user_created
    ON public.rider_email_verification_otp (user_id, created_at DESC)
    WHERE verified = FALSE;

CREATE INDEX IF NOT EXISTS idx_rider_email_verify_otp_expires_at
    ON public.rider_email_verification_otp (expires_at);

ALTER TABLE public.rider_email_verification_otp ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.rider_email_verification_otp TO service_role;

COMMENT ON TABLE public.rider_email_verification_otp IS
    'Short-lived hashed email OTP records for the rider self-serve email-verification flow. Service-role-only; no frontend direct access.';

COMMENT ON COLUMN public.rider_email_verification_otp.email IS
    'Snapshot of the email address being verified at request time. Plaintext by design so the backend can verify OTP attempts and detect a mid-flow email change.';

COMMENT ON COLUMN public.rider_email_verification_otp.code_hash IS
    'SHA-256 hash of the numeric verification code; raw codes are never stored.';
