-- 333_drivers_regulatory_authority_backfill_round2.sql
-- Purpose: Backfill drivers.regulatory_authority/regulatory_region for 7
-- drivers created between 2026-07-30 and 2026-08-16 (ACTION_ITEMS.md B13,
-- round 2). Migration 265 backfilled the 22 legacy rows that predated the
-- field's introduction; these 7 are NOT legacy — they were created by the
-- real driver self-signup auto-create path (`routes/drivers/profile.py`'s
-- `/register` and `PUT /me` auto-create branches), which never set these
-- two columns at all. That root cause is fixed in the same PR as this
-- migration (both insert sites now call `_resolve_regulatory_defaults`).
-- This migration only backfills the rows the bug already produced before
-- the fix landed — it does not change the guard.
--
-- All 7 affected rows resolve to service_areas 'Saskatoon'
-- (361d17bb-ec55-4561-943f-e3bbee5d7a55, 6 rows) or 'Regina'
-- (d5bc6871-7c6d-4a5f-a194-679463f255ca, 1 row) as of this migration —
-- confirmed directly against the real project (soavhtdhefowwvforzwb) via
--   SELECT id, created_at, service_area_id FROM drivers
--   WHERE regulatory_authority IS NULL ORDER BY created_at;
-- Both are real Saskatchewan service areas (see migration 265's own note).
-- Scoped by driver id (not by service_area_id or a blanket WHERE NULL) so
-- this migration only ever touches the specific rows verified above, even
-- if new NULL rows are created by the time this runs (which the paired
-- code fix should now prevent going forward).
--
-- Rollback:
--   UPDATE public.drivers
--   SET regulatory_authority = NULL, regulatory_region = NULL
--   WHERE id IN (<the 7 ids below>);
--   Safe: restores the prior (grandfathered-through) state; the segregation
--   guard in sgi_forms.py still treats NULL as in-scope as of this
--   migration (the guard tightening described in B13 is intentionally NOT
--   part of this migration — it is a separate, later step once this
--   backfill is confirmed applied).

UPDATE public.drivers
SET regulatory_authority = 'SGI',
    regulatory_region = 'SK'
WHERE id IN (
    '9f8effbf-3762-44e6-ba37-96cd3470d998',
    '03861f4a-0c6c-414e-91b0-73c37ee70c92',
    'daab8db2-00b3-4900-bdee-34144b7227c2',
    'c00e14aa-9875-4911-bc7a-1c8ad94669a9',
    '1938ae31-5222-41b5-834b-a7e53fdaab7f',
    '4d2e2694-f712-4cfd-a6f6-ebe977549daf',
    '483bf09e-459e-4351-8c57-94029228d7c8'
)
AND regulatory_authority IS NULL;
