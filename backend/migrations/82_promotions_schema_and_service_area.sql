-- 82_promotions_schema_and_service_area.sql
--
-- Purpose:
--   1. Create the `promotions` table IF NOT EXISTS (the initial CREATE was
--      done manually in early prod and never landed in the migrations runner).
--   2. Create `promo_applications` IF NOT EXISTS.
--   3. Add `service_area_id TEXT` to `promotions` so promos can be scoped to
--      a specific city/service area instead of applying app-wide.
--      NULL = available in all areas (platform-wide code).
--
-- NOTE: service_areas.id is TEXT (not UUID) — confirmed by migration 08.
--
-- Rollback:
--   ALTER TABLE promotions DROP COLUMN IF EXISTS service_area_id;
--   DROP TABLE IF EXISTS promo_applications;
--   -- Do NOT drop promotions; it contains live data.

-- ── 1. promotions table ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS promotions (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    code                TEXT            NOT NULL,
    promo_type          TEXT,
    discount_type       TEXT            NOT NULL DEFAULT 'flat',
    discount_value      NUMERIC(10, 2)  NOT NULL DEFAULT 0,
    max_discount        NUMERIC(10, 2),
    max_uses            INTEGER         DEFAULT 0,
    max_uses_per_user   INTEGER         DEFAULT 1,
    uses                INTEGER         DEFAULT 0,
    expiry_date         TIMESTAMPTZ,
    is_active           BOOLEAN         DEFAULT TRUE,
    description         TEXT            DEFAULT '',
    min_ride_fare       NUMERIC(10, 2)  DEFAULT 0,
    total_budget        NUMERIC(10, 2)  DEFAULT 0,
    budget_used         NUMERIC(10, 2)  DEFAULT 0,
    first_ride_only     BOOLEAN         DEFAULT FALSE,
    new_user_days       INTEGER         DEFAULT 0,
    inactive_days       INTEGER         DEFAULT 0,
    min_total_rides     INTEGER         DEFAULT 0,
    max_total_rides     INTEGER         DEFAULT 0,
    assigned_user_ids   JSONB           DEFAULT '[]'::jsonb,
    service_area_id     TEXT            REFERENCES service_areas(id),
    created_at          TIMESTAMPTZ     DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_promotions_code ON promotions (code);

-- ── 2. Add service_area_id to existing prod table (TEXT to match service_areas.id) ──
ALTER TABLE promotions
    ADD COLUMN IF NOT EXISTS service_area_id TEXT REFERENCES service_areas(id);

-- ── 3. promo_applications ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS promo_applications (
    id               UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID            NOT NULL,
    promo_id         UUID            NOT NULL REFERENCES promotions(id) ON DELETE CASCADE,
    code             TEXT            NOT NULL,
    discount_applied NUMERIC(10, 2)  NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ     DEFAULT NOW()
);

-- ── 4. Indexes ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_promotions_is_active
    ON promotions (is_active);

CREATE INDEX IF NOT EXISTS idx_promotions_area_active
    ON promotions (service_area_id, is_active);

CREATE INDEX IF NOT EXISTS idx_promo_applications_user
    ON promo_applications (user_id);

CREATE INDEX IF NOT EXISTS idx_promo_applications_promo
    ON promo_applications (promo_id);

CREATE INDEX IF NOT EXISTS idx_promo_applications_user_promo
    ON promo_applications (user_id, promo_id);
