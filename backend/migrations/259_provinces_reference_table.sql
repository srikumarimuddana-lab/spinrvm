-- 259_provinces_reference_table.sql
--
-- Purpose: Create a normalized `provinces` reference table so Spinr's
-- multi-province expansion (corporate module, reporting module, bulk
-- upload) has one shared source of truth for province-level defaults,
-- instead of each service_area row carrying its own duplicated copy.
--
-- Context: `service_areas.province` is currently a bare free-text TEXT
-- column (added in migration 81_service_areas_missing_columns.sql), and
-- `service_areas.regulatory_authority` / `regulatory_region` (added in
-- migration 223_service_areas_regulatory_authority.sql) duplicate the same
-- regulator info on every service-area row within a province. This
-- migration is additive only — it does NOT touch `service_areas` or any
-- other existing table. The follow-up migration (260) backfills this table
-- from the existing free-text values and adds `service_areas.province_code`
-- as an FK; the legacy `province`/`regulatory_authority`/`regulatory_region`
-- columns are kept as nullable per-area overrides, not dropped.
--
-- Blast radius: zero. No existing table is read or written by this
-- migration; `provinces` has no callers yet.
--
-- Rollback:
--   DROP TABLE IF EXISTS public.provinces;

CREATE TABLE IF NOT EXISTS public.provinces (
    code TEXT PRIMARY KEY,                          -- e.g. 'SK', 'AB', 'ON' (Canada Post 2-letter code)
    name TEXT NOT NULL,                              -- e.g. 'Saskatchewan'
    default_regulatory_authority TEXT,                -- e.g. 'SGI' — the primary/default regulator for this province
    default_regulatory_requirements_url TEXT,
    default_regulatory_notes TEXT,
    default_timezone TEXT NOT NULL DEFAULT 'America/Regina',
    -- Free-form slot for province-specific parameters that don't yet warrant
    -- a dedicated column (e.g. a future surge-cap override, retention-window
    -- override). Intentionally unused by any code path today — do not read
    -- this column until a specific consumer and shape are defined.
    regulatory_config JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.provinces IS
    'Canadian province reference data — shared by the corporate module, reporting module, and bulk-upload utility for province/service-area scoping. See migration 260 for the service_areas.province_code backfill + FK.';

-- RLS enabled with no anon/authenticated policies defined — default-deny
-- for direct Supabase-client reads. No existing migration shows a
-- precedent for anon-readable access to `service_areas` (the comparable
-- reference table), so this does not introduce a new access pattern
-- speculatively. All current and near-term consumers (backend API routes,
-- admin dashboard, the reporting module) go through the backend, which
-- uses the service-role key and bypasses RLS by design. If a future
-- rider-app/driver-app screen needs to query this table directly via the
-- anon key (bypassing the backend), add an explicit SELECT-only policy in
-- a follow-up migration at that time — do not add it preemptively here.
ALTER TABLE public.provinces ENABLE ROW LEVEL SECURITY;

-- Seed Saskatchewan — the only live province today. Other provinces are
-- added via admin tooling (or a future migration) as expansion happens;
-- this migration intentionally does not pre-seed unlaunched provinces.
INSERT INTO public.provinces (code, name, default_regulatory_authority, default_timezone)
VALUES ('SK', 'Saskatchewan', 'SGI', 'America/Regina')
ON CONFLICT (code) DO NOTHING;
