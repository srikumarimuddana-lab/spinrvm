"""Allowance service tests — thin wrapper over the apply-delta RPC."""

from unittest.mock import MagicMock, patch

import pytest


def _rpc_ok():
    r = MagicMock()
    r.data = [
        {
            "master_txn_id": "t_m",
            "member_txn_id": "t_u",
            "master_balance_after": 900,
            "allowance_used_after": -100,
        }
    ]
    return r


@pytest.mark.asyncio
async def test_apply_grant_calls_rpc_with_correct_params():
    with patch("services.corporate_allowance_service.supabase") as mock_sb:
        mock_sb.rpc.return_value.execute.return_value = _rpc_ok()
        from services.corporate_allowance_service import apply_grant

        out = await apply_grant(
            wallet_id="w1",
            allowance_id="a1",
            member_id="m1",
            amount=100,
            actor_user_id="admin1",
            notes="monthly topup",
            floor=-50,
        )
    assert out["master_balance_after"] == 900
    called_name, called_params = mock_sb.rpc.call_args[0]
    assert called_name == "corporate_allowance_apply_delta"
    assert called_params["p_type"] == "allowance_grant"
    assert called_params["p_amount"] == "100"
    assert called_params["p_floor"] == "-50"


@pytest.mark.asyncio
async def test_apply_grant_rejects_non_positive():
    from services.corporate_allowance_service import apply_grant

    with pytest.raises(ValueError):
        await apply_grant(
            wallet_id="w1",
            allowance_id="a1",
            member_id="m1",
            amount=0,
            actor_user_id="admin1",
        )


@pytest.mark.asyncio
async def test_apply_reset_uses_zero_amount():
    with patch("services.corporate_allowance_service.supabase") as mock_sb:
        mock_sb.rpc.return_value.execute.return_value = _rpc_ok()
        from services.corporate_allowance_service import apply_reset

        await apply_reset(
            wallet_id="w1",
            allowance_id="a1",
            member_id="m1",
            actor_user_id="system",
        )
    _, params = mock_sb.rpc.call_args[0]
    assert params["p_type"] == "allowance_reset"
    assert params["p_amount"] == "0"


@pytest.mark.asyncio
async def test_apply_rollback_positive_delta():
    with patch("services.corporate_allowance_service.supabase") as mock_sb:
        mock_sb.rpc.return_value.execute.return_value = _rpc_ok()
        from services.corporate_allowance_service import apply_rollback

        await apply_rollback(
            wallet_id="w1",
            allowance_id="a1",
            member_id="m1",
            amount=50,
            actor_user_id="admin1",
            notes="refund grant",
        )
    _, params = mock_sb.rpc.call_args[0]
    assert params["p_type"] == "allowance_rollback"
    assert params["p_amount"] == "50"


@pytest.mark.asyncio
async def test_apply_ride_debit_uses_ride_debit_type():
    """Ride settlement must NOT use allowance_rollback — that type's master
    delta is positive, which credited the company on every allowance-covered
    ride instead of charging it (migration 248)."""
    with patch("services.corporate_allowance_service.supabase") as mock_sb:
        mock_sb.rpc.return_value.execute.return_value = _rpc_ok()
        from services.corporate_allowance_service import apply_ride_debit

        await apply_ride_debit(
            wallet_id="w1",
            allowance_id="a1",
            member_id="m1",
            amount=50,
            actor_user_id="rider1",
            notes="ride:r1:allowance",
        )
    _, params = mock_sb.rpc.call_args[0]
    assert params["p_type"] == "ride_debit"
    assert params["p_amount"] == "50"


@pytest.mark.asyncio
async def test_apply_ride_debit_rejects_non_positive():
    from services.corporate_allowance_service import apply_ride_debit

    with pytest.raises(ValueError):
        await apply_ride_debit(wallet_id="w1", allowance_id="a1", member_id="m1", amount=0)


@pytest.mark.asyncio
async def test_apply_ride_debit_reversal_uses_reversal_type():
    """Compensation must be the exact inverse of ride_debit. apply_grant would
    debit master a second time instead of refunding it."""
    with patch("services.corporate_allowance_service.supabase") as mock_sb:
        mock_sb.rpc.return_value.execute.return_value = _rpc_ok()
        from services.corporate_allowance_service import apply_ride_debit_reversal

        await apply_ride_debit_reversal(
            wallet_id="w1",
            allowance_id="a1",
            member_id="m1",
            amount=50,
            notes="ride:r1:allowance_compensation",
        )
    _, params = mock_sb.rpc.call_args[0]
    assert params["p_type"] == "ride_debit_reversal"
    assert params["p_amount"] == "50"


@pytest.mark.asyncio
async def test_apply_ride_debit_reversal_rejects_non_positive():
    from services.corporate_allowance_service import apply_ride_debit_reversal

    with pytest.raises(ValueError):
        await apply_ride_debit_reversal(wallet_id="w1", allowance_id="a1", member_id="m1", amount=0)


@pytest.mark.asyncio
async def test_apply_rollback_rejects_non_positive():
    from services.corporate_allowance_service import apply_rollback

    with pytest.raises(ValueError):
        await apply_rollback(wallet_id="w1", allowance_id="a1", member_id="m1", amount=0)


@pytest.mark.asyncio
async def test_apply_late_tip_debit_uses_late_tip_debit_type():
    """Late-tip debits (migration 319, PR #4047) must use a dedup key
    distinct from ride_debit — reusing 'ride_debit' for a late tip on an
    already-settled ride would silently collide with the RPC's ride-scoped
    idempotency check and apply zero additional money movement."""
    with patch("services.corporate_allowance_service.supabase") as mock_sb:
        mock_sb.rpc.return_value.execute.return_value = _rpc_ok()
        from services.corporate_allowance_service import apply_late_tip_debit

        await apply_late_tip_debit(
            wallet_id="w1",
            allowance_id="a1",
            member_id="m1",
            amount=5,
            actor_user_id="rider1",
            notes="ride:r1:late_tip_allowance",
            floor=0,
            ride_id="r1",
        )
    _, params = mock_sb.rpc.call_args[0]
    assert params["p_type"] == "late_tip_debit"
    assert params["p_amount"] == "5"
    assert params["p_ride_id"] == "r1"
    assert params["p_floor"] == "0"


@pytest.mark.asyncio
async def test_apply_late_tip_debit_rejects_non_positive():
    from services.corporate_allowance_service import apply_late_tip_debit

    with pytest.raises(ValueError, match="positive"):
        await apply_late_tip_debit(wallet_id="w1", allowance_id="a1", member_id="m1", amount=0, ride_id="r1")
    with pytest.raises(ValueError, match="positive"):
        await apply_late_tip_debit(wallet_id="w1", allowance_id="a1", member_id="m1", amount=-5, ride_id="r1")


@pytest.mark.asyncio
async def test_apply_raises_when_rpc_returns_no_row():
    """The RPC is defined RETURNS TABLE and always RETURN NEXTs exactly one
    row on success (see migration 258) — an empty response means something
    upstream (a bad wallet_id/allowance_id that somehow didn't RAISE, or a
    client/PostgREST-layer truncation) is silently dropping the result. Must
    fail loudly rather than let a caller dereference an empty dict for
    balances it never got, per CLAUDE.md's "don't silently swallow DB
    errors" rule."""
    with patch("services.corporate_allowance_service.supabase") as mock_sb:
        empty = MagicMock()
        empty.data = []
        mock_sb.rpc.return_value.execute.return_value = empty
        from services.corporate_allowance_service import apply_grant

        with pytest.raises(RuntimeError, match="allowance RPC returned no row"):
            await apply_grant(wallet_id="w1", allowance_id="a1", member_id="m1", amount=100)
