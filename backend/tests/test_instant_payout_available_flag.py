"""GET /drivers/balance's instant_payout_available must match the instant-payout gate.

Regression for the spinr-money-auditor SHOULD-FIX on #5791 (ROADMAP N22):
request_instant_payout refuses (403) a driver with no service_area_id or one
that matches no service_areas row, but the balance flag still said
"available" for them. Both now read _shared._instant_payout_area_verdict.

Patch targets follow test_earnings_coverage.py: `backend.db_supabase.get_rows`
is shared by every importer (earnings, payouts, _shared); the raw supabase
client (incentive-claims lookup) is the conftest `mock_supabase_client`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.anyio

USER_ID = "user_ipa_flag"
DRIVER_ID = "driver_ipa_flag"
SA_ID = "sa_ipa_flag"


def _driver(**extra) -> dict:
    return {"id": DRIVER_ID, "user_id": USER_ID, **extra}


def _get_rows(driver: dict, service_areas):
    """get_rows stand-in. `service_areas` is a list of rows, or an Exception to raise."""

    async def get_rows(table, filters=None, **kw):
        if table == "drivers":
            return [driver]
        if table == "service_areas":
            if isinstance(service_areas, Exception):
                raise service_areas
            return service_areas
        return []

    return get_rows


async def _balance(driver: dict, service_areas, mock_supabase_client) -> dict:
    from backend.routes.drivers import get_driver_balance

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows(driver, service_areas))),
        patch("backend.db_supabase.count_documents", AsyncMock(return_value=0)),
        patch("backend.db_supabase.supabase", mock_supabase_client),
    ):
        return await get_driver_balance(current_user={"id": USER_ID})


async def _gate_allows(driver: dict, service_areas) -> bool:
    from backend.routes.drivers.payouts import _require_instant_payout_enabled

    with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows(driver, service_areas))):
        try:
            await _require_instant_payout_enabled(driver)
        except HTTPException as exc:
            assert exc.status_code == 403
            return False
    return True


class TestInstantPayoutAvailableFlag:
    @pytest.mark.parametrize("sa_id", [None, ""])
    async def test_no_service_area_is_not_available(self, sa_id, mock_supabase_client):
        result = await _balance(_driver(service_area_id=sa_id), [], mock_supabase_client)
        assert result["instant_payout_available"] is False

    async def test_unresolvable_service_area_is_not_available(self, mock_supabase_client):
        result = await _balance(_driver(service_area_id="sa_does_not_exist"), [], mock_supabase_client)
        assert result["instant_payout_available"] is False

    @pytest.mark.parametrize("enabled", [True, None])
    async def test_valid_area_with_feature_on_is_available(self, enabled, mock_supabase_client):
        # None = column absent/NULL: the kill switch is opt-out (DEFAULT TRUE),
        # only an explicit False disables, same as the gate.
        row = {"id": SA_ID, "instant_payout_enabled": enabled}
        result = await _balance(_driver(service_area_id=SA_ID), [row], mock_supabase_client)
        assert result["instant_payout_available"] is True

    async def test_kill_switch_off_is_not_available(self, mock_supabase_client):
        row = {"id": SA_ID, "instant_payout_enabled": False}
        result = await _balance(_driver(service_area_id=SA_ID), [row], mock_supabase_client)
        assert result["instant_payout_available"] is False

    async def test_area_lookup_failure_reports_false_and_logs_error(self, mock_supabase_client):
        # Chosen behaviour: false + error log, NOT 503 — a 503 would blank the
        # whole balance (and the payout screen reads its onboarding status
        # from this same response) over an advisory flag.
        with patch("backend.routes.drivers.earnings.logger") as log:
            result = await _balance(
                _driver(service_area_id=SA_ID), RuntimeError("service_areas unreachable"), mock_supabase_client
            )
        assert result["instant_payout_available"] is False
        # The money fields still render.
        assert result["payable_balance"] == "0.00"
        log.error.assert_called_once()
        assert "instant_payout_available" in log.error.call_args.args[0]
        assert log.error.call_args.kwargs["extra"]["driver_id"] == DRIVER_ID

    @pytest.mark.parametrize(
        "sa_id, rows",
        [
            (None, []),
            ("sa_does_not_exist", []),
            (SA_ID, [{"id": SA_ID, "instant_payout_enabled": True}]),
            (SA_ID, [{"id": SA_ID, "instant_payout_enabled": False}]),
            (SA_ID, [{"id": SA_ID}]),
        ],
    )
    async def test_flag_matches_request_gate(self, sa_id, rows, mock_supabase_client):
        driver = _driver(service_area_id=sa_id)
        result = await _balance(driver, rows, mock_supabase_client)
        assert result["instant_payout_available"] is await _gate_allows(driver, rows)

    async def test_daily_cap_is_not_part_of_the_flag(self, mock_supabase_client):
        # The flag is "feature available to you", not "cap room left": the
        # balance endpoint never reads settings or today's instant payouts.
        row = {"id": SA_ID, "instant_payout_enabled": True}
        with patch("backend.settings_loader.get_app_settings", AsyncMock()) as settings:
            result = await _balance(_driver(service_area_id=SA_ID), [row], mock_supabase_client)
        assert result["instant_payout_available"] is True
        settings.assert_not_awaited()
