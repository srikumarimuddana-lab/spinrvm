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


# ── _check_leg_completeness ──────────────────────────────────────────────
#
# This check is the ONLY alarm for a dead double-entry projection loop that
# still leaves the header ledger intact — the unbalanced-view check beside it
# can only see legs that exist, so a header that never got legs is invisible
# to it. Its failure mode is therefore not "misses a problem" but "cries wolf
# during the intended backfill until nobody reads it", which is what these
# tests pin.


def _queued(event_id: str, created_at: str = "2020-01-01T00:00:00+00:00") -> dict:
    return {"id": event_id, "created_at": created_at, "event_type": "stripe_charge"}


async def _run_leg_check(*, rows, previous_head, flag=True):
    """Drive _check_leg_completeness with a fake queue + marker store.

    Returns (captured_logger, marker_store) so a test can assert both what was
    logged and what the run left behind for the next one.
    """
    store: dict[str, str] = {}
    if previous_head is not None:
        store["spinr:ledger:projection:queue_head"] = previous_head

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, ttl=None):
        store[key] = value

    async def fake_delete(key):
        store.pop(key, None)

    rpc_mock = AsyncMock(return_value=rows) if not isinstance(rows, Exception) else AsyncMock(side_effect=rows)
    log = MagicMock()

    with (
        patch("services.ledger_service.double_entry_enabled", AsyncMock(return_value=flag)),
        patch("db_supabase.rpc", rpc_mock),
        patch("utils.redis_client.redis_get", fake_get),
        patch("utils.redis_client.redis_set", fake_set),
        patch("utils.redis_client.redis_delete", fake_delete),
        patch("utils.reconciliation.logger", log),
    ):
        from utils.reconciliation import _check_leg_completeness

        await _check_leg_completeness()

    return log, store, rpc_mock


@pytest.mark.asyncio
async def test_leg_check_silent_when_double_entry_flag_is_off():
    """Flag off means the queue is EXPECTED to grow without bound — alerting
    on it would be permanent noise on a feature that is deliberately dark."""
    log, store, rpc_mock = await _run_leg_check(rows=[_queued("e1")], previous_head=None, flag=False)

    rpc_mock.assert_not_awaited()
    log.error.assert_not_called()
    assert store == {}, "no marker should be written while the projection is off"


@pytest.mark.asyncio
async def test_leg_check_silent_when_rpc_absent():
    """Partial deploy (code live, migration 287 not applied) is not an alert."""
    log, _store, _rpc = await _run_leg_check(rows=RuntimeError("PGRST202"), previous_head=None)

    log.error.assert_not_called()


@pytest.mark.asyncio
async def test_leg_check_empty_queue_clears_the_marker():
    log, store, _rpc = await _run_leg_check(rows=[], previous_head="e_old")

    log.error.assert_not_called()
    assert store == {}, "a drained queue must not leave a stale head behind"


@pytest.mark.asyncio
async def test_leg_check_first_observation_records_head_without_alerting():
    """No prior marker (first run ever, or lost with an in-process Redis
    fallback across a restart) — cannot judge progress yet, so do not guess."""
    log, store, _rpc = await _run_leg_check(rows=[_queued("e1"), _queued("e2")], previous_head=None)

    log.error.assert_not_called()
    assert store["spinr:ledger:projection:queue_head"] == "e1"


@pytest.mark.asyncio
async def test_leg_check_does_not_alert_during_the_initial_backfill():
    """REGRESSION: the check used to alert on queue depth + absolute row age.

    When ledger_double_entry_enabled first turns on, EVERY historical header
    is leg-less and years old, so a depth/age rule fires at ERROR every night
    for the entire (intended, working) backfill — training on-call to ignore
    the one alert that means the projection is dead. A deep queue of ancient
    rows whose head is ADVANCING is a healthy backfill, not an incident.
    """
    ancient = [_queued(f"e{i}", "2019-06-01T00:00:00+00:00") for i in range(500)]
    log, store, _rpc = await _run_leg_check(rows=ancient, previous_head="e_from_yesterday")

    log.error.assert_not_called()
    assert store["spinr:ledger:projection:queue_head"] == "e0"


@pytest.mark.asyncio
async def test_leg_check_alerts_when_the_queue_head_has_not_moved():
    """The actual dead-loop signal: same head 24 h later. Nothing can pin the
    head legitimately — an undecomposable event is booked DEGRADED, not
    skipped, precisely so it leaves the queue."""
    log, store, _rpc = await _run_leg_check(rows=[_queued("stuck_head"), _queued("e2")], previous_head="stuck_head")

    log.error.assert_called_once()
    assert "NO progress" in log.error.call_args[0][0]
    assert "stuck_head" in log.error.call_args[0][0]
    assert store["spinr:ledger:projection:queue_head"] == "stuck_head"


@pytest.mark.asyncio
async def test_leg_check_alerts_on_a_frozen_head_even_with_a_short_queue():
    """Depth is reported, never a trigger: one stuck row is still a dead loop."""
    log, _store, _rpc = await _run_leg_check(rows=[_queued("only")], previous_head="only")

    log.error.assert_called_once()


# ── _check_entry_balance ─────────────────────────────────────────────────
#
# The trial-balance check: financial_event_entries journals whose debit and
# credit legs do not net to zero. Expected to return zero rows forever, which
# is exactly why it needs tests — a silently-broken check looks identical to a
# healthy ledger. It goes through the migration-293 RPC rather than filtering
# the view on MIN(created_at), which cannot be pushed below the view's
# GROUP BY and so re-aggregated the whole table nightly.


async def _run_entry_balance(*, rpc_result, run_date=date(2026, 1, 14)):
    rpc_mock = (
        AsyncMock(side_effect=rpc_result) if isinstance(rpc_result, Exception) else AsyncMock(return_value=rpc_result)
    )
    log = MagicMock()

    with (
        patch("db_supabase.rpc", rpc_mock),
        patch("utils.reconciliation.logger", log),
    ):
        from utils.reconciliation import _check_entry_balance

        await _check_entry_balance(run_date)

    return log, rpc_mock


@pytest.mark.asyncio
async def test_entry_balance_uses_the_scoped_rpc_with_a_one_day_window():
    """The date bound must reach the DB as RPC params — filtering the view on
    its MIN(created_at) column is what forced a full-table aggregate."""
    _log, rpc_mock = await _run_entry_balance(rpc_result=[])

    rpc_mock.assert_awaited_once()
    name, params = rpc_mock.await_args[0]
    assert name == "financial_event_entries_unbalanced_between"
    assert params["p_start"] == "2026-01-14T00:00:00+00:00"
    assert params["p_end"] == "2026-01-15T00:00:00+00:00"


@pytest.mark.asyncio
async def test_entry_balance_month_end_window_does_not_raise():
    """Same day+1 trap that broke _sum_financial_events every month end."""
    _log, rpc_mock = await _run_entry_balance(rpc_result=[], run_date=date(2026, 1, 31))

    _name, params = rpc_mock.await_args[0]
    assert params["p_end"] == "2026-02-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_entry_balance_silent_when_all_legs_balance():
    log, _rpc = await _run_entry_balance(rpc_result=[])

    log.error.assert_not_called()


@pytest.mark.asyncio
async def test_entry_balance_alerts_on_an_unbalanced_journal():
    log, _rpc = await _run_entry_balance(
        rpc_result=[{"event_id": "evt_bad", "debit_cents": 2000, "credit_cents": 1500, "imbalance_cents": 500}]
    )

    log.error.assert_called_once()
    assert "evt_bad" in log.error.call_args[0][0]


@pytest.mark.asyncio
async def test_entry_balance_treats_a_missing_rpc_as_not_deployed_not_an_alert():
    """Partial deploy (code live, migration 293 not applied) must not page."""
    log, _rpc = await _run_entry_balance(rpc_result=RuntimeError("PGRST202 Could not find the function"))

    log.error.assert_not_called()


@pytest.mark.asyncio
async def test_entry_balance_never_raises_out_of_the_reconciliation_run():
    """It is appended after the Stripe-vs-ledger comparison and must never
    mask that result, whatever the DB does."""
    log, _rpc = await _run_entry_balance(rpc_result=RuntimeError("connection reset"))

    log.error.assert_not_called()
