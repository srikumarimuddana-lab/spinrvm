"""Auto-top-up scheduled tick for corporate wallets."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_triggers_charge_when_balance_below_threshold():
    wallets = [
        {
            "id": "w1",
            "company_id": "c1",
            "balance": "30.00",
            "auto_topup_enabled": True,
            "auto_topup_threshold": "100.00",
            "auto_topup_amount": "500.00",
            "auto_topup_daily_cap": "5000.00",
        }
    ]
    company = {"id": "c1", "stripe_customer_id": "cus_X", "status": "active"}
    intent = MagicMock(id="pi_auto")

    with (
        patch(
            "utils.corporate_autotopup.list_wallets_needing_autotopup",
            AsyncMock(return_value=wallets),
        ),
        patch(
            "utils.corporate_autotopup.get_corporate_account_by_id",
            AsyncMock(return_value=company),
        ),
        patch(
            "utils.corporate_autotopup.sum_autotopups_today",
            AsyncMock(return_value=0),
        ),
        patch(
            "utils.corporate_autotopup.get_default_payment_method",
            AsyncMock(return_value="pm_1"),
        ),
        patch("stripe.PaymentIntent.create", return_value=intent) as m_pi,
        patch(
            "utils.corporate_autotopup.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
        ),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()

    m_pi.assert_called_once()
    kwargs = m_pi.call_args.kwargs
    assert kwargs["amount"] == 50000  # 500 CAD
    assert kwargs["customer"] == "cus_X"
    assert kwargs["payment_method"] == "pm_1"
    assert kwargs["off_session"] is True
    assert kwargs["confirm"] is True
    assert kwargs["metadata"]["scope"] == "corporate_topup"
    assert kwargs["metadata"]["wallet_id"] == "w1"
    assert kwargs["metadata"]["initiated_by"] == "autotopup"


@pytest.mark.asyncio
async def test_skips_when_daily_cap_reached():
    wallets = [
        {
            "id": "w1",
            "company_id": "c1",
            "balance": "30.00",
            "auto_topup_enabled": True,
            "auto_topup_threshold": "100.00",
            "auto_topup_amount": "500.00",
            "auto_topup_daily_cap": "500.00",
        }
    ]
    with (
        patch(
            "utils.corporate_autotopup.list_wallets_needing_autotopup",
            AsyncMock(return_value=wallets),
        ),
        patch(
            "utils.corporate_autotopup.get_corporate_account_by_id",
            AsyncMock(return_value={"status": "active", "stripe_customer_id": "cus_X"}),
        ),
        patch(
            "utils.corporate_autotopup.sum_autotopups_today",
            AsyncMock(return_value=500),
        ),
        patch("stripe.PaymentIntent.create") as m_pi,
        patch(
            "utils.corporate_autotopup.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
        ),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()

    m_pi.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_company_not_active():
    wallets = [
        {
            "id": "w1",
            "company_id": "c1",
            "balance": "30.00",
            "auto_topup_enabled": True,
            "auto_topup_threshold": "100.00",
            "auto_topup_amount": "500.00",
            "auto_topup_daily_cap": "5000.00",
        }
    ]
    with (
        patch(
            "utils.corporate_autotopup.list_wallets_needing_autotopup",
            AsyncMock(return_value=wallets),
        ),
        patch(
            "utils.corporate_autotopup.get_corporate_account_by_id",
            AsyncMock(return_value={"status": "suspended", "stripe_customer_id": "cus_X"}),
        ),
        patch("stripe.PaymentIntent.create") as m_pi,
        patch(
            "utils.corporate_autotopup.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
        ),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()

    m_pi.assert_not_called()


@pytest.mark.asyncio
async def test_no_op_when_stripe_secret_missing():
    with (
        patch(
            "utils.corporate_autotopup.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": ""}),
        ),
        patch(
            "utils.corporate_autotopup.list_wallets_needing_autotopup",
            AsyncMock(),
        ) as m_list,
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()

    m_list.assert_not_awaited()
