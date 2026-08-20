-- 349: rollout flag for the route finalizer's booked-dropoff tail anchor.
--
-- Context: reconstruction (4-tier gap fill, route_reconstruction.py) only ran
-- when a completion GPS fix was recorded at ride end. A ride whose capture
-- died mid-trip (SPR-YDEBCH, 2026-08-19: 11-minute background-capture hole,
-- no completion fix uploaded) therefore skipped reconstruction entirely and
-- published bare observed fragments — the "map not populated" symptom. With
-- this flag on, the finalizer anchors the reconstruction tail to the BOOKED
-- dropoff when no completion fix exists (the start anchor has always been the
-- booked pickup), so internal gaps, missing starts, and missing tails are all
-- bridged with road-following inferred segments. Provenance is recorded in
-- route_quality.completion_anchor_source ('completion_fix' | 'booked_dropoff').
--
-- Owner directive 2026-08-20: a completed ride's map must never show a
-- missing path. Default false per the ship-dark rule; flip via admin settings.
--
-- Rollback:
--   ALTER TABLE settings DROP COLUMN IF EXISTS route_booked_dropoff_anchor_enabled;
--
-- Forward-compatible: additive defaulted column; older backends ignore it.

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS route_booked_dropoff_anchor_enabled boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN settings.route_booked_dropoff_anchor_enabled IS
    'Route finalizer: when no completion GPS fix was recorded, anchor route '
    'reconstruction''s tail to the booked dropoff so gaps are still bridged '
    '(inferred, provenance in route_quality.completion_anchor_source). '
    'Off = legacy behavior (skip reconstruction, publish observed fragments).';
