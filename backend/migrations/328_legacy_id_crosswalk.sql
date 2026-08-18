-- 328_legacy_id_crosswalk.sql
--
-- CR-2026-4106 (issue #4106): the old app used TWO incompatible ID
-- namespaces for the same physical drivers, never cross-referenced against
-- each other:
--
--   • rides.legacy_import_metadata.old_driver_id / old_customer_id — Mongo
--     ObjectIds, stamped by backend/services/booking_import_service.py from
--     the booking export. Covers both drivers AND riders.
--   • drivers.legacy_import_metadata.old_driver_id — a 10-digit numeric ID,
--     stamped by backend/services/driver_import_service.py from a separate
--     Saskatoon driver CSV. Covers drivers only.
--
-- Both importers resolve the *actual* rider_id/driver_id FK by phone-match
-- against already-imported users/drivers rows (see normalize_phone() +
-- users_by_phone/drivers_by_phone in booking_import_service.py) — the
-- ride↔driver link itself is fine. What's missing is a durable, queryable
-- record of "which old ID(s) does Spinr UUID X correspond to?" — today that
-- question can only be answered by re-deriving the phone-match by hand.
--
-- This table is that record: one row per legacy entity (driver or rider),
-- carrying whichever old ID(s) applied to it, pointing at the users/drivers
-- row the import resolved it to.
--
-- SCHEMA ONLY — NO BACKFILL in this migration. Per the CR's own risk note:
-- populating this from today's Supabase-side data alone would just re-encode
-- the existing phone-match, not add real cross-referencing value. Backfill
-- is deferred until the fresh old-app export (ACTION_ITEMS.md A34) lands
-- with the actual source files (Mongo `_id`s / numeric driver IDs / phone),
-- per docs/audit/2026-08-15-dual-run-cutover/P2-migration-completeness.md:37.
--
-- Columns:
--   old_mongo_object_id   — booking export's driver/customer ObjectId
--                            (rides.legacy_import_metadata namespace).
--                            Nullable: a rider imported only via a booking
--                            row still gets one; a driver row sourced solely
--                            from the driver CSV (never appeared in a
--                            booking) would not.
--   old_numeric_driver_id — Saskatoon driver CSV's 10-digit ID
--                            (drivers.legacy_import_metadata namespace).
--                            Nullable: never populated for entity_type =
--                            'rider'; riders have no equivalent CSV.
--   spinr_user_id         — the resolved Spinr row: drivers.id when
--                            entity_type = 'driver', users.id when
--                            entity_type = 'rider'. TEXT to match
--                            users.id/drivers.id (both TEXT PRIMARY KEY,
--                            application-generated UUID strings — see
--                            backend/supabase_schema.sql; NOT the native
--                            Postgres UUID type). No FK constraint: the
--                            target table depends on entity_type, which
--                            Postgres cannot express as a single declarative
--                            FK. Referential integrity for this column is
--                            enforced by the importer, same as
--                            legacy_import_metadata's own driver/customer ID
--                            fields today.
--   entity_type           — 'driver' or 'rider'. Picks which table
--                            spinr_user_id points into.
--   batch                 — import batch tag, same convention as
--                            rides/drivers.legacy_import_metadata->>'batch'
--                            (e.g. driver_import_service.py's --batch /
--                            booking_import_service.py's --batch). Lets a
--                            bad batch be identified and rolled back without
--                            touching rows from other batches.
--   imported_at            — when this crosswalk row was written (not when
--                            the underlying legacy entity was created).
--
-- IDs only, no PII per the audit's own design sketch (P2-migration-
-- completeness.md:37: "IDs only, no PII, RLS service-role-only").
--
-- RLS: user-linked data (even ID-only) → RLS ENABLED with NO policies,
-- which denies all anon/authenticated access; only the backend service role
-- (which bypasses RLS by design) reads/writes it. Same pattern as
-- 190_marketing_consents.sql. The frontend anon key must never touch this
-- table directly.
--
-- Forward-compatible: pure CREATE TABLE / CREATE INDEX, safe under live
-- traffic. No money columns. No write path from any existing table/route —
-- this migration only adds a new, currently-empty table.
--
-- Rollback:
--   DROP TABLE IF EXISTS public.legacy_id_crosswalk;
-- Safe as a plain DROP: no data exists yet (schema-only migration, backfill
-- deferred — see header above) and nothing else reads or writes this table
-- yet. Once backfilled, per the audit's stated design, reversibility for a
-- bad batch is row-level first: DELETE FROM legacy_id_crosswalk WHERE batch
-- = '<batch>', not a full table drop.

CREATE TABLE IF NOT EXISTS public.legacy_id_crosswalk (
    id                     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    old_mongo_object_id    TEXT,
    old_numeric_driver_id  TEXT,
    spinr_user_id          TEXT        NOT NULL,
    entity_type            TEXT        NOT NULL CHECK (entity_type IN ('driver', 'rider')),
    batch                  TEXT        NOT NULL,
    imported_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT legacy_id_crosswalk_has_an_old_id CHECK (
        old_mongo_object_id IS NOT NULL OR old_numeric_driver_id IS NOT NULL
    )
);

-- Queried by old ID to find the new UUID (the table's whole reason to
-- exist) — partial indexes since both old-ID columns are frequently NULL.
CREATE INDEX IF NOT EXISTS legacy_id_crosswalk_mongo_object_id_idx
    ON public.legacy_id_crosswalk (old_mongo_object_id)
    WHERE old_mongo_object_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS legacy_id_crosswalk_numeric_driver_id_idx
    ON public.legacy_id_crosswalk (old_numeric_driver_id)
    WHERE old_numeric_driver_id IS NOT NULL;

-- Queried the other direction too: "what old IDs map to this Spinr UUID?"
CREATE INDEX IF NOT EXISTS legacy_id_crosswalk_spinr_user_id_idx
    ON public.legacy_id_crosswalk (spinr_user_id);

-- "Which rows came from batch X" — for the row-level rollback path above.
CREATE INDEX IF NOT EXISTS legacy_id_crosswalk_batch_idx
    ON public.legacy_id_crosswalk (batch);

ALTER TABLE public.legacy_id_crosswalk ENABLE ROW LEVEL SECURITY;
-- Intentionally no policies: service-role-only (backend). Locked to anon/auth.

COMMENT ON TABLE public.legacy_id_crosswalk IS
    'Old-app identity crosswalk: maps the old app''s two incompatible driver-ID namespaces (Mongo ObjectId from the booking export, 10-digit numeric ID from the Saskatoon driver CSV) plus rider Mongo ObjectIds to Spinr''s own users/drivers UUIDs. IDs only, no PII. Schema shipped in migration 328; backfill deferred until a fresh old-app export lands (ACTION_ITEMS.md A34) — see CR #4106. Service-role-only (RLS enabled, no policies).';
