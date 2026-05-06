-- Migration 72: Composite dispatch index for WAV rides
--
-- Rollback: DROP INDEX IF EXISTS idx_drivers_dispatch_wav;
--
-- Migration 60 (60_wav_dispatch.sql) added is_wav + idx_drivers_is_wav.
-- This migration adds a tighter composite index used by the dispatch
-- get_rows call when requires_wav=true:
--
--   WHERE is_online = true AND is_available = true AND is_wav = true
--
-- The composite partial index lets Postgres satisfy that filter in a
-- single index-only scan instead of intersecting three separate indexes.
-- Non-WAV dispatch paths are unaffected.

CREATE INDEX IF NOT EXISTS idx_drivers_dispatch_wav
  ON drivers (is_online, is_available, is_wav)
  WHERE is_online = true AND is_available = true AND is_wav = true;
