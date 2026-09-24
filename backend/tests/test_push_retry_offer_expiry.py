"""push_retry drops expired v2 offer pushes and applies the offer TTL (T6-6)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils import push_retry


def _row(data):
    return {
        "id": "row-1",
        "user_id": "u1",
        "title": "t",
        "body": "b",
        "data": data,
        "attempts": 0,
        "target_app": "driver",
        "users": {"fcm_token_driver": "fcm-token"},
    }


def _v2(expires_at):
    return {"type": "new_ride_assignment", "offer_protocol": "v2", "expires_at": expires_at.isoformat()}


@pytest.mark.anyio
async def test_expired_v2_offer_row_is_deleted_without_sending():
    past = datetime.now(timezone.utc) - timedelta(seconds=2)
    with (
        patch.object(push_retry, "_delete_row", AsyncMock()) as delete,
        patch.object(push_retry, "_claim_row", AsyncMock(return_value=True)) as claim,
        patch.object(push_retry, "_send_fcm_push", AsyncMock(return_value=True)) as send,
        patch.object(push_retry, "_record_offer_push_skipped", MagicMock()) as skipped,
    ):
        await push_retry._process_row(_row(_v2(past)))
    delete.assert_awaited_once_with("row-1")
    claim.assert_not_awaited()
    send.assert_not_awaited()
    skipped.assert_called_once_with("expired")


@pytest.mark.anyio
async def test_live_v2_and_legacy_rows_are_sent():
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    legacy = {"type": "new_ride_assignment", "offer_expires_at": past.isoformat()}
    for data in (_v2(future), legacy):
        with (
            patch.object(push_retry, "_delete_row", AsyncMock()) as delete,
            patch.object(push_retry, "_claim_row", AsyncMock(return_value=True)),
            patch.object(push_retry, "_send_fcm_push", AsyncMock(return_value=True)) as send,
            patch.object(push_retry, "run_sync", AsyncMock()),
        ):
            await push_retry._process_row(_row(data))
        send.assert_awaited_once()
        delete.assert_not_awaited()


@pytest.mark.anyio
async def test_send_fcm_push_applies_v2_ttl():
    import sys
    import types

    captured = {}

    class _Msg:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    messaging = types.SimpleNamespace(
        **{
            n: _Msg
            for n in (
                "AndroidConfig",
                "AndroidNotification",
                "APNSConfig",
                "APNSPayload",
                "Aps",
                "ApsAlert",
                "CriticalSound",
                "Message",
                "Notification",
            )
        }
    )

    def _send(message):
        captured["message"] = message
        return "ok"

    messaging.send = _send
    pkg = types.ModuleType("firebase_admin")
    pkg.messaging = messaging
    future = datetime.now(timezone.utc) + timedelta(seconds=9.5)
    with patch.dict(sys.modules, {"firebase_admin": pkg, "firebase_admin.messaging": messaging}):
        ok = await push_retry._send_fcm_push("tok", "t", "b", _v2(future), "u1", "driver")
    assert ok is True
    msg = captured["message"]
    ttl = msg.kwargs["android"].kwargs["ttl"]
    assert 9 <= ttl.total_seconds() <= 10
    assert msg.kwargs["apns"].kwargs["headers"]["apns-expiration"] == str(int(future.timestamp()))
