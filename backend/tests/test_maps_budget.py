"""Unit tests for backend/utils/maps_budget.py's spend estimation.

estimate_today_usd() previously issued one redis_get per SKU (7 sequential
round-trips as of the "distance_matrix" SKU added in R4,
docs/audit/ride-experience/ROADMAP.md). check_budget() is now reachable from
routes/rides/matching.py's dispatch-matching hot path, which has a
purpose-built 1.2s timeout carve-out for the Distance Matrix call - sequential
round-trips there eat directly into that budget. These pin the fix (a single
redis_mget batch call) and its fail-open behavior on a Redis error, since
redis_mget raises on failure unlike redis_get's own per-key soft-fail.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import maps_budget as mb


@pytest.mark.asyncio
async def test_estimate_today_usd_sums_across_skus_in_one_mget_call():
    mget_mock = AsyncMock(return_value=["10", "5", None, "2", "0", "1", "3"])
    with patch.object(mb, "redis_mget", mget_mock):
        total = await mb.estimate_today_usd()

    mget_mock.assert_awaited_once()
    # 10*0.00283 + 5*0.017 + 0(None->skipped) + 2*0.005 + 0*0.032 + 1*0.005 + 3*0.010
    expected = 10 * 0.00283 + 5 * 0.017 + 2 * 0.005 + 0 * 0.032 + 1 * 0.005 + 3 * 0.010
    assert round(total, 6) == round(expected, 6)


@pytest.mark.asyncio
async def test_estimate_today_usd_fails_open_on_redis_error():
    with patch.object(mb, "redis_mget", AsyncMock(side_effect=Exception("redis down"))):
        total = await mb.estimate_today_usd()

    assert total == 0.0


@pytest.mark.asyncio
async def test_estimate_today_usd_ignores_malformed_values():
    # One malformed ("not-a-number") value must not poison the total or raise.
    mget_mock = AsyncMock(return_value=["not-a-number"] * len(mb._PRICE_USD))
    with patch.object(mb, "redis_mget", mget_mock):
        total = await mb.estimate_today_usd()

    assert total == 0.0


@pytest.mark.asyncio
async def test_check_budget_allowed_when_under_ceiling():
    with (
        patch.object(mb, "estimate_today_usd", AsyncMock(return_value=1.0)),
        patch.object(mb, "_daily_budget_usd", return_value=5.0),
    ):
        allowed, spent, budget = await mb.check_budget()

    assert allowed is True
    assert spent == 1.0
    assert budget == 5.0


@pytest.mark.asyncio
async def test_check_budget_not_allowed_when_over_ceiling():
    with (
        patch.object(mb, "estimate_today_usd", AsyncMock(return_value=5.5)),
        patch.object(mb, "_daily_budget_usd", return_value=5.0),
    ):
        allowed, spent, budget = await mb.check_budget()

    assert allowed is False
    assert spent == 5.5


@pytest.mark.asyncio
async def test_distance_matrix_is_a_registered_sku():
    """R4 regression: distance_matrix was previously outside the Sku Literal
    entirely, so estimate_today_usd() could not total it regardless of
    volume — the breaker was structurally blind, not merely unwired."""
    assert "distance_matrix" in mb._PRICE_USD
    assert mb._PRICE_USD["distance_matrix"] > 0
