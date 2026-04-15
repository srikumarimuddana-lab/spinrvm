-- Migration 21: Rider Loyalty / Rewards Program
-- Points-based loyalty system with tiers and reward redemption

CREATE TABLE IF NOT EXISTS loyalty_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE,
    points INTEGER NOT NULL DEFAULT 0,
    lifetime_points INTEGER NOT NULL DEFAULT 0,
    tier TEXT NOT NULL DEFAULT 'bronze' CHECK (tier IN ('bronze', 'silver', 'gold', 'platinum')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS loyalty_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    points INTEGER NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('ride_earned', 'bonus', 'referral', 'redeemed', 'expired', 'tier_bonus')),
    reference_id TEXT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_loyalty_accounts_user ON loyalty_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_loyalty_txn_user ON loyalty_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_loyalty_txn_created ON loyalty_transactions(created_at DESC);

ALTER TABLE loyalty_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE loyalty_transactions ENABLE ROW LEVEL SECURITY;
