-- 220_corporate_email_otp_records.sql
-- Purpose: Store short-lived hashed email verification codes for Spinr for Business login.
--
-- Rollback plan:
--   DROP TABLE IF EXISTS public.corporate_email_otp_records;
--
-- Security:
--   The table stores login verification material and plaintext normalized email
--   addresses. It is backend/service-role only. RLS is enabled and no anon or
--   authenticated policies are created.

CREATE TABLE IF NOT EXISTS public.corporate_email_otp_records (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT NOT NULL,
    code_hash   TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    verified    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_corporate_email_otp_email_created
    ON public.corporate_email_otp_records (email, created_at DESC)
    WHERE verified = FALSE;

CREATE INDEX IF NOT EXISTS idx_corporate_email_otp_expires_at
    ON public.corporate_email_otp_records (expires_at);

ALTER TABLE public.corporate_email_otp_records ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.corporate_email_otp_records TO service_role;

COMMENT ON TABLE public.corporate_email_otp_records IS
    'Short-lived hashed email OTP records for company portal login. Service-role-only; no frontend direct access.';

COMMENT ON COLUMN public.corporate_email_otp_records.email IS
    'Normalized work email address used for company portal login. Plaintext by design so the backend can verify OTP attempts.';

COMMENT ON COLUMN public.corporate_email_otp_records.code_hash IS
    'SHA-256 hash of the numeric verification code; raw codes are never stored.';
