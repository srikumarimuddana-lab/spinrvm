-- Rollback:
--   DROP FUNCTION IF EXISTS claim_promo_user_slot(uuid, text, int);
--   DROP FUNCTION IF EXISTS release_promo_user_slot(uuid, text);
--   DROP TABLE IF EXISTS promo_user_redemptions;
--
-- Closes the per-user promo-redemption TOCTOU race. The per-user cap
-- (promotions.max_uses_per_user, default 1) was enforced only by a
-- count_documents() read on promo_applications before the insert, and there is
-- no unique constraint backing it. N concurrent /promo/apply or /rides (with a
-- promo_code) calls each read user_uses=0, all pass, all insert — redeeming a
-- one-per-rider "first ride free" code N times.
--
-- A plain UNIQUE(user_id, promo_id) can't express "at most N" for N>1, so we
-- use an atomic per-(promo,user) counter row locked by ON CONFLICT DO UPDATE —
-- the same row-lock pattern as increment_promo_uses (migration 51) and the
-- wallet/allowance RPCs. Concurrent claims serialize on the row; the one that
-- pushes uses past the cap is compensated back and returns FALSE.

CREATE TABLE IF NOT EXISTS promo_user_redemptions (
    promo_id   uuid NOT NULL,
    user_id    text NOT NULL,
    uses       integer NOT NULL DEFAULT 0 CHECK (uses >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (promo_id, user_id)
);

-- RLS: backend-only counter table; service role bypasses. No user-facing reads.
ALTER TABLE promo_user_redemptions ENABLE ROW LEVEL SECURITY;

-- Atomically claim one per-user redemption slot. Returns TRUE if the user was
-- still under the cap (slot taken), FALSE if already at the cap.
-- p_max_per_user <= 0 means unlimited.
CREATE OR REPLACE FUNCTION claim_promo_user_slot(
    p_promo_id     uuid,
    p_user_id      text,
    p_max_per_user int
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_uses int;
BEGIN
    INSERT INTO promo_user_redemptions (promo_id, user_id, uses, updated_at)
    VALUES (p_promo_id, p_user_id, 1, now())
    ON CONFLICT (promo_id, user_id)
    DO UPDATE SET uses = promo_user_redemptions.uses + 1, updated_at = now()
    RETURNING uses INTO v_uses;

    IF p_max_per_user > 0 AND v_uses > p_max_per_user THEN
        -- Over the cap: compensate the increment we just made and refuse.
        UPDATE promo_user_redemptions
           SET uses = uses - 1, updated_at = now()
         WHERE promo_id = p_promo_id AND user_id = p_user_id;
        RETURN FALSE;
    END IF;
    RETURN TRUE;
END;
$$;

-- Release one previously-claimed slot — used to compensate when a LATER step of
-- the same redemption fails (e.g. the global-uses cap is exhausted), so a user
-- isn't charged a redemption for a promo they never actually got.
CREATE OR REPLACE FUNCTION release_promo_user_slot(
    p_promo_id uuid,
    p_user_id  text
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    UPDATE promo_user_redemptions
       SET uses = GREATEST(uses - 1, 0), updated_at = now()
     WHERE promo_id = p_promo_id AND user_id = p_user_id;
END;
$$;

REVOKE ALL ON FUNCTION claim_promo_user_slot(uuid, text, int) FROM anon, authenticated;
REVOKE ALL ON FUNCTION release_promo_user_slot(uuid, text) FROM anon, authenticated;
