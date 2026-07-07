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
