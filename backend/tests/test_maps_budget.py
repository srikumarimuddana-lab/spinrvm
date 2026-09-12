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


class TestReserveBudget:
    """ACTION_ITEMS.md C104: reserve_budget() closes the check_budget() +
    record_call() check-then-act race via a single atomic Redis Lua script
    (run through redis_eval()). These tests mock redis_eval() itself, so
    they pin reserve_budget()'s own argument-construction and
    response-parsing logic and its fallback behavior — they cannot prove
    real cross-request atomicity, since this repo's unit-test tier has no
    real Redis to run the Lua script against (same limitation
    utils/h3_location_index.py's own Lua-based upsert_driver() tests carry;
    see this item's Change Impact Log for what that leaves unverified)."""

    @pytest.mark.asyncio
    async def test_allowed_when_lua_reports_under_budget(self):
        eval_mock = AsyncMock(return_value=[1, "2.50"])
        with (
            patch.object(mb, "redis_eval", eval_mock),
            patch.object(mb, "_daily_budget_usd", return_value=5.0),
        ):
            allowed, spent, budget = await mb.reserve_budget("directions")

        assert allowed is True
        assert spent == 2.50
        assert budget == 5.0
        eval_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_denied_when_lua_reports_over_budget(self):
        eval_mock = AsyncMock(return_value=[0, "6.10"])
        with (
            patch.object(mb, "redis_eval", eval_mock),
            patch.object(mb, "_daily_budget_usd", return_value=5.0),
        ):
            allowed, spent, budget = await mb.reserve_budget("directions")

        assert allowed is False
        assert spent == 6.10
        assert budget == 5.0

    @pytest.mark.asyncio
    async def test_script_args_use_the_right_sku_index_and_key_order(self):
        """KEYS must be every tracked SKU's key in _PRICE_USD's fixed order;
        the 1-based incr-index argument must point at the SKU being
        reserved ("directions" is the 6th of 7 entries)."""
        skus = list(mb._PRICE_USD.items())
        expected_keys = [mb._key(sku) for sku, _price in skus]
        expected_incr_idx = next(i for i, (sku, _price) in enumerate(skus, start=1) if sku == "directions")

        eval_mock = AsyncMock(return_value=[1, "0.0"])
        with (
            patch.object(mb, "redis_eval", eval_mock),
            patch.object(mb, "_daily_budget_usd", return_value=5.0),
        ):
            await mb.reserve_budget("directions")

        args, _kwargs = eval_mock.call_args
        _script, numkeys, *rest = args
        assert numkeys == len(skus)
        assert list(rest[: len(skus)]) == expected_keys
        prices_and_tail = rest[len(skus) :]
        assert prices_and_tail[-3] == str(expected_incr_idx)
        assert prices_and_tail[-2] == "5.0"

    @pytest.mark.asyncio
    async def test_falls_back_to_check_and_record_when_redis_unconfigured(self):
        """redis_eval() raises RuntimeError when REDIS_URL is unset — there
        is no in-process Lua interpreter (see its own docstring). This must
        degrade to the old, non-atomic pair rather than raise or deny."""
        check_mock = AsyncMock(return_value=(True, 1.0, 5.0))
        record_mock = AsyncMock()
        with (
            patch.object(mb, "redis_eval", AsyncMock(side_effect=RuntimeError("no redis"))),
            patch.object(mb, "check_budget", check_mock),
            patch.object(mb, "record_call", record_mock),
        ):
            allowed, spent, budget = await mb.reserve_budget("directions")

        assert (allowed, spent, budget) == (True, 1.0, 5.0)
        record_mock.assert_awaited_once_with("directions")

    @pytest.mark.asyncio
    async def test_fallback_does_not_record_when_check_denies(self):
        check_mock = AsyncMock(return_value=(False, 5.5, 5.0))
        record_mock = AsyncMock()
        with (
            patch.object(mb, "redis_eval", AsyncMock(side_effect=RuntimeError("no redis"))),
            patch.object(mb, "check_budget", check_mock),
            patch.object(mb, "record_call", record_mock),
        ):
            allowed, spent, budget = await mb.reserve_budget("directions")

        assert allowed is False
        record_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_on_any_other_redis_eval_error_too(self):
        """A configured-but-unavailable Redis (redis_eval's other error
        path) must fail open the same way as the unconfigured case, not
        propagate and 500 the caller."""
        check_mock = AsyncMock(return_value=(True, 0.5, 5.0))
        record_mock = AsyncMock()
        with (
            patch.object(mb, "redis_eval", AsyncMock(side_effect=Exception("connection reset"))),
            patch.object(mb, "check_budget", check_mock),
            patch.object(mb, "record_call", record_mock),
        ):
            allowed, spent, budget = await mb.reserve_budget("directions")

        assert (allowed, spent, budget) == (True, 0.5, 5.0)
        record_mock.assert_awaited_once_with("directions")
