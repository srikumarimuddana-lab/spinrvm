"""Unit tests for services/payment_service.py::charge_late_wallet_tip.

Real wallet debit mechanism for a tip added AFTER a ride's fare was already
settled via the wallet (build wallet/corporate absorb-cost path into a real
debit mechanism — follow-up to Finding 1,
docs/proposals/2026-08-17-tip-capture-stripe-cost-minimization-strategy.md).

Covers:
  - sufficient balance      -> full collection, wallet_apply_delta called with
                                type='late_tip_debit' (migration 319's disjoint
                                dedup key, distinct from settlement's
                                'ride_payment')
  - insufficient balance    -> partial collection (clamp_to_floor), never raises
  - no wallet on file       -> absorbs full amount, no RPC call
  - suspended wallet        -> absorbs full amount, no RPC call
  - RPC raises              -> absorbs full amount, never propagates

charge_late_wallet_tip must NEVER raise — the whole point is that a late
tip on a wallet-paid ride never surfaces a rider-facing error.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

RIDE_ID = "ride_late_wallet_tip_1"
RIDER_ID = "rider_late_wallet_tip_1"
WALLET_ID = "wallet_late_wallet_tip_1"


def _ride(**extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "payment_method": "wallet",
        "payment_status": "paid",
    }
    row.update(extra)
    return row


def _wallet(**extra) -> dict:
    row = {"id": WALLET_ID, "user_id": RIDER_ID, "is_active": True, "balance": "50.00"}
    row.update(extra)
    return row


async def test_charge_late_wallet_tip_sufficient_balance_fully_collected():
    from backend.services import payment_service

    with (
        patch("backend.services.payment_service.db_supabase.find_one", AsyncMock(return_value=_wallet())),
        patch(
            "backend.services.payment_service.db_supabase.wallet_apply_delta",
            AsyncMock(
                return_value={
                    "transaction_id": "txn-1",
                    "balance_after": "45.00",
                    "applied_delta": "-5.00",
                    "deduped": False,
                }
            ),
        ) as mock_apply,
    ):
        collected = await payment_service.charge_late_wallet_tip(_ride(), RIDE_ID, RIDER_ID, Decimal("5.00"))

    assert collected == Decimal("5.00")
    mock_apply.assert_awaited_once()
    kwargs = mock_apply.await_args.kwargs
    assert kwargs["type_"] == "late_tip_debit"
    assert kwargs["delta"] == Decimal("-5.00")
    assert kwargs["reference_id"] == RIDE_ID
    assert kwargs["clamp_to_floor"] is True
    assert kwargs["floor"] == Decimal("0")


async def test_charge_late_wallet_tip_insufficient_balance_partial_collection():
    """clamp_to_floor means the RPC itself returns a smaller applied_delta
    than requested rather than raising — the function must surface exactly
    what was actually collected, never the requested amount."""
    from backend.services import payment_service

    with (
        patch(
            "backend.services.payment_service.db_supabase.find_one",
            AsyncMock(return_value=_wallet(balance="2.00")),
        ),
        patch(
            "backend.services.payment_service.db_supabase.wallet_apply_delta",
            AsyncMock(
                return_value={
                    "transaction_id": "txn-2",
                    "balance_after": "0.00",
                    "applied_delta": "-2.00",  # clamped down from the requested -5.00
                    "deduped": False,
                }
            ),
        ),
    ):
        collected = await payment_service.charge_late_wallet_tip(_ride(), RIDE_ID, RIDER_ID, Decimal("5.00"))

    assert collected == Decimal("2.00")


async def test_charge_late_wallet_tip_no_wallet_absorbs_without_rpc_call():
    from backend.services import payment_service

    with (
        patch("backend.services.payment_service.db_supabase.find_one", AsyncMock(return_value=None)),
        patch("backend.services.payment_service.db_supabase.wallet_apply_delta", AsyncMock()) as mock_apply,
    ):
        collected = await payment_service.charge_late_wallet_tip(_ride(), RIDE_ID, RIDER_ID, Decimal("5.00"))

    assert collected == Decimal("0")
    mock_apply.assert_not_awaited()


async def test_charge_late_wallet_tip_suspended_wallet_absorbs_without_rpc_call():
    from backend.services import payment_service

    with (
        patch(
            "backend.services.payment_service.db_supabase.find_one",
            AsyncMock(return_value=_wallet(is_active=False)),
        ),
        patch("backend.services.payment_service.db_supabase.wallet_apply_delta", AsyncMock()) as mock_apply,
    ):
        collected = await payment_service.charge_late_wallet_tip(_ride(), RIDE_ID, RIDER_ID, Decimal("5.00"))

    assert collected == Decimal("0")
    mock_apply.assert_not_awaited()


async def test_charge_late_wallet_tip_rpc_error_absorbs_never_raises():
    from backend.services import payment_service

    with (
        patch("backend.services.payment_service.db_supabase.find_one", AsyncMock(return_value=_wallet())),
        patch(
            "backend.services.payment_service.db_supabase.wallet_apply_delta",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
    ):
        collected = await payment_service.charge_late_wallet_tip(_ride(), RIDE_ID, RIDER_ID, Decimal("5.00"))

    assert collected == Decimal("0")
