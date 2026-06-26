/* 197_wallet_txn_reference_index.sql
 * Covering index for the wallet_apply_credit idempotency lookup added in
 * migration 196. That function dedups a top-up on
 *   WHERE wallet_id = ? AND reference_id = ? AND type = ?
 * inside a FOR UPDATE wallet-row lock. Without this index the lookup scans the
 * wallet's whole transaction history (idx_wallet_txn_wallet_id then in-memory
 * filter), lengthening the lock hold-time under high top-up volume.
 *
 * NON-unique and partial (reference_id IS NOT NULL) — a unique index could fail
 * to build against any pre-existing duplicate rows produced by the C6 bug, so
 * the authoritative dedup stays the lock-then-check in wallet_apply_credit. This
 * index is purely for lookup performance.
 *
 * Kept separate from 196 because CONCURRENTLY cannot run inside a transaction.
 * The runner (backend/scripts/migrate.py) applies any file containing
 * CONCURRENTLY in a per-statement autocommit path that does a raw split on the
 * semicolon character BEFORE stripping comments, so this comment block contains
 * NO semicolons at all (one inside would split it into a malformed chunk and the
 * migration would fail). Block-comment header (no "--" prefix) and no comment
 * before the CREATE so the first chunk starts with SQL.
 *
 * Rollback (no semicolon here either, same reason)
 *   DROP INDEX CONCURRENTLY IF EXISTS idx_wallet_txn_reference_lookup
 */
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wallet_txn_reference_lookup
    ON wallet_transactions (wallet_id, reference_id, type)
    WHERE reference_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';
