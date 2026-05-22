-- 97_driver_intent_timestamps.sql
-- Records driver "Go Online" / "Go Offline" intent as explicit timestamps,
-- separating intent (durable, audit-grade) from reachability (Redis
-- presence, transient). This is the persistence half of the Uber/Lyft-
-- style presence model — see backend/utils/presence_sweeper.py for the
-- retired loop that this design replaces.
--
-- Why two timestamp columns instead of an enum or a single "last_intent"
-- value:
-- ── Both endpoints of the toggle are interesting to analytics
--    (utilisation, surge detection, weekly active driver retention
--    against KPI targets) and to operators investigating "when did this
--    driver go offline today". Storing both means we never have to JOIN
--    against driver_activity_log just to answer the question.
-- ── "Latest of the two" derives the current intent ("online" if
--    went_online_at IS NOT NULL AND (went_offline_at IS NULL OR
--    went_online_at > went_offline_at)) without dropping the historical
--    counter-timestamp.
-- ── Append-only by convention; readers compose intent + Redis presence
--    at query time. Nothing on the hot path writes is_online=false based
--    on these columns.
--
-- Backfill strategy:
--   Existing rows with is_online=true → went_online_at  := updated_at,
--                                       went_offline_at := NULL.
--   Existing rows with is_online=false → went_offline_at := updated_at,
--                                        went_online_at := NULL (we have
--                                        no historical "last-online" to
--                                        recover; leaving NULL is
--                                        truthful).
--   For drivers that never toggled (created but never tapped Go Online),
--   both columns stay NULL → intent_online() returns false. Correct.
--
-- Rollback:
--   ALTER TABLE public.drivers
--       DROP COLUMN IF EXISTS went_online_at,
--       DROP COLUMN IF EXISTS went_offline_at;
--   DROP INDEX IF EXISTS idx_drivers_went_online_at;

ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS went_online_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS went_offline_at TIMESTAMPTZ NULL;

-- One-shot backfill from the legacy is_online column. Safe to re-run
-- under the migrator's filename idempotency, but the WHERE clauses also
-- guard against overwriting timestamps we've already populated through
-- the API.
UPDATE public.drivers
   SET went_online_at = updated_at
 WHERE is_online = TRUE
   AND went_online_at IS NULL;

UPDATE public.drivers
   SET went_offline_at = updated_at
 WHERE is_online = FALSE
   AND went_offline_at IS NULL
   AND updated_at IS NOT NULL;

-- Index supports "who went online in the last N minutes" analytics and
-- the admin "online since…" sort. Partial index keeps it small — most
-- driver rows are offline at any moment.
CREATE INDEX IF NOT EXISTS idx_drivers_went_online_at
    ON public.drivers (went_online_at)
 WHERE went_online_at IS NOT NULL;

COMMENT ON COLUMN public.drivers.went_online_at IS
    'UTC timestamp of the last successful Go Online intent. Durable; survives Redis loss and replica restarts. Composed with Redis presence at read time to derive effective_online.';

COMMENT ON COLUMN public.drivers.went_offline_at IS
    'UTC timestamp of the last successful Go Offline intent (driver-initiated or admin force-off). NULL until the driver has gone offline at least once.';
