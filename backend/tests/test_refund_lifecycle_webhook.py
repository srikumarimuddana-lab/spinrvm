from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_refund_failure_webhook_updates_operation_and_ride_without_success_label():
    from backend.routes import webhooks

    operation = {"id": "op1", "ride_id": "ride1"}
    updates = AsyncMock()
    with (
        patch.object(webhooks.db_supabase, "find_one", AsyncMock(return_value=operation)),
        patch.object(webhooks.db_supabase, "update_one", updates),
        patch.object(webhooks, "mark_stripe_event_processed", AsyncMock()),
    ):
        result = await webhooks._dispatch_stripe_event(
            "evt_refund_failed", "refund.failed", {},
            {"id": "re1", "status": "failed", "amount": 300, "metadata": {"ride_id": "ride1"}},
        )

    assert result["received"] is True
    assert updates.await_count == 2
    assert updates.await_args_list[0].args[1] == {"id": "op1"}
    assert updates.await_args_list[0].args[2]["status"] == "failed"
    assert updates.await_args_list[1].args[1] == {"id": "ride1"}
    assert updates.await_args_list[1].args[2] == {"refund_id": "re1", "refund_status": "failed"}
