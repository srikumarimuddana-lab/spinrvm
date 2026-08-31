-- 375: rollout flag for real ride-incentive eligibility enforcement.
--
-- Context: ride_incentives has carried start_date, end_date, max_budget,
-- budget_used, bonus_type and conditions since migration 96, and NOTHING
-- honoured any of them. `is_active` was the only gate on every code path,
-- display and settlement alike. Consequences in production today:
--
--   * a time_limited campaign whose end_date has passed keeps paying forever;
--   * a campaign capped at max_budget pays out without limit, while the admin
--     dashboard renders "Budget: $0 / $500" because budget_used was never
--     incremented by anything (grep: migration 96 declares the column and
--     nothing has ever written it);
--   * bonus_type='percentage' is paid as if the amount were dollars;
--   * a conditions.min_distance_km of 20 pays on a 2 km ride.
--
-- services/incentive_service.py now enforces all of it, but that changes what
-- drivers are PAID, so it ships dark per CLAUDE.md's flagged-rollout rule.
-- Flag off = byte-for-byte today's behaviour. Flip via admin settings without
-- a redeploy, verify on staging, then enable.
--
-- budget_used is now maintained as a denormalized mirror recomputed from the
-- ride_incentive_claims ledger after each claim (never incremented in place),
-- so it is self-healing and the admin budget bar becomes truthful. The ledger
-- stays the source of truth for the cap itself.
--
-- Rollback:
--   ALTER TABLE settings DROP COLUMN IF EXISTS incentive_eligibility_enforced;
--   DROP INDEX IF EXISTS idx_ride_incentive_claims_incentive_id;
--
-- Forward-compatible: additive defaulted column + additive index; older
-- backends ignore both.

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS incentive_eligibility_enforced boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN settings.incentive_eligibility_enforced IS
    'When true, ride-incentive matching honours start_date/end_date, the conditions '
    'JSONB, bonus_type=percentage and the max_budget cap. Default false preserves the '
    'pre-373 behaviour where is_active was the only gate. See migration 373.';

-- The budget check sums ride_incentive_claims by incentive_id on every match
-- for a capped incentive; the idempotency check reads by ride_id. Neither
-- column was indexed.
CREATE INDEX IF NOT EXISTS idx_ride_incentive_claims_incentive_id
    ON ride_incentive_claims (incentive_id);

CREATE INDEX IF NOT EXISTS idx_ride_incentive_claims_ride_id
    ON ride_incentive_claims (ride_id);
