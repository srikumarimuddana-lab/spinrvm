-- 333: Idle (Period-1) v2 recording sessions + Phase-1 capture/rollout flags.
--
-- WHY: "driving around" (online, no ride) GPS was only persisted through one
-- narrow WebSocket single-ping path; the modern v2 outbox protocol could not
-- record it at all (ride_id NOT NULL end-to-end). Phase 1 of the tracking
-- overhaul routes idle capture through the same durable client outbox and a
-- new server persist path that inserts driver_location_history rows with
-- ride_id NULL / tracking_phase 'online_idle'. Owner decisions (2026-08-18):
-- idle GPS retention 90 days (matches trip breadcrumbs; enables per-day
-- Distance Logs + map replay), rollout via flags below.
--
-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS uq_dlh_session_sequence;
--   ALTER TABLE settings
--     DROP COLUMN IF EXISTS idle_location_v2_enabled,
--     DROP COLUMN IF EXISTS idle_breadcrumb_retention_hours,
--     DROP COLUMN IF EXISTS p2_route_geometry_enabled,
--     DROP COLUMN IF EXISTS rider_show_pickup_leg_enabled,
--     DROP COLUMN IF EXISTS location_health_push_nudge_enabled,
--     DROP COLUMN IF EXISTS stale_p3_autoclose_enabled;
--
-- Forward-compatible: additive defaulted columns + a NON-partial unique index
-- (see the ON CONFLICT rationale below); the old backend ignores both. All
-- flags default OFF so deploy order is free.
--
-- Production pre-flight (a CONCURRENTLY build that hits a duplicate fails and
-- leaves an INVALID index to drop manually — verify uniqueness first):
--   SELECT driver_id, recording_session_id, sequence_number, COUNT(*)
--   FROM driver_location_history WHERE recording_session_id IS NOT NULL
--   GROUP BY 1,2,3 HAVING COUNT(*) > 1;

-- Idempotent replay for idle batches: the migration-239 unique index keys on
-- (ride_id, driver_id, recording_session_id, sequence_number), and Postgres
-- treats NULL ride_id rows as distinct there — idle rows need their own
-- session identity. FULL (non-partial) index deliberately: PostgREST's
-- on_conflict clause cannot carry a WHERE predicate, so ON CONFLICT inference
-- only matches a non-partial unique index. Safe for trip rows too — a
-- recording_session_id is a per-session UUID, so (driver, session, sequence)
-- is unique across both kinds; legacy rows with NULL session ids stay
-- distinct under Postgres NULL semantics. CONCURRENTLY: this table is written
-- every few seconds fleet-wide; a blocking build would stall live GPS ingest.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_dlh_session_sequence
    ON public.driver_location_history (driver_id, recording_session_id, sequence_number);

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS idle_location_v2_enabled boolean NOT NULL DEFAULT false,
    -- 90 days per the owner's retention decision; the retention purge and the
    -- manual admin cleanup endpoint both read this instead of hardcoding 24h.
    ADD COLUMN IF NOT EXISTS idle_breadcrumb_retention_hours integer NOT NULL DEFAULT 2160,
    -- Phase 1.5: widen ride_routes v2 geometry to the pickup leg (P2).
    ADD COLUMN IF NOT EXISTS p2_route_geometry_enabled boolean NOT NULL DEFAULT false,
    -- Phase 4: rider-facing pickup-leg rendering (actual-route-only stays default).
    ADD COLUMN IF NOT EXISTS rider_show_pickup_leg_enabled boolean NOT NULL DEFAULT false,
    -- Phase 0.5: FCM data-message fallback for the WS-only location_health nudge.
    ADD COLUMN IF NOT EXISTS location_health_push_nudge_enabled boolean NOT NULL DEFAULT false,
    -- Phase 1.7: close abandoned open Period-3 insurance spans (alert-first).
    ADD COLUMN IF NOT EXISTS stale_p3_autoclose_enabled boolean NOT NULL DEFAULT false;

NOTIFY pgrst, 'reload schema';
