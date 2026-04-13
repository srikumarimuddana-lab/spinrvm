-- Migration 20: Quest / Bonus Challenge System
-- Gamified challenges for drivers to earn bonus rewards

-- Quest definitions (created by admins)
CREATE TABLE IF NOT EXISTS quests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('ride_count', 'earnings_target', 'online_hours', 'peak_rides', 'consecutive_days', 'rating_maintained')),
    target_value NUMERIC(12,2) NOT NULL,  -- e.g., 20 rides, $500 earnings, 8 hours
    reward_amount NUMERIC(12,2) NOT NULL, -- bonus payout in CAD
    reward_type TEXT NOT NULL DEFAULT 'cash' CHECK (reward_type IN ('cash', 'wallet_credit')),
    start_date TIMESTAMPTZ NOT NULL,
    end_date TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    max_participants INTEGER,  -- NULL = unlimited
    service_area_id UUID,      -- NULL = all areas
    min_driver_rating NUMERIC(3,2),  -- minimum rating to qualify
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Driver quest progress tracking
CREATE TABLE IF NOT EXISTS quest_progress (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quest_id UUID NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
    driver_id UUID NOT NULL,
    current_value NUMERIC(12,2) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'claimed', 'expired')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    claimed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT quest_progress_unique UNIQUE (quest_id, driver_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_quests_active ON quests(is_active, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_quests_type ON quests(type);
CREATE INDEX IF NOT EXISTS idx_quest_progress_driver ON quest_progress(driver_id);
CREATE INDEX IF NOT EXISTS idx_quest_progress_quest ON quest_progress(quest_id);
CREATE INDEX IF NOT EXISTS idx_quest_progress_status ON quest_progress(status);

-- RLS
ALTER TABLE quests ENABLE ROW LEVEL SECURITY;
ALTER TABLE quest_progress ENABLE ROW LEVEL SECURITY;
