"""KYB re-verification staleness reminder tick (corporate + admin portal
review round 2, business decision: scheduled staleness reminder — never
auto-status-change). Mirrors test_corporate_low_balance.py's pattern.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

_SETTINGS_ON = {"corporate_kyb_reverification_enabled": True}


def _company(**extra):
    return {
        "id": "c1",
        "name": "Acme",
        "status": "active",
        "kyb_last_decision": "approved",
        "kyb_reviewed_at": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
        "kyb_reverify_flagged_at": None,
        **extra,
    }


@pytest.mark.asyncio
async def test_flags_stale_company_and_emits_metric():
    with (
        patch("utils.kyb_reverification.get_app_settings", AsyncMock(return_value=_SETTINGS_ON)),
        patch(
            "utils.kyb_reverification.list_companies_needing_kyb_reverification",
            AsyncMock(return_value=[_company()]),
        ),
        patch("utils.kyb_reverification.mark_kyb_reverify_flagged", AsyncMock()) as m_mark,
        patch("utils.kyb_reverification._metric_inc") as m_inc,
        patch("utils.kyb_reverification._metric_gauge") as m_gauge,
    ):
        from utils.kyb_reverification import run_kyb_reverification_tick

        await run_kyb_reverification_tick()

    m_mark.assert_awaited_once_with(company_id="c1")
    m_inc.assert_any_call("spinr_corporate_kyb_reverification_due_total", {}, by=1)
    m_gauge.assert_any_call("spinr_corporate_kyb_reverification_pending", 1)


@pytest.mark.asyncio
async def test_never_changes_company_status():
    """The product decision is explicit: visibility only. This tick must
    never call anything that mutates corporate_accounts.status."""
    with (
        patch("utils.kyb_reverification.get_app_settings", AsyncMock(return_value=_SETTINGS_ON)),
        patch(
            "utils.kyb_reverification.list_companies_needing_kyb_reverification",
            AsyncMock(return_value=[_company()]),
        ),
        patch("utils.kyb_reverification.mark_kyb_reverify_flagged", AsyncMock()) as m_mark,
        patch("utils.kyb_reverification._metric_inc"),
        patch("utils.kyb_reverification._metric_gauge"),
    ):
        from utils.kyb_reverification import run_kyb_reverification_tick

        await run_kyb_reverification_tick()

    # The only write this tick performs is the claim flag — no status update
    # call exists anywhere in this module to assert against, which is itself
    # the point: mark_kyb_reverify_flagged's own signature only ever touches
    # kyb_reverify_flagged_at (see repositories/corporate_repo.py).
    m_mark.assert_awaited_once_with(company_id="c1")


@pytest.mark.asyncio
async def test_within_reflag_cooldown_is_skipped():
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    with (
        patch("utils.kyb_reverification.get_app_settings", AsyncMock(return_value=_SETTINGS_ON)),
        patch(
            "utils.kyb_reverification.list_companies_needing_kyb_reverification",
            AsyncMock(return_value=[_company(kyb_reverify_flagged_at=recent)]),
        ),
        patch("utils.kyb_reverification.mark_kyb_reverify_flagged", AsyncMock()) as m_mark,
        patch("utils.kyb_reverification._metric_inc") as m_inc,
        patch("utils.kyb_reverification._metric_gauge"),
    ):
        from utils.kyb_reverification import run_kyb_reverification_tick

        await run_kyb_reverification_tick()

    m_mark.assert_not_awaited()
    # Zero newly-flagged this tick -> the due_total counter must not fire.
    for call in m_inc.call_args_list:
        assert call.args[0] != "spinr_corporate_kyb_reverification_due_total"


@pytest.mark.asyncio
async def test_reflags_after_cooldown_elapsed():
    stale_flag = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    with (
        patch("utils.kyb_reverification.get_app_settings", AsyncMock(return_value=_SETTINGS_ON)),
        patch(
            "utils.kyb_reverification.list_companies_needing_kyb_reverification",
            AsyncMock(return_value=[_company(kyb_reverify_flagged_at=stale_flag)]),
        ),
        patch("utils.kyb_reverification.mark_kyb_reverify_flagged", AsyncMock()) as m_mark,
        patch("utils.kyb_reverification._metric_inc"),
        patch("utils.kyb_reverification._metric_gauge"),
    ):
        from utils.kyb_reverification import run_kyb_reverification_tick

        await run_kyb_reverification_tick()

    m_mark.assert_awaited_once_with(company_id="c1")


@pytest.mark.asyncio
async def test_kill_switch_short_circuits_before_any_query():
    with (
        patch(
            "utils.kyb_reverification.get_app_settings",
            AsyncMock(return_value={"corporate_kyb_reverification_enabled": False}),
        ),
        patch("utils.kyb_reverification.list_companies_needing_kyb_reverification", AsyncMock()) as m_list,
    ):
        from utils.kyb_reverification import run_kyb_reverification_tick

        await run_kyb_reverification_tick()

    m_list.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_threshold_months_is_passed_through():
    settings = {"corporate_kyb_reverification_enabled": True, "corporate_kyb_reverify_after_months": 6}
    with (
        patch("utils.kyb_reverification.get_app_settings", AsyncMock(return_value=settings)),
        patch(
            "utils.kyb_reverification.list_companies_needing_kyb_reverification",
            AsyncMock(return_value=[]),
        ) as m_list,
        patch("utils.kyb_reverification._metric_inc"),
        patch("utils.kyb_reverification._metric_gauge"),
    ):
        from utils.kyb_reverification import run_kyb_reverification_tick

        await run_kyb_reverification_tick()

    m_list.assert_awaited_once()
    cutoff_iso = m_list.call_args.kwargs["reviewed_before_iso"]
    cutoff_dt = datetime.fromisoformat(cutoff_iso)
    # ~6 months back (30-day months, same approximation the module itself
    # uses) rather than the 12-month default.
    expected = datetime.now(timezone.utc) - timedelta(days=6 * 30)
    assert abs((cutoff_dt - expected).total_seconds()) < 60


@pytest.mark.asyncio
async def test_flagging_failure_for_one_company_does_not_block_others():
    with (
        patch("utils.kyb_reverification.get_app_settings", AsyncMock(return_value=_SETTINGS_ON)),
        patch(
            "utils.kyb_reverification.list_companies_needing_kyb_reverification",
            AsyncMock(return_value=[_company(id="c1"), _company(id="c2")]),
        ),
        patch(
            "utils.kyb_reverification.mark_kyb_reverify_flagged",
            AsyncMock(side_effect=[Exception("db down"), None]),
        ) as m_mark,
        patch("utils.kyb_reverification._metric_inc") as m_inc,
        patch("utils.kyb_reverification._metric_gauge"),
    ):
        from utils.kyb_reverification import run_kyb_reverification_tick

        await run_kyb_reverification_tick()

    assert m_mark.await_count == 2
    # Only c2 succeeded -> exactly 1 newly-flagged this tick.
    m_inc.assert_any_call("spinr_corporate_kyb_reverification_due_total", {}, by=1)
