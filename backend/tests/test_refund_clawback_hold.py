"""Both Stripe event paths delegate refunds and holds to atomic projection."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.routes import webhooks


@pytest.mark.anyio
@pytest.mark.parametrize("event_type", ["charge.refunded", "refund.updated"])
@pytest.mark.parametrize("fails", [False, True])
async def test_refund_projection_owns_hold_and_retries_atomically(event_type, fails):
    db = AsyncMock()
    ride = {
        "id": "ride-1",
        "driver_id": "drv-1",
        "driver_earnings": "20.00",
        "completed_at": "2026-09-01T00:00:00+00:00",
    }

    async def rows(table, *args, **kwargs):
        return [ride] if table == "rides" else [{"created_at": "2026-09-02T00:00:00+00:00"}]

    db.get_rows.side_effect = rows
    db.find_one.return_value = None
    projection = AsyncMock(return_value={"outcome": "applied", "delta_cents": 500})
    if fails:
        projection.side_effect = RuntimeError("atomic hold insert failed")
    unclaim = AsyncMock()
    data = {
        "id": "re_1",
        "status": "succeeded",
        "payment_intent": "pi_1",
        "amount_refunded": 500,
        "metadata": {"ride_id": "ride-1"},
    }
    with (
        patch.object(webhooks, "db_supabase", db),
        patch.object(webhooks, "unclaim_stripe_event", unclaim),
        patch("backend.services.payment_service.reconcile_confirmed_stripe_refund", projection),
    ):
        if fails:
            with pytest.raises(HTTPException) as error:
                await webhooks._dispatch_stripe_event("evt_1", event_type, {}, data)
            assert error.value.status_code == 500
            unclaim.assert_awaited_once_with("evt_1")
        else:
            await webhooks._dispatch_stripe_event("evt_1", event_type, {}, data)
            unclaim.assert_not_awaited()
    projection.assert_awaited_once_with(ride_id="ride-1", payment_intent_id="pi_1")
    assert not any(call.args[0] == "payouts" for call in db.insert_one.await_args_list)
