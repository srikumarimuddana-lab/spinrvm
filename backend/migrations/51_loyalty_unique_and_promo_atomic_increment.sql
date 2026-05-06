-- P1-1: Partial unique index prevents double-awarding loyalty points for the
-- same ride. Only applies to ride_earned rows so other transaction types are
-- unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS uq_loyalty_user_ride
    ON loyalty_transactions (user_id, reference_id)
    WHERE type = 'ride_earned';

-- P1-2: Atomic conditional promo-uses increment. Returns TRUE if the row was
-- updated (promo still has capacity), FALSE if uses >= max_uses.
-- Callers should raise HTTP 409 on FALSE.
CREATE OR REPLACE FUNCTION increment_promo_uses(
    p_promo_id UUID,
    p_max_uses  INT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_updated INT;
BEGIN
    UPDATE promotions
       SET uses       = uses + 1,
           updated_at = NOW()
     WHERE id        = p_promo_id
       AND (p_max_uses <= 0 OR uses < p_max_uses);

    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN v_updated > 0;
END;
$$;
