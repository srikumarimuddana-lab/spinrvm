"""Unit tests for corporate_wallet_service.

Uses sync MagicMock on supabase.rpc(...).execute() because the service
wraps the sync call with run_sync (see plan header re: supabase-py 2.x).
"""
from unittest.mock import MagicMock, patch

import pytest


def _build_rpc(rows):
    """Mock supabase.rpc(...).execute() returning the given rows."""
    resp = MagicMock(data=rows)
    builder = MagicMock()
    builder.execute = MagicMock(return_value=resp)
    rpc = MagicMock(return_value=builder)
    return rpc


@pytest.mark.asyncio
async def test_topup_calls_rpc_with_positive_delta():
    rows = [{"transaction_id": "t1", "balance_after": "100.00"}]
    rpc = _build_rpc(rows)
    with patch("services.corporate_wallet_service.supabase") as mock_sb:
        mock_sb.rpc = rpc
        from services.corporate_wallet_service import apply_topup

        result = await apply_topup(
            wallet_id="w1",
            amount=100,
            stripe_payment_intent_id="pi_123",
        )
    assert result["balance_after"] == "100.00"
    rpc.assert_called_once()
    fn_name, params = rpc.call_args.args
    assert fn_name == "corporate_wallet_apply_delta"
    assert params["p_delta"] == 100
    assert params["p_type"] == "topup"
    assert params["p_scope"] == "master"
    assert params["p_stripe_pi"] == "pi_123"


@pytest.mark.asyncio
async def test_idempotent_on_duplicate_stripe_pi():
    rows = [{"transaction_id": "t1", "balance_after": "100.00"}]
    rpc = _build_rpc(rows)
    with patch("services.corporate_wallet_service.supabase") as mock_sb:
        mock_sb.rpc = rpc
        from services.corporate_wallet_service import apply_topup

        a = await apply_topup(
            wallet_id="w1", amount=100, stripe_payment_intent_id="pi_123"
        )
        b = await apply_topup(
            wallet_id="w1", amount=100, stripe_payment_intent_id="pi_123"
        )
    assert a == b


@pytest.mark.asyncio
async def test_adjustment_routes_through_rpc_with_floor():
    rows = [{"transaction_id": "t2", "balance_after": "-25.00"}]
    rpc = _build_rpc(rows)
    with patch("services.corporate_wallet_service.supabase") as mock_sb:
        mock_sb.rpc = rpc
        from services.corporate_wallet_service import apply_adjustment

        await apply_adjustment(
            wallet_id="w1",
            amount=-25,
            notes="manual correction",
            actor_user_id="admin_1",
            floor=-50,
        )
    params = rpc.call_args.args[1]
    assert params["p_type"] == "adjustment"
    assert params["p_delta"] == -25
    assert params["p_floor"] == -50
    assert params["p_notes"] == "manual correction"
    assert params["p_actor_user_id"] == "admin_1"


@pytest.mark.asyncio
async def test_topup_rejects_non_positive_amount():
    from services.corporate_wallet_service import apply_topup

    with pytest.raises(ValueError, match="positive"):
        await apply_topup(wallet_id="w1", amount=0, stripe_payment_intent_id="pi")
    with pytest.raises(ValueError, match="positive"):
        await apply_topup(wallet_id="w1", amount=-5, stripe_payment_intent_id="pi")


@pytest.mark.asyncio
async def test_adjustment_rejects_zero_amount():
    from services.corporate_wallet_service import apply_adjustment

    with pytest.raises(ValueError, match="zero"):
        await apply_adjustment(
            wallet_id="w1", amount=0, notes="noop", actor_user_id="a1"
        )


@pytest.mark.asyncio
async def test_refund_routes_with_ride_id():
    rows = [{"transaction_id": "t3", "balance_after": "90.00"}]
    rpc = _build_rpc(rows)
    with patch("services.corporate_wallet_service.supabase") as mock_sb:
        mock_sb.rpc = rpc
        from services.corporate_wallet_service import apply_refund

        await apply_refund(
            wallet_id="w1",
            amount=10,
            ride_id="ride_1",
            actor_user_id="admin_1",
        )
    params = rpc.call_args.args[1]
    assert params["p_type"] == "refund"
    assert params["p_delta"] == 10
    assert params["p_ride_id"] == "ride_1"


@pytest.mark.asyncio
async def test_raises_when_rpc_returns_empty():
    rpc = _build_rpc([])
    with patch("services.corporate_wallet_service.supabase") as mock_sb:
        mock_sb.rpc = rpc
        from services.corporate_wallet_service import apply_topup

        with pytest.raises(RuntimeError, match="no row"):
            await apply_topup(
                wallet_id="w1", amount=100, stripe_payment_intent_id="pi_x"
            )
