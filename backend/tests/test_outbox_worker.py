"""Unit tests for the transactional outbox poller.

The repository RPCs are mocked — lease/CAS behaviour is covered by the
real-Postgres suite in tests/rls/test_transactional_outbox.py.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def _dead_letter_count(topic: str = "ride_receipt.v1") -> int:
    from utils import metrics
    from utils.metrics import _labels_to_key

    bucket = metrics.snapshot().get("counters", {}).get("spinr_outbox_dead_lettered_total", {})
    return int(bucket.get(_labels_to_key({"topic": topic}), 0))


def _result(status, error_code=None, message_id=None, provider="ses"):
    from utils.email_provider import EmailDeliveryResult, EmailDeliveryStatus

    return EmailDeliveryResult(
        status=EmailDeliveryStatus[status] if isinstance(status, str) else status,
        provider=provider if status == "accepted" else None,
        message_id=message_id,
        error_code=error_code,
    )


def _msg(**overrides):
    base = {
        "id": "ob-1",
        "topic": "ride_receipt.v1",
        "dedupe_key": "auto:ride-1",
        "payload": {"ride_id": "ride-1"},
        "status": "processing",
        "attempt_count": 1,
        "max_attempts": 8,
        "lease_token": "tok-1",
        "leased_by": "worker-a",
    }
    base.update(overrides)
    return base


@pytest.fixture
def repo():
    with (
        patch("services.outbox.claim_batch", new_callable=AsyncMock) as claim,
        patch("services.outbox.ack", new_callable=AsyncMock) as ack,
        patch("services.outbox.discard", new_callable=AsyncMock) as discard,
        patch("services.outbox.fail", new_callable=AsyncMock) as fail,
        patch("services.outbox.stats", new_callable=AsyncMock) as stats,
        patch("services.outbox.cleanup", new_callable=AsyncMock) as cleanup,
    ):
        ack.return_value = True
        discard.return_value = True
        fail.return_value = True
        stats.return_value = []
        cleanup.return_value = {}
        yield {
            "claim": claim,
            "ack": ack,
            "discard": discard,
            "fail": fail,
            "stats": stats,
            "cleanup": cleanup,
        }


async def test_accepted_delivery_acks(repo):
    from utils.outbox_worker import outbox_tick

    repo["claim"].return_value = [_msg()]
    with patch(
        "services.payment_service.send_ride_receipt_result",
        AsyncMock(return_value=_result("accepted", message_id="m1")),
    ):
        n = await outbox_tick("worker-a")
    assert n == 1
    repo["ack"].assert_awaited_once_with("ob-1", "tok-1")
    repo["fail"].assert_not_awaited()
    repo["discard"].assert_not_awaited()


async def test_terminal_skip_discards(repo):
    from utils.outbox_worker import outbox_tick

    repo["claim"].return_value = [_msg()]
    with patch(
        "services.payment_service.send_ride_receipt_result",
        AsyncMock(return_value=_result("terminal_skip", error_code="no_recipient")),
    ):
        await outbox_tick("worker-a")
    repo["discard"].assert_awaited_once_with("ob-1", "tok-1", "no_recipient")
    repo["ack"].assert_not_awaited()


async def test_retryable_failure_fails_for_backoff(repo):
    from utils.outbox_worker import outbox_tick

    repo["claim"].return_value = [_msg()]
    with patch(
        "services.payment_service.send_ride_receipt_result",
        AsyncMock(return_value=_result("retryable_failure", error_code="provider_unavailable")),
    ):
        await outbox_tick("worker-a")
    repo["fail"].assert_awaited_once_with("ob-1", "tok-1", "provider_unavailable")
    repo["ack"].assert_not_awaited()


async def test_unknown_topic_retries_never_discards(repo):
    from utils.outbox_worker import outbox_tick

    repo["claim"].return_value = [_msg(topic="future.topic.v1", payload={"ride_id": "ride-1"})]
    await outbox_tick("worker-a")
    repo["fail"].assert_awaited_once_with("ob-1", "tok-1", "unknown_topic")
    repo["discard"].assert_not_awaited()
    repo["ack"].assert_not_awaited()


async def test_malformed_payload_is_terminal_discard(repo):
    from utils.outbox_worker import outbox_tick

    repo["claim"].return_value = [_msg(payload={"email": "x@y.z"})]
    await outbox_tick("worker-a")
    repo["discard"].assert_awaited_once_with("ob-1", "tok-1", "malformed_payload")
    repo["fail"].assert_not_awaited()


async def test_stale_token_ack_is_noop(repo):
    from utils.outbox_worker import outbox_tick

    repo["claim"].return_value = [_msg()]
    repo["ack"].return_value = False
    with patch(
        "services.payment_service.send_ride_receipt_result",
        AsyncMock(return_value=_result("accepted", message_id="m1")),
    ):
        await outbox_tick("worker-a")
    repo["ack"].assert_awaited_once()


async def test_final_attempt_retryable_emits_dead_letter_metric(repo):
    from utils.outbox_worker import outbox_tick

    before = _dead_letter_count()
    repo["claim"].return_value = [_msg(attempt_count=8, max_attempts=8)]
    with patch(
        "services.payment_service.send_ride_receipt_result",
        AsyncMock(return_value=_result("retryable_failure", error_code="provider_unavailable")),
    ):
        await outbox_tick("worker-a")
    repo["fail"].assert_awaited_once()
    assert _dead_letter_count() == before + 1


async def test_stale_token_final_attempt_does_not_emit_dead_letter(repo):
    from utils.outbox_worker import outbox_tick

    before = _dead_letter_count()
    repo["fail"].return_value = False
    repo["claim"].return_value = [_msg(attempt_count=8, max_attempts=8)]
    with patch(
        "services.payment_service.send_ride_receipt_result",
        AsyncMock(return_value=_result("retryable_failure", error_code="provider_unavailable")),
    ):
        await outbox_tick("worker-a")
    repo["fail"].assert_awaited_once()
    assert _dead_letter_count() == before


async def test_claim_failure_does_not_record_heartbeat(repo):
    from utils.outbox_worker import outbox_tick

    repo["claim"].side_effect = RuntimeError("rpc down")
    with patch("utils.outbox_worker.record_heartbeat") as heartbeat:
        n = await outbox_tick("worker-a")
    assert n == 0
    heartbeat.assert_not_called()


async def test_successful_tick_records_heartbeat(repo):
    from utils.outbox_worker import _LOOP_NAME, outbox_tick

    repo["claim"].return_value = []
    with patch("utils.outbox_worker.record_heartbeat") as heartbeat:
        n = await outbox_tick("worker-a")
    assert n == 0
    heartbeat.assert_called_once_with(_LOOP_NAME)


async def test_claim_returned_dead_lettered_rows_emit_metric_and_are_not_dispatched(repo):
    from utils.outbox_worker import outbox_tick

    before = _dead_letter_count()
    repo["claim"].return_value = [
        _msg(status="dead_lettered", lease_token=None, attempt_count=8),
    ]
    with patch(
        "services.payment_service.send_ride_receipt_result",
        AsyncMock(),
    ) as send:
        n = await outbox_tick("worker-a")
    assert n == 0
    send.assert_not_awaited()
    repo["ack"].assert_not_awaited()
    repo["fail"].assert_not_awaited()
    assert _dead_letter_count() == before + 1


async def test_mixed_claim_batch_dispatches_processing_only(repo):
    from utils.outbox_worker import outbox_tick

    before = _dead_letter_count()
    repo["claim"].return_value = [
        _msg(id="ob-dlq", status="dead_lettered", lease_token=None, attempt_count=8),
        _msg(id="ob-live", status="processing", lease_token="tok-live"),
    ]
    with patch(
        "services.payment_service.send_ride_receipt_result",
        AsyncMock(return_value=_result("accepted", message_id="m1")),
    ) as send:
        n = await outbox_tick("worker-a")
    assert n == 1
    send.assert_awaited_once()
    repo["ack"].assert_awaited_once_with("ob-live", "tok-live")
    assert _dead_letter_count() == before + 1


async def test_claim_batch_raises_when_rpc_returns_none():
    from services import outbox

    with patch("services.outbox.db_supabase.rpc", AsyncMock(return_value=None)):
        with pytest.raises(RuntimeError, match="unavailable"):
            await outbox.claim_batch("worker-a")


async def test_claim_batch_raises_when_rpc_returns_unexpected_type():
    from services import outbox

    with patch("services.outbox.db_supabase.rpc", AsyncMock(return_value={"ok": True})):
        with pytest.raises(RuntimeError, match="unexpected type"):
            await outbox.claim_batch("worker-a")


async def test_handler_exception_is_retryable_without_raising(repo):
    from utils.outbox_worker import outbox_tick

    repo["claim"].return_value = [_msg()]
    with patch(
        "services.payment_service.send_ride_receipt_result",
        AsyncMock(side_effect=RuntimeError("SES 500")),
    ):
        n = await outbox_tick("worker-a")
    assert n == 1
    repo["fail"].assert_awaited_once_with("ob-1", "tok-1", "provider_unavailable")


async def test_adaptive_idle_polling_uses_longer_sleep():
    from utils import outbox_worker

    sleeps: list[float] = []
    stop = asyncio.Event()

    async def fake_tick(_worker_id):
        if len(sleeps) >= 2:
            stop.set()
            return 0
        return 0 if not sleeps else 1

    async def fake_wait(aw, timeout=None):
        sleeps.append(timeout)
        if hasattr(aw, "close"):
            aw.close()
        if len(sleeps) >= 3:
            stop.set()
        await asyncio.sleep(0)

    with (
        patch.object(outbox_worker, "outbox_tick", side_effect=fake_tick),
        patch.object(asyncio, "wait_for", side_effect=fake_wait),
    ):
        await outbox_worker.run_outbox_worker(stop, worker_id="w")

    assert 10 in sleeps
    assert 1 in sleeps


async def test_is_auto_receipt_queued_true_when_row_exists():
    from services import outbox

    with patch("services.outbox.db_supabase.find_one", AsyncMock(return_value={"id": "ob-1"})):
        assert await outbox.is_auto_receipt_queued("ride-1") is True


async def test_is_auto_receipt_queued_false_when_missing():
    from services import outbox

    with patch("services.outbox.db_supabase.find_one", AsyncMock(return_value=None)):
        assert await outbox.is_auto_receipt_queued("ride-1") is False


async def test_is_auto_receipt_queued_lookup_failure_returns_false():
    from services import outbox

    with patch("services.outbox.db_supabase.find_one", AsyncMock(side_effect=RuntimeError("db down"))):
        assert await outbox.is_auto_receipt_queued("ride-1") is False


async def test_cleanup_uses_delete_many_not_rpc():
    from services import outbox

    calls = []

    async def fake_delete(table, filters):
        calls.append((table, filters))
        return [{"id": f"row-{len(calls)}"}]

    with (
        patch("services.outbox.db_supabase.delete_many", new=fake_delete),
        patch("services.outbox.db_supabase.rpc", new=AsyncMock()) as rpc,
    ):
        result = await outbox.cleanup()

    rpc.assert_not_awaited()
    assert result["published_discarded_deleted"] == 1
    assert result["dead_lettered_deleted"] == 2
    assert len(calls) == 3
    assert all(table == "outbox_messages" for table, _ in calls)
    assert calls[0][1]["status"]["$in"] == ["published", "discarded"]
    assert "$lt" in calls[0][1]["updated_at"]
    assert calls[1][1]["status"] == "dead_lettered"
    assert "$lt" in calls[1][1]["dead_lettered_at"]
    assert calls[2][1]["status"] == "dead_lettered"
    assert calls[2][1]["dead_lettered_at"] is None
    assert "$lt" in calls[2][1]["updated_at"]


async def test_cleanup_counts_none_as_zero():
    from services import outbox

    with patch("services.outbox.db_supabase.delete_many", AsyncMock(return_value=None)):
        result = await outbox.cleanup()
    assert result == {
        "published_discarded_deleted": 0,
        "dead_lettered_deleted": 0,
    }
