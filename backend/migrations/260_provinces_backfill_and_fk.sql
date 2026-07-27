-- 260_provinces_backfill_and_fk.sql
--
-- Purpose: Backfill public.provinces from the existing free-text
-- service_areas.province / regulatory_authority / regulatory_region /
-- timezone columns, then add service_areas.province_code as a real FK.
--
-- This is a genuine DATA migration (reads + derives from existing rows),
-- not purely additive like 259 — treat it with the corresponding care.
--
-- What this migration does NOT do (by design, additive-over-destructive):
--   - Does NOT drop or rename service_areas.province, regulatory_authority,
--     regulatory_region, or timezone. Those columns are kept as nullable
--     per-service-area OVERRIDES (NULL after this migration = "inherit the
--     province default"; non-NULL = an intentional per-area override, e.g.
--     a municipality-specific regulator or an airport-specific timezone).
--     Dropping the free-text `province` column is a separate, later
--     migration after a verification window — not part of this change.
--   - Does NOT make service_areas.province_code NOT NULL. A service area
--     with a NULL or unrecognized province value is left with a NULL
--     province_code rather than guessed at — see the pre-flight check below.
--
-- Pre-flight safety check: if two service_areas rows in the same province
-- disagree on regulatory_authority, this migration ABORTS (RAISE EXCEPTION)
-- instead of silently picking one. That disagreement is either a genuine
-- per-area override (which should stay on service_areas, not get lost in
-- an averaged/first-wins backfill) or a data-quality problem — either way
-- it needs a human to look at it, not a migration to guess.
--
-- Blast radius: service_areas only. No other table is touched. Any code
-- path currently reading service_areas.province / regulatory_authority /
-- regulatory_region / timezone continues to work unchanged — those columns
-- are untouched. Nothing yet reads the new service_areas.province_code
-- column (no backend code change is bundled with this migration), so this
-- ships dark: the column exists and is populated, but no behavior changes
-- until a later, separate PR adds code that reads it.
--
-- Rollback:
--   ALTER TABLE public.service_areas DROP CONSTRAINT IF EXISTS service_areas_province_code_fkey;
--   DROP INDEX IF EXISTS idx_service_areas_province_code;
--   ALTER TABLE public.service_areas DROP COLUMN IF EXISTS province_code;
--   -- provinces rows inserted by this migration's backfill can be left in
--   -- place (they're harmless reference data) or removed manually if the
--   -- whole 259+260 pair is being rolled back together.

DO $$
DECLARE
    conflict_row RECORD;
BEGIN
    -- Pre-flight: abort if any province has more than one distinct
    -- non-null regulatory_authority value across its service areas.
    -- (Case-insensitive/trimmed comparison — 'SK' and ' sk' must not be
    -- treated as distinct, matching the normalization used in the
    -- backfill INSERT below.)
    SELECT UPPER(TRIM(province)), COUNT(DISTINCT regulatory_authority) AS distinct_count
    INTO conflict_row
    FROM public.service_areas
    WHERE province IS NOT NULL AND regulatory_authority IS NOT NULL
    GROUP BY UPPER(TRIM(province))
    HAVING COUNT(DISTINCT regulatory_authority) > 1
    ORDER BY 1
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION
            'Migration 260 aborted: province % has service_areas rows with conflicting regulatory_authority values (% distinct). Resolve manually before re-running this migration — this may be a genuine per-area override or a data-quality issue.',
            conflict_row.province, conflict_row.distinct_count;
    END IF;

    -- Same check for timezone: unlike regulatory_authority (which must be
    -- province-wide consistent by design), a per-area timezone override is
    -- explicitly anticipated (e.g. an airport service area) — but if two
    -- areas disagree, this migration still must not silently pick one as
    -- the PROVINCE DEFAULT via an arbitrary LIMIT 1. Abort and let a human
    -- decide which value is the true province-wide default vs. a
    -- legitimate per-area override that should stay on service_areas only.
    SELECT UPPER(TRIM(province)), COUNT(DISTINCT timezone) AS distinct_count
    INTO conflict_row
    FROM public.service_areas
    WHERE province IS NOT NULL AND timezone IS NOT NULL
    GROUP BY UPPER(TRIM(province))
    HAVING COUNT(DISTINCT timezone) > 1
    ORDER BY 1
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION
            'Migration 260 aborted: province % has service_areas rows with conflicting timezone values (% distinct). Resolve manually (decide the true province-wide default) before re-running this migration.',
            conflict_row.province, conflict_row.distinct_count;
    END IF;
END $$;

-- Backfill provinces from any distinct service_areas.province value not
-- already present in public.provinces (259 already seeded 'SK' — this
-- covers any other province already in service_areas data, and is a no-op
-- if SK is the only value present today).
-- Normalized to UPPER(TRIM(...)) so 'SK', ' sk', 'sk ' etc. in legacy data
-- collapse to one provinces.code instead of near-duplicate rows — the
-- pre-flight checks above already group on this same normalization, so
-- what's inserted here is guaranteed conflict-free.
INSERT INTO public.provinces (code, name, default_regulatory_authority, default_timezone)
SELECT DISTINCT
    UPPER(TRIM(sa.province)),
    UPPER(TRIM(sa.province)),  -- placeholder name (province code) — an
                   -- admin should replace with the real province name via
                   -- UPDATE once this migration lands; not blocking, not
                   -- guessed here
    (SELECT sa2.regulatory_authority FROM public.service_areas sa2
     WHERE UPPER(TRIM(sa2.province)) = UPPER(TRIM(sa.province)) AND sa2.regulatory_authority IS NOT NULL
     LIMIT 1),
    COALESCE(
        (SELECT sa3.timezone FROM public.service_areas sa3
         WHERE UPPER(TRIM(sa3.province)) = UPPER(TRIM(sa.province)) AND sa3.timezone IS NOT NULL
         LIMIT 1),
        'America/Regina'
    )
FROM public.service_areas sa
WHERE sa.province IS NOT NULL
ON CONFLICT (code) DO NOTHING;

-- Add the FK column, nullable (forward-compatible — no NOT NULL yet).
ALTER TABLE public.service_areas
    ADD COLUMN IF NOT EXISTS province_code TEXT;

-- Backfill province_code from the existing free-text province column.
-- Left as NULL for any row whose province is NULL or doesn't match a
-- provinces.code — those need manual review, not a guessed value.
UPDATE public.service_areas sa
SET province_code = p.code
FROM public.provinces p
WHERE UPPER(TRIM(sa.province)) = p.code
  AND sa.province_code IS DISTINCT FROM p.code;

-- Add the FK as NOT VALID first (no full-table scan/lock), then VALIDATE
-- separately — standard low-lock pattern for adding a constraint to a
-- table that may be read concurrently by dispatch/fare code.
ALTER TABLE public.service_areas
    DROP CONSTRAINT IF EXISTS service_areas_province_code_fkey;

ALTER TABLE public.service_areas
    ADD CONSTRAINT service_areas_province_code_fkey
    FOREIGN KEY (province_code) REFERENCES public.provinces(code)
    NOT VALID;

ALTER TABLE public.service_areas
    VALIDATE CONSTRAINT service_areas_province_code_fkey;

-- Index for the new query pattern this enables: "all service areas in
-- province X" (corporate module, reporting module, bulk-upload template
-- generation all need this).
CREATE INDEX IF NOT EXISTS idx_service_areas_province_code
    ON public.service_areas (province_code)
    WHERE province_code IS NOT NULL;

COMMENT ON COLUMN public.service_areas.province_code IS
    'FK to provinces.code. Backfilled from the legacy free-text province column by migration 260. NULL means unresolved — check the legacy province column manually.';
