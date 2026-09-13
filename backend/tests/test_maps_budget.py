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

import asyncio
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


# ── C104: atomic check-and-increment (reserve_budget) ───────────────────────
#
# check_budget() (a read) followed later by record_call() (a write) is a
# classic check-then-act race: N concurrent callers can all read "under
# budget" before any of them increments, letting all N through and
# collectively blowing well past MAPS_DAILY_BUDGET_USD. These tests run
# entirely against the in-process dict fallback (REDIS_URL is unset in this
# suite) — see the C104 Change Impact Log for what this does NOT verify
# (the real Redis Lua-script path has no coverage here).


async def _racy_check_then_increment(mock_redis, sku: str) -> bool:
    """Reference reimplementation of the OLD check_budget()+record_call()
    pattern, with an explicit yield between the two steps standing in for
    the real, unbounded-latency Google HTTP call that sits between them in
    every real call site. Used only to demonstrate the race this ticket
    closes -- production code never looked like this function.
    """
    allowed, _spent, budget = await mb.check_budget()
    if not allowed:
        return False
    await asyncio.sleep(0)  # yield -- lets sibling callers run their own "check" here
    await mb.record_call(sku)
    return True


@pytest.mark.asyncio
async def test_old_check_then_act_pattern_overshoots_budget_under_concurrency(mock_redis, monkeypatch):
    """Demonstrates the bug this ticket fixes: with a yield between the read
    and the write (standing in for the real Google HTTP call), concurrent
    callers all observe the same stale "under budget" reading and all get
    admitted -- total spend sails past the cap by far more than one call's
    worth. This is the failure mode reserve_budget() (tested below) closes.
    """
    monkeypatch.setattr(mb, "_daily_budget_usd", lambda: 0.05)  # cap = $0.05
    price = mb._PRICE_USD["geocode"]  # $0.005/call -> cap allows ~10 calls

    results = await asyncio.gather(*(_racy_check_then_increment(mock_redis, "geocode") for _ in range(40)))

    admitted = sum(results)
    spent = await mb.estimate_today_usd()
    # All 40 raced past the same stale read and got admitted -- nowhere
    # near the ~10-call cap the budget should have enforced.
    assert admitted == 40
    assert spent == pytest.approx(40 * price, rel=1e-6)
    assert spent > 0.05 + price  # overshoots by far more than one call's worth


@pytest.mark.asyncio
async def test_reserve_budget_never_overshoots_by_more_than_one_call_under_concurrency(mock_redis, monkeypatch):
    """C104 fix: reserve_budget() folds the check and the increment into one
    atomic step, so the same 40-concurrent-callers scenario above must admit
    only as many calls as a correct, perfectly-sequential caller would have
    admitted -- never more, and total spend must never exceed the cap by
    more than one call's worth (the call that crosses the threshold is
    still allowed; the one after it is not -- same tolerance a single
    sequential caller would have).

    The "correct" admitted count is computed by replaying the SAME
    before-total-vs-budget arithmetic reserve_budget() itself uses
    (integer call count times price, not accumulated float addition, which
    drifts differently) rather than hardcoding a count.
    """
    budget = 0.05
    monkeypatch.setattr(mb, "_daily_budget_usd", lambda: budget)
    price = mb._PRICE_USD["geocode"]  # $0.005/call -> ~10 calls fit under $0.05

    expected_admitted = 0
    for _ in range(40):
        before_total = expected_admitted * price
        if before_total >= budget:
            break
        expected_admitted += 1
    seq_total = expected_admitted * price

    results = await asyncio.gather(*(mb.reserve_budget("geocode") for _ in range(40)))

    admitted = [r for r in results if r[0]]
    rejected = [r for r in results if not r[0]]
    spent = await mb.estimate_today_usd()

    assert 0 < expected_admitted < 40  # sanity: the scenario is neither trivial nor unbounded
    assert len(admitted) == expected_admitted  # matches sequential ground truth exactly, not 40
    assert len(rejected) == 40 - expected_admitted
    # Total spend matches exactly what was admitted -- no lost or duplicated
    # increments from concurrent reservations stepping on each other.
    assert spent == pytest.approx(seq_total, rel=1e-9)
    # And it never overshoots the cap by more than one call's worth.
    assert spent < budget + price
    # Every rejected reservation reports the SAME pre-cap total (nothing was
    # charged for a rejected attempt) and the real daily budget.
    for _allowed, rejected_spent, rejected_budget in rejected:
        assert rejected_spent == pytest.approx(seq_total, rel=1e-9)
        assert rejected_budget == budget


@pytest.mark.asyncio
async def test_reserve_budget_rejects_without_recording_when_over_budget(mock_redis, monkeypatch):
    monkeypatch.setattr(mb, "_daily_budget_usd", lambda: 0.001)
    await mb.record_call("geocode")  # $0.005 spent, already over the $0.001 cap

    allowed, spent, budget = await mb.reserve_budget("geocode")

    assert allowed is False
    assert spent == pytest.approx(mb._PRICE_USD["geocode"], rel=1e-6)
    assert budget == 0.001
    # Rejected -- the counter must be unchanged, not incremented-then-rolled-back
    # into some other visible intermediate state.
    assert await mb.estimate_today_usd() == pytest.approx(mb._PRICE_USD["geocode"], rel=1e-6)


@pytest.mark.asyncio
async def test_reserve_budget_allows_and_records_under_ceiling(mock_redis, monkeypatch):
    monkeypatch.setattr(mb, "_daily_budget_usd", lambda: 1.0)

    allowed, spent, budget = await mb.reserve_budget("directions")

    assert allowed is True
    assert spent == pytest.approx(mb._PRICE_USD["directions"], rel=1e-6)
    assert budget == 1.0
    assert await mb.estimate_today_usd() == pytest.approx(mb._PRICE_USD["directions"], rel=1e-6)


@pytest.mark.asyncio
async def test_reserve_budget_falls_open_on_a_real_redis_error(mock_redis, monkeypatch):
    """Matches check_budget()'s/record_call()'s documented "errors fail
    open" contract -- a genuine (non-RuntimeError) failure from redis_eval
    must never block Maps traffic."""

    async def _boom(*_a, **_kw):
        raise ConnectionError("redis down")

    monkeypatch.setattr(mb, "redis_eval", _boom)

    allowed, spent, budget = await mb.reserve_budget("geocode")

    assert allowed is True
    assert spent == 0.0
    assert budget == mb._daily_budget_usd()
