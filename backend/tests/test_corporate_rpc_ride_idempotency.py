"""Ride-scoped idempotency for the corporate money RPCs (migration 297).

corporate_wallet_apply_delta already deduped on stripe_payment_intent_id but
had no protection for the two INTERNAL debits settle_corporate makes per ride
(the allowance-covered debit, the master-wallet fallback debit) — a retried
settle_corporate call for the same ride could apply either delta twice.
corporate_allowance_apply_delta had no ride scoping at all.

This file has three layers:
  1. Service-layer unit tests: confirm ride_id is actually threaded into the
     RPC params dict (the fix is a no-op if the Python side doesn't pass it).
  2. settle_corporate-level tests: confirm ride_id reaches both underlying
     calls, and that a `deduped=True` RPC response doesn't break settlement.
  3. A migration-SQL contract test (same style as
     test_allowance_rpc_sign_contract.py): parses migration 297 directly so a
     future edit that removes the dedup check, or moves it back before the
     row lock, fails here even with no database involved.
"""

from __future__ import annotations

import pathlib
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "297_corporate_rpc_ride_idempotency.sql"


def _rpc_client(row: dict) -> MagicMock:
    """A minimal supabase-client stand-in: .rpc(name, params).execute() -> data=[row]."""
    client = MagicMock()
    builder = MagicMock()
    builder.execute = MagicMock(return_value=MagicMock(data=[row]))
    client.rpc = MagicMock(return_value=builder)
    return client


# ─────────────────────────────────────────────────────────────────────────────
# 1. Service-layer: ride_id actually reaches the RPC params.
# ─────────────────────────────────────────────────────────────────────────────


class TestCorporateWalletServiceRideIdThreading:
    async def test_apply_adjustment_passes_ride_id_to_rpc(self):
        from backend.services import corporate_wallet_service as svc

        client = _rpc_client({"transaction_id": "t1", "balance_after": "80.00", "deduped": False})
        with patch.object(svc, "supabase", client):
            await svc.apply_adjustment(
                wallet_id="w1",
                amount=Decimal("-20.00"),
                notes="Ride fallback debit ride_1",
                actor_user_id="user_1",
                floor=Decimal("0"),
                ride_id="ride_1",
            )

        client.rpc.assert_called_once()
        name, params = client.rpc.call_args.args
        assert name == "corporate_wallet_apply_delta"
        assert params["p_ride_id"] == "ride_1"

    async def test_apply_adjustment_without_ride_id_sends_none(self):
        """Ad-hoc admin adjustments (no specific ride) must not accidentally
        engage ride-scoped dedup — p_ride_id stays NULL, same as before this
        migration existed."""
        from backend.services import corporate_wallet_service as svc

        client = _rpc_client({"transaction_id": "t1", "balance_after": "80.00", "deduped": False})
        with patch.object(svc, "supabase", client):
            await svc.apply_adjustment(
                wallet_id="w1",
                amount=Decimal("50.00"),
                notes="support credit",
                actor_user_id="admin-001",
            )

        _, params = client.rpc.call_args.args
        assert params["p_ride_id"] is None

    async def test_apply_refund_already_threads_ride_id(self):
        """apply_refund took a ride_id parameter before this migration --
        confirm it now benefits from dedup automatically with no code change
        of its own."""
        from backend.services import corporate_wallet_service as svc

        client = _rpc_client({"transaction_id": "t1", "balance_after": "120.00", "deduped": False})
        with patch.object(svc, "supabase", client):
            await svc.apply_refund(wallet_id="w1", amount=Decimal("20.00"), ride_id="ride_9")

        _, params = client.rpc.call_args.args
        assert params["p_ride_id"] == "ride_9"


class TestCorporateAllowanceServiceRideIdThreading:
    async def test_apply_ride_debit_passes_ride_id_to_rpc(self):
        from backend.services import corporate_allowance_service as svc

        row = {
            "master_txn_id": "mt1",
            "member_txn_id": "et1",
            "master_balance_after": "80.00",
            "allowance_used_after": "20.00",
            "deduped": False,
        }
        client = _rpc_client(row)
        with patch.object(svc, "supabase", client):
            await svc.apply_ride_debit(
                wallet_id="w1",
                allowance_id="a1",
                member_id="m1",
                amount=Decimal("20.00"),
                actor_user_id="user_1",
                notes="ride:ride_1:allowance",
                floor=Decimal("0"),
                ride_id="ride_1",
            )

        name, params = client.rpc.call_args.args
        assert name == "corporate_allowance_apply_delta"
        assert params["p_ride_id"] == "ride_1"
        assert params["p_type"] == "ride_debit"

    async def test_apply_ride_debit_reversal_passes_ride_id_to_rpc(self):
        from backend.services import corporate_allowance_service as svc

        row = {
            "master_txn_id": "mt2",
            "member_txn_id": "et2",
            "master_balance_after": "100.00",
            "allowance_used_after": "0.00",
            "deduped": False,
        }
        client = _rpc_client(row)
        with patch.object(svc, "supabase", client):
            await svc.apply_ride_debit_reversal(
                wallet_id="w1",
                allowance_id="a1",
                member_id="m1",
                amount=Decimal("20.00"),
                notes="ride:ride_1:allowance_compensation",
                ride_id="ride_1",
            )

        _, params = client.rpc.call_args.args
        assert params["p_ride_id"] == "ride_1"
        assert params["p_type"] == "ride_debit_reversal"

    async def test_apply_grant_omits_ride_id_key_entirely(self):
        """Non-ride-scoped calls (grant/reset/rollback) must not send
        p_ride_id AT ALL, not even as null -- corporate_allowance_apply_delta
        gained this parameter in migration 297, so a Supabase instance that
        hasn't had 297 applied yet doesn't recognize the key. Sending it
        (even as None) makes PostgREST fail to resolve the function for
        EVERY allowance call, including grant/reset/rollback, until the
        migration lands. Omitting the key when unset keeps those three types
        working regardless of migration/deploy ordering."""
        from backend.services import corporate_allowance_service as svc

        row = {
            "master_txn_id": "mt3",
            "member_txn_id": "et3",
            "master_balance_after": "100.00",
            "allowance_used_after": "-30.00",
            "deduped": False,
        }
        client = _rpc_client(row)
        with patch.object(svc, "supabase", client):
            await svc.apply_grant(wallet_id="w1", allowance_id="a1", member_id="m1", amount=Decimal("30.00"))

        _, params = client.rpc.call_args.args
        assert "p_ride_id" not in params

    async def test_apply_ride_debit_includes_ride_id_key(self):
        """The inverse of the above: ride_debit DOES need the migration
        applied first, since it always sends a real ride_id. This is the
        residual deploy-ordering requirement documented in the Change
        Impact Log — not something the Python layer alone can fully close."""
        from backend.services import corporate_allowance_service as svc

        row = {
            "master_txn_id": "mt1",
            "member_txn_id": "et1",
            "master_balance_after": "80.00",
            "allowance_used_after": "20.00",
            "deduped": False,
        }
        client = _rpc_client(row)
        with patch.object(svc, "supabase", client):
            await svc.apply_ride_debit(
                wallet_id="w1",
                allowance_id="a1",
                member_id="m1",
                amount=Decimal("20.00"),
                ride_id="ride_1",
            )

        _, params = client.rpc.call_args.args
        assert params["p_ride_id"] == "ride_1"


# ─────────────────────────────────────────────────────────────────────────────
# 2. settle_corporate: ride_id reaches both underlying calls; a deduped retry
#    doesn't break settlement.
# ─────────────────────────────────────────────────────────────────────────────

_RIDE = {
    "id": "ride_1",
    "rider_id": "rider_1",
    "corporate_account_id": "company_1",
    "corporate_member_id": "member_1",
}


def _member():
    return {"id": "member_1", "company_id": "company_1", "status": "active", "user_id": "rider_1"}


@pytest.mark.anyio
class TestSettleCorporateRideIdThreading:
    async def test_allowance_debit_call_carries_ride_id(self):
        from backend.services.payment_service import settle_corporate

        apply_ride_debit = AsyncMock(return_value={"master_txn_id": "mt", "member_txn_id": "et", "deduped": False})
        with (
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
                AsyncMock(return_value=_member()),
            ),
            patch(
                "backend.services.payment_service.db_supabase.get_member_allowance",
                AsyncMock(return_value={"id": "allow_1", "type": "fixed_recurring", "amount": "50.00", "used": "0.00"}),
            ),
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
                AsyncMock(return_value={"id": "wallet_1"}),
            ),
            patch("backend.services.payment_service.db_supabase.get_corporate_policy", AsyncMock(return_value={})),
            patch("backend.services.payment_service.db_supabase.insert_one", AsyncMock()),
            patch("backend.services.payment_service.db_supabase.update_ride", AsyncMock()),
            patch("backend.services.payment_service.corporate_allowance_service.apply_ride_debit", apply_ride_debit),
            patch("backend.services.payment_service.evaluate_policy", lambda *a, **k: {"pass": True}),
        ):
            result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

        assert result.success is True
        assert apply_ride_debit.await_args.kwargs["ride_id"] == "ride_1"

    async def test_master_fallback_debit_call_carries_ride_id(self):
        from backend.services.payment_service import settle_corporate

        apply_adjustment = AsyncMock(return_value={"transaction_id": "t1", "balance_after": "0.00", "deduped": False})
        with (
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
                AsyncMock(return_value=_member()),
            ),
            patch(
                "backend.services.payment_service.db_supabase.get_member_allowance",
                # Unlimited=False, amount=0 -> allowance_debit resolves to 0, full fare falls to master.
                AsyncMock(return_value={"id": "allow_1", "type": "fixed_recurring", "amount": "0.00", "used": "0.00"}),
            ),
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
                AsyncMock(return_value={"id": "wallet_1"}),
            ),
            patch("backend.services.payment_service.db_supabase.get_corporate_policy", AsyncMock(return_value={})),
            patch("backend.services.payment_service.db_supabase.insert_one", AsyncMock()),
            patch("backend.services.payment_service.db_supabase.update_ride", AsyncMock()),
            patch("backend.services.payment_service.corporate_wallet_service.apply_adjustment", apply_adjustment),
            patch("backend.services.payment_service.evaluate_policy", lambda *a, **k: {"pass": True}),
        ):
            result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

        assert result.success is True
        apply_adjustment.assert_awaited_once()
        assert apply_adjustment.await_args.kwargs["ride_id"] == "ride_1"

    async def test_deduped_retry_of_both_debits_still_settles_successfully(self):
        """The actual re-drive scenario the audit flagged: settle_corporate
        called twice for the same ride. On the second call the RPC layer
        reports deduped=True for both debits instead of raising or
        double-charging -- settlement must still complete successfully."""
        from backend.services.payment_service import settle_corporate

        apply_ride_debit = AsyncMock(return_value={"master_txn_id": "mt", "member_txn_id": "et", "deduped": True})
        apply_adjustment = AsyncMock(return_value={"transaction_id": "t1", "balance_after": "80.00", "deduped": True})
        with (
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
                AsyncMock(return_value=_member()),
            ),
            patch(
                "backend.services.payment_service.db_supabase.get_member_allowance",
                AsyncMock(return_value={"id": "allow_1", "type": "fixed_recurring", "amount": "10.00", "used": "0.00"}),
            ),
            patch(
                "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
                AsyncMock(return_value={"id": "wallet_1"}),
            ),
            patch("backend.services.payment_service.db_supabase.get_corporate_policy", AsyncMock(return_value={})),
            patch("backend.services.payment_service.db_supabase.insert_one", AsyncMock()),
            patch("backend.services.payment_service.db_supabase.update_ride", AsyncMock()),
            patch("backend.services.payment_service.corporate_allowance_service.apply_ride_debit", apply_ride_debit),
            patch("backend.services.payment_service.corporate_wallet_service.apply_adjustment", apply_adjustment),
            patch("backend.services.payment_service.evaluate_policy", lambda *a, **k: {"pass": True}),
        ):
            # $20 fare, $10 allowance -> $10 allowance debit + $10 master fallback.
            result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

        assert result.success is True
        apply_ride_debit.assert_awaited_once()
        apply_adjustment.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Migration-SQL contract: dedup exists, runs after the lock, on both RPCs.
# ─────────────────────────────────────────────────────────────────────────────


class TestMigrationRideIdempotencyContract:
    @pytest.fixture(autouse=True)
    def _sql(self):
        self.sql = _MIGRATION.read_text()

    def test_wallet_rpc_drops_old_signature_before_recreating(self):
        assert "DROP FUNCTION IF EXISTS corporate_wallet_apply_delta(" in self.sql
        assert "DROP FUNCTION IF EXISTS corporate_allowance_apply_delta(" in self.sql

    def test_wallet_rpc_returns_deduped_column(self):
        wallet_start = self.sql.index("CREATE OR REPLACE FUNCTION corporate_wallet_apply_delta")
        wallet_end = self.sql.index("CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta")
        wallet_body = self.sql[wallet_start:wallet_end]
        assert "RETURNS TABLE(transaction_id UUID, balance_after NUMERIC(12,2), deduped BOOLEAN)" in wallet_body
        # Dedup check runs AFTER the row lock, not before (closes the TOCTOU
        # window migration 249's own comment on corporate_wallet_apply_delta
        # called out).
        lock_pos = wallet_body.index("FOR UPDATE")
        dedup_pos = wallet_body.index("Idempotency short-circuit #1")
        assert dedup_pos > lock_pos, "dedup check must run after the FOR UPDATE lock, not before"
        assert "p_ride_id IS NOT NULL" in wallet_body

    def test_allowance_rpc_gains_ride_id_param_and_deduped_column(self):
        allowance_start = self.sql.index("CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta")
        allowance_body = self.sql[allowance_start:]
        assert "p_ride_id            UUID DEFAULT NULL" in allowance_body
        assert "deduped               BOOLEAN" in allowance_body
        # Both locks (master wallet, then allowance) must appear before the
        # dedup check -- deterministic lock order was already established by
        # migration 214/29 to prevent deadlock; the new dedup check must not
        # disturb that ordering.
        wallet_lock_pos = allowance_body.index("FROM corporate_wallets")
        allowance_lock_pos = allowance_body.index("FROM corporate_member_allowances")
        dedup_pos = allowance_body.index("Idempotency short-circuit")
        assert wallet_lock_pos < allowance_lock_pos < dedup_pos

    def test_allowance_rpc_preserves_277_sign_contract_verbatim(self):
        """This migration must not silently change the type->delta mapping
        while adding idempotency -- that's a separate concern (and migration
        277's own dedicated contract test already locks the mapping via its
        own file). Spot-check the two load-bearing lines survived the copy."""
        allowance_start = self.sql.index("CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta")
        allowance_body = self.sql[allowance_start:]
        assert "v_master_delta := 0;\n        v_used_delta   := -p_amount;" in allowance_body, (
            "allowance_grant must stay a pure limit raise (migration 277) -- a nonzero "
            "master delta here double-charges the company on a grant-funded ride"
        )

    def test_allowance_rpc_restores_per_member_cap_guard(self):
        """The migration-258/261 per-member allowance ceiling was silently
        dropped by migration 277 (confirmed by the spinr-migration-reviewer
        audit that reviewed this file) and is restored here. This is the
        real-SQL cross-check for test_corporate_allowance_cap_race.py, which
        only tests a hand-ported copy of the algorithm and would keep
        passing even if this guard vanished from the actual function again."""
        allowance_start = self.sql.index("CREATE OR REPLACE FUNCTION corporate_allowance_apply_delta")
        allowance_body = self.sql[allowance_start:]
        assert "v_cap            NUMERIC(12,2);" in allowance_body, "v_cap must be declared"
        assert "SELECT used, amount INTO v_used, v_cap" in allowance_body, (
            "the ceiling must be read under the same row lock as `used` -- reading it "
            "separately (or not at all) reopens the migration-258 race"
        )
        assert "allowance_cap_exceeded" in allowance_body
        cap_check = "IF p_type = 'ride_debit' AND v_cap IS NOT NULL AND v_used_new > v_cap THEN"
        assert cap_check in allowance_body
        # The cap check must run AFTER v_used_new is computed (needs the
        # post-delta value) and BEFORE the ledger writes (must block the
        # write, not just log after the fact).
        used_new_pos = allowance_body.index("v_used_new   := v_used + v_used_delta;")
        cap_pos = allowance_body.index(cap_check)
        insert_pos = allowance_body.index("INSERT INTO corporate_wallet_transactions")
        assert used_new_pos < cap_pos < insert_pos

    def test_grants_reapplied_for_new_signatures(self):
        assert (
            "GRANT EXECUTE ON FUNCTION corporate_wallet_apply_delta(UUID, TEXT, TEXT, NUMERIC, UUID, UUID, TEXT, TEXT, TEXT, NUMERIC)\n"
            "    TO service_role;" in self.sql
        )
        assert (
            "GRANT EXECUTE ON FUNCTION corporate_allowance_apply_delta(UUID, UUID, UUID, TEXT, NUMERIC, TEXT, TEXT, NUMERIC, UUID)\n"
            "    TO service_role;" in self.sql
        )
        assert "REVOKE EXECUTE ON FUNCTION corporate_wallet_apply_delta" in self.sql
        assert "REVOKE EXECUTE ON FUNCTION corporate_allowance_apply_delta" in self.sql
