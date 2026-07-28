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
    # Money values cross the JSON boundary as decimal strings (Audit-17 P0-1).
    # Postgres' numeric type accepts string literals losslessly.
    assert params["p_delta"] == "100.00"
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

        a = await apply_topup(wallet_id="w1", amount=100, stripe_payment_intent_id="pi_123")
        b = await apply_topup(wallet_id="w1", amount=100, stripe_payment_intent_id="pi_123")
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
    assert params["p_delta"] == "-25.00"
    assert params["p_floor"] == "-50.00"
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
        await apply_adjustment(wallet_id="w1", amount=0, notes="noop", actor_user_id="a1")


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
    assert params["p_delta"] == "10.00"
    assert params["p_ride_id"] == "ride_1"


@pytest.mark.asyncio
async def test_refund_rejects_non_positive_amount():
    from services.corporate_wallet_service import apply_refund

    with pytest.raises(ValueError, match="positive"):
        await apply_refund(wallet_id="w1", amount=0, ride_id="ride_1")
    with pytest.raises(ValueError, match="positive"):
        await apply_refund(wallet_id="w1", amount=-5, ride_id="ride_1")


@pytest.mark.asyncio
async def test_money_str_rounds_half_up_and_accepts_various_numeric_types():
    """Decimal rounding must be ROUND_HALF_UP, not banker's rounding, and
    the RPC boundary must accept int/float/str/Decimal inputs uniformly
    (Postgres numeric receives a lossless decimal string either way)."""
    rows = [{"transaction_id": "t4", "balance_after": "10.13"}]
    rpc = _build_rpc(rows)
    with patch("services.corporate_wallet_service.supabase") as mock_sb:
        mock_sb.rpc = rpc
        from services.corporate_wallet_service import apply_topup

        # 10.125 half-up should round to 10.13, not banker's-round to 10.12
        await apply_topup(wallet_id="w1", amount="10.125", stripe_payment_intent_id="pi_r1")
    params = rpc.call_args.args[1]
    assert params["p_delta"] == "10.13"


@pytest.mark.asyncio
async def test_adjustment_without_floor_omits_floor_param():
    rows = [{"transaction_id": "t5", "balance_after": "5.00"}]
    rpc = _build_rpc(rows)
    with patch("services.corporate_wallet_service.supabase") as mock_sb:
        mock_sb.rpc = rpc
        from services.corporate_wallet_service import apply_adjustment

        await apply_adjustment(wallet_id="w1", amount=5, notes="bonus", actor_user_id="admin_1")
    params = rpc.call_args.args[1]
    assert params["p_floor"] is None


@pytest.mark.asyncio
async def test_apply_propagates_rpc_exception_without_swallowing():
    """A DB/RPC failure (e.g. lock timeout, constraint violation) must
    surface loudly, never be swallowed into a generic fallback (CLAUDE.md
    'do not silently swallow errors'). run_sync wraps the underlying
    exception in a DatabaseError (see repositories/_base.py) rather than
    letting it disappear — assert that wrapped error carries the original
    message so the root cause stays visible."""
    from utils.error_handling import DatabaseError

    builder = MagicMock()
    builder.execute = MagicMock(side_effect=RuntimeError("db unavailable"))
    rpc = MagicMock(return_value=builder)
    with patch("services.corporate_wallet_service.supabase") as mock_sb:
        mock_sb.rpc = rpc
        from services.corporate_wallet_service import apply_topup

        with pytest.raises(DatabaseError) as exc_info:
            await apply_topup(wallet_id="w1", amount=100, stripe_payment_intent_id="pi_err")
    assert "db unavailable" in exc_info.value.details["original"]


@pytest.mark.asyncio
async def test_raises_when_rpc_returns_empty():
    rpc = _build_rpc([])
    with patch("services.corporate_wallet_service.supabase") as mock_sb:
        mock_sb.rpc = rpc
        from services.corporate_wallet_service import apply_topup

        with pytest.raises(RuntimeError, match="no row"):
            await apply_topup(wallet_id="w1", amount=100, stripe_payment_intent_id="pi_x")
