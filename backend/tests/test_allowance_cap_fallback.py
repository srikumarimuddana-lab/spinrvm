"""P1: per-member corporate allowance ceiling is enforced under contention.

The RPC (migration 258) raises allowance_cap_exceeded when a ride_debit would
push `used` past `amount` — the atomic gate the non-locking min(remaining,total)
split could not provide. settle_corporate must catch that and route the whole
fare to the company master wallet instead of over-spending the cap.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.payment_service import settle_corporate
from backend.utils.error_handling import DatabaseError

_RIDE = {
    "id": "ride_1",
    "rider_id": "rider_1",
    "corporate_account_id": "company_1",
    "corporate_member_id": "member_1",
}


def _member():
    return {"id": "member_1", "company_id": "company_1", "status": "active", "user_id": "rider_1"}


@pytest.mark.anyio
async def test_cap_exceeded_routes_fare_to_master():
    """apply_ride_debit raises allowance_cap_exceeded → master wallet is charged
    the full fare and settlement still succeeds."""
    apply_ride_debit = AsyncMock(
        side_effect=DatabaseError(details={"original": "allowance_cap_exceeded: used_new=60.00 cap=50.00"})
    )
    apply_adjustment = AsyncMock()

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id", AsyncMock(return_value=_member())
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value={"id": "allow_1", "type": "fixed_recurring", "amount": "50.00", "used": "45.00"}),
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
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True
    # Master wallet charged the FULL fare (allowance absorbed nothing).
    apply_adjustment.assert_awaited_once()
    assert apply_adjustment.await_args.kwargs["amount"] == -20.00


@pytest.mark.anyio
async def test_non_cap_error_still_raises():
    """A different RPC error must NOT be swallowed as a cap breach."""
    apply_ride_debit = AsyncMock(side_effect=DatabaseError(details={"original": "wallet_below_floor: new=-5 floor=0"}))

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id", AsyncMock(return_value=_member())
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value={"id": "allow_1", "type": "fixed_recurring", "amount": "50.00", "used": "10.00"}),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "wallet_1"}),
        ),
        patch("backend.services.payment_service.db_supabase.get_corporate_policy", AsyncMock(return_value={})),
        patch("backend.services.payment_service.corporate_allowance_service.apply_ride_debit", apply_ride_debit),
        pytest.raises(DatabaseError),
    ):
        await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))
