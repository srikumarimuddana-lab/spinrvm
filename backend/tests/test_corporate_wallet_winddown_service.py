# backend/tests/test_corporate_wallet_winddown_service.py
"""Covers wallet wind-down on corporate account close (gap #2 — offboarding).

See docs/change-log for the corresponding Change Impact Log entry — 'closed'
is terminal, so a wallet balance left behind at close time had no path back
to the company. This refunds it via Stripe against the original top-up
PaymentIntent(s), best-effort, and never silently writes off a remainder it
couldn't refund.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import corporate_wallet_winddown_service as svc

_SETTINGS = {"stripe_secret_key": "sk_test_123"}


def _wallet(balance="100.00", id="w1"):
    return {"id": id, "company_id": "c1", "balance": balance}


def _topup(id, amount, pi):
    return {
        "id": id,
        "wallet_id": "w1",
        "type": "topup",
        "amount": amount,
        "stripe_payment_intent_id": pi,
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.mark.unit
@pytest.mark.anyio
async def test_no_wallet_skips():
    with patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value=None)):
        result = await svc.refund_wallet_balance_on_close(company_id="c1", stripe_customer_id="cus_1")

    assert result["skipped_reason"] == "no_wallet"
    assert result["attempted"] is False


@pytest.mark.unit
@pytest.mark.anyio
async def test_zero_balance_skips():
    with patch.object(
        svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value=_wallet(balance="0.00"))
    ):
        result = await svc.refund_wallet_balance_on_close(company_id="c1", stripe_customer_id="cus_1")

    assert result["skipped_reason"] == "zero_or_negative_balance"
    assert result["attempted"] is False


@pytest.mark.unit
@pytest.mark.anyio
async def test_no_stripe_customer_reports_unrefundable():
    with patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value=_wallet())):
        result = await svc.refund_wallet_balance_on_close(company_id="c1", stripe_customer_id=None)

    assert result["skipped_reason"] == "no_stripe_customer"
    assert result["unrefundable_amount"] == "100.00"
    assert result["attempted"] is False


@pytest.mark.unit
@pytest.mark.anyio
async def test_single_topup_fully_covers_balance():
    refund_obj = MagicMock(id="re_1")
    topups = [_topup("t1", "150.00", "pi_1")]

    with (
        patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value=_wallet())),
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=topups)),
        patch.object(svc, "get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch.object(svc.stripe.Refund, "create", return_value=refund_obj) as mock_refund,
        patch.object(svc, "apply_adjustment", AsyncMock()) as mock_adjust,
    ):
        result = await svc.refund_wallet_balance_on_close(
            company_id="c1", stripe_customer_id="cus_1", actor_user_id="admin-1"
        )

    mock_refund.assert_called_once()
    kwargs = mock_refund.call_args.kwargs
    assert kwargs["payment_intent"] == "pi_1"
    assert kwargs["amount"] == 10000  # $100.00 balance, capped below the $150 topup
    assert kwargs["idempotency_key"] == "corp-close-refund-w1-t1"

    mock_adjust.assert_awaited_once()
    adj_kwargs = mock_adjust.call_args.kwargs
    assert adj_kwargs["wallet_id"] == "w1"
    assert adj_kwargs["amount"] == Decimal("-100.00")
    assert adj_kwargs["floor"] == Decimal("0")
    assert adj_kwargs["actor_user_id"] == "admin-1"

    assert result["refunded_total"] == "100.00"
    assert result["unrefundable_amount"] == "0.00"
    assert result["stripe_refund_ids"] == ["re_1"]
    assert result["skipped_reason"] is None


@pytest.mark.unit
@pytest.mark.anyio
async def test_multiple_topups_refunded_lifo_until_covered():
    topups = [
        _topup("t2", "40.00", "pi_2"),  # most recent, returned first by desc order
        _topup("t1", "40.00", "pi_1"),
    ]
    refund_objs = [MagicMock(id="re_2"), MagicMock(id="re_1")]

    with (
        patch.object(
            svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value=_wallet(balance="70.00"))
        ),
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=topups)),
        patch.object(svc, "get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch.object(svc.stripe.Refund, "create", side_effect=refund_objs) as mock_refund,
        patch.object(svc, "apply_adjustment", AsyncMock()) as mock_adjust,
    ):
        result = await svc.refund_wallet_balance_on_close(company_id="c1", stripe_customer_id="cus_1")

    assert mock_refund.call_count == 2
    first_call, second_call = mock_refund.call_args_list
    assert first_call.kwargs["payment_intent"] == "pi_2"
    assert first_call.kwargs["amount"] == 4000
    assert second_call.kwargs["payment_intent"] == "pi_1"
    assert second_call.kwargs["amount"] == 3000  # only $30 of the $40 topup needed

    mock_adjust.assert_awaited_once()
    assert mock_adjust.call_args.kwargs["amount"] == Decimal("-70.00")
    assert result["refunded_total"] == "70.00"
    assert result["stripe_refund_ids"] == ["re_2", "re_1"]


@pytest.mark.unit
@pytest.mark.anyio
async def test_balance_exceeds_available_topups_leaves_unrefundable_remainder():
    """Balance built partly from a manual adjustment with no underlying charge —
    never silently written off, surfaced for finance to handle."""
    refund_obj = MagicMock(id="re_1")
    topups = [_topup("t1", "50.00", "pi_1")]

    with (
        patch.object(
            svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value=_wallet(balance="80.00"))
        ),
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=topups)),
        patch.object(svc, "get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch.object(svc.stripe.Refund, "create", return_value=refund_obj),
        patch.object(svc, "apply_adjustment", AsyncMock()) as mock_adjust,
    ):
        result = await svc.refund_wallet_balance_on_close(company_id="c1", stripe_customer_id="cus_1")

    assert result["refunded_total"] == "50.00"
    assert result["unrefundable_amount"] == "30.00"
    assert result["skipped_reason"] == "topups_exhausted_balance_remains"
    # Ledger is only debited by what was actually refunded — never zeroed past that.
    assert mock_adjust.call_args.kwargs["amount"] == Decimal("-50.00")


@pytest.mark.unit
@pytest.mark.anyio
async def test_stripe_error_stops_and_does_not_swallow():
    topups = [_topup("t1", "100.00", "pi_1")]

    with (
        patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value=_wallet())),
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=topups)),
        patch.object(svc, "get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch.object(
            svc.stripe.Refund,
            "create",
            side_effect=svc.stripe.error.StripeError("card issuer declined the refund"),
        ),
        patch.object(svc, "apply_adjustment", AsyncMock()) as mock_adjust,
    ):
        result = await svc.refund_wallet_balance_on_close(company_id="c1", stripe_customer_id="cus_1")

    assert result["stripe_error"] == "card issuer declined the refund"
    assert result["refunded_total"] == "0.00"
    assert result["unrefundable_amount"] == "100.00"
    mock_adjust.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_ledger_write_failure_after_successful_refund_is_not_lost():
    """Corporate + admin portal review, High #3: if the ledger debit raises
    AFTER Stripe refunds already succeeded, the real Stripe outcome
    (refunded_total, stripe_refund_ids) must still be returned — not lost to
    an uncaught exception — and ledger_write_failed must flag the
    divergence so ops/finance can reconcile it manually."""
    refund_obj = MagicMock(id="re_1")
    topups = [_topup("t1", "100.00", "pi_1")]

    with (
        patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(return_value=_wallet())),
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=topups)),
        patch.object(svc, "get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch.object(svc.stripe.Refund, "create", return_value=refund_obj),
        patch.object(svc, "apply_adjustment", AsyncMock(side_effect=RuntimeError("db unreachable"))),
    ):
        # Must not raise — the whole point of this fix is that a ledger
        # failure after a successful Stripe refund is reported, not thrown.
        result = await svc.refund_wallet_balance_on_close(company_id="c1", stripe_customer_id="cus_1")

    assert result["ledger_write_failed"] is True
    # The Stripe refund genuinely happened — this must not be erased.
    assert result["refunded_total"] == "100.00"
    assert result["stripe_refund_ids"] == ["re_1"]
