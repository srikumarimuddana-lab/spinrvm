-- 200_zoho_desk_ticket_service_area.sql
--
-- Rollback:
--   ALTER TABLE zoho_desk_tickets
--     DROP COLUMN IF EXISTS service_area_id,
--     DROP COLUMN IF EXISTS service_area_source,
--     DROP COLUMN IF EXISTS service_area_assigned_at,
--     DROP COLUMN IF EXISTS service_area_assigned_by;
--   DROP INDEX IF EXISTS idx_zdt_service_area;
--
-- Purpose: let the admin Help Desk attach a Spinr service area to a Zoho Desk
-- ticket. Zoho has no concept of our service areas, so this is stored only in
-- our local mirror. The ticket-sync upsert (utils/zoho_desk_sync.py) writes
-- only the columns produced by _map_ticket(), which does NOT include these —
-- so an assignment made here is preserved across every subsequent sync (a
-- PostgREST merge-upsert only updates the columns present in the payload).
--
-- Assignment is "auto" when the ticket's contact email resolves to a known
-- rider/driver whose most recent ride has a service area; otherwise the UI
-- highlights the ticket for a manual ("manual") assignment. Assignment is
-- never mandatory.
--
-- Backend-only table (RLS enabled, no policies -> anon/authenticated denied;
-- the service-role key bypasses RLS). Forward-compatible & idempotent; safe
-- against live traffic (additive nullable columns + a single new index).
--
-- ON DELETE SET NULL is safe here: service areas are soft-deleted (is_active
-- flag), not hard-deleted, so the cascading UPDATE on this mirror is not a
-- routine operation. A ticket simply loses its (advisory, non-mandatory) area
-- link if an area is ever truly removed.

ALTER TABLE zoho_desk_tickets
    ADD COLUMN IF NOT EXISTS service_area_id          TEXT
        REFERENCES service_areas(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS service_area_source      TEXT
        CHECK (service_area_source IS NULL OR service_area_source IN ('auto', 'manual')),
    ADD COLUMN IF NOT EXISTS service_area_assigned_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS service_area_assigned_by TEXT;   -- admin id, or 'system' for auto

-- Filtering tickets by service area (admin Help Desk).
CREATE INDEX IF NOT EXISTS idx_zdt_service_area ON zoho_desk_tickets (service_area_id);

NOTIFY pgrst, 'reload schema';
