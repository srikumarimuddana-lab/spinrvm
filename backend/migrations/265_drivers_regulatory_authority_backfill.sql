-- 265_drivers_regulatory_authority_backfill.sql
-- Purpose: Backfill drivers.regulatory_authority/regulatory_region for the
-- 22 legacy drivers that predate the field's introduction (ACTION_ITEMS.md
-- B13). Their NULL value was deliberately grandfathered-through (not
-- blocked) by the regulatory segregation guard added in sgi_forms.py
-- (see PR #2749) precisely because backfilling them was still pending.
--
-- All 22 affected rows resolve to service_areas 'Regina'
-- (d5bc6871-7c6d-4a5f-a194-679463f255ca) or 'Saskatoon'
-- (361d17bb-ec55-4561-943f-e3bbee5d7a55) as of this migration — confirmed
-- directly against the real project (soavhtdhefowwvforzwb) via
--   SELECT id, service_area_id FROM drivers WHERE regulatory_authority IS NULL;
-- Both are real Saskatchewan service areas (service_areas.Regina already
-- carries regulatory_authority='SGI', regulatory_region='SK'; Saskatoon is
-- the province's other major market — see CLAUDE.md "Saskatchewan-first").
-- Scoped by driver id (not by service_area_id or a blanket WHERE NULL) so
-- this migration only ever touches the specific rows verified above, even
-- if new NULL rows are created by the time this runs.
--
-- Does NOT touch service_areas.province/regulatory_authority for
-- 'Saskatoon', 'Regina Airpot', 'riyadh', or 'riyadh airport' — those rows
-- being NULL is a separate, pre-existing gap (tracked in ACTION_ITEMS.md,
-- not part of B13) and out of scope here; this migration only backfills the
-- specific driver rows already confirmed to be Saskatchewan-based.
--
-- Rollback:
--   UPDATE public.drivers
--   SET regulatory_authority = NULL, regulatory_region = NULL
--   WHERE id IN (<the 22 ids below>);
--   Safe: restores the prior (grandfathered-through) state; the segregation
--   guard treats NULL as in-scope, so rollback does not newly block anyone.

UPDATE public.drivers
SET regulatory_authority = 'SGI',
    regulatory_region = 'SK'
WHERE id IN (
    '89ac584b-322c-4adf-af7b-a8dcd81157c8',
    '4c959ecd-7252-435f-9326-f570649d4c6a',
    '0035af1d-6834-4891-9ac5-7fb93cd7e6bb',
    '94766caa-009f-405d-92c7-795cc4e23692',
    '373bc278-0ad4-45c0-b98d-f7e42c311816',
    'ca55a964-bb56-4205-b81e-9fbff1b4b0e7',
    '5e29699f-e3ff-48b4-a673-8ba47ef0c428',
    '7c6fc687-215d-4ca5-9c56-a4ddaa4686cf',
    'da25a3c1-1dac-4dd0-84be-f5ae3048852a',
    'b9405f72-1c5d-44e4-97e8-7ec6b5c8fb9f',
    'a4b85b24-6071-473b-8dd1-ccc4bf9eba59',
    '2fbde775-4c9d-4027-92d1-eadb3c2edffc',
    '4f3ee2ee-a2e8-440e-a0f0-ec24efd6df30',
    'b51bf229-c8b7-4405-89ab-7a3a401bac91',
    '19533599-19b0-4e5c-a81f-1a46a9d281b6',
    '1ac4a74a-2aa1-4608-9f26-25329e53109b',
    '6244e0d6-49bb-466a-a907-21ed599bf632',
    'd1ce7fab-dbb3-44e5-90c2-3e9b8dd18e77',
    'e7b6d433-968a-46b4-a2ca-caeeb876cc67',
    'b23e0f82-77a3-4c70-82d4-1987e2207341',
    '9c2dc9dc-0564-4f3c-bf6f-70c281af3bec',
    'f2a6c10f-6605-4d35-9951-bf00868f99eb'
)
AND regulatory_authority IS NULL;
