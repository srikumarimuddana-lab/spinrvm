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


# ── ride_payment_sources recording (spinr-corporate-billing-reviewer finding:
# without this, collected money is invisible in the company's billing summary/
# statement/PDF, which sum allowance_debit_amount + master_fallback_amount off
# this exact table) ─────────────────────────────────────────────────────────


async def test_full_collection_updates_existing_ride_payment_sources_row():
    from backend.services import payment_service

    existing_row = {"ride_id": RIDE_ID, "allowance_debit_amount": 20.00, "master_fallback_amount": 0.00}

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
        ),
        patch("backend.services.payment_service.db_supabase.find_one", AsyncMock(return_value=existing_row)),
        patch("backend.services.payment_service.db_supabase.update_one", AsyncMock(return_value={})) as mock_upd,
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("5.00")
    mock_upd.assert_awaited_once()
    args = mock_upd.await_args
    assert args.args[0] == "ride_payment_sources"
    assert args.args[1] == {"ride_id": RIDE_ID}
    # Existing $20 allowance_debit_amount + the $5 late tip = $25; the
    # existing amount must be preserved and INCREMENTED, not overwritten.
    assert args.args[2]["allowance_debit_amount"] == 25.00
    assert args.args[2]["master_fallback_amount"] == 0.00


async def test_partial_collection_records_both_amounts_separately():
    from backend.services import payment_service

    existing_row = {"ride_id": RIDE_ID, "allowance_debit_amount": 20.00, "master_fallback_amount": 3.00}

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
            AsyncMock(return_value=_membership()),
        ),
        patch(
            "backend.services.payment_service.db_supabase.get_member_allowance",
            AsyncMock(return_value=_allowance(amount="100.00", used="98.00")),  # $2 remaining
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
            AsyncMock(return_value={"deduped": False}),
        ),
        patch("backend.services.payment_service.db_supabase.find_one", AsyncMock(return_value=existing_row)),
        patch("backend.services.payment_service.db_supabase.update_one", AsyncMock(return_value={})) as mock_upd,
    ):
        # $5 tip: $2 via allowance, $3 via master fallback.
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("5.00")
    args = mock_upd.await_args
    assert args.args[2]["allowance_debit_amount"] == 22.00  # 20 existing + 2 late tip
    assert args.args[2]["master_fallback_amount"] == 6.00  # 3 existing + 3 late tip


async def test_missing_ride_payment_sources_row_logs_error_but_still_returns_collected():
    """A legacy/edge-case ride with no ride_payment_sources row must not
    lose the fact that real money WAS collected — only the bookkeeping
    update is skipped, loudly."""
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
            AsyncMock(return_value={"deduped": False}),
        ),
        patch("backend.services.payment_service.db_supabase.find_one", AsyncMock(return_value=None)),
        patch("backend.services.payment_service.db_supabase.update_one", AsyncMock()) as mock_upd,
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("5.00")
    mock_upd.assert_not_awaited()


async def test_ride_payment_sources_update_failure_does_not_undo_collection():
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
            AsyncMock(return_value={"deduped": False}),
        ),
        patch(
            "backend.services.payment_service.db_supabase.find_one",
            AsyncMock(return_value={"ride_id": RIDE_ID}),
        ),
        patch(
            "backend.services.payment_service.db_supabase.update_one",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    # The wallet-side debit already succeeded — a bookkeeping-table failure
    # must not claw that back or hide it from the caller.
    assert collected == Decimal("5.00")


async def test_section_spend_recorded_when_member_has_section_id():
    from backend.services import payment_service

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
            AsyncMock(return_value=_membership(section_id="section_1")),
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
            AsyncMock(return_value={"deduped": False}),
        ),
        patch("backend.services.payment_service.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.services.payment_service.db_supabase.record_section_spend", AsyncMock()
        ) as mock_section,
    ):
        collected = await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("5.00")
    mock_section.assert_awaited_once()
    assert mock_section.await_args.kwargs["section_id"] == "section_1"
    assert mock_section.await_args.kwargs["amount"] == Decimal("5.00")


async def test_section_spend_not_recorded_when_member_has_no_section():
    from backend.services import payment_service

    with (
        patch(
            "backend.services.payment_service.db_supabase.get_corporate_member_by_id",
            AsyncMock(return_value=_membership()),  # no section_id
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
            AsyncMock(return_value={"deduped": False}),
        ),
        patch("backend.services.payment_service.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.services.payment_service.db_supabase.record_section_spend", AsyncMock()
        ) as mock_section,
    ):
        await payment_service.charge_late_corporate_tip(_ride(), RIDE_ID, Decimal("5.00"))

    mock_section.assert_not_awaited()


async def test_legacy_ride_without_stamped_member_id_falls_back_to_membership_lookup():
    """Mirrors settle_corporate's identical fallback: a ride booked before
    rides.corporate_member_id was stamped derives the payer from the
    rider's active memberships instead."""
    from backend.services import payment_service

    ride = _ride(corporate_member_id=None)

    with (
        patch("backend.services.payment_service.db_supabase.get_corporate_member_by_id", AsyncMock()) as mock_stamped,
        patch(
            "backend.services.payment_service.db_supabase.list_active_memberships_for_user",
            AsyncMock(return_value=[{"id": "other_company_member"}, _membership()]),
        ) as mock_list,
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
            AsyncMock(return_value={"deduped": False}),
        ) as mock_allowance,
        patch("backend.services.payment_service.db_supabase.find_one", AsyncMock(return_value=None)),
    ):
        collected = await payment_service.charge_late_corporate_tip(ride, RIDE_ID, Decimal("5.00"))

    assert collected == Decimal("5.00")
    mock_stamped.assert_not_awaited()
    mock_list.assert_awaited_once_with(RIDER_ID)
    assert mock_allowance.await_args.kwargs["member_id"] == MEMBER_ID
