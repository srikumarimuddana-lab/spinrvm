"""Wallet-transfer RPC migration contract (227 / M5.2).

CI has no Postgres; pin the SQL contract textually. Semantics (floor breach,
idempotent replay, cross-company guard) get integration coverage when the
service wrapper lands in M5.4.
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL = (MIGRATIONS / "227_corporate_wallet_transfer_rpc.sql").read_text(encoding="utf-8")
SQL_214 = (MIGRATIONS / "214_corporate_actor_user_id_text.sql").read_text(encoding="utf-8")


class TestTypeCheck:
    def test_allocation_added_to_ledger_types(self):
        assert "'allocation'" in SQL
        # Existing types preserved.
        for t in ("'topup'", "'ride_debit'", "'refund'", "'adjustment'", "'allowance_grant'"):
            assert t in SQL

    def test_check_dropped_by_lookup_not_hardcoded_name(self):
        assert "pg_get_constraintdef" in SQL
        assert "DROP CONSTRAINT %I" in SQL


class TestApplyDeltaReFork:
    def test_floor_now_covers_section_scope(self):
        assert "p_scope = 'master' OR p_scope LIKE 'section:%'" in SQL

    def test_keeps_214_security_posture(self):
        # Same hardening as the authoritative 214 version.
        assert SQL.count("SECURITY DEFINER") >= 2
        assert SQL.count("SET search_path = public, pg_catalog") >= 2
        assert "p_actor_user_id      TEXT DEFAULT NULL" in SQL  # TEXT actor (214), not UUID

    def test_idempotency_short_circuit_preserved(self):
        assert "WHERE wt.stripe_payment_intent_id = p_stripe_pi" in SQL


class TestTransferRpc:
    def test_guards_amount_same_wallet_cross_company(self):
        assert "transfer_amount_invalid" in SQL
        assert "transfer_same_wallet" in SQL
        assert "transfer_cross_company" in SQL

    def test_locks_both_rows_in_id_order(self):
        # Deadlock safety: deterministic lock order regardless of direction.
        assert "IF p_from_wallet_id < p_to_wallet_id THEN" in SQL
        assert SQL.count("FOR UPDATE") >= 3  # apply_delta + both transfer locks

    def test_section_floor_derived_server_side(self):
        # Reviewer hardening: Q9 as a DB invariant — a section source is
        # floored at 0 no matter what the caller passes.
        assert "IF v_from.section_id IS NOT NULL THEN" in SQL
        assert "v_floor := 0;" in SQL
        # Master floor can be tightened by the caller but never loosened
        # below the wallet's own soft_negative_floor.
        assert "GREATEST(" in SQL

    def test_paired_legs_share_idempotency_key_with_suffixes(self):
        # ':out'/':in' suffixes let both legs coexist under migration 27's
        # partial unique index on stripe_payment_intent_id.
        assert "p_idempotency_key || ':out'" in SQL
        assert "p_idempotency_key || ':in'" in SQL
        # NULL key stays NULL (no 'NULL:out' collisions).
        assert "CASE WHEN p_idempotency_key IS NULL THEN NULL" in SQL

    def test_ledger_legs_are_allocation_type(self):
        assert SQL.count("'allocation', -p_amount") == 1
        assert SQL.count("'allocation', p_amount") == 1

    def test_lockdown_grants(self):
        assert "REVOKE EXECUTE ON FUNCTION corporate_wallet_transfer" in SQL
        assert "GRANT EXECUTE ON FUNCTION corporate_wallet_transfer" in SQL
        assert "TO service_role" in SQL
