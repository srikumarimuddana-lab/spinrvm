"""B27: charge.dispute.closed's balance-transaction debit(s)/fee must reach
financial_events. Direct unit coverage for record_dispute_close_events,
mirroring test_refund_ledger.py's pattern -- these assert on the actual
INSERT payload (event_type/delta_cents/metadata), which the webhook-level
tests in test_routes_webhooks_coverage.py cannot see since they mock
record_dispute_close_events itself.

Regression coverage for a spinr-money-auditor finding (2026-08-17): the
first version of this function used a per-balance-transaction-type
event_type string (e.g. "stripe_dispute_adjustment"), which violates
financial_events.event_type's fixed CHECK-constraint enum (migration 58,
does not include per-subtype dispute values) -- every insert would have
failed. event_type must always be the literal "stripe_dispute".
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
async def test_writes_one_row_per_balance_transaction_with_literal_event_type():
    from backend.services.payment_service import record_dispute_close_events

    balance_transactions = [
        {"id": "txn_1", "type": "adjustment", "amount": -2500, "fee": 0, "currency": "cad"},
        {"id": "txn_2", "type": "stripe_fee", "amount": -1500, "fee": 1500, "currency": "cad"},
    ]
    with patch(
        "backend.services.payment_service.db_supabase.insert_one",
        AsyncMock(return_value={}),
    ) as ins:
        await record_dispute_close_events(
            dispute_id="dp_1",
            user_id="rider_1",
            ride_id="ride_1",
            balance_transactions=balance_transactions,
            dispute_status="lost",
        )

    assert ins.call_count == 2
    for call in ins.call_args_list:
        assert call.args[0] == "financial_events"
        row = call.args[1]
        # Regression pin: must be the literal enum value, never a
        # per-subtype string -- financial_events.event_type's CHECK
        # constraint (migration 58) would reject anything else.
        assert row["event_type"] == "stripe_dispute"
        assert row["user_id"] == "rider_1"
        assert row["ride_id"] == "ride_1"
        assert row["ref"] == "dp_1"

    row1 = ins.call_args_list[0].args[1]
    assert row1["delta_cents"] == -2500
    assert row1["metadata"]["balance_transaction_id"] == "txn_1"
    assert row1["metadata"]["balance_transaction_type"] == "adjustment"
    assert row1["metadata"]["fee_cents"] == 0
    assert row1["metadata"]["dispute_status"] == "lost"

    row2 = ins.call_args_list[1].args[1]
    assert row2["delta_cents"] == -1500
    assert row2["metadata"]["balance_transaction_type"] == "stripe_fee"
    assert row2["metadata"]["fee_cents"] == 1500


@pytest.mark.anyio
async def test_zero_amount_transaction_skipped():
    from backend.services.payment_service import record_dispute_close_events

    balance_transactions = [{"id": "txn_zero", "type": "adjustment", "amount": 0, "fee": 0}]
    with patch(
        "backend.services.payment_service.db_supabase.insert_one",
        AsyncMock(return_value={}),
    ) as ins:
        await record_dispute_close_events(
            dispute_id="dp_2",
            user_id="rider_2",
            ride_id="ride_2",
            balance_transactions=balance_transactions,
            dispute_status="won",
        )

    ins.assert_not_awaited()


@pytest.mark.anyio
async def test_no_user_id_skips_write_entirely():
    """financial_events.user_id is NOT NULL REFERENCES users(id) -- an
    unresolved rider must never reach the insert."""
    from backend.services.payment_service import record_dispute_close_events

    balance_transactions = [{"id": "txn_3", "type": "adjustment", "amount": -1000, "fee": 0}]
    with patch(
        "backend.services.payment_service.db_supabase.insert_one",
        AsyncMock(return_value={}),
    ) as ins:
        await record_dispute_close_events(
            dispute_id="dp_3",
            user_id="",
            ride_id=None,
            balance_transactions=balance_transactions,
            dispute_status="lost",
        )

    ins.assert_not_awaited()


@pytest.mark.anyio
async def test_non_dict_balance_transaction_entry_skipped_not_raised():
    """A malformed/non-dict entry must not blow up the whole webhook (which
    would otherwise turn into a 5xx and a stuck stripe_events row)."""
    from backend.services.payment_service import record_dispute_close_events

    balance_transactions = ["not-a-dict", {"id": "txn_4", "type": "adjustment", "amount": -500, "fee": 0}]
    with patch(
        "backend.services.payment_service.db_supabase.insert_one",
        AsyncMock(return_value={}),
    ) as ins:
        await record_dispute_close_events(
            dispute_id="dp_4",
            user_id="rider_4",
            ride_id="ride_4",
            balance_transactions=balance_transactions,
            dispute_status="won",
        )

    ins.assert_awaited_once()
    assert ins.call_args.args[1]["metadata"]["balance_transaction_id"] == "txn_4"


@pytest.mark.anyio
async def test_never_raises_on_ledger_error():
    from backend.services.payment_service import record_dispute_close_events

    balance_transactions = [{"id": "txn_5", "type": "adjustment", "amount": -500, "fee": 0}]
    with patch(
        "backend.services.payment_service.db_supabase.insert_one",
        AsyncMock(side_effect=Exception("db down")),
    ):
        await record_dispute_close_events(
            dispute_id="dp_5",
            user_id="rider_5",
            ride_id="ride_5",
            balance_transactions=balance_transactions,
            dispute_status="lost",
        )  # no raise
