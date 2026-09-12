"""Tests for send_dispatch_offer_pushes_batch (R10, docs/audit/ride-experience/ROADMAP.md).

backend/routes/rides/matching.py's batch-offer dispatch loop used to spawn one
independent send_push_notification() per candidate driver -- N separate FCM
round-trips per batch offer. send_dispatch_offer_pushes_batch (backend/
features.py) groups those into Firebase Admin SDK send_each() calls instead.

firebase_admin.messaging is mocked via sys.modules, matching the established
convention in test_p3_push_notifications.py (the function does a local
`from firebase_admin import messaging`).
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _push(user_id: str, title: str = "New ride · $12.50", body: str = "A → B") -> dict:
    return {"user_id": user_id, "title": title, "body": body, "data": {"type": "new_ride_assignment"}}


def _mock_fcm(send_each_result=None, send_each_side_effect=None):
    mock_messaging = MagicMock()
    mock_messaging.send_each = (
        MagicMock(side_effect=send_each_side_effect)
        if send_each_side_effect
        else MagicMock(return_value=send_each_result)
    )
    mock_firebase = MagicMock()
    mock_firebase.messaging = mock_messaging

    class _FakeNotFoundError(Exception):
        pass

    mock_firebase.exceptions.NotFoundError = _FakeNotFoundError
    return mock_firebase, mock_messaging, _FakeNotFoundError


def _send_response(success: bool, exception=None):
    resp = MagicMock()
    resp.success = success
    resp.exception = exception
    return resp


class TestEmptyAndTokenLookup:
    async def test_empty_pushes_is_a_no_op(self):
        from backend import features as features_mod

        with patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock()) as lookup:
            await features_mod.send_dispatch_offer_pushes_batch([])
        lookup.assert_not_called()

    async def test_batches_the_token_lookup_in_one_query(self):
        """N drivers must be one get_rows_batched_in call, not N."""
        from backend import features as features_mod

        pushes = [_push(f"drv-{i}") for i in range(5)]
        users = [{"id": f"drv-{i}", "fcm_token_driver": f"fcm-{i}"} for i in range(5)]
        mock_firebase, mock_messaging, _ = _mock_fcm(
            send_each_result=MagicMock(responses=[_send_response(True) for _ in range(5)])
        )

        with (
            patch.dict(sys.modules, {"firebase_admin": mock_firebase, "firebase_admin.messaging": mock_messaging}),
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(return_value=users)) as lookup,
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)

        lookup.assert_awaited_once()
        assert lookup.await_args.args[0] == "users"
        assert lookup.await_args.args[1] == "id"
        assert set(lookup.await_args.args[2]) == {f"drv-{i}" for i in range(5)}
        mock_messaging.send_each.assert_called_once()
        assert len(mock_messaging.send_each.call_args.args[0]) == 5

    async def test_token_lookup_failure_falls_back_to_one_by_one_send(self):
        """A Supabase hiccup during the batched lookup must not silently drop
        the whole batch's offers -- falls back to the pre-R10 per-driver
        send_push_notification() path."""
        from backend import features as features_mod

        pushes = [_push("drv-1"), _push("drv-2")]
        spawned = []

        with (
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(side_effect=RuntimeError("db down"))),
            patch("backend.utils.background.spawn", side_effect=lambda coro: spawned.append(coro) or coro.close()),
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)

        assert len(spawned) == 2


class TestSuccessAndOutcomes:
    async def test_all_success_records_success_outcome_and_no_retry(self):
        from backend import features as features_mod
        from backend.utils import metrics

        def _count(outcome: str) -> int:
            return metrics.snapshot()["counters"].get("spinr_push_send_total", {}).get((("outcome", outcome),), 0)

        pushes = [_push("drv-1"), _push("drv-2")]
        users = [
            {"id": "drv-1", "fcm_token_driver": "fcm-1"},
            {"id": "drv-2", "fcm_token_driver": "fcm-2"},
        ]
        mock_firebase, mock_messaging, _ = _mock_fcm(
            send_each_result=MagicMock(responses=[_send_response(True), _send_response(True)])
        )
        before = _count("success")

        with (
            patch.dict(sys.modules, {"firebase_admin": mock_firebase, "firebase_admin.messaging": mock_messaging}),
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(return_value=users)),
            patch("backend.utils.push_retry.enqueue_push", AsyncMock()) as retry_mock,
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)

        assert _count("success") == before + 2
        retry_mock.assert_not_called()
        # Each message built with the right per-driver token.
        messages_arg = mock_messaging.send_each.call_args.args[0]
        assert mock_messaging.Message.call_args_list  # _build_fcm_message called via messaging.Message(...)
        assert len(messages_arg) == 2

    async def test_missing_token_skips_that_driver_without_calling_send_each_for_it(self):
        from backend import features as features_mod

        pushes = [_push("drv-1"), _push("drv-2")]
        # drv-2 has no fcm token on file at all.
        users = [{"id": "drv-1", "fcm_token_driver": "fcm-1"}, {"id": "drv-2"}]
        mock_firebase, mock_messaging, _ = _mock_fcm(send_each_result=MagicMock(responses=[_send_response(True)]))

        with (
            patch.dict(sys.modules, {"firebase_admin": mock_firebase, "firebase_admin.messaging": mock_messaging}),
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(return_value=users)),
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)

        assert len(mock_messaging.send_each.call_args.args[0]) == 1

    async def test_expo_token_recipient_bypasses_send_each(self):
        """send_each is FCM-only -- an Expo-token recipient must go through
        the existing single-recipient Expo path instead. The Expo send
        happens inside a spawned task (_send_expo_with_retry), so this uses
        the real spawn() (a real asyncio.create_task) and yields once to let
        it run, rather than mocking spawn to a no-op close()."""
        from backend import features as features_mod

        pushes = [_push("drv-1")]
        users = [{"id": "drv-1", "fcm_token_driver": "ExponentPushToken[abc123]"}]

        with (
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(return_value=users)),
            patch("backend.features._send_expo_push", AsyncMock(return_value=True)) as expo_mock,
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)
            await asyncio.sleep(0)

        expo_mock.assert_called_once()
        assert expo_mock.call_args.args[0] == "ExponentPushToken[abc123]"

    async def test_expo_send_failure_enqueues_retry(self):
        """The Expo path must honor the same guaranteed-delivery contract as
        the FCM chunk below it -- a failed/errored Expo send must not just
        disappear (spinr-dispatch-reviewer finding)."""
        from backend import features as features_mod

        pushes = [_push("drv-1")]
        users = [{"id": "drv-1", "fcm_token_driver": "ExponentPushToken[abc123]"}]

        with (
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(return_value=users)),
            patch("backend.features._send_expo_push", AsyncMock(return_value=False)),
            patch("backend.utils.push_retry.enqueue_push", AsyncMock()) as retry_mock,
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)
            await asyncio.sleep(0)

        retry_mock.assert_awaited_once()
        assert retry_mock.await_args.args[0] == "drv-1"

    async def test_expo_send_exception_enqueues_retry(self):
        from backend import features as features_mod

        pushes = [_push("drv-1")]
        users = [{"id": "drv-1", "fcm_token_driver": "ExponentPushToken[abc123]"}]

        with (
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(return_value=users)),
            patch("backend.features._send_expo_push", AsyncMock(side_effect=RuntimeError("Expo API down"))),
            patch("backend.utils.push_retry.enqueue_push", AsyncMock()) as retry_mock,
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)
            await asyncio.sleep(0)

        retry_mock.assert_awaited_once()


class TestFailureHandling:
    async def test_stale_token_is_purged_and_retry_enqueued(self):
        from backend import features as features_mod
        from backend.utils import metrics

        def _count(outcome: str) -> int:
            return metrics.snapshot()["counters"].get("spinr_push_send_total", {}).get((("outcome", outcome),), 0)

        pushes = [_push("drv-1")]
        users = [{"id": "drv-1", "fcm_token_driver": "fcm-stale"}]
        mock_firebase, mock_messaging, fake_not_found = _mock_fcm()

        def _send_each(messages):
            return MagicMock(responses=[_send_response(False, exception=fake_not_found("gone"))])

        mock_messaging.send_each = MagicMock(side_effect=_send_each)
        before = _count("stale_token")

        with (
            patch.dict(sys.modules, {"firebase_admin": mock_firebase, "firebase_admin.messaging": mock_messaging}),
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(return_value=users)),
            patch("backend.features.db_supabase.update_one", AsyncMock()) as purge_mock,
            patch("backend.features.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.utils.push_retry.enqueue_push", AsyncMock()) as retry_mock,
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)

        assert _count("stale_token") == before + 1
        purge_mock.assert_awaited_once()
        purge_args = purge_mock.await_args.args
        assert purge_args[0] == "users"
        assert purge_args[1] == {"id": "drv-1"}
        assert purge_args[2] == {"fcm_token": None, "fcm_token_driver": None}
        retry_mock.assert_awaited_once()
        assert retry_mock.await_args.kwargs["priority"] == "dispatch"
        assert retry_mock.await_args.kwargs["target_app"] == "driver"

    async def test_generic_send_failure_records_failed_outcome_and_retries(self):
        from backend import features as features_mod
        from backend.utils import metrics

        def _count(outcome: str) -> int:
            return metrics.snapshot()["counters"].get("spinr_push_send_total", {}).get((("outcome", outcome),), 0)

        pushes = [_push("drv-1")]
        users = [{"id": "drv-1", "fcm_token_driver": "fcm-1"}]
        mock_firebase, mock_messaging, _ = _mock_fcm(
            send_each_result=MagicMock(responses=[_send_response(False, exception=RuntimeError("quota exceeded"))])
        )
        before = _count("failed")

        with (
            patch.dict(sys.modules, {"firebase_admin": mock_firebase, "firebase_admin.messaging": mock_messaging}),
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(return_value=users)),
            patch("backend.utils.push_retry.enqueue_push", AsyncMock()) as retry_mock,
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)

        assert _count("failed") == before + 1
        retry_mock.assert_awaited_once()

    async def test_send_each_raising_marks_every_message_in_chunk_failed_and_retried(self):
        from backend import features as features_mod
        from backend.utils import metrics

        def _count(outcome: str) -> int:
            return metrics.snapshot()["counters"].get("spinr_push_send_total", {}).get((("outcome", outcome),), 0)

        pushes = [_push("drv-1"), _push("drv-2")]
        users = [
            {"id": "drv-1", "fcm_token_driver": "fcm-1"},
            {"id": "drv-2", "fcm_token_driver": "fcm-2"},
        ]
        mock_firebase, mock_messaging, _ = _mock_fcm(send_each_side_effect=RuntimeError("FCM outage"))
        before = _count("failed")

        with (
            patch.dict(sys.modules, {"firebase_admin": mock_firebase, "firebase_admin.messaging": mock_messaging}),
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(return_value=users)),
            patch("backend.utils.push_retry.enqueue_push", AsyncMock()) as retry_mock,
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)

        assert _count("failed") == before + 2
        assert retry_mock.await_count == 2

    async def test_one_recipients_message_build_failure_does_not_abort_the_rest_of_the_chunk(self):
        """spinr-dispatch-reviewer finding: _build_fcm_message used to be
        called in a list comprehension outside any try, so one recipient's
        construction failure would raise out of the whole spawned task,
        silently dropping delivery AND retry-enqueue for every other driver
        in the chunk. Must now be isolated per-recipient."""
        from backend import features as features_mod
        from backend.utils import metrics

        def _count(outcome: str) -> int:
            return metrics.snapshot()["counters"].get("spinr_push_send_total", {}).get((("outcome", outcome),), 0)

        pushes = [_push("drv-1"), _push("drv-2")]
        users = [
            {"id": "drv-1", "fcm_token_driver": "fcm-1"},
            {"id": "drv-2", "fcm_token_driver": "fcm-2"},
        ]
        mock_firebase, mock_messaging, _ = _mock_fcm(send_each_result=MagicMock(responses=[_send_response(True)]))
        before_failed = _count("failed")
        before_success = _count("success")

        with (
            patch.dict(sys.modules, {"firebase_admin": mock_firebase, "firebase_admin.messaging": mock_messaging}),
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(return_value=users)),
            patch(
                "backend.features._build_fcm_message",
                side_effect=lambda token, title, body, data, target_app: (
                    (_ for _ in ()).throw(RuntimeError("bad data")) if token == "fcm-1" else MagicMock()
                ),
            ),
            patch("backend.utils.push_retry.enqueue_push", AsyncMock()) as retry_mock,
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)

        # drv-1's build failed -> retried, not sent. drv-2's build succeeded
        # and was still included in the (single-message) send_each call.
        assert _count("failed") == before_failed + 1
        assert _count("success") == before_success + 1
        assert len(mock_messaging.send_each.call_args.args[0]) == 1
        retry_mock.assert_awaited_once()
        assert retry_mock.await_args.args[0] == "drv-1"


class TestChunking:
    async def test_more_recipients_than_batch_size_split_across_multiple_send_each_calls(self):
        from backend import features as features_mod

        pushes = [_push(f"drv-{i}") for i in range(5)]
        users = [{"id": f"drv-{i}", "fcm_token_driver": f"fcm-{i}"} for i in range(5)]
        mock_firebase, mock_messaging, _ = _mock_fcm()
        # Every call returns as many successes as messages it was given.
        mock_messaging.send_each = MagicMock(
            side_effect=lambda messages: MagicMock(responses=[_send_response(True) for _ in messages])
        )

        with (
            patch.dict(sys.modules, {"firebase_admin": mock_firebase, "firebase_admin.messaging": mock_messaging}),
            patch("backend.features.db_supabase.get_rows_batched_in", AsyncMock(return_value=users)),
            patch("backend.features._FCM_BATCH_SIZE", 2),
        ):
            await features_mod.send_dispatch_offer_pushes_batch(pushes)

        # 5 recipients at a batch size of 2 -> 3 calls (2, 2, 1).
        assert mock_messaging.send_each.call_count == 3
        sizes = sorted(len(c.args[0]) for c in mock_messaging.send_each.call_args_list)
        assert sizes == [1, 2, 2]
