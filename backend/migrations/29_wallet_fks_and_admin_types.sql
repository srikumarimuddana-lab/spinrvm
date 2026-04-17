-- Migration 29: Drop wrong-target wallet FK + expand transaction type allowlist
--
-- Problem 1 — FK mismatch:
--   wallets.user_id (UUID) was FK'd to auth.users(id). Spinr's phone-OTP
--   riders only exist in public.users — they never get an auth.users row,
--   so admin-side wallet creates fail with:
--     "insert or update on table wallets violates foreign key constraint
--      wallets_user_id_fkey"
--
--   We can't just retarget the FK: public.users.id is TEXT (stores
--   uuid-formatted strings but is typed TEXT), so Postgres rejects any FK
--   between UUID and TEXT columns with 42804 "incompatible types".
--
--   Fix: drop the FK entirely. Referential integrity moves to the app
--   layer — every admin wallet handler already calls
--   db_supabase.get_user_by_id() and returns 404 before writing, so
--   orphan wallets can't be created through the API.
--
-- Problem 2 — CHECK allowlist:
--   wallet_transactions.type CHECK predates the admin panel. 'admin_credit'
--   and 'admin_debit' weren't on the allowlist, so the ledger insert would
--   23514 even after the FK is fixed.
--
-- Both statements are idempotent — safe to re-run.

BEGIN;

-- ── 1. Drop wallets.user_id → auth.users FK ──────────────────────
-- Keep the UNIQUE constraint and the column type (UUID) as-is; app layer
-- (backend/routes/admin/wallet.py and routes/wallet.py) validates the
-- user exists in public.users before touching this table.
ALTER TABLE wallets DROP CONSTRAINT IF EXISTS wallets_user_id_fkey;

-- ── 2. Expand wallet_transactions.type allowlist ─────────────────
ALTER TABLE wallet_transactions DROP CONSTRAINT IF EXISTS wallet_transactions_type_check;

ALTER TABLE wallet_transactions
    ADD CONSTRAINT wallet_transactions_type_check
    CHECK (type IN (
        'top_up',
        'ride_payment',
        'ride_refund',
        'bonus',
        'referral',
        'cashout',
        'fare_split_received',
        'fare_split_sent',
        'quest_reward',
        'admin_credit',
        'admin_debit'
    ));

COMMIT;

-- After running this migration in Supabase SQL Editor, reload the PostgREST
-- schema cache so PGRST sees the new constraints:
--   NOTIFY pgrst, 'reload schema';
