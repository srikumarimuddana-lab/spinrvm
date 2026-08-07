"""Ledger projection loop — decomposition, degradation, and safety contracts.

The projection derives financial_event_entries (double-entry legs) from
financial_events headers via the missing-legs work queue (migration 287).
What must hold:

- Every projectable source decomposes to BALANCED legs from the right input
  (ride row for fares, metadata fee split for cancellation fees,
  metadata.tax_reversed for refunds).
- An event that cannot be decomposed is booked DEGRADED (whole amount to
  platform_revenue, loudly flagged) rather than skipped — a skipped row
  would sit at the head of the oldest-first queue forever and starve all
  newer events.
- The flag is checked once per tick; the missing-RPC partial-deploy case
  idles quietly; one bad row never stops the batch; prod writers pass no
  legs (single-writer invariant: only the projection writes legs).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import ledger_service as ls
from backend.utils import ledger_projection as lp


def _event(**overrides) -> dict:
    base = {
        "id": "evt_1",
        "event_type": "stripe_charge",
        "user_id": "u1",
        "ride_id": "ride_1",
        "delta_cents": 2000,
        "ref": "pi_1",
        "metadata": {"source": "process_payment"},
        "created_at": "2026-08-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _ride(**overrides) -> dict:
    base = {
        "id": "ride_1",
        "total_fare": "18.00",
        "grand_total": "20.00",
        "tax_amount": "2.20",
        "driver_earnings": "15.00",
        "tip_amount": "0.00",
    }
    base.update(overrides)
    return base


# ── _decompose ───────────────────────────────────────────────────────


def test_fare_charge_decomposes_from_ride_row():
    legs, degraded, reason = lp._decompose(_event(), _ride())
    assert not degraded and reason is None
    ls.assert_balanced(legs)
    by = {(leg.account, leg.side): leg.amount_cents for leg in legs}
    assert by[(ls.ACCT_STRIPE_RECEIVABLE, ls.DEBIT)] == 2000
    assert by[(ls.ACCT_DRIVER_PAYABLE, ls.CREDIT)] == 1500
    assert by[(ls.ACCT_TAX_PAYABLE, ls.CREDIT)] == 220
    assert by[(ls.ACCT_PLATFORM_REVENUE, ls.CREDIT)] == 280


def test_fare_charge_with_missing_ride_degrades():
    legs, degraded, reason = lp._decompose(_event(), None)
    assert degraded and reason == "ride_missing"
    ls.assert_balanced(legs)
    by = {(leg.account, leg.side): leg.amount_cents for leg in legs}
    assert by == {
        (ls.ACCT_STRIPE_RECEIVABLE, ls.DEBIT): 2000,
        (ls.ACCT_PLATFORM_REVENUE, ls.CREDIT): 2000,
    }


def test_fare_charge_with_inconsistent_amounts_degrades():
    # driver + tax exceed the charged total — build_charge_legs refuses.
    ride = _ride(driver_earnings="25.00", tax_amount="5.00")
    legs, degraded, reason = lp._decompose(_event(), ride)
    assert degraded and reason == "amounts_inconsistent"
    ls.assert_balanced(legs)


def test_cancellation_fee_decomposes_from_metadata_split():
    ev = _event(
        delta_cents=450,
        metadata={"source": "cancellation_fee", "fee_admin": "0.50", "fee_driver": "4.00"},
    )
    # Deliberately no ride row: the fee split must come from metadata, never
    # from the ride's fare fields (which belong to the original fare).
    legs, degraded, reason = lp._decompose(ev, None)
    assert not degraded and reason is None
    ls.assert_balanced(legs)
    by = {(leg.account, leg.side): leg.amount_cents for leg in legs}
    assert by[(ls.ACCT_DRIVER_PAYABLE, ls.CREDIT)] == 400
    assert by[(ls.ACCT_PLATFORM_REVENUE, ls.CREDIT)] == 50


def test_cancellation_fee_without_split_metadata_degrades():
    """Historical rows predate the fee_admin/fee_driver metadata."""
    ev = _event(delta_cents=450, metadata={"source": "cancellation_fee"})
    legs, degraded, reason = lp._decompose(ev, _ride())
    assert degraded and reason == "no_fee_split_metadata"
    ls.assert_balanced(legs)


def test_notice_fee_books_all_platform_not_degraded():
    ev = _event(delta_cents=300, metadata={"source": "scheduled_cancel_notice_fee"})
    legs, degraded, reason = lp._decompose(ev, None)
    assert not degraded, "all-platform is the CORRECT split for a rider-only fee, not a fallback"
    ls.assert_balanced(legs)
    by = {(leg.account, leg.side): leg.amount_cents for leg in legs}
    assert by[(ls.ACCT_PLATFORM_REVENUE, ls.CREDIT)] == 300


def test_refund_decomposes_from_metadata_tax_reversed():
    ev = _event(
        event_type="stripe_refund",
        delta_cents=-2000,
        metadata={"source": "charge.refunded", "tax_reversed": "2.20"},
    )
    legs, degraded, reason = lp._decompose(ev, None)
    assert not degraded and reason is None
    ls.assert_balanced(legs)
    by = {(leg.account, leg.side): leg.amount_cents for leg in legs}
    assert by[(ls.ACCT_STRIPE_RECEIVABLE, ls.CREDIT)] == 2000
    assert by[(ls.ACCT_TAX_PAYABLE, ls.DEBIT)] == 220
    assert by[(ls.ACCT_PLATFORM_REVENUE, ls.DEBIT)] == 1780
    assert not any(leg.account == ls.ACCT_DRIVER_PAYABLE for leg in legs)


def test_zero_amount_event_is_skipped_defensively():
    """The RPC filters delta_cents <> 0; this is the belt-and-braces path."""
    legs, degraded, reason = lp._decompose(_event(delta_cents=0), _ride())
    assert legs == [] and reason == "zero_amount"


# ── project_pending_legs ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_tick_projects_batch_and_reports_stats():
    events = [_event(), _event(id="evt_2", ride_id="ride_2", delta_cents=1000)]
    rides = [_ride(), _ride(id="ride_2", driver_earnings="8.00", tax_amount="1.00", grand_total="10.00")]
    written = []

    async def cap_write(event_id, legs, **kwargs):
        written.append((event_id, legs, kwargs))
        return True

    with (
        patch.object(lp.ledger_service, "double_entry_enabled", AsyncMock(return_value=True)),
        patch.object(lp.db_supabase, "rpc", AsyncMock(return_value=events)),
        patch.object(lp.db_supabase, "get_rows", AsyncMock(return_value=rides)) as rows_mock,
        patch.object(lp.ledger_service, "write_legs", side_effect=cap_write),
    ):
        stats = await lp.project_pending_legs()

    assert stats == {"fetched": 2, "projected": 2, "degraded": 0, "skipped": 0, "failed": 0}
    assert [w[0] for w in written] == ["evt_1", "evt_2"]
    # Flag is checked once per tick — write_legs must NOT re-check it.
    assert all(w[2]["check_flag"] is False for w in written)
    # Rides fetched in ONE batched query, not per event.
    rows_mock.assert_awaited_once()
    assert rows_mock.call_args.args[1] == {"id": {"$in": ["ride_1", "ride_2"]}}


@pytest.mark.anyio
async def test_tick_flag_off_touches_nothing():
    with (
        patch.object(lp.ledger_service, "double_entry_enabled", AsyncMock(return_value=False)),
        patch.object(lp.db_supabase, "rpc", AsyncMock()) as rpc_mock,
    ):
        stats = await lp.project_pending_legs()
    assert stats["fetched"] == 0
    rpc_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_tick_missing_rpc_idles_quietly():
    """Partial deploy: code live, migration 287 not applied."""
    lp._rpc_missing_logged = False

    async def missing(*_a, **_k):
        raise RuntimeError("PGRST202: Could not find the function financial_events_missing_legs")

    with (
        patch.object(lp.ledger_service, "double_entry_enabled", AsyncMock(return_value=True)),
        patch.object(lp.db_supabase, "rpc", side_effect=missing),
    ):
        stats = await lp.project_pending_legs()
        stats2 = await lp.project_pending_legs()

    assert stats["fetched"] == 0 and stats2["fetched"] == 0
    assert lp._rpc_missing_logged is True
    lp._rpc_missing_logged = False


@pytest.mark.anyio
async def test_tick_defers_when_ride_batch_fetch_fails():
    """A dead rides query must not project every fare event as degraded."""

    async def boom(*_a, **_k):
        raise RuntimeError("db down")

    with (
        patch.object(lp.ledger_service, "double_entry_enabled", AsyncMock(return_value=True)),
        patch.object(lp.db_supabase, "rpc", AsyncMock(return_value=[_event()])),
        patch.object(lp.db_supabase, "get_rows", side_effect=boom),
        patch.object(lp.ledger_service, "write_legs", AsyncMock()) as write_mock,
    ):
        stats = await lp.project_pending_legs()

    assert stats == {"fetched": 1, "projected": 0, "degraded": 0, "skipped": 0, "failed": 0}
    write_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_tick_isolates_per_event_failure():
    """One raising row must not stop the rest of the batch."""
    events = [_event(), _event(id="evt_bad", ride_id="ride_1"), _event(id="evt_3", ride_id="ride_1")]
    calls = {"n": 0}

    async def flaky_write(event_id, legs, **kwargs):
        calls["n"] += 1
        if event_id == "evt_bad":
            raise RuntimeError("boom")
        return True

    with (
        patch.object(lp.ledger_service, "double_entry_enabled", AsyncMock(return_value=True)),
        patch.object(lp.db_supabase, "rpc", AsyncMock(return_value=events)),
        patch.object(lp.db_supabase, "get_rows", AsyncMock(return_value=[_ride()])),
        patch.object(lp.ledger_service, "write_legs", side_effect=flaky_write),
    ):
        stats = await lp.project_pending_legs()

    assert calls["n"] == 3
    assert stats["projected"] == 2 and stats["failed"] == 1


@pytest.mark.anyio
async def test_tick_degraded_event_still_written_and_escalated():
    ev = _event(ride_id="ride_gone")
    with (
        patch.object(lp.ledger_service, "double_entry_enabled", AsyncMock(return_value=True)),
        patch.object(lp.db_supabase, "rpc", AsyncMock(return_value=[ev])),
        patch.object(lp.db_supabase, "get_rows", AsyncMock(return_value=[])),
        patch.object(lp.ledger_service, "write_legs", AsyncMock(return_value=True)) as write_mock,
        patch.object(lp.ledger_service, "_escalate") as escalate,
    ):
        stats = await lp.project_pending_legs()

    assert stats["degraded"] == 1 and stats["projected"] == 1
    write_mock.assert_awaited_once()
    assert escalate.call_args.kwargs["alert"] == ls.ALERT_LEGS_DEGRADED


# ── single-writer invariant ──────────────────────────────────────────


@pytest.mark.anyio
async def test_prod_charge_writer_passes_no_legs():
    """Only the projection writes financial_event_entries — the request-path
    writers must hand record_event a bare header."""
    from backend.services.payment_service import record_payment_event

    with patch("backend.services.ledger_service.record_event", AsyncMock(return_value="e")) as rec:
        await record_payment_event(
            "ride_1",
            "u1",
            2000,
            "pi_1",
            ride=_ride(),
            tip_amount=Decimal("2.00"),
        )

    assert rec.call_args.kwargs.get("legs") is None


@pytest.mark.anyio
async def test_prod_refund_writer_passes_no_legs():
    from backend.services.payment_service import record_refund_event

    with patch("backend.services.ledger_service.record_event", AsyncMock(return_value="e")) as rec:
        await record_refund_event("ride_1", "u1", 2000, "pi_1", ride=_ride())

    assert rec.call_args.kwargs.get("legs") is None
    # The projection's refund input must still be present.
    assert "tax_reversed" in rec.call_args.kwargs["metadata"]
