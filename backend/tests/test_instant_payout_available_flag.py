"""GET /drivers/balance keeps ``instant_payout_available`` for API compatibility,
but it is always False: Spinr pays drivers weekly only (owner decision
2026-09-25), so the balance no longer reads ``service_areas`` to compute it.

Patch targets follow test_earnings_coverage.py: `backend.db_supabase.get_rows`
is shared by every importer (earnings, payouts, _shared); the raw supabase
client (incentive-claims lookup) is the conftest `mock_supabase_client`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

USER_ID = "user_ipa_flag"
DRIVER_ID = "driver_ipa_flag"
SA_ID = "sa_ipa_flag"


async def _balance(driver: dict, mock_supabase_client) -> tuple[dict, list[str]]:
    """Run get_driver_balance; return the response and every table get_rows read."""
    from backend.routes.drivers import get_driver_balance

    tables: list[str] = []

    async def get_rows(table, filters=None, **kw):
        tables.append(table)
        if table == "drivers":
            return [driver]
        if table == "service_areas":
            # An area with the old kill switch ON must still not surface as available.
            return [{"id": SA_ID, "instant_payout_enabled": True}]
        return []

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
        patch("backend.db_supabase.count_documents", AsyncMock(return_value=0)),
        patch("backend.db_supabase.supabase", mock_supabase_client),
    ):
        return await get_driver_balance(current_user={"id": USER_ID}), tables


class TestInstantPayoutAvailableAlwaysFalse:
    @pytest.mark.parametrize("sa_id", [None, "", SA_ID])
    async def test_flag_is_false_and_service_areas_not_read(self, sa_id, mock_supabase_client):
        driver = {"id": DRIVER_ID, "user_id": USER_ID, "service_area_id": sa_id}
        result, tables = await _balance(driver, mock_supabase_client)
        assert "instant_payout_available" in result
        assert result["instant_payout_available"] is False
        assert "service_areas" not in tables

    async def test_area_verdict_helper_is_gone(self):
        from backend.routes.drivers import _shared

        assert not hasattr(_shared, "_instant_payout_area_verdict")
