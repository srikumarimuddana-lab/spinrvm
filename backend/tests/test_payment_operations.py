from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_record_operation_returns_existing_idempotency_winner():
    existing = {"id": "op1", "status": "pending"}
    with patch("backend.utils.payment_operations.db.find_one", AsyncMock(return_value=existing)) as find, patch(
        "backend.utils.payment_operations.db.insert_one", AsyncMock()
    ) as insert:
        from backend.utils.payment_operations import record_operation

        result = await record_operation(operation_type="refund", ride_id="ride1", idempotency_key="refund1")

    assert result == existing
    find.assert_awaited_once()
    insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_operation_uses_status_and_attempt_count_compare_and_swap():
    claimed = {"id": "op1", "status": "processing", "attempt_count": 1}
    with patch("backend.utils.payment_operations.db.update_one", AsyncMock(return_value=claimed)) as update:
        from backend.utils.payment_operations import claim_due_operation

        result = await claim_due_operation({"id": "op1", "status": "pending", "attempt_count": 0})

    assert result == claimed
    update.assert_awaited_once()
    assert update.await_args.args[1] == {"id": "op1", "attempt_count": 0, "status": "pending"}


@pytest.mark.asyncio
async def test_missing_durable_insert_is_an_error():
    with patch("backend.utils.payment_operations.db.find_one", AsyncMock(return_value=None)), patch(
        "backend.utils.payment_operations.db.insert_one", AsyncMock(return_value=None)
    ):
        from backend.utils.payment_operations import record_operation

        with pytest.raises(RuntimeError, match="durably recorded"):
            await record_operation(operation_type="refund", ride_id="ride1", idempotency_key="refund1")


@pytest.mark.asyncio
async def test_refund_attempt_reuses_pending_operation_without_new_insert():
    pending = {"id": "op1", "status": "pending", "idempotency_key": "key1"}
    with patch("backend.utils.payment_operations.db.get_rows", AsyncMock(return_value=[pending])), patch(
        "backend.utils.payment_operations.db.insert_one", AsyncMock()
    ) as insert:
        from backend.utils.payment_operations import prepare_refund_operation

        result = await prepare_refund_operation(ride_id="ride1", payment_intent_id="pi1", amount_cents=500)

    assert result == pending
    insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_refund_creates_a_distinct_deterministic_retry_key():
    failed = {"id": "op1", "status": "failed", "idempotency_key": "key1"}
    created = {"id": "op2", "status": "requested", "idempotency_key": "ride-cancelrefund-ride1-500-a2"}
    with patch("backend.utils.payment_operations.db.get_rows", AsyncMock(return_value=[failed])), patch(
        "backend.utils.payment_operations.db.find_one", AsyncMock(return_value=None)
    ), patch("backend.utils.payment_operations.db.insert_one", AsyncMock(return_value=created)) as insert:
        from backend.utils.payment_operations import prepare_refund_operation

        result = await prepare_refund_operation(ride_id="ride1", payment_intent_id="pi1", amount_cents=500)

    assert result == created
    assert insert.await_args.args[1]["idempotency_key"] == "ride-cancelrefund-ride1-500-a2"


@pytest.mark.asyncio
async def test_due_authorization_release_is_claimed_and_completed():
    operation = {
        "id": "op-release", "ride_id": "ride1", "operation_type": "authorization_release",
        "payment_intent_id": "pi1", "status": "requested", "attempt_count": 0,
    }
    claimed = {**operation, "status": "processing", "attempt_count": 1}
    with (
        patch("backend.utils.payment_operations.db.get_rows", AsyncMock(return_value=[operation])),
        patch("backend.utils.payment_operations.db.update_one", AsyncMock(side_effect=[claimed, {"id": "op-release"}])),
        patch("backend.utils.stripe_charge.cancel_authorization", AsyncMock(return_value=True)) as cancel,
    ):
        from backend.utils.payment_operations import reconcile_due_operations

        processed = await reconcile_due_operations()

    assert processed == 1
    cancel.assert_awaited_once_with(ride_id="ride1", payment_intent_id="pi1")


@pytest.mark.asyncio
async def test_refund_attempts_stop_at_bounded_limit():
    prior = [{"id": f"op{i}", "status": "failed", "attempt_count": 1} for i in range(8, 0, -1)]
    with patch("backend.utils.payment_operations.db.get_rows", AsyncMock(return_value=prior)), patch(
        "backend.utils.payment_operations.db.update_one", AsyncMock(return_value={"id": "op8"})
    ) as update, patch("backend.utils.payment_operations.db.insert_one", AsyncMock()) as insert:
        from backend.utils.payment_operations import prepare_refund_operation

        result = await prepare_refund_operation(ride_id="ride1", payment_intent_id="pi1", amount_cents=500)

    assert result["status"] == "exhausted"
    assert update.await_args.args[2]["status"] == "exhausted"
    insert.assert_not_awaited()
