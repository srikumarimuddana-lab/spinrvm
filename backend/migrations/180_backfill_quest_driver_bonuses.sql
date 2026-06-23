-- Migration 180: backfill driver_bonuses for already-claimed quests
--
-- Quests claimed BEFORE migration 179 credited the rider `wallets` table, not
-- the driver_bonuses ledger — so those rewards never folded into payable_balance
-- and were invisible/unpayable to the driver. Backfill a driver_bonuses row for
-- every already-claimed quest_progress that doesn't have one yet, so the money
-- becomes payable and shows in earnings/payout history.
--
-- The orphaned rider-wallet credit from the old path is left as-is: a driver's
-- wallet balance is never paid out (no driver wallet UI / cash-out), so it is
-- dead money, not a double-pay. The payout only draws from payable_balance,
-- which this backfill feeds exactly once per claim.
--
-- Idempotent: the NOT EXISTS guard + the unique (kind, source_id) index from
-- migration 179 make a re-run a no-op. Forward-compatible: a single bounded
-- INSERT...SELECT over claimed rows (small set); no long lock.
--
-- Rollback:
--   DELETE FROM driver_bonuses WHERE kind = 'quest' AND description LIKE 'Quest reward:%';
--   (or restore from backup) — only removes backfilled quest bonus rows.

INSERT INTO driver_bonuses (driver_id, user_id, amount, kind, source_id, description, created_at)
SELECT
    qp.driver_id,
    d.user_id,
    q.reward_amount,
    'quest',
    qp.id,
    'Quest reward: ' || q.title,
    COALESCE(qp.claimed_at, qp.updated_at, now())
FROM quest_progress qp
JOIN quests q   ON q.id = qp.quest_id
JOIN drivers d  ON d.id = qp.driver_id
WHERE qp.status = 'claimed'
  AND q.reward_amount IS NOT NULL
  AND NOT EXISTS (
        SELECT 1 FROM driver_bonuses db
        WHERE db.kind = 'quest' AND db.source_id = qp.id
  );
