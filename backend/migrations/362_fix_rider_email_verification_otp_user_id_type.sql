-- 362_fix_rider_email_verification_otp_user_id_type.sql
--
-- Fixes a bug in migration 299 (299_rider_email_verification_otp.sql):
-- that file declares `user_id UUID NOT NULL REFERENCES public.users(id)`,
-- but users.id is TEXT in this schema (see migration 24/49 and every other
-- FK to users.id in this repo, e.g. driver_insurance_period_corrections
-- .corrected_by). Running 299 as merged fails immediately:
--
--   ERROR: 42804: foreign key constraint
--   "rider_email_verification_otp_user_id_fkey" cannot be implemented
--   DETAIL: Key columns "user_id" and "id" are of incompatible types:
--            uuid and text.
--
-- Found 2026-08-21 during a full migration-drift audit (this session):
-- migration 299 had never actually been applied to the live database
-- (schema_migrations had no row for it), and attempting to apply it
-- verbatim hit the error above. Per backend/migrations/CLAUDE.md's
-- append-only rule, 299 itself is not edited -- this is the corrective
-- follow-up migration instead.
--
-- The live database already has the corrected table (created directly,
-- with user_id TEXT, in this same audit session) -- this migration exists
-- so a FRESH database built from scratch by replaying every migration file
-- in order ends up with the same correct schema, and so schema_migrations
-- has an honest, replayable record instead of a silent hand-applied fix.
-- ON CONFLICT/IF NOT EXISTS guards make this safe to run whether or not
-- 299's (broken) version was ever attempted.
--
-- Rollback: DROP TABLE IF EXISTS public.rider_email_verification_otp;
--   (same as 299's own rollback -- this migration doesn't add anything
--   299 didn't already intend, it just corrects one column's type.)

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'rider_email_verification_otp'
    ) THEN
        -- Table already exists (either from this fix being applied directly,
        -- or from a hypothetical prior successful run of 299 -- shouldn't be
        -- possible given the type error, but guard anyway). If user_id is
        -- still UUID for some reason, correct it; otherwise no-op.
        IF (
            SELECT data_type FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'rider_email_verification_otp'
              AND column_name = 'user_id'
        ) = 'uuid' THEN
            RAISE EXCEPTION
                'rider_email_verification_otp.user_id is UUID with data present -- '
                'manual reconciliation required, this migration will not silently '
                'convert a populated UUID column to TEXT.';
        END IF;
    ELSE
        CREATE TABLE public.rider_email_verification_otp (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     TEXT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            email       TEXT NOT NULL,
            code_hash   TEXT NOT NULL,
            expires_at  TIMESTAMPTZ NOT NULL,
            verified    BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_rider_email_verify_otp_user_created
    ON public.rider_email_verification_otp (user_id, created_at DESC)
    WHERE verified = FALSE;

CREATE INDEX IF NOT EXISTS idx_rider_email_verify_otp_expires_at
    ON public.rider_email_verification_otp (expires_at);

ALTER TABLE public.rider_email_verification_otp ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.rider_email_verification_otp TO service_role;

DROP POLICY IF EXISTS rider_email_verification_otp_service_only ON public.rider_email_verification_otp;
CREATE POLICY rider_email_verification_otp_service_only
    ON public.rider_email_verification_otp
    FOR ALL
    TO authenticated
    USING (false);

COMMENT ON TABLE public.rider_email_verification_otp IS
    'Short-lived hashed email OTP records for the rider self-serve email-verification flow. Service-role-only; no frontend direct access. user_id is TEXT (matches users.id) -- corrected here from migration 299''s erroneous UUID declaration, see migration 362 header.';

COMMENT ON COLUMN public.rider_email_verification_otp.email IS
    'Snapshot of the email address being verified at request time. Plaintext by design so the backend can verify OTP attempts and detect a mid-flow email change.';

COMMENT ON COLUMN public.rider_email_verification_otp.code_hash IS
    'SHA-256 hash of the numeric verification code; raw codes are never stored.';

NOTIFY pgrst, 'reload schema';
