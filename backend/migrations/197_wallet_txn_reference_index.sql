/* 197_wallet_txn_reference_index.sql
 * Covering index for the wallet_apply_credit idempotency lookup added in
 * migration 196. That function dedups a top-up on
 *   WHERE wallet_id = ? AND reference_id = ? AND type = ?
 * inside a FOR UPDATE wallet-row lock; without this index the lookup scans the
 * wallet's whole transaction history (idx_wallet_txn_wallet_id then in-memory
 * filter), lengthening the lock hold-time under high top-up volume.
 *
 * NON-unique + partial (reference_id IS NOT NULL): a unique index could fail to
 * build against any pre-existing duplicate rows produced by the C6 bug, so the
 * authoritative dedup stays the lock-then-check in wallet_apply_credit; this
 * index is purely for lookup performance.
 *
 * Kept separate from 196 because CONCURRENTLY cannot run inside a transaction;
 * the runner (backend/scripts/migrate.py) applies any file containing
 * CONCURRENTLY in a per-statement autocommit path. Block-comment header (no "--"
 * prefix) and no comment before the CREATE so the first chunk starts with SQL.
 *
 * Rollback (no semicolons here to avoid splitting this comment):
 *   DROP INDEX CONCURRENTLY IF EXISTS idx_wallet_txn_reference_lookup
 */
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wallet_txn_reference_lookup
    ON wallet_transactions (wallet_id, reference_id, type)
    WHERE reference_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';
