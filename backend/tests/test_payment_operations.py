from unittest.mock import AsyncMock, MagicMock, patch

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
    claimed = {"id": "op1", "status": "processing", "attempt_count": 0}
    with patch("backend.utils.payment_operations.db.update_one", AsyncMock(return_value=claimed)) as update:
        from backend.utils.payment_operations import claim_due_operation

        result = await claim_due_operation({"id": "op1", "status": "pending", "attempt_count": 0,
                                            "next_attempt_at": "2026-09-22T00:00:00+00:00"})

    assert result == claimed
    update.assert_awaited_once()
    assert update.await_args.args[1] == {"id": "op1", "attempt_count": 0, "status": "pending",
                                         "next_attempt_at": "2026-09-22T00:00:00+00:00"}
    assert update.await_args.args[2]["status"] == "processing"
    assert "next_attempt_at" in update.await_args.args[2]
    assert "attempt_count" not in update.await_args.args[2]


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

        result = await prepare_refund_operation(ride_id="ride1", payment_intent_id="pi1", amount_cents=500,
                                                allow_terminal_advance=True)

    assert result == created
    assert insert.await_args.args[1]["idempotency_key"] == "ride-cancelrefund-ride1-500-a2"


@pytest.mark.asyncio
async def test_request_path_does_not_advance_terminal_refund_without_provider_reconciliation():
    failed = {"id": "op1", "status": "failed", "idempotency_key": "key1"}
    with patch("backend.utils.payment_operations.db.get_rows", AsyncMock(return_value=[failed])), patch(
        "backend.utils.payment_operations.db.insert_one", AsyncMock()
    ) as insert:
        from backend.utils.payment_operations import prepare_refund_operation

        result = await prepare_refund_operation(ride_id="ride1", payment_intent_id="pi1", amount_cents=500)

    assert result == failed
    insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_refund_is_reused_without_advancing_key():
    ambiguous = {"id": "op1", "status": "pending", "idempotency_key": "key1"}
    with patch("backend.utils.payment_operations.db.get_rows", AsyncMock(return_value=[ambiguous])), patch(
        "backend.utils.payment_operations.db.insert_one", AsyncMock()
    ) as insert:
        from backend.utils.payment_operations import prepare_refund_operation

        result = await prepare_refund_operation(ride_id="ride1", payment_intent_id="pi1", amount_cents=500)

    assert result == ambiguous
    insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_due_authorization_release_is_claimed_and_completed():
    operation = {
        "id": "op-release", "ride_id": "ride1", "operation_type": "authorization_release",
        "payment_intent_id": "pi1", "status": "requested", "attempt_count": 0,
    }
    claimed = {**operation, "status": "processing", "attempt_count": 0}
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


@pytest.mark.asyncio
async def test_successful_refund_recovery_updates_aggregate_and_exactly_once_ledger():
    ride = {"id": "ride1", "rider_id": "rider1", "payment_intent_id": "pi1", "refund_amount": "0.00",
            "grand_total": "20.00", "tax_amount": "1.00"}
    operation = {"id": "op1", "ride_id": "ride1", "payment_intent_id": "pi1", "provider_object_id": "re1"}
    with (
        patch("backend.utils.payment_operations.db.find_one", AsyncMock(return_value=ride)),
        patch("backend.utils.payment_operations.db.update_one", AsyncMock(return_value={"id": "ride1"})) as update,
        patch("backend.utils.stripe_charge.read_capture_state", AsyncMock(return_value={
            "captured_cents": 2000, "refunded_cents": 500, "pending_refund_cents": 0,
        })),
        patch("backend.services.payment_service.refund_booked_cents", AsyncMock(return_value=0)),
        patch("backend.services.payment_service.record_refund_event", AsyncMock(return_value="ledger1")) as record,
    ):
        from backend.utils.payment_operations import finalize_refund_success

        await finalize_refund_success(operation)

    assert update.await_args.args[2]["refund_amount"] == "5.00"
    assert update.await_args.args[2]["payment_status"] == "partially_refunded"
    assert record.await_args.kwargs["refund_cents"] == 500
    assert record.await_args.kwargs["dedupe_key"] == "stripe_refund|pi1|500"


@pytest.mark.asyncio
async def test_refund_finalizer_cas_matches_null_initial_aggregate():
    ride = {"id": "ride1", "rider_id": "rider1", "payment_intent_id": "pi1", "refund_amount": None,
            "grand_total": "20.00", "tax_amount": "1.00"}
    operation = {"id": "op1", "ride_id": "ride1", "payment_intent_id": "pi1", "provider_object_id": "re1"}
    with (
        patch("backend.utils.payment_operations.db.find_one", AsyncMock(return_value=ride)),
        patch("backend.utils.payment_operations.db.update_one", AsyncMock(return_value={"id": "ride1"})) as update,
        patch("backend.utils.stripe_charge.read_capture_state", AsyncMock(return_value={
            "captured_cents": 2000, "refunded_cents": 500, "pending_refund_cents": 0,
        })),
        patch("backend.services.payment_service.refund_booked_cents", AsyncMock(return_value=0)),
        patch("backend.services.payment_service.record_refund_event", AsyncMock(return_value="ledger1")),
    ):
        from backend.utils.payment_operations import finalize_refund_success

        await finalize_refund_success(operation)

    assert update.await_args.args[1] == {"id": "ride1", "refund_amount": None}


@pytest.mark.asyncio
async def test_refund_finalizer_keeps_operation_open_when_stripe_aggregate_is_unknown():
    with (
        patch("backend.utils.stripe_charge.read_capture_state", AsyncMock(return_value=None)),
        patch("backend.utils.payment_operations.db.find_one", AsyncMock()) as find,
    ):
        from backend.utils.payment_operations import finalize_refund_success

        with pytest.raises(RuntimeError, match="aggregate is unavailable"):
            await finalize_refund_success({"ride_id": "ride1", "payment_intent_id": "pi1"})

    find.assert_not_awaited()


@pytest.mark.asyncio
async def test_wallet_notice_fee_recovery_never_treats_transaction_id_as_stripe_pi():
    operation = {
        "id": "op-wallet", "ride_id": "ride1", "operation_type": "scheduled_notice_fee",
        "payment_intent_id": None, "status": "pending", "attempt_count": 0,
        "metadata": {"outcome_status": "succeeded", "collected_cents": 50, "provider_reference": "txn1"},
    }
    claimed = {**operation, "status": "processing", "attempt_count": 1}
    with (
        patch("backend.utils.payment_operations.db.get_rows", AsyncMock(return_value=[operation])),
        patch("backend.utils.payment_operations.db.update_one", AsyncMock(side_effect=[claimed, {"id": "ride1"}, {"id": "op-wallet"}])) as update,
        patch("backend.utils.stripe_charge.stripe") as stripe,
    ):
        from backend.utils.payment_operations import reconcile_due_operations

        await reconcile_due_operations()

    assert update.await_args_list[1].args[2]["scheduled_notice_fee_payment_intent_id"] == "txn1"
    stripe.PaymentIntent.retrieve.assert_not_called()


@pytest.mark.asyncio
async def test_refund_reconciliation_does_not_create_when_refund_history_is_paginated():
    operation = {
        "id": "op-refund", "ride_id": "ride1", "operation_type": "refund", "payment_intent_id": "pi1",
        "amount_cents": 500, "idempotency_key": "key1", "status": "requested", "attempt_count": 0,
    }
    claimed = {**operation, "status": "processing", "attempt_count": 0}
    stripe_mock = MagicMock()
    stripe_mock.Refund.list.return_value = MagicMock(data=[], has_more=True)
    with (
        patch("backend.utils.payment_operations.db.get_rows", AsyncMock(return_value=[operation])),
        patch("backend.utils.payment_operations.db.update_one", AsyncMock(side_effect=[claimed, {"id": "op-refund"}])),
        patch("backend.utils.stripe_charge.stripe", stripe_mock),
        patch("backend.utils.stripe_charge._resolve_stripe_secret", AsyncMock(return_value="sk_test")),
    ):
        from backend.utils.payment_operations import reconcile_due_operations

        await reconcile_due_operations()

    stripe_mock.Refund.create.assert_not_called()


@pytest.mark.asyncio
async def test_pending_refund_can_be_polled_past_old_attempt_limit_without_exhausting():
    operation = {
        "id": "op-pending", "ride_id": "ride1", "operation_type": "refund", "payment_intent_id": "pi1",
        "provider_object_id": "re1", "amount_cents": 500, "status": "pending", "attempt_count": 8,
        "next_attempt_at": "2026-09-22T00:00:00+00:00",
    }
    claimed = {**operation, "status": "processing"}
    refund = MagicMock(id="re1", status="pending", amount=500)
    stripe_mock = MagicMock()
    stripe_mock.Refund.retrieve.return_value = refund
    with (
        patch("backend.utils.payment_operations.db.get_rows", AsyncMock(return_value=[operation])),
        patch("backend.utils.payment_operations.db.update_one", AsyncMock(side_effect=[
            claimed, {"id": "op-pending"}, {"id": "ride1"},
        ])) as update,
        patch("backend.utils.stripe_charge.stripe", stripe_mock),
        patch("backend.utils.stripe_charge._resolve_stripe_secret", AsyncMock(return_value="sk_test")),
    ):
        from backend.utils.payment_operations import reconcile_due_operations

        processed = await reconcile_due_operations()

    assert processed == 1
    assert update.await_args_list[1].args[2]["status"] == "pending"
    assert update.await_args_list[1].args[2]["next_attempt_at"]
    assert update.await_args_list[1].args[2].get("attempt_count") is None


@pytest.mark.asyncio
async def test_pending_poll_count_does_not_spend_transport_error_budget():
    with patch("backend.utils.payment_operations.db.update_one", AsyncMock(return_value={"id": "op1"})) as update:
        from backend.utils.payment_operations import schedule_poll

        await schedule_poll("op1")

    changes = update.await_args.args[2]
    assert changes["status"] == "pending"
    assert "metadata" not in changes
    assert "attempt_count" not in changes
    assert update.await_args.args[1] == {"id": "op1"}


@pytest.mark.asyncio
async def test_retry_error_budget_is_separate_from_legacy_poll_attempt_count():
    operation = {"id": "op1", "attempt_count": 8, "metadata": {"source": "refund"}}
    with patch("backend.utils.payment_operations.db.update_one", AsyncMock(return_value={"id": "op1"})) as update:
        from backend.utils.payment_operations import schedule_retry

        await schedule_retry(operation, error="provider timeout")

    changes = update.await_args.args[2]
    assert changes["status"] == "pending"
    assert changes["metadata"] == {"source": "refund", "error_attempt_count": 1}
    assert "attempt_count" not in changes
