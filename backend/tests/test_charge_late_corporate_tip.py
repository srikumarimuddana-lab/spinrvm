"""Unit tests for services/payment_service.py::charge_late_corporate_tip.

Real corporate debit mechanism (allowance, with master-wallet fallback) for
a tip added AFTER a ride's fare was already settled via a company account
(build wallet/corporate absorb-cost path into a real debit mechanism —
follow-up to Finding 1,
docs/proposals/2026-08-17-tip-capture-stripe-cost-minimization-strategy.md).

Covers:
  - allowance fully covers the tip           -> full collection, no master call
  - unlimited allowance                      -> full collection via allowance
  - allowance partially covers it            -> allowance + master fallback,
                                                 both called, sum collected
  - allowance_cap_exceeded (contention)      -> routes full tip to master,
                                                 mirrors settle_corporate's own
                                                 contention handling
  - allowance debit raises a generic error   -> routes full tip to master
  - master debit also fails                  -> absorbs whatever the master
                                                 side couldn't cover; allowance
                                                 portion already collected is
                                                 KEPT, not reversed (the
                                                 deliberate no-compensation
                                                 design — see the function's
                                                 own docstring)
  - no active membership / no corp wallet    -> absorbs full amount
  - unexpected exception anywhere in the saga -> absorbs full amount, never
                                                  raises

charge_late_corporate_tip must NEVER raise.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

RIDE_ID = "ride_late_corp_tip_1"
RIDER_ID = "rider_late_corp_tip_1"
COMPANY_ID = "company_1"
MEMBER_ID = "member_1"
ALLOWANCE_ID = "allowance_1"
CORP_WALLET_ID = "corp_wallet_1"


def _ride(**extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "payment_method": "company_allowance",
        "payment_status": "paid",
        "corporate_account_id": COMPANY_ID,
        "corporate_member_id": MEMBER_ID,
    }
    row.update(extra)
    return row


def _membership(**extra) -> dict:
    row = {"id": MEMBER_ID, "company_id": COMPANY_ID, "status": "active", "user_id": RIDER_ID}
    row.update(extra)
    return row


def _allowance(**extra) -> dict:
    row = {"id": ALLOWANCE_ID, "type": "fixed_recurring", "amount": "100.00", "used": "0.00"}
    row.update(extra)
    return row


def _corp_wallet(**extra) -> dict:
    row = {"id": CORP_WALLET_ID}
    row.update(extra)
    return row


def _base_patches(*, membership=None, allowance=None, corp_wallet=None):
    return [
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
            AsyncMock(return_value=membership),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value=allowance),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
            AsyncMock(return_value=corp_wallet),
        ),
    ]


async def test_allowance_fully_covers_tip_no_master_call():
    from backend.services import payment_service

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
            AsyncMock(return_value=_membership()),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value=_allowance(amount="100.00", used="10.00")),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
            AsyncMock(return_value=_corp_wallet()),
        ),
        patch(
            "backend.services.corporate_allowance_service.apply_late_tip_debit",
            AsyncMock(return_value={"deduped": False}),
        ) as mock_allowance,
        patch(
            "backend.services.corporate_wallet_service.apply_late_tip_master_debit",
            AsyncMock(),
        ) as mock_master,
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("5.00")
    mock_allowance.assert_awaited_once()
    assert mock_allowance.await_args.kwargs["amount"] == 5.00
    assert mock_allowance.await_args.kwargs["ride_id"] == RIDE_ID
    mock_master.assert_not_awaited()


async def test_unlimited_allowance_fully_covers_tip():
    from backend.services import payment_service

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
            AsyncMock(return_value=_membership()),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value=_allowance(type="unlimited", amount=None, used=None)),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
            AsyncMock(return_value=_corp_wallet()),
        ),
        patch(
            "backend.services.corporate_allowance_service.apply_late_tip_debit",
            AsyncMock(return_value={"deduped": False}),
        ) as mock_allowance,
        patch("backend.services.corporate_wallet_service.apply_late_tip_master_debit", AsyncMock()) as mock_master,
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("8.00"))

    assert collected == Decimal("8.00")
    assert mock_allowance.await_args.kwargs["amount"] == 8.00
    mock_master.assert_not_awaited()


async def test_allowance_partial_falls_back_to_master_for_remainder():
    from backend.services import payment_service

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
            AsyncMock(return_value=_membership()),
        ),
        # Only $2 remaining on the allowance for a $5 tip.
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value=_allowance(amount="100.00", used="98.00")),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
            AsyncMock(return_value=_corp_wallet()),
        ),
        patch(
            "backend.services.corporate_allowance_service.apply_late_tip_debit",
            AsyncMock(return_value={"deduped": False}),
        ) as mock_allowance,
        patch(
            "backend.services.corporate_wallet_service.apply_late_tip_master_debit",
            AsyncMock(return_value={"deduped": False}),
        ) as mock_master,
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("5.00")
    assert mock_allowance.await_args.kwargs["amount"] == 2.00
    mock_master.assert_awaited_once()
    assert mock_master.await_args.kwargs["amount"] == Decimal("3.00")
    assert mock_master.await_args.kwargs["ride_id"] == RIDE_ID


async def test_allowance_cap_exceeded_routes_full_tip_to_master():
    """Mirrors settle_corporate's own contention handling: a cap-exceeded
    error under the RPC's row lock means the allowance is genuinely full —
    route the WHOLE tip to master, not the pre-computed partial split."""
    from backend.services import payment_service

    cap_err = RuntimeError("allowance_cap_exceeded: used_new=101.00 cap=100.00")

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
            AsyncMock(return_value=_membership()),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value=_allowance(amount="100.00", used="98.00")),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
            AsyncMock(return_value=_corp_wallet()),
        ),
        patch("backend.services.corporate_allowance_service.apply_late_tip_debit", AsyncMock(side_effect=cap_err)),
        patch(
            "backend.services.corporate_wallet_service.apply_late_tip_master_debit",
            AsyncMock(return_value={"deduped": False}),
        ) as mock_master,
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("5.00")
    # Full tip routed to master, not just the $3 remainder the pre-computed split expected.
    assert mock_master.await_args.kwargs["amount"] == Decimal("5.00")


async def test_allowance_generic_error_routes_full_tip_to_master():
    from backend.services import payment_service

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
            AsyncMock(return_value=_membership()),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value=_allowance(amount="100.00", used="0.00")),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
            AsyncMock(return_value=_corp_wallet()),
        ),
        patch(
            "backend.services.corporate_allowance_service.apply_late_tip_debit",
            AsyncMock(side_effect=RuntimeError("connection reset")),
        ),
        patch(
            "backend.services.corporate_wallet_service.apply_late_tip_master_debit",
            AsyncMock(return_value={"deduped": False}),
        ) as mock_master,
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("5.00")
    assert mock_master.await_args.kwargs["amount"] == Decimal("5.00")


async def test_master_fallback_also_fails_keeps_allowance_portion_no_reversal():
    """The deliberate no-compensation design: the allowance portion already
    collected must be KEPT (not reversed) when the master fallback fails —
    unlike settle_corporate's own all-or-nothing saga."""
    from backend.services import payment_service

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
            AsyncMock(return_value=_membership()),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value=_allowance(amount="100.00", used="98.00")),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
            AsyncMock(return_value=_corp_wallet()),
        ),
        patch(
            "backend.services.corporate_allowance_service.apply_late_tip_debit",
            AsyncMock(return_value={"deduped": False}),
        ),
        patch(
            "backend.services.corporate_wallet_service.apply_late_tip_master_debit",
            AsyncMock(side_effect=RuntimeError("master wallet at floor")),
        ),
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    # $2 from the allowance was collected and kept; the $3 master shortfall is absorbed.
    assert collected == Decimal("2.00")


async def test_no_active_membership_absorbs_full_amount():
    from backend.services import payment_service

    with (
        patch("backend.services.payment_service.db_supabase.get_corporate_member_by_id", AsyncMock(return_value=None)),
        patch(
            "backend.services.corporate_allowance_service.apply_late_tip_debit", AsyncMock()
        ) as mock_allowance,
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("0")
    mock_allowance.assert_not_awaited()


async def test_no_corporate_wallet_absorbs_full_amount():
    from backend.services import payment_service

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
            AsyncMock(return_value=_membership()),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value=_allowance()),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_wallet_by_company",
            AsyncMock(return_value=None),
        ),
        patch("backend.services.corporate_allowance_service.apply_late_tip_debit", AsyncMock()) as mock_allowance,
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("0")
    mock_allowance.assert_not_awaited()


async def test_no_corporate_account_id_absorbs_without_any_lookup():
    from backend.services import payment_service

    with patch(
        "backend.services.payment_service.db_supabase.get_corporate_member_by_id", AsyncMock()
    ) as mock_lookup:
        collected = await payment_service.charge_late_corporate_tip(
            _ride(corporate_account_id=None), RIDE_ID, Decimal("5.00")
        )

    assert collected == Decimal("0")
    mock_lookup.assert_not_awaited()


async def test_unexpected_exception_absorbs_never_raises():
    from backend.services import payment_service

    with patch(
        "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
        AsyncMock(side_effect=RuntimeError("totally unexpected")),
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("0")
