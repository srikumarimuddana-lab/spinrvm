-- 139_driver_onboarding_reminder_log.sql
-- Rollback:
--   DROP TABLE IF EXISTS public.driver_onboarding_reminder_log;
--
-- Daily driver onboarding pushes are sent from every backend replica. This
-- append-only claim log gives the loop a unique per-driver/per-local-day key
-- so vehicle-details and document-upload reminders are not duplicated.

CREATE TABLE IF NOT EXISTS public.driver_onboarding_reminder_log (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id       text        NOT NULL REFERENCES public.drivers(id) ON DELETE CASCADE,
    user_id         text        NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    reminder_type   text        NOT NULL CHECK (reminder_type IN ('vehicle_details', 'vehicle_documents')),
    local_date      date        NOT NULL,
    claimed_at      timestamptz NOT NULL DEFAULT now(),
    attempted_at    timestamptz,
    delivered_at    timestamptz,
    send_success    boolean,
    send_error      text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS driver_onboarding_reminder_log_daily_uidx
    ON public.driver_onboarding_reminder_log (driver_id, reminder_type, local_date);

CREATE INDEX IF NOT EXISTS driver_onboarding_reminder_log_user_created_idx
    ON public.driver_onboarding_reminder_log (user_id, created_at DESC);

ALTER TABLE public.driver_onboarding_reminder_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY driver_onboarding_reminder_log_no_client_access
    ON public.driver_onboarding_reminder_log
    FOR ALL
    TO authenticated
    USING (false)
    WITH CHECK (false);

CREATE POLICY driver_onboarding_reminder_log_service_role
    ON public.driver_onboarding_reminder_log
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

COMMENT ON TABLE public.driver_onboarding_reminder_log IS
    'Append-only idempotency log for daily driver onboarding push reminders.';

COMMENT ON COLUMN public.driver_onboarding_reminder_log.local_date IS
    'Driver-local calendar date used for the daily 08:00 reminder uniqueness key.';

NOTIFY pgrst, 'reload schema';
