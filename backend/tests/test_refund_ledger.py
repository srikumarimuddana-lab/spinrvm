"""C3: a refund must leave a compensating financial_events row (previously
none), reverse the rider-side tax for remittance, and — per policy — retain the
driver's pay (Spinr absorbs it) rather than clawing driver_earnings back."""

from decimal import Decimal  # noqa: F401 — kept for parity with money modules
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
async def test_refund_event_writes_negative_row_and_retains_driver_pay():
    from backend.services.payment_service import record_refund_event

    ride = {
        "id": "r1",
        "rider_id": "u1",
        "driver_id": "d1",
        "grand_total": "20.00",
        "tax_amount": "2.20",
        "driver_earnings": "15.00",
        "tax_breakdown": {"GST": {"amount": 1.0}, "PST": {"amount": 1.2}},
    }
    with patch(
        "backend.services.payment_service.db_supabase.insert_one",
        AsyncMock(return_value={}),
    ) as ins:
        await record_refund_event("r1", "u1", refund_cents=2000, payment_intent_id="pi_1", ride=ride)

    assert ins.call_args.args[0] == "financial_events"
    row = ins.call_args.args[1]
    assert row["event_type"] == "stripe_refund"
    assert row["delta_cents"] == -2000, "refund must be a negative ledger delta"
    assert row["ref"] == "pi_1"
    meta = row["metadata"]
    assert meta["driver_pay_absorbed_by_platform"] is True
    assert meta["driver_earnings_retained"] == "15.00", "driver keeps pay (policy)"
    assert meta["tax_reversed"] == "2.20", "full refund reverses all rider-side tax"
    assert meta["refund_amount"] == "20.00"


@pytest.mark.anyio
async def test_refund_event_prorates_tax_on_partial_refund():
    from backend.services.payment_service import record_refund_event

    ride = {
        "id": "r2",
        "rider_id": "u2",
        "grand_total": "20.00",
        "tax_amount": "2.00",
        "driver_earnings": "15.00",
    }
    with patch(
        "backend.services.payment_service.db_supabase.insert_one",
        AsyncMock(return_value={}),
    ) as ins:
        await record_refund_event("r2", "u2", refund_cents=1000, ride=ride)  # 50% refund

    meta = ins.call_args.args[1]["metadata"]
    assert meta["tax_reversed"] == "1.00", "partial refund reverses tax proportionally"
    assert meta["driver_earnings_retained"] == "15.00"


@pytest.mark.anyio
async def test_refund_event_never_raises_on_ledger_error():
    """Best-effort — a ledger write failure must not break the refund webhook."""
    from backend.services.payment_service import record_refund_event

    with patch(
        "backend.services.payment_service.db_supabase.insert_one",
        AsyncMock(side_effect=Exception("db down")),
    ):
        await record_refund_event("r3", "u3", refund_cents=500, ride=None)  # no raise


@pytest.mark.anyio
async def test_refund_event_forwards_dedupe_key_and_returns_ledger_id():
    """F1: dedupe_key must reach ledger_service.record_event (so a replay
    books once, matching the dispute path's pattern), and the caller must get
    back the ledger row id (not None) to distinguish success from failure."""
    from backend.services.ledger_service import derive_event_id
    from backend.services.payment_service import record_refund_event

    with patch(
        "backend.services.payment_service.db_supabase.insert_one",
        AsyncMock(return_value={}),
    ) as ins:
        result = await record_refund_event(
            "r4", "u4", refund_cents=1000, payment_intent_id="pi_4", dedupe_key="stripe_refund|pi_4|1000"
        )

    assert result == derive_event_id("stripe_refund|pi_4|1000")
    assert ins.call_args.args[1]["id"] == derive_event_id("stripe_refund|pi_4|1000")


@pytest.mark.anyio
async def test_refund_event_returns_none_when_ledger_write_fails():
    from backend.services.payment_service import record_refund_event

    with patch(
        "backend.services.payment_service.db_supabase.insert_one",
        AsyncMock(side_effect=Exception("db down")),
    ):
        result = await record_refund_event("r5", "u5", refund_cents=500, dedupe_key="stripe_refund|pi_5|500")

    assert result is None


class TestRefundBookedCents:
    @pytest.mark.anyio
    async def test_sums_negative_deltas_as_positive_total(self):
        from backend.services.payment_service import refund_booked_cents

        rows = [{"delta_cents": -1000}, {"delta_cents": -500}]
        with patch(
            "backend.services.payment_service.db_supabase.get_rows",
            AsyncMock(return_value=rows),
        ):
            assert await refund_booked_cents("pi_sum_1") == 1500

    @pytest.mark.anyio
    async def test_no_rows_returns_zero(self):
        from backend.services.payment_service import refund_booked_cents

        with patch(
            "backend.services.payment_service.db_supabase.get_rows",
            AsyncMock(return_value=[]),
        ):
            assert await refund_booked_cents("pi_sum_2") == 0

    @pytest.mark.anyio
    async def test_ignores_non_negative_rows(self):
        """Defensive — only a refund's own negative delta_cents rows should
        count; a positive row under the same ref (shouldn't happen for
        stripe_refund, but this query filters on ref not event_type alone at
        the DB layer in some paths) must not be double-subtracted."""
        from backend.services.payment_service import refund_booked_cents

        rows = [{"delta_cents": -1000}, {"delta_cents": 200}]
        with patch(
            "backend.services.payment_service.db_supabase.get_rows",
            AsyncMock(return_value=rows),
        ):
            assert await refund_booked_cents("pi_sum_3") == 1000

    @pytest.mark.anyio
    async def test_raises_on_db_error_money_path(self):
        """Unlike record_refund_event (best-effort, never raises), a read
        failure here must surface loudly rather than silently reporting 0
        already-booked — that would make the caller re-book money that may
        already be recorded."""
        from backend.services.payment_service import refund_booked_cents

        with patch(
            "backend.services.payment_service.db_supabase.get_rows",
            AsyncMock(side_effect=Exception("db down")),
        ):
            with pytest.raises(Exception, match="db down"):
                await refund_booked_cents("pi_sum_4")
