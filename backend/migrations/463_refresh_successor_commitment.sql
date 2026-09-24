-- T11 backend X8 / C10: refresh-token successor commitment (default OFF).
-- Adds a single settings flag. With it on, /auth/refresh accepts an optional
-- client-proposed successor token so a lost rotation response can be
-- recovered without tripping the reuse cascade (backend design A1/X8).
-- Needs a spinr-security-auditor pass before it is ever enabled; this PR
-- never enables it. Settings column only: no function, table or data change.
-- Rollback: ALTER TABLE public.settings DROP COLUMN IF EXISTS refresh_successor_commitment_enabled;
--           (or simply leave it false; false is the pre-migration behaviour).
BEGIN;
SET LOCAL lock_timeout = '2s';

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS refresh_successor_commitment_enabled boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN public.settings.refresh_successor_commitment_enabled IS
    'Dark gate (T11 X8): accept client-proposed refresh successors for lost-rotation recovery. Security audit required before enabling.';

COMMIT;
NOTIFY pgrst, 'reload schema';
