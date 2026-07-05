-- 206_corporate_sections.sql
--
-- Corporate guest booking (Spinr for Business): companies are organised into
-- sections (showroom, service department, ...); employees belong to a section
-- so bookings and spend reports can be filtered by it. Budgets stay
-- per-employee (corporate_member_allowances) — sections are grouping only.
--
-- Forward-compatible: additive table + one nullable FK column on
-- corporate_members (metadata-only add, no rewrite). Safe against in-flight
-- traffic.
--
-- Rollback (on paper):
--   DROP POLICY IF EXISTS "Admin full access corporate_sections" ON corporate_sections;
--   DROP INDEX IF EXISTS corp_members_section_idx;
--   ALTER TABLE corporate_members DROP COLUMN IF EXISTS section_id;
--   DROP TRIGGER IF EXISTS update_corporate_sections_updated_at ON corporate_sections;
--   DROP INDEX IF EXISTS corp_sections_company_idx;
--   DROP INDEX IF EXISTS corp_sections_company_name_unique;
--   DROP TABLE IF EXISTS corporate_sections;

CREATE TABLE IF NOT EXISTS corporate_sections (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES corporate_accounts(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    -- users.id is TEXT; corporate_members.user_id is UUID (pre-existing type
    -- drift, see migration 27) — store the creator loosely, no FK.
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One section name per company (case-insensitive); portal list query.
CREATE UNIQUE INDEX IF NOT EXISTS corp_sections_company_name_unique
    ON corporate_sections (company_id, lower(name));
CREATE INDEX IF NOT EXISTS corp_sections_company_idx
    ON corporate_sections (company_id, status);

-- Employees belong to (at most) one section; archiving/deleting a section
-- must never orphan members — they fall back to "no section".
ALTER TABLE corporate_members
    ADD COLUMN IF NOT EXISTS section_id UUID REFERENCES corporate_sections(id) ON DELETE SET NULL;
-- CONCURRENTLY: corporate_members is a live table with existing rows (the
-- runner detects this keyword and applies the whole file in per-statement
-- autocommit; the two corporate_sections indexes above build instantly on
-- the just-created empty table either way).
CREATE INDEX CONCURRENTLY IF NOT EXISTS corp_members_section_idx
    ON corporate_members (section_id) WHERE section_id IS NOT NULL;

-- updated_at trigger (reuse the shared function; migration 27 pattern).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_corporate_sections_updated_at') THEN
        CREATE TRIGGER update_corporate_sections_updated_at
            BEFORE UPDATE ON corporate_sections
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- RLS: service role (backend) bypasses; company scoping is enforced by the
-- /company/{id}/** route guards. Frontends never touch this table directly.
ALTER TABLE corporate_sections ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'corporate_sections'
          AND policyname = 'Admin full access corporate_sections'
    ) THEN
        EXECUTE 'CREATE POLICY "Admin full access corporate_sections" ON corporate_sections '
                'FOR ALL TO authenticated '
                'USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text AND users.role = ''admin''))';
    END IF;
END $$;

COMMENT ON TABLE corporate_sections IS
    'Company departments/sections (grouping + reporting only; budgets remain per-member allowances).';
COMMENT ON COLUMN corporate_members.section_id IS
    'Optional section the member belongs to; SET NULL when the section is deleted.';
