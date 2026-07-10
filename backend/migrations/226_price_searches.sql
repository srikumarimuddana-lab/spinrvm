-- 226_price_searches.sql
--
-- Append-only log of fare-estimate ("price search") requests, so the admin
-- Operational-health funnel can answer "how many users searched for a price"
-- for a window — the top of the ride funnel, before a ride is ever created.
--
-- Written fire-and-forget from POST /rides/estimate (the rider tapping to see
-- a price). Intentionally excluded surfaces — only the rider-app estimate
-- call counts as a consumer price search:
--   • AI-assistant fare quotes (compute_ride_estimates with the default
--     track_search=False) — assistant-driven, not a rider shopping a price.
--   • Corporate guest-booking quotes (GET /bookings/fare-estimate → the
--     separate compute_fare_estimate engine) — a B2B booking surface, outside
--     the consumer funnel this table feeds. Revisit if corporate quotes ever
--     belong in the ops funnel.
--
-- PIPEDA-safe: stores the acting user id + the matched service area id only.
-- NEVER add raw pickup/dropoff coordinates or addresses — the funnel needs a
-- count, not location traces (raw GPS is prohibited in analytics tables).
--
--   • user_id          TEXT        — the rider who searched (id only)
--   • service_area_id  TEXT        — matched pickup area (nullable; area filter)
--   • created_at       TIMESTAMPTZ — when the price was requested
--
-- Append-only, RLS enabled with NO policies (service role only; anon/authenticated
-- denied) — same posture as ai_security_events. The admin dashboard reads this
-- through the backend service role; the frontend anon key never touches it.
--
-- Forward-compatible: new table, no change to hot-path schema. Estimate logging
-- is best-effort — a write failure here must never fail a fare quote.
--
-- Rollback:
--   DROP TABLE IF EXISTS price_searches;

CREATE TABLE IF NOT EXISTS price_searches (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id          TEXT,
    service_area_id  TEXT
);

-- Recent-window scans (the funnel counts by created_at), optionally area-scoped.
CREATE INDEX IF NOT EXISTS idx_price_searches_recent
    ON price_searches (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_searches_area
    ON price_searches (service_area_id, created_at DESC);

-- Backend (service role) bypasses RLS; enabling with no policies keeps this
-- analytics feed backend-only.
ALTER TABLE price_searches ENABLE ROW LEVEL SECURITY;
