-- 410: settings column gating whether the AI chat's fare-quote tool shows a
-- priced-but-unbookable option for a vehicle type with no drivers online.
--
-- WHY: ACTION_ITEMS.md AI17/F4 — get_fare_quote() (backend/ai/tools_booking.py)
-- currently omits price entirely for any unavailable vehicle type (partial
-- outage: skipped from `quotes`; total outage: the whole call returns
-- `{"no_drivers": true, "quotes": []}` with no fare fields at all), while
-- rider-app/app/ride-options.tsx already shows a real, live-computed price
-- for every vehicle type regardless of availability — dimmed, "No drivers
-- nearby", booking disabled. Product decision (confirmed): the AI chat
-- should match the rider-app screen's existing, presumably-approved pattern
-- rather than the other way around.
--
-- What this flag actually controls: TRUE makes get_fare_quote() include a
-- priced quote object (with `available: false`) for every unavailable
-- vehicle type, in both the partial-outage and total-outage cases, instead
-- of omitting price for them. FALSE (default) keeps today's exact behaviour
-- unchanged. This is a genuinely NEW behaviour, not a preserved default —
-- unlike migration 409's kill switch — so it ships dark (default FALSE) per
-- CLAUDE.md's release-gate rule 3 ("ship dark, verify in staging/canary,
-- then flip on") rather than defaulting on.
--
-- Safety invariant this flag does NOT change: an unavailable option is never
-- bookable through the AI. The Redis quote-pin used for the typed "book it"
-- shortcut (rule 6c) keeps its existing safe, endpoints-only shape whenever
-- nothing is currently bookable — this flag only affects what price
-- information is SHOWN, never what can be silently booked.
--
-- Rollback:
--   ALTER TABLE settings
--     DROP COLUMN IF EXISTS ai_fare_quote_show_unavailable_enabled;
-- (Column-only rollback restores exactly today's behaviour: the read site
-- defaults to False when the column/key is absent, matching current
-- production. No data migration needed either direction.)
--
-- Forward-compatible: single additive defaulted column; old backends ignore
-- it. No index: the settings row is a single-row table read through the 60s
-- settings_loader cache, never filtered on this column.

ALTER TABLE settings
  ADD COLUMN IF NOT EXISTS ai_fare_quote_show_unavailable_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN settings.ai_fare_quote_show_unavailable_enabled IS
  'AI17/F4: when TRUE, the AI chat''s fare-quote tool (backend/ai/tools_booking.py get_fare_quote) shows a priced-but-unbookable option (available: false) for any vehicle type with no drivers online, matching rider-app/app/ride-options.tsx''s existing display. FALSE (default) keeps the current behaviour of omitting price for unavailable types entirely. Never affects what can actually be booked — the pinned-quote "book it" shortcut stays safe either way.';
