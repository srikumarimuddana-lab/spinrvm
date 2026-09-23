"""Refund clawback holds are per Stripe event and only after this trip was paid."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.routes.webhooks import _hold_driver_payout_for_refund
from backend.utils.error_handling import DuplicateRecordError


@pytest.mark.anyio
async def test_second_partial_refund_inserts_a_second_hold():
    inserted = []

    async def _insert(table, doc):
        inserted.append(doc)
        return doc

    ride = {
        "id": "ride-1",
        "driver_id": "drv-1",
        "completed_at": "2026-09-01T00:00:00+00:00",
        "driver_earnings": "20.00",
    }
    with (
        patch(
            "backend.routes.webhooks.db_supabase.get_rows",
            AsyncMock(
                return_value=[{"created_at": "2026-09-02T00:00:00+00:00", "payout_type": "auto", "status": "completed"}]
            ),
        ),
        patch("backend.routes.webhooks.db_supabase.insert_one", AsyncMock(side_effect=_insert)),
    ):
        await _hold_driver_payout_for_refund(ride, Decimal("5.00"), event_id="evt_1")
        await _hold_driver_payout_for_refund(ride, Decimal("10.00"), event_id="evt_2")

    assert len(inserted) == 2
    assert inserted[0]["id"] != inserted[1]["id"]
    assert inserted[0]["amount"] == Decimal("5.00")
    assert inserted[1]["amount"] == Decimal("10.00")


@pytest.mark.anyio
async def test_unpaid_trip_does_not_insert_a_hold():
    insert = AsyncMock()
    ride = {
        "id": "ride-1",
        "driver_id": "drv-1",
        "completed_at": "2026-09-01T00:00:00+00:00",
        "driver_earnings": "20.00",
    }
    with (
        patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[])),
        patch("backend.routes.webhooks.db_supabase.insert_one", insert),
    ):
        await _hold_driver_payout_for_refund(ride, Decimal("5.00"), event_id="evt_1")
    insert.assert_not_awaited()


@pytest.mark.anyio
async def test_same_event_retry_does_not_double_debit():
    insert = AsyncMock(side_effect=DuplicateRecordError("dup"))
    ride = {
        "id": "ride-1",
        "driver_id": "drv-1",
        "completed_at": "2026-09-01T00:00:00+00:00",
        "driver_earnings": "20.00",
    }
    with (
        patch(
            "backend.routes.webhooks.db_supabase.get_rows",
            AsyncMock(return_value=[{"created_at": "2026-09-02T00:00:00+00:00"}]),
        ),
        patch("backend.routes.webhooks.db_supabase.insert_one", insert),
    ):
        await _hold_driver_payout_for_refund(ride, Decimal("5.00"), event_id="evt_1")
    insert.assert_awaited_once()
