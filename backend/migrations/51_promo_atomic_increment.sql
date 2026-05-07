-- Migration: 51_promo_atomic_increment.sql
-- Purpose : Replace TOCTOU read-compute-write promo `uses` increment with an
--           atomic Postgres function.  Fixes P1-2.
-- Rollback: DROP FUNCTION promo_increment_uses; application falls back to the
--           previous non-atomic update (re-introduces the race).
-- Forward-compatible: additive only — no existing columns removed or renamed.

-- Atomically increments the uses counter for a promo and returns the new value.
-- Raises SQLSTATE 'P0002' if the promo does not exist or is inactive.
-- Raises SQLSTATE 'P0001' if max_uses has already been reached.
-- max_uses = 0 means unlimited.
CREATE OR REPLACE FUNCTION promo_increment_uses(p_promo_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_new_uses INTEGER;
BEGIN
    UPDATE promotions
       SET uses       = uses + 1,
           updated_at = now()
     WHERE id        = p_promo_id
       AND is_active  = TRUE
       AND (max_uses  = 0 OR uses < max_uses)
    RETURNING uses INTO v_new_uses;

    IF NOT FOUND THEN
        IF NOT EXISTS (SELECT 1 FROM promotions WHERE id = p_promo_id AND is_active = TRUE) THEN
            RAISE EXCEPTION 'promo not found or inactive: %', p_promo_id
                USING ERRCODE = 'P0002';
        ELSE
            RAISE EXCEPTION 'promo usage limit reached: %', p_promo_id
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    RETURN v_new_uses;
END;
$$;
