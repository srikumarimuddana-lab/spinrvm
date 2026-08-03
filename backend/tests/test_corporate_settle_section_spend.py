"""Section-budget visibility tracking at ride settlement (corporate + admin
portal review round 2, business decision: "department/section budgets" —
track and display spend, never block a booking).

Follows the exact `_patches()` + `contextlib.ExitStack` template established
in test_corporate_settle_suspended_audit_flag.py for adding a new
completion-time behavior to settle_corporate.
"""

from __future__ import annotations

import contextlib
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


def _member(section_id="section_1"):
    row = {"id": "member_1", "company_id": "company_1", "status": "active", "user_id": "rider_1"}
    if section_id is not None:
        row["section_id"] = section_id
    return row


def _patches(member, record_spend_mock: AsyncMock):
    return (
        patch("backend.services.payment_service.db_supabase.get_corporate_member_by_id", AsyncMock(return_value=member)),
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
            AsyncMock(return_value={"id": "company_1", "status": "active"}),
        ),
        patch("backend.services.payment_service.corporate_allowance_service.apply_ride_debit", AsyncMock()),
        patch("backend.services.payment_service.db_supabase.insert_one", AsyncMock()),
        patch("backend.services.payment_service.db_supabase.update_ride", AsyncMock()),
        patch("backend.services.payment_service.db_supabase.record_section_spend", record_spend_mock),
    )


@pytest.mark.anyio
async def test_records_spend_for_member_with_section():
    record_spend_mock = AsyncMock(return_value=Decimal("20.00"))
    with contextlib.ExitStack() as stack:
        for p in _patches(_member(section_id="section_1"), record_spend_mock):
            stack.enter_context(p)
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True
    record_spend_mock.assert_awaited_once()
    kwargs = record_spend_mock.call_args.kwargs
    assert kwargs["section_id"] == "section_1"
    assert kwargs["amount"] == Decimal("20.00")
    assert kwargs["month"]  # a YYYY-MM string was supplied


@pytest.mark.anyio
async def test_skips_recording_when_member_has_no_section():
    record_spend_mock = AsyncMock()
    with contextlib.ExitStack() as stack:
        for p in _patches(_member(section_id=None), record_spend_mock):
            stack.enter_context(p)
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True
    record_spend_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_recording_failure_never_blocks_or_alters_settlement():
    """A failure recording section spend must be swallowed — it's a
    visibility feature, never a settlement gate."""
    record_spend_mock = AsyncMock(side_effect=Exception("db down"))
    with contextlib.ExitStack() as stack:
        for p in _patches(_member(section_id="section_1"), record_spend_mock):
            stack.enter_context(p)
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True
    record_spend_mock.assert_awaited_once()
