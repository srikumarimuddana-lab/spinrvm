-- Migration 19: In-App Wallet System
-- Supports rider/driver wallets with top-up, debit, and balance tracking

-- Wallet table: one per user (rider or driver)
CREATE TABLE IF NOT EXISTS wallets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    balance NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    currency TEXT NOT NULL DEFAULT 'CAD',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT wallets_user_id_unique UNIQUE (user_id),
    CONSTRAINT wallets_balance_non_negative CHECK (balance >= 0)
);

-- Wallet transactions ledger: immutable append-only log
CREATE TABLE IF NOT EXISTS wallet_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_id UUID NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('top_up', 'ride_payment', 'ride_refund', 'bonus', 'referral', 'cashout', 'fare_split_received', 'fare_split_sent', 'quest_reward')),
    amount NUMERIC(12,2) NOT NULL,
    balance_after NUMERIC(12,2) NOT NULL,
    reference_id TEXT,  -- ride_id, promo_id, quest_id, etc.
    description TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fare splits table: tracks split requests between riders
CREATE TABLE IF NOT EXISTS fare_splits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ride_id UUID NOT NULL,
    requester_id UUID NOT NULL,  -- rider who initiated the split
    total_fare NUMERIC(12,2) NOT NULL,
    split_count INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'partial', 'completed', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Individual split participants
CREATE TABLE IF NOT EXISTS fare_split_participants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fare_split_id UUID NOT NULL REFERENCES fare_splits(id) ON DELETE CASCADE,
    user_id UUID,  -- NULL if invited by phone (not yet registered)
    phone TEXT,     -- phone number of invited participant
    share_amount NUMERIC(12,2) NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'declined', 'paid')),
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for wallet lookups
CREATE INDEX IF NOT EXISTS idx_wallets_user_id ON wallets(user_id);
CREATE INDEX IF NOT EXISTS idx_wallet_txn_wallet_id ON wallet_transactions(wallet_id);
CREATE INDEX IF NOT EXISTS idx_wallet_txn_user_id ON wallet_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_wallet_txn_created_at ON wallet_transactions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fare_splits_ride_id ON fare_splits(ride_id);
CREATE INDEX IF NOT EXISTS idx_fare_splits_requester ON fare_splits(requester_id);
CREATE INDEX IF NOT EXISTS idx_fare_split_participants_split ON fare_split_participants(fare_split_id);
CREATE INDEX IF NOT EXISTS idx_fare_split_participants_user ON fare_split_participants(user_id);

-- RLS policies
ALTER TABLE wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE wallet_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE fare_splits ENABLE ROW LEVEL SECURITY;
ALTER TABLE fare_split_participants ENABLE ROW LEVEL SECURITY;
