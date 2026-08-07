"""Tests for utils/reconciliation.py — daily Stripe <-> DB <-> wallet
reconciliation loop.

No dedicated test file existed before this one (0% direct coverage; the
module's local, function-scoped dual imports mean nothing outside this file
ever exercised it). Money-adjacent per CLAUDE.md: this loop is the only
alarm for a Stripe/financial_events drift going undetected, so these tests
pin every fail-open/fail-closed branch and the discrepancy-threshold math,
not just the happy path.

Patch targets: the module imports `db_supabase.run_sync`,
`supabase_client.supabase`, `settings_loader.get_app_settings`, and `stripe`
locally inside each function body (not at module scope), so they're patched
at their *source* module, not `utils.reconciliation.*` — the one exception
is `redis_set_nx`, which the module imports at the top level and is patched
as `utils.reconciliation.redis_set_nx` accordingly.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


async def _passthrough_run_sync(fn):
    return fn()


def _pi(id_: str = "pi_1", status: str = "succeeded", amount: int = 1000) -> MagicMock:
    m = MagicMock()
    m.id = id_
    m.status = status
    m.amount = amount
    return m


def _stripe_page(pis: list, has_more: bool = False) -> MagicMock:
    page = MagicMock()
    page.data = pis
    page.has_more = has_more
    return page


# ── reconciliation_loop ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_calls_tick_each_iteration_and_sleeps():
    tick_calls = 0

    async def fake_tick():
        nonlocal tick_calls
        tick_calls += 1

    sleep_calls: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    with (
        patch("utils.reconciliation._maybe_run_tick", fake_tick),
        patch("utils.reconciliation.asyncio.sleep", fake_sleep),
    ):
        from utils.reconciliation import reconciliation_loop

        with pytest.raises(asyncio.CancelledError):
            await reconciliation_loop()

    assert tick_calls == 2
    assert sleep_calls == [60, 60]


@pytest.mark.asyncio
async def test_loop_logs_error_and_continues_when_tick_raises():
    """A tick failure must not crash the loop -- it's caught, logged, and the
    loop sleeps and tries again next iteration."""
    tick_calls = 0

    async def failing_tick():
        nonlocal tick_calls
        tick_calls += 1
        raise RuntimeError("boom")

    sleep_calls: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    with (
        patch("utils.reconciliation._maybe_run_tick", failing_tick),
        patch("utils.reconciliation.asyncio.sleep", fake_sleep),
    ):
        from utils.reconciliation import reconciliation_loop

        with pytest.raises(asyncio.CancelledError):
            await reconciliation_loop()

    # Loop survived two failing ticks -- proves the exception was swallowed,
    # not propagated.
    assert tick_calls == 2


# ── _maybe_run_tick ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_maybe_run_tick_before_2am_skips_entirely():
    fake_now = MagicMock()
    fake_now.now.return_value = datetime(2026, 1, 15, 1, 59, tzinfo=timezone.utc)

    lock_mock = AsyncMock()

    with (
        patch("utils.reconciliation.datetime", fake_now),
        patch("utils.reconciliation.redis_set_nx", lock_mock),
    ):
        from utils.reconciliation import _maybe_run_tick

        await _maybe_run_tick()

    lock_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_run_tick_lock_not_acquired_skips_reconciliation():
    fake_now = MagicMock()
    fake_now.now.return_value = datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc)

    run_mock = AsyncMock()

    with (
        patch("utils.reconciliation.datetime", fake_now),
        patch("utils.reconciliation.redis_set_nx", AsyncMock(return_value=False)),
        patch("utils.reconciliation._run_reconciliation", run_mock),
    ):
        from utils.reconciliation import _maybe_run_tick

        await _maybe_run_tick()

    run_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_run_tick_lock_acquired_runs_for_yesterday():
    fake_now = MagicMock()
    fake_now.now.return_value = datetime(2026, 1, 15, 2, 30, tzinfo=timezone.utc)

    run_mock = AsyncMock()

    with (
        patch("utils.reconciliation.datetime", fake_now),
        patch("utils.reconciliation.redis_set_nx", AsyncMock(return_value=True)),
        patch("utils.reconciliation._run_reconciliation", run_mock),
    ):
        from utils.reconciliation import _maybe_run_tick

        await _maybe_run_tick()

    run_mock.assert_awaited_once_with(date(2026, 1, 14))


# ── _run_reconciliation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_reconciliation_skips_when_stripe_key_not_configured():
    stripe_mock = AsyncMock(side_effect=RuntimeError("stripe_secret_key not configured"))
    db_mock = AsyncMock()

    with (
        patch("utils.reconciliation._sum_stripe_intents", stripe_mock),
        patch("utils.reconciliation._sum_financial_events", db_mock),
    ):
        from utils.reconciliation import _run_reconciliation

        await _run_reconciliation(date(2026, 1, 14))

    db_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_reconciliation_returns_early_on_other_stripe_runtime_error():
    stripe_mock = AsyncMock(side_effect=RuntimeError("Stripe API 500"))
    db_mock = AsyncMock()

    with (
        patch("utils.reconciliation._sum_stripe_intents", stripe_mock),
        patch("utils.reconciliation._sum_financial_events", db_mock),
    ):
        from utils.reconciliation import _run_reconciliation

        await _run_reconciliation(date(2026, 1, 14))

    db_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_reconciliation_returns_early_on_generic_stripe_exception():
    stripe_mock = AsyncMock(side_effect=ValueError("unexpected"))
    db_mock = AsyncMock()

    with (
        patch("utils.reconciliation._sum_stripe_intents", stripe_mock),
        patch("utils.reconciliation._sum_financial_events", db_mock),
    ):
        from utils.reconciliation import _run_reconciliation

        await _run_reconciliation(date(2026, 1, 14))

    db_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_reconciliation_returns_early_when_financial_events_query_fails():
    stripe_mock = AsyncMock(return_value=5000)
    db_mock = AsyncMock(side_effect=RuntimeError("DB down"))
    record_mock = AsyncMock()

    with (
        patch("utils.reconciliation._sum_stripe_intents", stripe_mock),
        patch("utils.reconciliation._sum_financial_events", db_mock),
        patch("utils.reconciliation._record_discrepancy", record_mock),
    ):
        from utils.reconciliation import _run_reconciliation

        await _run_reconciliation(date(2026, 1, 14))

    record_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_reconciliation_records_discrepancy_when_over_threshold():
    stripe_mock = AsyncMock(return_value=5000)
    db_mock = AsyncMock(return_value=4998)  # 2-cent drift, over the 1-cent threshold
    record_mock = AsyncMock()

    with (
        patch("utils.reconciliation._sum_stripe_intents", stripe_mock),
        patch("utils.reconciliation._sum_financial_events", db_mock),
        patch("utils.reconciliation._record_discrepancy", record_mock),
    ):
        from utils.reconciliation import _run_reconciliation

        await _run_reconciliation(date(2026, 1, 14))

    record_mock.assert_awaited_once_with(date(2026, 1, 14), 5000, 4998, 2)


@pytest.mark.asyncio
async def test_run_reconciliation_at_threshold_boundary_does_not_alert():
    """discrepancy == threshold (1 cent) must NOT trigger -- the guard is
    strictly '> threshold', not '>='."""
    stripe_mock = AsyncMock(return_value=5000)
    db_mock = AsyncMock(return_value=4999)  # exactly 1-cent drift
    record_mock = AsyncMock()

    with (
        patch("utils.reconciliation._sum_stripe_intents", stripe_mock),
        patch("utils.reconciliation._sum_financial_events", db_mock),
        patch("utils.reconciliation._record_discrepancy", record_mock),
    ):
        from utils.reconciliation import _run_reconciliation

        await _run_reconciliation(date(2026, 1, 14))

    record_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_reconciliation_no_discrepancy_when_totals_match():
    stripe_mock = AsyncMock(return_value=5000)
    db_mock = AsyncMock(return_value=5000)
    record_mock = AsyncMock()

    with (
        patch("utils.reconciliation._sum_stripe_intents", stripe_mock),
        patch("utils.reconciliation._sum_financial_events", db_mock),
        patch("utils.reconciliation._record_discrepancy", record_mock),
    ):
        from utils.reconciliation import _run_reconciliation

        await _run_reconciliation(date(2026, 1, 14))

    record_mock.assert_not_awaited()


# ── _sum_stripe_intents ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sum_stripe_intents_raises_when_no_key_configured():
    with patch("settings_loader.get_app_settings", AsyncMock(return_value={})):
        from utils.reconciliation import _sum_stripe_intents

        with pytest.raises(RuntimeError, match="stripe_secret_key not configured"):
            await _sum_stripe_intents(date(2026, 1, 14))


@pytest.mark.asyncio
async def test_sum_stripe_intents_sums_only_succeeded_single_page():
    stripe_mock = MagicMock()
    stripe_mock.PaymentIntent.list.return_value = _stripe_page(
        [_pi("pi_1", "succeeded", 1000), _pi("pi_2", "requires_action", 500), _pi("pi_3", "succeeded", 250)],
        has_more=False,
    )

    with (
        patch("settings_loader.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.reconciliation import _sum_stripe_intents

        total = await _sum_stripe_intents(date(2026, 1, 14))

    assert total == 1250
    stripe_mock.PaymentIntent.list.assert_called_once()
    assert "starting_after" not in stripe_mock.PaymentIntent.list.call_args.kwargs


@pytest.mark.asyncio
async def test_sum_stripe_intents_paginates_across_pages():
    page1 = _stripe_page([_pi("pi_1", "succeeded", 1000)], has_more=True)
    page2 = _stripe_page([_pi("pi_2", "succeeded", 500)], has_more=False)

    stripe_mock = MagicMock()
    stripe_mock.PaymentIntent.list.side_effect = [page1, page2]

    with (
        patch("settings_loader.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        from utils.reconciliation import _sum_stripe_intents

        total = await _sum_stripe_intents(date(2026, 1, 14))

    assert total == 1500
    assert stripe_mock.PaymentIntent.list.call_count == 2
    second_call_kwargs = stripe_mock.PaymentIntent.list.call_args_list[1].kwargs
    assert second_call_kwargs["starting_after"] == "pi_1"


# ── _sum_financial_events ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sum_financial_events_sums_delta_cents_and_skips_none():
    mock_client = MagicMock()
    rows = mock_client.table.return_value.select.return_value.eq.return_value.gte.return_value.lt.return_value.execute.return_value
    rows.data = [{"delta_cents": 100}, {"delta_cents": None}, {"delta_cents": 50}]

    with (
        patch("db_supabase.run_sync", AsyncMock(side_effect=_passthrough_run_sync)),
        patch("supabase_client.supabase", mock_client),
    ):
        from utils.reconciliation import _sum_financial_events

        total = await _sum_financial_events(date(2026, 1, 14), "stripe_charge")

    assert total == 150
    mock_client.table.assert_called_with("financial_events")
    mock_client.table.return_value.select.return_value.eq.assert_called_with("event_type", "stripe_charge")


@pytest.mark.asyncio
async def test_sum_financial_events_returns_zero_when_no_rows():
    mock_client = MagicMock()
    rows = mock_client.table.return_value.select.return_value.eq.return_value.gte.return_value.lt.return_value.execute.return_value
    rows.data = None

    with (
        patch("db_supabase.run_sync", AsyncMock(side_effect=_passthrough_run_sync)),
        patch("supabase_client.supabase", mock_client),
    ):
        from utils.reconciliation import _sum_financial_events

        total = await _sum_financial_events(date(2026, 1, 14), "stripe_charge")

    assert total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_date", "expected_end"),
    [
        (date(2026, 1, 31), "2026-02-01"),  # 31-day month → next month
        (date(2026, 2, 28), "2026-03-01"),  # non-leap February
        (date(2028, 2, 29), "2028-03-01"),  # leap-day
        (date(2026, 12, 31), "2027-01-01"),  # year boundary
        (date(2026, 4, 30), "2026-05-01"),  # 30-day month
    ],
)
async def test_sum_financial_events_month_end_boundary(run_date, expected_end):
    """Regression: datetime(y, m, day + 1) raised ValueError on the last day of
    every month, killing the whole reconciliation tick ~12 nights a year. The
    window must roll into the next month/year via timedelta instead."""
    mock_client = MagicMock()
    rows = mock_client.table.return_value.select.return_value.eq.return_value.gte.return_value.lt.return_value.execute.return_value
    rows.data = [{"delta_cents": 100}]

    with (
        patch("db_supabase.run_sync", AsyncMock(side_effect=_passthrough_run_sync)),
        patch("supabase_client.supabase", mock_client),
    ):
        from utils.reconciliation import _sum_financial_events

        total = await _sum_financial_events(run_date, "stripe_charge")

    assert total == 100
    gte_call = mock_client.table.return_value.select.return_value.eq.return_value.gte
    lt_call = gte_call.return_value.lt
    assert gte_call.call_args.args == ("created_at", f"{run_date.isoformat()}T00:00:00+00:00")
    assert lt_call.call_args.args == ("created_at", f"{expected_end}T00:00:00+00:00")


# ── _record_discrepancy ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_discrepancy_inserts_expected_row_shape():
    mock_client = MagicMock()

    with (
        patch("db_supabase.run_sync", AsyncMock(side_effect=_passthrough_run_sync)),
        patch("supabase_client.supabase", mock_client),
    ):
        from utils.reconciliation import _record_discrepancy

        await _record_discrepancy(date(2026, 1, 14), 5000, 4998, 2)

    mock_client.table.assert_called_with("reconciliation_discrepancies")
    inserted = mock_client.table.return_value.insert.call_args[0][0]
    assert inserted == {
        "date": "2026-01-14",
        "stripe_total_cents": 5000,
        "db_total_cents": 4998,
        "discrepancy_cents": 2,
        "status": "open",
    }


@pytest.mark.asyncio
async def test_record_discrepancy_swallows_insert_failure():
    """A failure writing the audit row must not raise -- the error log in
    _run_reconciliation is the primary alert mechanism, per the module's own
    docstring."""
    with (
        patch("db_supabase.run_sync", AsyncMock(side_effect=RuntimeError("insert failed"))),
        patch("supabase_client.supabase", MagicMock()),
    ):
        from utils.reconciliation import _record_discrepancy

        await _record_discrepancy(date(2026, 1, 14), 5000, 4998, 2)
