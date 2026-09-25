-- 473_instant_payout_daily_cap.sql
--
-- Per-driver daily cap (CAD) on instant payouts — ROADMAP N22.
--
-- Why: POST /api/drivers/payouts/instant (routes/drivers/payouts.py) can be
-- called by any authenticated driver, in every service area where
-- instant_payout_enabled is true, with no per-driver daily limit. Paired
-- with a stolen card "rider", that is the fast cash-out step of the
-- collusion pattern in docs/audit/clean-sheet/08-hostile-review.md §1.4
-- (TSF-002). On a 0 % commission platform there is no fare margin to absorb
-- the chargeback once the money has left.
--
-- Semantics: the most a single driver may move by instant payout per
-- calendar day, summed over the gross `payouts.amount` of today's instant
-- rows that moved (or are moving) money — statuses 'failed' and 'reversed'
-- are not counted. "Today" is the driver's service-area timezone
-- (service_areas.timezone), UTC if that is missing or invalid.
--
-- NULL (the default) means no cap — identical to behaviour before this
-- migration. The column ships dark; ops turns the cap on from
-- PUT /api/admin/settings (instant_payout_daily_cap_cad) without a deploy.
-- Money over the cap is not lost: it stays in the driver's balance and goes
-- out with the Sunday auto-payout.
--
-- Rollback:
--   UPDATE settings SET instant_payout_daily_cap_cad = NULL WHERE id = 'app_settings';
--     -- behavioural rollback, no deploy (the admin PUT drops None values,
--     -- so clearing the cap is this SQL, not the admin screen)
--   ALTER TABLE settings DROP COLUMN IF EXISTS instant_payout_daily_cap_cad;
--     -- the reader treats a missing key as NULL (no cap), so the drop is
--     -- safe against live code; remove the SettingsUpdateRequest field in
--     -- the same change or an admin save that sets it will 500 (PGRST204).

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS instant_payout_daily_cap_cad NUMERIC(10, 2)
        CHECK (instant_payout_daily_cap_cad IS NULL OR instant_payout_daily_cap_cad > 0);

COMMENT ON COLUMN public.settings.instant_payout_daily_cap_cad IS
    'Max CAD one driver may take by instant payout per service-area calendar day (gross amount; failed/reversed rows excluded). NULL = no cap. ROADMAP N22.';
