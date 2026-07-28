"""In-progress rides are grandfathered when a company is suspended/closed
mid-trip: the ride state machine forbids cancelling after trip start, so
settle_corporate still bills the company normally. This only checks that the
completion-time audit trail (corporate_policy_evaluations) records the fact
via a "company_inactive_during_ride" flag, and that it never blocks or
alters the payment outcome.
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
    with contextlib.ExitStack() as stack:
        for p in _patches("suspended", insert_mock):
            stack.enter_context(p)
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True  # payment is NOT blocked — grandfathered

    audit_calls = [c for c in insert_mock.await_args_list if c.args[0] == "corporate_policy_evaluations"]
    assert len(audit_calls) == 1
    assert "company_inactive_during_ride" in audit_calls[0].args[1]["failed_rules"]


@pytest.mark.anyio
async def test_closed_company_still_bills_ride_but_flags_audit():
    insert_mock = AsyncMock()
    with contextlib.ExitStack() as stack:
        for p in _patches("closed", insert_mock):
            stack.enter_context(p)
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True
    audit_calls = [c for c in insert_mock.await_args_list if c.args[0] == "corporate_policy_evaluations"]
    assert "company_inactive_during_ride" in audit_calls[0].args[1]["failed_rules"]


@pytest.mark.anyio
async def test_active_company_no_audit_flag():
    insert_mock = AsyncMock()
    with contextlib.ExitStack() as stack:
        for p in _patches("active", insert_mock):
            stack.enter_context(p)
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True
    audit_calls = [c for c in insert_mock.await_args_list if c.args[0] == "corporate_policy_evaluations"]
    assert audit_calls == []


_RIDE_WITH_CREATED_AT = {
    **_RIDE,
    "created_at": "2026-01-01T00:00:00+00:00",
}


def _patches_policy(company_status: str, policy: dict, insert_mock: AsyncMock, ride: dict):
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
        patch("backend.services.payment_service.db_supabase.get_corporate_policy", AsyncMock(return_value=policy)),
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
async def test_policy_edited_after_booking_flags_audit_but_still_bills():
    """Corporate module lifecycle audit Finding 7: policy tightening (e.g. a
    lowered max_fare_per_ride, or allowed_payment_source narrowed) was never
    re-checked against rides already in flight — evaluate_policy_for_ride only
    runs at booking time, and settle_corporate re-fetches the CURRENT policy
    at completion with no comparison against what was in effect at booking.
    Phase A: make the drift visible in the audit trail (never blocks or
    re-prices the ride — that's a separate, larger product decision)."""
    insert_mock = AsyncMock()
    policy = {"updated_at": "2026-01-02T00:00:00+00:00"}  # edited AFTER ride.created_at
    with contextlib.ExitStack() as stack:
        for p in _patches_policy("active", policy, insert_mock, _RIDE_WITH_CREATED_AT):
            stack.enter_context(p)
        result = await settle_corporate(_RIDE_WITH_CREATED_AT, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True  # never blocks or alters settlement
    audit_calls = [c for c in insert_mock.await_args_list if c.args[0] == "corporate_policy_evaluations"]
    assert len(audit_calls) == 1
    assert "policy_changed_since_booking" in audit_calls[0].args[1]["failed_rules"]


@pytest.mark.anyio
async def test_policy_unchanged_since_booking_no_audit_flag():
    """Control case: a policy last edited BEFORE the ride was booked must not
    trip the flag — this only fires on drift, not on every corporate ride."""
    insert_mock = AsyncMock()
    policy = {"updated_at": "2025-12-01T00:00:00+00:00"}  # edited BEFORE ride.created_at
    with contextlib.ExitStack() as stack:
        for p in _patches_policy("active", policy, insert_mock, _RIDE_WITH_CREATED_AT):
            stack.enter_context(p)
        result = await settle_corporate(_RIDE_WITH_CREATED_AT, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True
    audit_calls = [c for c in insert_mock.await_args_list if c.args[0] == "corporate_policy_evaluations"]
    assert audit_calls == []


@pytest.mark.anyio
async def test_no_policy_updated_at_never_raises():
    """A policy dict with no updated_at (or a ride with no created_at, e.g. a
    legacy row) must fail safe — never raise, never flag. Audit-only signals
    must never become a new way to break settlement."""
    insert_mock = AsyncMock()
    policy = {}  # no updated_at at all
    with contextlib.ExitStack() as stack:
        for p in _patches_policy("active", policy, insert_mock, _RIDE):
            stack.enter_context(p)
        result = await settle_corporate(_RIDE, "ride_1", Decimal("20.00"), Decimal("0.00"))

    assert result.success is True
    audit_calls = [c for c in insert_mock.await_args_list if c.args[0] == "corporate_policy_evaluations"]
    assert audit_calls == []
