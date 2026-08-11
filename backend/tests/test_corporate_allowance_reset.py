"""Monthly allowance reset tick tests (Task 9)."""

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_reset_runs_for_stale_allowances():
    stale = {
        "id": "a1",
        "member_id": "m1",
        "type": "fixed_recurring",
        "status": "active",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "rollover": False,
        "used": -100,
    }
    with (
        patch(
            "utils.allowance_reset.list_allowances_due_for_reset",
            AsyncMock(return_value=[stale]),
        ),
        patch(
            "utils.allowance_reset.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m1", "company_id": "c1", "status": "active"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c1", "status": "active"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "soft_negative_floor": -50}),
        ),
        patch(
            "utils.allowance_reset.apply_reset",
            AsyncMock(return_value={"master_balance_after": 0, "allowance_used_after": 0}),
        ) as m_reset,
        patch(
            "utils.allowance_reset.reset_allowance_period",
            AsyncMock(return_value={"id": "a1"}),
        ) as m_period,
    ):
        from utils.allowance_reset import run_allowance_reset_tick

        await run_allowance_reset_tick(now=date(2026, 4, 1))
    m_reset.assert_awaited_once()
    m_period.assert_awaited_once()
    period_args = m_period.await_args.kwargs
    assert period_args["period_start"] == "2026-03-31"
    assert period_args["period_end"].startswith("2026-04-")


@pytest.mark.asyncio
async def test_reset_notifies_rider_on_successful_reset():
    """R43 (ACTION_ITEMS.md N15): a non-rollover reset must push the member
    a heads-up that their allowance zeroed out for the new period."""
    stale = {
        "id": "a1",
        "member_id": "m1",
        "type": "fixed_recurring",
        "status": "active",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "rollover": False,
        "used": -100,
    }
    with (
        patch(
            "utils.allowance_reset.list_allowances_due_for_reset",
            AsyncMock(return_value=[stale]),
        ),
        patch(
            "utils.allowance_reset.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m1", "company_id": "c1", "status": "active", "user_id": "user_1"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c1", "status": "active"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "soft_negative_floor": -50}),
        ),
        patch(
            "utils.allowance_reset.apply_reset",
            AsyncMock(return_value={"master_balance_after": 0, "allowance_used_after": 0}),
        ),
        patch(
            "utils.allowance_reset.reset_allowance_period",
            AsyncMock(return_value={"id": "a1"}),
        ),
        patch(
            "utils.allowance_reset.send_push_notification",
            AsyncMock(),
        ) as m_push,
    ):
        from utils.allowance_reset import run_allowance_reset_tick

        processed = await run_allowance_reset_tick(now=date(2026, 4, 1))

    assert processed == 1
    m_push.assert_awaited_once()
    args, kwargs = m_push.await_args
    assert args[0] == "user_1"
    assert kwargs["data"] == {"type": "corporate_allowance_reset"}
    assert kwargs["priority"] == "normal"
    assert kwargs["target_app"] == "rider"


@pytest.mark.asyncio
async def test_reset_rollover_does_not_notify():
    """A rollover allowance's `used` is untouched — no notice is fired
    because nothing changed from the rider's perspective."""
    rollover = {
        "id": "a2",
        "member_id": "m2",
        "type": "fixed_recurring",
        "status": "active",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "rollover": True,
        "used": -100,
    }
    with (
        patch(
            "utils.allowance_reset.list_allowances_due_for_reset",
            AsyncMock(return_value=[rollover]),
        ),
        patch(
            "utils.allowance_reset.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m2", "company_id": "c2", "status": "active", "user_id": "user_2"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c2", "status": "active"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w2", "soft_negative_floor": -50}),
        ),
        patch("utils.allowance_reset.apply_reset", AsyncMock()),
        patch(
            "utils.allowance_reset.reset_allowance_period",
            AsyncMock(return_value={"id": "a2"}),
        ),
        patch(
            "utils.allowance_reset.send_push_notification",
            AsyncMock(),
        ) as m_push,
    ):
        from utils.allowance_reset import run_allowance_reset_tick

        await run_allowance_reset_tick(now=date(2026, 4, 1))

    m_push.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_push_failure_does_not_abort_processing():
    """A push failure is non-fatal — the reset itself already succeeded and
    must still count as processed."""
    stale = {
        "id": "a1",
        "member_id": "m1",
        "type": "fixed_recurring",
        "status": "active",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "rollover": False,
        "used": -100,
    }
    with (
        patch(
            "utils.allowance_reset.list_allowances_due_for_reset",
            AsyncMock(return_value=[stale]),
        ),
        patch(
            "utils.allowance_reset.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m1", "company_id": "c1", "status": "active", "user_id": "user_1"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c1", "status": "active"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "soft_negative_floor": -50}),
        ),
        patch("utils.allowance_reset.apply_reset", AsyncMock()),
        patch(
            "utils.allowance_reset.reset_allowance_period",
            AsyncMock(return_value={"id": "a1"}),
        ),
        patch(
            "utils.allowance_reset.send_push_notification",
            AsyncMock(side_effect=RuntimeError("push down")),
        ),
    ):
        from utils.allowance_reset import run_allowance_reset_tick

        processed = await run_allowance_reset_tick(now=date(2026, 4, 1))

    assert processed == 1


@pytest.mark.asyncio
async def test_reset_skips_rollover_flag():
    rollover = {
        "id": "a2",
        "member_id": "m2",
        "type": "fixed_recurring",
        "status": "active",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "rollover": True,
        "used": -100,
    }
    with (
        patch(
            "utils.allowance_reset.list_allowances_due_for_reset",
            AsyncMock(return_value=[rollover]),
        ),
        patch(
            "utils.allowance_reset.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m2", "company_id": "c2", "status": "active"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c2", "status": "active"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w2", "soft_negative_floor": -50}),
        ),
        patch(
            "utils.allowance_reset.apply_reset",
            AsyncMock(),
        ) as m_reset,
        patch(
            "utils.allowance_reset.reset_allowance_period",
            AsyncMock(return_value={"id": "a2"}),
        ) as m_period,
    ):
        from utils.allowance_reset import run_allowance_reset_tick

        await run_allowance_reset_tick(now=date(2026, 4, 1))
    m_reset.assert_not_awaited()
    m_period.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_skips_removed_member():
    """Gap #3: a removed member's allowance must not keep replenishing —
    the loop previously only checked the member row existed, not that it
    was still 'active', so a removed employee's budget reset to full every
    period indefinitely."""
    due = {
        "id": "a3",
        "member_id": "m3",
        "type": "fixed_recurring",
        "status": "active",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "rollover": False,
        "used": -100,
    }
    with (
        patch(
            "utils.allowance_reset.list_allowances_due_for_reset",
            AsyncMock(return_value=[due]),
        ),
        patch(
            "utils.allowance_reset.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m3", "company_id": "c3", "status": "removed"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_wallet_by_company",
            AsyncMock(),
        ) as m_wallet,
        patch("utils.allowance_reset.apply_reset", AsyncMock()) as m_reset,
        patch("utils.allowance_reset.reset_allowance_period", AsyncMock()) as m_period,
    ):
        from utils.allowance_reset import run_allowance_reset_tick

        processed = await run_allowance_reset_tick(now=date(2026, 4, 1))

    assert processed == 0
    m_wallet.assert_not_awaited()
    m_reset.assert_not_awaited()
    m_period.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_skips_suspended_company():
    """Corporate module lifecycle audit Finding 2: this loop previously only
    checked the MEMBER's own status, never the company's — a suspended
    company's still-active members kept getting their monthly allowance
    auto-refilled indefinitely."""
    due = {
        "id": "a4",
        "member_id": "m4",
        "type": "fixed_recurring",
        "status": "active",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "rollover": False,
        "used": -100,
    }
    with (
        patch(
            "utils.allowance_reset.list_allowances_due_for_reset",
            AsyncMock(return_value=[due]),
        ),
        patch(
            "utils.allowance_reset.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m4", "company_id": "c4", "status": "active"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c4", "status": "suspended"}),
        ),
        patch(
            "utils.allowance_reset.get_corporate_wallet_by_company",
            AsyncMock(),
        ) as m_wallet,
        patch("utils.allowance_reset.apply_reset", AsyncMock()) as m_reset,
        patch("utils.allowance_reset.reset_allowance_period", AsyncMock()) as m_period,
    ):
        from utils.allowance_reset import run_allowance_reset_tick

        processed = await run_allowance_reset_tick(now=date(2026, 4, 1))

    assert processed == 0
    m_wallet.assert_not_awaited()
    m_reset.assert_not_awaited()
    m_period.assert_not_awaited()


# ── E5 kill switch: corporate_billing_enabled ──────────────────────────────
#
# get_app_settings is a lazy (function-local) dual import in this file --
# see settle_corporate's identical pattern in services/payment_service.py
# for why -- so these tests patch it at its source (settings_loader) rather
# than as an allowance_reset module attribute.


@pytest.mark.asyncio
async def test_no_op_when_corporate_billing_disabled():
    with (
        patch(
            "backend.settings_loader.get_app_settings",
            AsyncMock(return_value={"corporate_billing_enabled": False}),
        ),
        patch("utils.allowance_reset.list_allowances_due_for_reset", AsyncMock()) as m_list,
    ):
        from utils.allowance_reset import run_allowance_reset_tick

        processed = await run_allowance_reset_tick(now=date(2026, 4, 1))

    assert processed == 0
    m_list.assert_not_awaited()


@pytest.mark.asyncio
async def test_proceeds_when_corporate_billing_key_missing():
    """A settings dict with no corporate_billing_enabled key (legacy row)
    must still proceed -- the flag defaults to enabled."""
    with (
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
        patch(
            "utils.allowance_reset.list_allowances_due_for_reset",
            AsyncMock(return_value=[]),
        ) as m_list,
    ):
        from utils.allowance_reset import run_allowance_reset_tick

        await run_allowance_reset_tick(now=date(2026, 4, 1))

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
            "utils.allowance_reset.list_allowances_due_for_reset",
            AsyncMock(return_value=[]),
        ) as m_list,
    ):
        from utils.allowance_reset import run_allowance_reset_tick

        await run_allowance_reset_tick(now=date(2026, 4, 1))

    m_list.assert_awaited_once()
