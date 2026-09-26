-- CONCURRENCY-001: admin-edit-only freshness marker for drivers.
-- PUT /admin/drivers/{id} (routes/admin/drivers.py admin_update_driver) locks
-- an edit on this column so two admins cannot silently overwrite each other.
-- It cannot use drivers.updated_at: a driver's own location pings bump that
-- about every 3 s while online, and utils/stale_intent_reconciler.py relies on
-- it as a liveness signal, so every edit of an online driver would be a false
-- "changed by someone else".
-- Written only by admin_update_driver. NULL = no admin edit since this
-- migration. Nullable, no default, no backfill: a metadata-only ALTER with no
-- table rewrite. Existing RLS/grants on drivers are unchanged. No index: the
-- lock filter is always combined with the primary key.
-- Rollback: first deploy a backend without admin_edited_at (it writes the
-- column on every admin driver save), then:
-- ALTER TABLE public.drivers DROP COLUMN IF EXISTS admin_edited_at;
SET lock_timeout = '5s';
ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS admin_edited_at TIMESTAMPTZ;
RESET lock_timeout;
COMMENT ON COLUMN public.drivers.admin_edited_at IS
    'Last save through PUT /admin/drivers/{id}; optimistic-lock marker for '
    'concurrent admin edits (CONCURRENCY-001). Never written by driver traffic.';
