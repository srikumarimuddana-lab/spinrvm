-- 94_safety_incidents.sql
-- The `safety_incidents` table referenced by routes/safety.py and
-- utils/safety_checkin_loop.py never existed. Every SOS report from a
-- rider/driver returned 503, and every escalation from the safety
-- check-in loop raised silently inside its try/except and the
-- escalated_key flag never got set so the loop kept failing on every
-- 30s tick. Sprint goal: close the "SOS silent failure" P0.
--
-- Schema mirrors the two insert call sites exactly so existing code
-- doesn't need to change once this lands:
--   • backend/routes/safety.py — user-submitted SOS / abuse report,
--     carries optional GPS coords + ride context
--   • backend/utils/safety_checkin_loop.py — automated no-response
--     escalation, no GPS (the rider already stopped responding)
--
-- Operational columns (assigned_to_admin_id, resolved_*, severity,
-- updated_at) are added now even though neither call site writes them
-- today — the admin dashboard's safety triage UI will. Cheaper to
-- ship in one migration than to ALTER later.
--
-- PIPEDA note: latitude/longitude ARE stored here on purpose. CLAUDE.md
-- forbids GPS in *logs* / Sentry / analytics payloads. This table is
-- the regulated audit record for safety investigations (Saskatchewan
-- Transportation Act + insurance requirements) — that's a different
-- access pattern, gated by RLS to super_admin only on read.
--
-- Rollback (manual):
--   DROP POLICY IF EXISTS "Reporter can read own safety_incidents" ON safety_incidents;
--   DROP POLICY IF EXISTS "Admin read/update safety_incidents" ON safety_incidents;
--   DROP POLICY IF EXISTS "Service role bypass safety_incidents" ON safety_incidents;
--   DROP INDEX IF EXISTS idx_safety_incidents_status_reported_at;
--   DROP INDEX IF EXISTS idx_safety_incidents_ride;
--   DROP INDEX IF EXISTS idx_safety_incidents_reporter;
--   DROP TABLE IF EXISTS safety_incidents;

CREATE TABLE IF NOT EXISTS public.safety_incidents (
    id                      TEXT PRIMARY KEY,

    -- WHO. role=='system' for auto-escalations from safety_checkin_loop,
    -- in which case reported_by_user_id is the rider on the affected ride.
    reported_by_user_id     TEXT,
    role                    TEXT NOT NULL DEFAULT 'rider',

    -- WHAT. category is free text so the rider/driver app can ship new
    -- abuse taxonomies without a migration; admin UI groups by known
    -- values. description is the rider/driver's words verbatim (or the
    -- loop's templated message for auto-escalations).
    category                TEXT NOT NULL,
    description             TEXT NOT NULL,

    -- Triage state.
    status                  TEXT NOT NULL DEFAULT 'open',
    severity                TEXT,

    -- Optional ride correlation. Indexed (idx below) so the ride
    -- detail page can surface "this ride has 1 open safety incident".
    ride_id                 TEXT,

    -- Where the user was when they reported. Nullable because:
    --   • the rider app may not have a location fix at the moment of
    --     pressing SOS (background fetch failed, permission revoked)
    --   • auto-escalations from safety_checkin_loop don't have a fresh
    --     fix to attach.
    latitude                DOUBLE PRECISION,
    longitude               DOUBLE PRECISION,
    location_accuracy       DOUBLE PRECISION,

    -- Triage workflow. Set by the admin safety dashboard once a
    -- reviewer picks up the incident.
    assigned_to_admin_id    TEXT,
    resolved_at             TIMESTAMPTZ,
    resolved_by             TEXT,
    resolution_notes        TEXT,

    -- Timestamps. reported_at can drift from created_at if the client
    -- buffered the report offline and replayed it later.
    reported_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT safety_incidents_status_check
        CHECK (status IN ('open', 'in_progress', 'resolved', 'closed', 'duplicate')),
    CONSTRAINT safety_incidents_role_check
        CHECK (role IN ('rider', 'driver', 'system')),
    CONSTRAINT safety_incidents_severity_check
        CHECK (severity IS NULL OR severity IN ('sev1', 'sev2', 'sev3'))
);

-- The admin safety queue's primary read pattern is
-- "open incidents, newest first" — index ordered that way so the
-- dashboard query doesn't sort the whole table.
CREATE INDEX IF NOT EXISTS idx_safety_incidents_status_reported_at
    ON public.safety_incidents (status, reported_at DESC);

-- Surface-on-ride lookups: "show all safety incidents on this ride".
CREATE INDEX IF NOT EXISTS idx_safety_incidents_ride
    ON public.safety_incidents (ride_id)
    WHERE ride_id IS NOT NULL;

-- Reporter history: "show me a rider's submitted reports".
CREATE INDEX IF NOT EXISTS idx_safety_incidents_reporter
    ON public.safety_incidents (reported_by_user_id, reported_at DESC)
    WHERE reported_by_user_id IS NOT NULL;

-- updated_at auto-maintained so admin status changes leave an
-- accurate timeline.
CREATE OR REPLACE FUNCTION safety_incidents_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS safety_incidents_updated_at ON public.safety_incidents;
CREATE TRIGGER safety_incidents_updated_at
    BEFORE UPDATE ON public.safety_incidents
    FOR EACH ROW
    EXECUTE FUNCTION safety_incidents_set_updated_at();

ALTER TABLE public.safety_incidents ENABLE ROW LEVEL SECURITY;

-- Service role (backend) writes + reads everything.
CREATE POLICY "Service role bypass safety_incidents"
    ON public.safety_incidents FOR ALL TO service_role
    USING (true)
    WITH CHECK (true);

-- Admin / super_admin: full read + update for triage. Insert is
-- service-role only — admins escalate via the backend API, not by
-- writing directly to the table.
CREATE POLICY "Admin read/update safety_incidents"
    ON public.safety_incidents FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.users
            WHERE public.users.id = auth.uid()::text
              AND public.users.role IN ('admin', 'super_admin')
        )
    );

CREATE POLICY "Admin update safety_incidents"
    ON public.safety_incidents FOR UPDATE TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.users
            WHERE public.users.id = auth.uid()::text
              AND public.users.role IN ('admin', 'super_admin')
        )
    );

-- Reporter can read their own submitted incidents (so the rider /
-- driver app can show a "my reports" history list). Not allowed to
-- update — once filed, the record is admin-owned.
CREATE POLICY "Reporter can read own safety_incidents"
    ON public.safety_incidents FOR SELECT TO authenticated
    USING (reported_by_user_id = auth.uid()::text);

COMMENT ON TABLE public.safety_incidents IS
    'SOS / abuse / safety-checkin-no-response reports. Append-only from app code; admin triage workflow updates status + resolution columns. Regulated audit record under SK Transportation Act — do not purge.';
COMMENT ON COLUMN public.safety_incidents.latitude IS
    'Reporter location at time of submission. Stored on purpose for safety investigation — this table is the regulated audit record. Never echo into logs/Sentry/analytics (see PIPEDA rules in CLAUDE.md).';
COMMENT ON COLUMN public.safety_incidents.role IS
    'Submitter role: rider | driver | system. "system" is used by utils/safety_checkin_loop.py when escalating a missed check-in.';
COMMENT ON COLUMN public.safety_incidents.severity IS
    'sev1=immediate safety risk, sev2=non-safety abuse, sev3=info. Nullable because user submissions don''t self-classify — set by admin on triage.';
