-- 355_driver_insurance_period_corrections.sql
-- ACTION_ITEMS.md B34: sanctioned way to correct a wrong
-- driver_insurance_periods row without mutating it.
--
-- Gap this closes: migration 64's immutability trigger unconditionally
-- blocks UPDATE on any closed driver_insurance_periods row (the only
-- UPDATE it ever permits is the NULL -> timestamp `ended_at` close
-- transition on a still-open row). Once a period row is wrong -- a bad
-- reconstruction, a bug in record_period_transition(), anything -- there
-- has been no sanctioned way to record a fix. `.claude/context/
-- domain-safety.md` already documented a `driver_insurance_period_
-- corrections` table as if it existed; it never did (confirmed by
-- `grep -rl driver_insurance_period_corrections backend/ docs/` returning
-- only prose references, and a live information_schema.tables check
-- against production finding only driver_insurance_periods). This
-- migration is that table, finally built.
--
-- Design mirrors migration 64's own driver_insurance_periods precedent
-- (per CLAUDE.md's Database & Migration Conventions -- "confirm the new
-- table's RLS/immutability pattern matches theirs, don't diverge
-- silently"):
--   * Service-role-only writes: RLS enabled, SELECT policy only, no
--     INSERT/UPDATE/DELETE policy for authenticated/anon (same as 64).
--   * Append-only via a BEFORE UPDATE OR DELETE trigger. Stricter than
--     64's trigger: a correction row is complete at insert time (no
--     open/closed lifecycle to it -- unlike the parent table there is no
--     "close" transition to permit), so this trigger blocks ALL UPDATE
--     and DELETE unconditionally, not just after some flag is set.
--
-- Supersedes-but-never-mutates: a correction references its original row
-- by original_period_id (FK) and stands next to it, never replacing or
-- editing it. `driver_insurance_periods` itself is completely untouched
-- by this migration -- no columns added, no rows changed.
--
-- One correction per original row (UNIQUE index on original_period_id):
-- keeps every consumer's "prefer the correction if present" lookup a
-- single dict/join keyed by original_period_id, with no "which of several
-- corrections is authoritative" ambiguity to resolve. If a correction
-- itself later turns out to be wrong, that is a new, separate design
-- question (correcting a correction) -- explicitly out of scope for this
-- migration; simplest thing that satisfies the acceptance criteria in
-- ACTION_ITEMS.md B34.
--
-- Filing this table is not itself a decision to correct anything. The
-- verification pass in docs/change-log/2026-08-20-insurance-period-
-- reconstruction-verification.md found 156 legacy rides whose
-- reconstructed Period-2 boundary diverges from real GPS data -- that
-- table existing now gives a sanctioned destination for a correction
-- decision, but applying corrections to those 156 rides remains a
-- separate, explicit call (not attempted here, not automated by this
-- migration).
--
-- Rollback:
--   DROP TRIGGER IF EXISTS driver_insurance_period_corrections_no_mutate
--       ON driver_insurance_period_corrections;
--   DROP FUNCTION IF EXISTS _driver_insurance_period_corrections_immutable();
--   DROP TABLE IF EXISTS driver_insurance_period_corrections;
--   Safe any time before a correction row has been written and relied on
--   by a downstream consumer (compliance_export.py, admin_driver_
--   distance_logs); once a correction exists, dropping the table means
--   consumers silently fall back to the (known-wrong) original row again
--   -- coordinate with whoever requested the correction before rolling
--   back in that case.
--
-- Forward-compatible: new table only. No existing table (including
-- driver_insurance_periods) is altered.
-- Retention: same 7-year regulatory retention as driver_insurance_periods
-- itself (Saskatchewan Transportation Act, insurance-period transitions
-- for commercial coverage audit) -- a correction to a retained row is
-- itself part of that audit trail. Migration 50's PII purge already
-- leaves driver_insurance_periods alone (migration 64's comment); this
-- table inherits that same exemption by the same reasoning -- no PII,
-- regulatory audit trail.

CREATE TABLE IF NOT EXISTS driver_insurance_period_corrections (
    id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The row being corrected. Not ON DELETE CASCADE: driver_insurance_periods
    -- rows can never be deleted (migration 64's trigger blocks it
    -- unconditionally), so this FK's delete behavior is unreachable in
    -- practice; left as the default RESTRICT rather than asserting a
    -- cascade semantic that should never fire.
    original_period_id   uuid        NOT NULL REFERENCES driver_insurance_periods(id),
    corrected_started_at timestamptz NOT NULL,
    -- Nullable like the parent's ended_at: a correction may need to
    -- re-open a period that was wrongly closed, or correct only the
    -- start boundary of a still-open period.
    corrected_ended_at   timestamptz,
    -- A correction without a stated reason is not auditable -- required,
    -- non-blank.
    reason                text        NOT NULL CHECK (length(btrim(reason)) > 0),
    corrected_by          text        NOT NULL REFERENCES users(id),
    corrected_at          timestamptz NOT NULL DEFAULT now(),
    created_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT driver_insurance_period_corrections_span_order
        CHECK (corrected_ended_at IS NULL OR corrected_ended_at >= corrected_started_at)
);

-- Query pattern: every consumer (compliance_export.py, admin_driver_
-- distance_logs) does "given a batch of original_period_id values, which
-- have a correction?" -- a single UNIQUE index both enforces the
-- one-correction-per-original-row rule above and serves that lookup.
CREATE UNIQUE INDEX IF NOT EXISTS driver_insurance_period_corrections_original_period_id_key
    ON driver_insurance_period_corrections (original_period_id);

-- Service-role-only write pattern, same as migration 64.
ALTER TABLE driver_insurance_period_corrections ENABLE ROW LEVEL SECURITY;

-- SELECT: the driver whose original period this corrects, or an admin.
-- No driver_id column on this table -- resolved through the original row,
-- same relationship shape as migration 64's own policy.
CREATE POLICY driver_insurance_period_corrections_select ON driver_insurance_period_corrections
    FOR SELECT USING (
        original_period_id IN (
            SELECT id FROM driver_insurance_periods
            WHERE driver_id = (
                SELECT id FROM drivers WHERE user_id = auth.uid()::text
            )
        )
        OR (SELECT role FROM users WHERE id = auth.uid()::text) IN ('admin', 'super_admin')
    );

-- No INSERT, UPDATE, or DELETE policies -> denied for authenticated/anon
-- by default. Only the service role (backend) can write, matching
-- driver_insurance_periods' own convention -- this regulatory audit table
-- must never be reachable from the anon/authenticated Supabase keys.

-- Tamper-evidence trigger: this table has no open/closed lifecycle (unlike
-- driver_insurance_periods, which permits exactly one UPDATE to close a
-- row) -- a correction is complete the moment it's inserted, so every
-- UPDATE and DELETE is blocked unconditionally.
CREATE OR REPLACE FUNCTION _driver_insurance_period_corrections_immutable()
RETURNS trigger LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS
$$
BEGIN
    RAISE EXCEPTION
        'driver_insurance_period_corrections rows are append-only and cannot be % (id=%)',
        TG_OP, OLD.id;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'driver_insurance_period_corrections_no_mutate'
          AND tgrelid = 'driver_insurance_period_corrections'::regclass
    ) THEN
        CREATE TRIGGER driver_insurance_period_corrections_no_mutate
            BEFORE UPDATE OR DELETE ON driver_insurance_period_corrections
            FOR EACH ROW EXECUTE FUNCTION _driver_insurance_period_corrections_immutable();
    END IF;
END;
$$;

COMMENT ON TABLE driver_insurance_period_corrections IS
    'Append-only corrections to driver_insurance_periods rows, referenced '
    'by original_period_id -- never mutates the original (migration 64 '
    'already blocks that). One correction per original row (UNIQUE on '
    'original_period_id). INSERT-only for the service role; UPDATE/DELETE '
    'blocked unconditionally by trigger. Required justification (reason). '
    'ACTION_ITEMS.md B34, 7-year retention alongside driver_insurance_periods. '
    'Created in migration 355. Filing this table is not itself a decision '
    'to correct any specific row -- see the 156-row divergence noted in '
    'docs/change-log/2026-08-20-insurance-period-reconstruction-verification.md.';
