"""Tests for stripe_reconcile._reconcile_stuck_reserved_payouts.

routes/drivers/payouts.py reserves a payouts row (status='reserved') before
calling stripe.Transfer; a process death in between strands it. The check is
read-only: it must flag, log and count stale reserved rows, never write.

The unit tests run the real repositories._base.get_rows against the
``mock_supabase_client`` fixture (autouse-patched in conftest), so the
PostgREST query chain the check builds is asserted, not just its Python pass.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils import stripe_reconcile as sr

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def _ts(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _payout(pid: str, status: str = "reserved", hours_ago: float = 3, payout_type: str | None = "instant") -> dict:
    return {
        "id": pid,
        "driver_id": f"drv_{pid}",
        "payout_type": payout_type,
        "status": status,
        "created_at": _ts(hours_ago),
    }


def _serve(mock_client: MagicMock, rows: list[dict]) -> MagicMock:
    table = mock_client.table.return_value
    table.execute.side_effect = lambda: MagicMock(data=rows)
    return table


async def test_stale_reserved_row_is_flagged_with_idempotency_key(mock_supabase_client, caplog):
    table = _serve(mock_supabase_client, [_payout("p1", payout_type="instant"), _payout("p2", payout_type="standard")])

    with patch.object(sr, "metrics"), caplog.at_level(logging.ERROR, logger=sr.logger.name):
        out = await sr._reconcile_stuck_reserved_payouts()

    assert [d["payout_id"] for d in out] == ["p1", "p2"]
    assert out[0]["type"] == "PAYOUT_STUCK_RESERVED"
    assert out[0]["stripe_idempotency_key"] == "instant-payout-transfer-p1"
    assert out[1]["stripe_idempotency_key"] == "payout-transfer-p2"

    # Bounded, column-scoped, server-side filtered query — not a table scan.
    mock_supabase_client.table.assert_called_with("payouts")
    table.select.assert_called_with("id,driver_id,payout_type,status,created_at")
    table.eq.assert_any_call("status", "reserved")
    lt_col, lt_val = table.lt.call_args[0]
    assert lt_col == "created_at"
    age = datetime.now(timezone.utc) - datetime.fromisoformat(lt_val)
    assert timedelta(minutes=59) < age < timedelta(minutes=61)
    table.limit.assert_called_with(sr._STUCK_RESERVED_PAYOUT_LIMIT)

    # Read-only: the check never writes.
    table.update.assert_not_called()
    table.insert.assert_not_called()
    table.upsert.assert_not_called()
    table.delete.assert_not_called()

    msgs = [r for r in caplog.records if "PAYOUT_STUCK_RESERVED" in r.getMessage()]
    assert len(msgs) == 2
    assert all(r.levelno == logging.ERROR and r.domain == "payments" for r in msgs)
    assert "instant-payout-transfer-p1" in msgs[0].getMessage()


async def test_metric_incremented_per_stuck_row_by_payout_type(mock_supabase_client):
    _serve(mock_supabase_client, [_payout("p1", payout_type="instant"), _payout("p2", payout_type="standard")])

    with patch.object(sr, "metrics") as m:
        out = await sr._reconcile_stuck_reserved_payouts()

    assert len(out) == 2
    m.inc.assert_any_call(sr._STUCK_RESERVED_METRIC, {"payout_type": "instant"})
    m.inc.assert_any_call(sr._STUCK_RESERVED_METRIC, {"payout_type": "standard"})
    assert sr._STUCK_RESERVED_METRIC == "spinr_payment_stuck_reserved_payouts_total"
    assert out[1]["stripe_idempotency_key"] == "payout-transfer-p2"


async def test_auto_rows_are_left_to_the_auto_payout_sweep(mock_supabase_client):
    # auto_payout.sweep_stale_reserved retries/escalates stale auto rows hourly,
    # and an escalated auto row stays 'reserved'; re-flagging it daily is noise.
    _serve(mock_supabase_client, [_payout("a1", payout_type="auto", hours_ago=72)])

    with patch.object(sr, "metrics") as m:
        out = await sr._reconcile_stuck_reserved_payouts()

    assert out == []
    m.inc.assert_not_called()
    mock_supabase_client.table.return_value.in_.assert_any_call("payout_type", ["standard", "instant"])


async def test_unexpected_payout_type_is_labelled_other(mock_supabase_client):
    _serve(mock_supabase_client, [_payout("x1", payout_type="mystery")])

    with patch.object(sr, "metrics") as m:
        out = await sr._reconcile_stuck_reserved_payouts()

    assert len(out) == 1
    m.inc.assert_called_once_with(sr._STUCK_RESERVED_METRIC, {"payout_type": "other"})


async def test_fresh_reserved_row_is_not_flagged(mock_supabase_client):
    # The mock ignores filters, so this also proves the Python re-check.
    _serve(mock_supabase_client, [_payout("fresh", hours_ago=0.1)])

    with patch.object(sr, "metrics") as m:
        out = await sr._reconcile_stuck_reserved_payouts()

    assert out == []
    m.inc.assert_not_called()


@pytest.mark.parametrize("status", ["completed", "failed", "pending", "transfer_completed", "reversed"])
async def test_non_reserved_rows_are_not_flagged(mock_supabase_client, status):
    _serve(mock_supabase_client, [_payout("old", status=status, hours_ago=48)])

    with patch.object(sr, "metrics") as m:
        out = await sr._reconcile_stuck_reserved_payouts()

    assert out == []
    m.inc.assert_not_called()


async def test_db_error_is_logged_at_error_and_returns_none(mock_supabase_client, caplog):
    mock_supabase_client.table.return_value.execute.side_effect = RuntimeError("pgrst down")

    with patch.object(sr, "metrics") as m, caplog.at_level(logging.ERROR, logger=sr.logger.name):
        out = await sr._reconcile_stuck_reserved_payouts()

    assert out is None  # "check failed", never an empty list that reads as clean
    m.inc.assert_called_once_with(sr._CHECK_FAILED_METRIC, {"check": "stuck_reserved_payout"})
    rec = [r for r in caplog.records if "stuck-reserved payouts query failed" in r.getMessage()]
    assert rec and rec[0].levelno == logging.ERROR and rec[0].exc_info


async def test_row_ceiling_is_surfaced(mock_supabase_client, caplog):
    rows = [_payout(f"p{i}") for i in range(sr._STUCK_RESERVED_PAYOUT_LIMIT)]
    _serve(mock_supabase_client, rows)

    with patch.object(sr, "metrics") as m, caplog.at_level(logging.ERROR, logger=sr.logger.name):
        out = await sr._reconcile_stuck_reserved_payouts()

    assert len(out) == sr._STUCK_RESERVED_PAYOUT_LIMIT
    m.inc.assert_any_call(sr._CHECK_FAILED_METRIC, {"check": "stuck_reserved_payout"})
    assert any("row ceiling" in r.getMessage() for r in caplog.records)


async def test_db_error_in_check_does_not_crash_the_tick():
    """A failing stuck-reserved query leaves the rest of the tick intact: the
    audit summary is still written, with the check recorded as None."""

    async def _rows(table, flt=None, **kw):
        if table == "payouts" and (flt or {}).get("status") == "reserved":
            raise RuntimeError("pgrst down")
        return []

    db_mock = AsyncMock()
    db_mock.get_rows.side_effect = _rows
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = MagicMock()
    stripe_mock.PaymentIntent.list.return_value.auto_paging_iter.side_effect = lambda: iter([])

    with (
        patch.object(sr, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch.object(sr, "db_supabase", db_mock),
        patch.object(sr, "metrics"),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        await sr._run_reconciliation_tick()

    details = db_mock.insert_one.call_args[0][1]["details"]
    assert details["payouts_stuck_reserved"] is None
    db_mock.update_one.assert_not_called()


async def test_tick_includes_stuck_reserved_in_summary():
    async def _rows(table, flt=None, **kw):
        if table == "payouts" and (flt or {}).get("status") == "reserved":
            return [_payout("p9")]
        return []

    db_mock = AsyncMock()
    db_mock.get_rows.side_effect = _rows
    db_mock.insert_one.return_value = {"id": "log1"}
    stripe_mock = MagicMock()
    stripe_mock.PaymentIntent.list.return_value.auto_paging_iter.side_effect = lambda: iter([])

    with (
        patch.object(sr, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch.object(sr, "db_supabase", db_mock),
        patch.object(sr, "metrics"),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        await sr._run_reconciliation_tick()

    details = db_mock.insert_one.call_args[0][1]["details"]
    assert details["payouts_stuck_reserved"] == 1
    assert any(d.get("type") == "PAYOUT_STUCK_RESERVED" for d in details["discrepancy_detail"])
    db_mock.update_one.assert_not_called()
