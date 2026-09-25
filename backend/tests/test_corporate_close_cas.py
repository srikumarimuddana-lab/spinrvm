# backend/tests/test_corporate_close_cas.py
"""change_company_status compare-and-set (clean-sheet audit CORP-001, ROADMAP N20).

Before: the only replay guard was ``if current.status == 'closed': 409``,
checked against a plain read, followed by an unconditional
``UPDATE corporate_accounts SET status = ... WHERE id = ...``. Two concurrent
"Close account" requests could both pass the read and both run every close
side effect (ride cancellation, wallet wind-down, subscription cancel).

After: the UPDATE also filters on the status the request read. The loser
matches zero rows, re-reads the row to distinguish "gone" (404) from "moved
underneath us" (409), and returns before any side effect.

The concurrency tests drive two real ``change_company_status`` coroutines
through ``asyncio.gather`` against a small stateful fake of the company row
whose read yields (``asyncio.sleep(0)``) after taking its snapshot, so both
requests genuinely read the pre-update status before either writes — the
exact interleaving the audit describes.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.tests._factories import corporate_account_row
from routes.corporate_accounts import CompanyStatusTransition, change_company_status

pytestmark = pytest.mark.unit

_ROUTE = "routes.corporate_accounts."
_ADMIN = {"id": "admin-1"}


class _FakeCompanyRow:
    """One corporate_accounts row with PostgREST-like CAS UPDATE semantics."""

    def __init__(self, status: str):
        self.row = corporate_account_row(status, id="c1", stripe_customer_id="cus_1")
        self.update_calls = []

    async def get(self, validated_id):
        snapshot = dict(self.row)
        await asyncio.sleep(0)  # let a concurrent request read the same pre-update row
        return snapshot

    async def update(self, company_id, status, expected_status=None):
        self.update_calls.append({"status": status, "expected_status": expected_status})
        if expected_status is not None and self.row["status"] != expected_status:
            return None  # 0 rows matched
        self.row["status"] = status
        return dict(self.row)


def _patch_side_effects(stack: ExitStack, fake: _FakeCompanyRow):
    mocks = {
        "refund": AsyncMock(return_value={"stripe_error": None, "unrefundable_amount": "0.00"}),
        "cancel_rides": AsyncMock(return_value=0),
        "cancel_sub": AsyncMock(return_value={"cancelled": True}),
        "audit": AsyncMock(),
    }
    stack.enter_context(patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(side_effect=fake.get)))
    stack.enter_context(patch("db_supabase.update_corporate_account_status", AsyncMock(side_effect=fake.update)))
    stack.enter_context(patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=None)))
    stack.enter_context(
        patch(
            _ROUTE + "get_app_settings",
            AsyncMock(
                return_value={
                    "corporate_suspend_cancels_pre_pickup_rides": True,
                    "corporate_close_refunds_wallet_balance": True,
                }
            ),
        )
    )
    stack.enter_context(patch(_ROUTE + "refund_wallet_balance_on_close", mocks["refund"]))
    stack.enter_context(patch(_ROUTE + "cancel_pre_pickup_rides_for_company", mocks["cancel_rides"]))
    stack.enter_context(patch(_ROUTE + "cancel_subscription", mocks["cancel_sub"]))
    stack.enter_context(patch(_ROUTE + "log_admin_action", mocks["audit"]))
    return mocks


async def _close(status: str = "closed"):
    try:
        return await change_company_status("c1", CompanyStatusTransition(status=status), _ADMIN)
    except HTTPException as exc:
        return exc


@pytest.mark.anyio
async def test_status_update_is_conditional_on_status_read():
    fake = _FakeCompanyRow("active")
    with ExitStack() as stack:
        _patch_side_effects(stack, fake)
        result = await _close()

    assert result["status"] == "closed"
    assert fake.update_calls == [{"status": "closed", "expected_status": "active"}]


@pytest.mark.anyio
async def test_cas_losing_path_is_409_and_runs_no_side_effects(caplog):
    """Row read as 'active', but by UPDATE time another request closed it."""
    fake = _FakeCompanyRow("active")
    with ExitStack() as stack:
        mocks = _patch_side_effects(stack, fake)
        stack.enter_context(
            patch(
                _ROUTE + "get_corporate_account_by_id",
                AsyncMock(side_effect=[dict(fake.row), {**fake.row, "status": "closed"}]),
            )
        )
        stack.enter_context(patch("db_supabase.update_corporate_account_status", AsyncMock(return_value=None)))
        with caplog.at_level(logging.WARNING, logger="routes.corporate_accounts"):
            result = await _close()

    assert isinstance(result, HTTPException)
    assert result.status_code == 409
    assert "now 'closed'" in result.detail
    mocks["refund"].assert_not_awaited()
    mocks["cancel_rides"].assert_not_awaited()
    mocks["cancel_sub"].assert_not_awaited()
    mocks["audit"].assert_not_awaited()
    assert any("lost a concurrent race" in r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_two_concurrent_closes_run_winddown_exactly_once():
    fake = _FakeCompanyRow("active")
    with ExitStack() as stack:
        mocks = _patch_side_effects(stack, fake)
        results = await asyncio.gather(_close(), _close())

    winners = [r for r in results if isinstance(r, dict)]
    losers = [r for r in results if isinstance(r, HTTPException)]
    assert len(winners) == 1 and winners[0]["status"] == "closed"
    assert len(losers) == 1 and losers[0].status_code == 409
    # Both requests read 'active' and both attempted the CAS...
    assert [c["expected_status"] for c in fake.update_calls] == ["active", "active"]
    # ...but only one ran the money-moving and ride-cancelling side effects.
    mocks["refund"].assert_awaited_once()
    mocks["cancel_rides"].assert_awaited_once()
    mocks["cancel_sub"].assert_awaited_once()
    mocks["audit"].assert_awaited_once()
    assert fake.row["status"] == "closed"


@pytest.mark.anyio
async def test_concurrent_suspend_cannot_overwrite_a_close():
    """Without CAS, a suspend racing a close could land second and leave a
    refunded, terminal company in the reversible 'suspended' state."""
    fake = _FakeCompanyRow("active")
    with ExitStack() as stack:
        mocks = _patch_side_effects(stack, fake)
        close_result, suspend_result = await asyncio.gather(_close("closed"), _close("suspended"))

    assert isinstance(close_result, dict) and close_result["status"] == "closed"
    assert isinstance(suspend_result, HTTPException) and suspend_result.status_code == 409
    assert fake.row["status"] == "closed"
    mocks["refund"].assert_awaited_once()


@pytest.mark.anyio
async def test_sequential_repeat_close_still_409s_on_the_read_guard():
    fake = _FakeCompanyRow("active")
    with ExitStack() as stack:
        mocks = _patch_side_effects(stack, fake)
        first = await _close()
        second = await _close()

    assert first["status"] == "closed"
    assert isinstance(second, HTTPException) and second.status_code == 409
    assert "cannot be reopened" in second.detail
    assert len(fake.update_calls) == 1  # the repeat never reached the UPDATE
    mocks["refund"].assert_awaited_once()
