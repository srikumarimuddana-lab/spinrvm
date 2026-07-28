"""Replay-safety tests for allowance_reset (F8): CAS claim on the period roll.

Two replicas could both list a due allowance and both run apply_reset + advance
the period. The loop now claims the roll-forward via a compare-and-swap on
period_end (reset_allowance_period(expected_period_end=...)); only the winner
runs apply_reset, so the reset ledger entry is written once per period.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_ROW = {"id": "a1", "member_id": "m1", "period_end": "2026-01-01", "rollover": False}


def _patches(reset_return):
    return (
        patch(
            "backend.utils.allowance_reset.list_allowances_due_for_reset",
            AsyncMock(return_value=[dict(_ROW)]),
        ),
        patch(
            "backend.utils.allowance_reset.get_corporate_member_by_id",
            AsyncMock(return_value={"company_id": "c1", "status": "active"}),
        ),
        patch(
            "backend.utils.allowance_reset.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c1", "status": "active"}),
        ),
        patch(
            "backend.utils.allowance_reset.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1"}),
        ),
        patch("backend.utils.allowance_reset.reset_allowance_period", AsyncMock(return_value=reset_return)),
        patch("backend.utils.allowance_reset.apply_reset", AsyncMock()),
    )


def test_reset_runs_once_when_cas_won():
    from backend.utils import allowance_reset as ar

    p_list, p_member, p_company, p_wallet, p_reset, p_apply = _patches(reset_return={"id": "a1"})
    with p_list, p_member, p_company, p_wallet, p_reset as reset, p_apply as apply_reset:
        processed = asyncio.run(ar.run_allowance_reset_tick())

    assert processed == 1
    apply_reset.assert_awaited_once()
    # Claim was a CAS on the observed period_end.
    assert reset.await_args.kwargs["expected_period_end"] == "2026-01-01"


def test_reset_skipped_when_cas_lost():
    from backend.utils import allowance_reset as ar

    p_list, p_member, p_company, p_wallet, p_reset, p_apply = _patches(reset_return=None)
    with p_list, p_member, p_company, p_wallet, p_reset, p_apply as apply_reset:
        processed = asyncio.run(ar.run_allowance_reset_tick())

    assert processed == 0
    apply_reset.assert_not_awaited()  # another replica owns this period -> no double reset
