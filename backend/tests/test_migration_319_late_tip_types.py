"""Migration-SQL contract tests for backend/migrations/319_late_tip_debit_types.sql.

Same style as test_corporate_rpc_ride_idempotency.py's
TestMigrationRideIdempotencyContract: parses the migration file directly so
a future edit that drops the per-member allowance-cap guard for the new
'late_tip_debit' type, or reverts the CHECK-constraint widening, fails here
even with no database involved.

Requested by spinr-corporate-billing-reviewer (2026-08-17): "No SQL-contract
test pins the widened per-member cap check ... A future edit that drops
'late_tip_debit' from that IN (...) list would silently let late tips
bypass the per-employee allowance cap, with nothing catching it in CI" —
the precedent this mirrors, test_corporate_rpc_ride_idempotency.py's
test_allowance_rpc_restores_per_member_cap_guard, exists for exactly the
same reason (a hand-ported Python replica of the algorithm "would keep
passing even if this guard vanished from the actual function again").
"""

from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.unit

_MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "319_late_tip_debit_types.sql"


class TestMigration319Contract:
    @pytest.fixture(autouse=True)
    def _sql(self):
        self.sql = _MIGRATION.read_text()

    def test_migration_override_annotation_present(self):
        """This migration redefines corporate_allowance_apply_delta (first
        defined in 29, last redefined in 297) — the repo's CI guardrail
        (Check for CREATE OR REPLACE conflicts across migrations) fails the
        PR without this annotation. Pinned here so it can't be silently
        dropped in a later edit of this file."""
        assert "migration-override-ok" in self.sql.lower()

    def test_wallet_transactions_check_gains_late_tip_debit(self):
        assert "ALTER TABLE wallet_transactions" in self.sql
        assert "'late_tip_debit'" in self.sql

    def test_corporate_wallet_transactions_check_gains_all_three_new_types(self):
        """Includes the bundled ride_debit_reversal bug fix (that type has
        been used by corporate_allowance_apply_delta since migration 248 but
        was never added to this table's CHECK constraint until here)."""
        # rindex, not index: the migration's own header comment quotes the
        # pre-319 ALTER statement as part of the rollback instructions, so
        # the first occurrence in the file is that comment, not the real
        # statement.
        alter_start = self.sql.rindex("ALTER TABLE corporate_wallet_transactions")
        alter_section = self.sql[alter_start : alter_start + 800]
        for value in ("'ride_debit_reversal'", "'late_tip_debit'", "'late_tip_adjustment'"):
            assert value in alter_section, f"{value} missing from the corporate_wallet_transactions CHECK widening"

    def test_allowance_rpc_whitelist_includes_late_tip_debit(self):
        assert (
            "IF p_type NOT IN ('allowance_grant','allowance_reset','allowance_rollback',"
            "'ride_debit','ride_debit_reversal','late_tip_debit') THEN" in self.sql
        )

    def test_late_tip_debit_has_ride_debit_identical_delta_math(self):
        """master -amount, used +amount — same as ride_debit; it IS a ride
        debit, just applied after settlement instead of during it."""
        branch_start = self.sql.index("ELSIF p_type = 'late_tip_debit' THEN")
        branch = self.sql[branch_start : branch_start + 150]
        assert "v_master_delta := -p_amount;" in branch
        assert "v_used_delta   := p_amount;" in branch

    def test_per_member_cap_guard_covers_late_tip_debit(self):
        """The migration-258/261/297 per-member allowance ceiling must cover
        the new type too — without this, a late tip bypasses the
        per-employee spending cap entirely."""
        cap_check = "IF p_type IN ('ride_debit', 'late_tip_debit') AND v_cap IS NOT NULL AND v_used_new > v_cap THEN"
        assert cap_check in self.sql
        # Same ordering requirement as the 297 contract test: the cap check
        # must run AFTER v_used_new is computed and BEFORE the ledger
        # writes (must block the write, not just log after the fact).
        used_new_pos = self.sql.index("v_used_new   := v_used + v_used_delta;")
        cap_pos = self.sql.index(cap_check)
        insert_pos = self.sql.index("INSERT INTO corporate_wallet_transactions")
        assert used_new_pos < cap_pos < insert_pos

    def test_function_signature_and_return_shape_unchanged_from_297(self):
        """CREATE OR REPLACE is only valid without a preceding DROP FUNCTION
        when neither the parameter list nor the RETURNS TABLE shape change
        — pin both so a future edit that widens either doesn't silently
        break deployment (Postgres would then require a DROP first, exactly
        as migration 297 itself needed vs. 277's differing signature)."""
        assert (
            "CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta(\n"
            "    p_wallet_id          UUID,\n"
            "    p_allowance_id       UUID,\n"
            "    p_member_id          UUID,\n"
            "    p_type               TEXT,\n"
            "    p_amount             NUMERIC(12,2),\n"
            "    p_actor_user_id      TEXT DEFAULT NULL,\n"
            "    p_notes              TEXT DEFAULT NULL,\n"
            "    p_floor              NUMERIC(12,2) DEFAULT NULL,\n"
            "    p_ride_id            UUID DEFAULT NULL\n"
            ")" in self.sql
        )
        assert (
            "RETURNS TABLE(\n"
            "    master_txn_id        UUID,\n"
            "    member_txn_id        UUID,\n"
            "    master_balance_after NUMERIC(12,2),\n"
            "    allowance_used_after NUMERIC(12,2),\n"
            "    deduped               BOOLEAN\n"
            ")" in self.sql
        )

    def test_security_definer_and_search_path_preserved(self):
        func_start = self.sql.index("CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta")
        func_section = self.sql[func_start : func_start + 700]
        assert "SECURITY DEFINER" in func_section
        assert "SET search_path = public, pg_temp" in func_section

    def test_rollback_comment_present(self):
        import re

        assert re.search(r"^--\s+[Rr]ollback:", self.sql, re.MULTILINE)
