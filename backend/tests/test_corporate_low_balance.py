"""Low-balance email notification tick."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_sends_email_when_below_threshold_and_autotopup_off():
    wallet = {
        "id": "w1",
        "company_id": "c1",
        "balance": "30.00",
        "auto_topup_enabled": False,
        "auto_topup_threshold": "100.00",
        "low_balance_notified_at": None,
    }
    with (
        patch(
            "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
            AsyncMock(return_value=[wallet]),
        ),
        patch(
            "utils.corporate_low_balance.get_corporate_account_by_id",
            AsyncMock(return_value={"billing_email": "billing@acme.test", "name": "Acme", "status": "active"}),
        ),
        patch("utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()) as m_mark,
        patch("utils.corporate_low_balance.send_email", AsyncMock()) as m_send,
    ):
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()

    m_send.assert_awaited_once()
    kwargs = m_send.call_args.kwargs
    assert kwargs["to"] == "billing@acme.test"
    assert "low" in kwargs["subject"].lower() or "balance" in kwargs["subject"].lower()
    m_mark.assert_awaited_once_with(wallet_id="w1")


@pytest.mark.asyncio
async def test_rate_limited_within_12h():
    recent = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    wallet = {
        "id": "w1",
        "company_id": "c1",
        "balance": "30.00",
        "auto_topup_enabled": False,
        "auto_topup_threshold": "100.00",
        "low_balance_notified_at": recent,
    }
    with (
        patch(
            "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
            AsyncMock(return_value=[wallet]),
        ),
        patch("utils.corporate_low_balance.send_email", AsyncMock()) as m_send,
        patch("utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()) as m_mark,
    ):
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()

    m_send.assert_not_awaited()
    m_mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_resends_after_rate_limit_elapsed():
    stale = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    wallet = {
        "id": "w1",
        "company_id": "c1",
        "balance": "30.00",
        "auto_topup_enabled": False,
        "auto_topup_threshold": "100.00",
        "low_balance_notified_at": stale,
    }
    with (
        patch(
            "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
            AsyncMock(return_value=[wallet]),
        ),
        patch(
            "utils.corporate_low_balance.get_corporate_account_by_id",
            AsyncMock(return_value={"billing_email": "ops@acme.test", "name": "Acme", "status": "active"}),
        ),
        patch("utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()),
        patch("utils.corporate_low_balance.send_email", AsyncMock()) as m_send,
    ):
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()

    m_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_when_company_missing_billing_email():
    wallet = {
        "id": "w1",
        "company_id": "c1",
        "balance": "30.00",
        "auto_topup_enabled": False,
        "auto_topup_threshold": "100.00",
        "low_balance_notified_at": None,
    }
    with (
        patch(
            "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
            AsyncMock(return_value=[wallet]),
        ),
        patch(
            "utils.corporate_low_balance.get_corporate_account_by_id",
            AsyncMock(return_value={"billing_email": None, "name": "Acme"}),
        ),
        patch("utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()) as m_mark,
        patch("utils.corporate_low_balance.send_email", AsyncMock()) as m_send,
    ):
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()

    m_send.assert_not_awaited()
    m_mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_company_suspended():
    """Corporate module lifecycle audit Finding 3: a suspended/closed company
    must not keep receiving 'top up your wallet' nudges — top-up is
    deliberately disabled during suspension, and a closed account's wallet
    may already be refunded to zero."""
    wallet = {
        "id": "w1",
        "company_id": "c1",
        "balance": "30.00",
        "auto_topup_enabled": False,
        "auto_topup_threshold": "100.00",
        "low_balance_notified_at": None,
    }
    with (
        patch(
            "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
            AsyncMock(return_value=[wallet]),
        ),
        patch(
            "utils.corporate_low_balance.get_corporate_account_by_id",
            AsyncMock(return_value={"billing_email": "billing@acme.test", "name": "Acme", "status": "suspended"}),
        ),
        patch("utils.corporate_low_balance.mark_low_balance_notified", AsyncMock()) as m_mark,
        patch("utils.corporate_low_balance.send_email", AsyncMock()) as m_send,
    ):
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()

    m_send.assert_not_awaited()
    m_mark.assert_not_awaited()


# ── E5 kill switch: corporate_billing_enabled ──────────────────────────────
#
# get_app_settings is a lazy (function-local) dual import in this file --
# see settle_corporate's identical pattern in services/payment_service.py
# for why -- so these tests patch it at its source (settings_loader) rather
# than as a corporate_low_balance module attribute.


@pytest.mark.asyncio
async def test_no_op_when_corporate_billing_disabled():
    with (
        patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(return_value={"corporate_billing_enabled": False}),
        ),
        patch(
            "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
            AsyncMock(),
        ) as m_list,
    ):
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()

    m_list.assert_not_awaited()


@pytest.mark.asyncio
async def test_proceeds_when_corporate_billing_key_missing():
    """A settings dict with no corporate_billing_enabled key (legacy row)
    must still proceed -- the flag defaults to enabled."""
    with (
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
        patch(
            "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
            AsyncMock(return_value=[]),
        ) as m_list,
    ):
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()

    m_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_fails_open_on_settings_lookup_error():
    """A settings-read error must never itself block the tick."""
    with (
        patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(side_effect=RuntimeError("settings down")),
        ),
        patch(
            "utils.corporate_low_balance.list_wallets_low_balance_no_autotopup",
            AsyncMock(return_value=[]),
        ) as m_list,
    ):
        from utils.corporate_low_balance import run_low_balance_tick

        await run_low_balance_tick()

    m_list.assert_awaited_once()
