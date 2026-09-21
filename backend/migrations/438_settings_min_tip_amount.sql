-- 438: Minimum tip amount setting.
--
-- WHY: riders could enter any custom tip above $0 (e.g. $0.05). On a card ride
-- a tip over the authorization buffer is charged separately, and Stripe
-- rejects any charge under $0.50 CAD, so a tiny tip silently failed: the
-- rider was never charged it and the driver never got it (ride
-- 0c24901f-..., 2026-09-21: "Amount must be at least $0.50 CAD").
--
-- min_tip_amount is the smallest NON-ZERO tip allowed (CAD). "No tip" ($0) is
-- always allowed. 0 turns the rule off without a deploy (CLAUDE.md release
-- gate 3: a new validation rule that rejects previously-valid input must be
-- switchable). Enforced server-side by utils/tip_policy.enforce_min_tip on
-- every tip entry point; the rider app reads it from GET /api/v1/settings.
--
-- ROLLOUT: ships OFF (default 0.00). Older rider-app builds don't pre-check,
-- so a rejected sub-minimum tip shows them a generic payment error and loses
-- their rating. Once the rider app that shows "Minimum tip is $1.00" is live
-- in the stores, set it on in admin Settings -> Operations -> Tips, or:
--   UPDATE settings SET min_tip_amount = 1.00 WHERE id = 'app_settings';
--
-- Additive, nullable-free with a default, so existing rows get 0.00 (off).
--
-- Rollback (preferred, no deploy): switch the rule off --
--   UPDATE settings SET min_tip_amount = 0 WHERE id = 'app_settings';
-- Only drop the column AFTER the backend code reading it is reverted: while
-- it is live, admin Settings saves send min_tip_amount and would fail with
-- PGRST204 (unknown column) --
--   ALTER TABLE settings DROP COLUMN IF EXISTS min_tip_amount;

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS min_tip_amount numeric(6, 2) NOT NULL DEFAULT 0.00;

DO $$
BEGIN
    ALTER TABLE settings
        ADD CONSTRAINT settings_min_tip_amount_range
        -- 0 = off; otherwise at least $0.50 (Stripe's minimum separate charge).
        CHECK (min_tip_amount = 0 OR (min_tip_amount >= 0.50 AND min_tip_amount <= 50));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

NOTIFY pgrst, 'reload schema';
