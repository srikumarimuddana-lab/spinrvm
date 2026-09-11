-- 409: settings column for the AI chat incremental-streaming kill switch.
--
-- WHY: ai/orchestrator.py's run_chat_turn streams the assistant's reply
-- through StreamingOutputFilter (backend/ai/stream_filter.py, F08/PR #5138),
-- which buffers and re-scrubs the whole turn so far before releasing a
-- prefix, coalescing releases every ~32 new characters. filter_tool_leakage
-- and scrub_pii(policy=AI_CHAT) already run on 100% of the reply either way
-- — that privacy guarantee is unconditional and does NOT depend on this
-- flag; see stream_filter.py's own docstring and contract.
--
-- What this flag actually controls: whether the rider sees the reply
-- token-by-token (TRUE, current/default behaviour, unchanged by this
-- migration) or as one single scrubbed chunk at the end of the turn
-- (FALSE) — an operational lever for an incident where the incremental
-- release path itself is suspected (e.g. a `StreamingOutputFilter` bug
-- producing wrong coalescing/timing), not a privacy off-switch. Setting it
-- FALSE forces StreamingOutputFilter's `holdback` far beyond any real
-- reply's length, so `_release(final=False)` always returns "" and every
-- reply ships from `flush()` alone — see orchestrator.py's construction of
-- `out_filter` for exactly how.
--
-- Default TRUE (unlike most AI flags in this table, which ship dark /
-- default FALSE): incremental streaming is ALREADY the live, unconditional
-- behaviour today. This flag is a rollback lever for that existing
-- behaviour, not a new feature being introduced — defaulting it FALSE
-- would silently degrade every rider's chat UX to single-shot replies on
-- deploy, which CLAUDE.md's "no silent behaviour change to an
-- already-shipped flow" rule forbids.
--
-- Rollback:
--   ALTER TABLE settings
--     DROP COLUMN IF EXISTS ai_stream_incremental_enabled;
-- (Column-only rollback restores exactly today's behaviour: the read site
-- defaults to True when the column/key is absent, matching current
-- production. No data migration needed either direction.)
--
-- Forward-compatible: single additive defaulted column; old backends
-- ignore it. No index: the settings row is a single-row table read through
-- the 60s settings_loader cache, never filtered on this column.

ALTER TABLE settings
  ADD COLUMN IF NOT EXISTS ai_stream_incremental_enabled BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN settings.ai_stream_incremental_enabled IS
  'AI chat incremental-streaming kill switch (backend/ai/orchestrator.py). TRUE (default, matches pre-existing behaviour) = reply streams token-by-token through StreamingOutputFilter''s incremental release. FALSE = the turn is buffered and released as one scrubbed chunk at the end — an operational lever for the release mechanism, NOT a privacy toggle: filter_tool_leakage/scrub_pii(AI_CHAT) run on the full reply either way before anything reaches the client.';
