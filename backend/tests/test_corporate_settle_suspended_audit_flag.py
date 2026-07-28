"""In-progress rides are grandfathered when a company is suspended/closed
mid-trip: the ride state machine forbids cancelling after trip start, so
settle_corporate still bills the company normally. This only checks that the
completion-time audit trail (corporate_policy_evaluations) records the fact
via a "company_inactive_during_ride" flag, and that it never blocks or
alters the payment outcome.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.payment_service import settle_corporate

_RIDE = {
    "id": "ride_1",
    "rider_id": "rider_1",
    "corporate_account_id": "company_1",
    "corporate_member_id": "member_1",
}


def _member():
    return {"id": "member_1", "company_id": "company_1", "status": "active", "user_id": "rider_1"}


def _patches(company_status: str, insert_mock: AsyncMock):
    return (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id", AsyncMock(return_value=_member())
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value={"id": "allow_1", "type": "unlimited"}),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "wallet_1"}),
        ),
        patch("backend.services.payment_service.db_supabase.get_corporate_policy", AsyncMock(return_value={})),
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "company_1", "status": company_status}),
        ),
        patch(
            "backend.services.payment_service.corporate_allowance_service.apply_ride_debit",
            AsyncMock(),
        ),
        patch("backend.services.payment_service.db_supabase.insert_one", insert_mock),
        patch("backend.services.payment_service.db_supabase.update_ride", AsyncMock()),
    )


@pytest.mark.anyio
async def test_suspended_company_still_bills_ride_but_flags_audit():
    insert_mock = AsyncMock()
    with _patches("suspended", insert_mock):
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True  # payment is NOT blocked — grandfathered

    audit_calls = [c for c in insert_mock.await_args_list if c.args[0] == "corporate_policy_evaluations"]
    assert len(audit_calls) == 1
    assert "company_inactive_during_ride" in audit_calls[0].args[1]["failed_rules"]


@pytest.mark.anyio
async def test_closed_company_still_bills_ride_but_flags_audit():
    insert_mock = AsyncMock()
    with _patches("closed", insert_mock):
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True
    audit_calls = [c for c in insert_mock.await_args_list if c.args[0] == "corporate_policy_evaluations"]
    assert "company_inactive_during_ride" in audit_calls[0].args[1]["failed_rules"]


@pytest.mark.anyio
async def test_active_company_no_audit_flag():
    insert_mock = AsyncMock()
    with _patches("active", insert_mock):
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True
    audit_calls = [c for c in insert_mock.await_args_list if c.args[0] == "corporate_policy_evaluations"]
    assert audit_calls == []
