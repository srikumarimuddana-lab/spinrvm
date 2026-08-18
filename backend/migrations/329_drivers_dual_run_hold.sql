-- 329_drivers_dual_run_hold.sql
-- Purpose: CR-4104 (dual-run cutover collision risk, ACTION_ITEMS.md A34) —
--   add an admin-settable "still active on the old app" hold flag so an
--   operator can block a legacy-imported driver from going online (and, by
--   extension, from ever entering the dispatch pool) or being paid out
--   while both platforms run in parallel ahead of the tentative Oct 31,
--   2026 old-app decommission.
--
--   This migration is purely additive and defaults to a no-op: the column
--   defaults to FALSE for every existing and future row, and nothing in
--   this change (or the application code that reads it — see
--   backend/routes/drivers/status.py and backend/routes/drivers/payouts.py)
--   ever sets it to TRUE. The guard only engages once an operator manually
--   flips the flag for a specific driver. Roster-coordination ownership —
--   *who* tracks which imported drivers are still active on the old app and
--   flips this flag, and when — remains a separate, organization-owned
--   policy decision (CR-4104's own framing); this column only makes the
--   mechanism available, it does not activate any policy on its own.
--
-- Rollback:
--   ALTER TABLE public.drivers
--     DROP COLUMN IF EXISTS dual_run_hold;
--
-- Notes:
-- - Nullable-safe additive column with a NOT NULL DEFAULT FALSE — safe to
--   run against production traffic in flight (Postgres 11+ adds a
--   constant-default column as a metadata-only change, no table rewrite).
-- - No new index: the flag is only ever read off a driver row already
--   fetched by primary key (go-online, request_payout) — no new
--   `WHERE dual_run_hold = ?` or `ORDER BY` query pattern is introduced by
--   this change, so no index is needed per backend/migrations/CLAUDE.md's
--   "indexes for new query patterns" rule.
-- - Deliberately scoped to `drivers` only (not `users`) — the CR's own risk
--   mitigation scopes the guard to drivers carrying non-empty
--   `legacy_import_metadata` (migration 221), and the application-code
--   guard additionally requires that before this column has any effect.

ALTER TABLE public.drivers
  ADD COLUMN IF NOT EXISTS dual_run_hold BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.drivers.dual_run_hold IS
  'CR-4104 / A34 dual-run cutover guard. Admin-settable: TRUE means this '
  '(legacy-imported) driver is confirmed still active on the old app and '
  'must be blocked from go-online, dispatch, and payout in the new app. '
  'Only meaningful for drivers carrying non-empty legacy_import_metadata '
  '(migration 221) -- the guard code additionally requires that. Defaults '
  'to FALSE and is never set by any automated process; an operator must '
  'flip it manually via the admin dashboard/DB.';
