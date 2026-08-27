-- 370: settings column for the driver location marker write gate flag.
--
-- WHY: utils/location_write_gate.py (PR #4594) reads
-- settings.location_marker_write_gate_enabled to decide whether the
-- Redis-keyed 3s marker-write window actually skips coalesced writes
-- (flag on) or only counts what it would have skipped as
-- outcome="shadow_throttled" (flag off — the shadow-measurement ship
-- state). The gate merged reading a column no migration had ever
-- created: an admin-dashboard flip was silently dropped by
-- SettingsUpdateRequest (extra="ignore" + model_dump(exclude_none=True)),
-- and a direct SQL flip failed on the missing column — the same
-- settings-column drift pattern test_admin_settings_write_allowlist_drift
-- exists to catch (legacy_consent_notice_enabled, rideless_sos_enabled).
-- The companion SettingsUpdateRequest field and allowlist-snapshot entry
-- land in the same PR, per that test's maintenance rule.
--
-- Rollback:
--   ALTER TABLE settings
--     DROP COLUMN IF EXISTS location_marker_write_gate_enabled;
-- (Column-only rollback restores exactly today's behaviour: the gate
-- reads settings.get(..., False), so a missing column means shadow mode.
-- The Redis-keyed window itself is code, not schema — reverting IT needs
-- a redeploy, not this rollback.)
--
-- Forward-compatible: single additive defaulted column; old backends
-- ignore it, and the gate's reader defaulted False before it existed, so
-- deploy order is free. No index: the settings row is a single-row table
-- read through the 60s settings_loader cache, never filtered on this
-- column.

ALTER TABLE settings
  ADD COLUMN IF NOT EXISTS location_marker_write_gate_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN settings.location_marker_write_gate_enabled IS
  'Driver location marker write gate (utils/location_write_gate.py): FALSE = shadow mode (count-only, every write still lands), TRUE = coalesced REST marker writes actually skip. WS handlers honour the 3s window regardless of this flag.';
