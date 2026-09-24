"""v2 offer push deadline: FCM TTL, APNs expiration, expired pushes skipped (T6-5)."""

import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend import features


class _Msg:
    """Record constructor kwargs for firebase_admin.messaging stand-ins."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _fake_messaging():
    mod = types.SimpleNamespace()
    for name in (
        "AndroidConfig",
        "AndroidNotification",
        "APNSConfig",
        "APNSPayload",
        "APNSFCMOptions",
        "Aps",
        "ApsAlert",
        "CriticalSound",
        "Message",
        "Notification",
    ):
        setattr(mod, name, _Msg)
    return mod


@pytest.fixture()
def fake_firebase():
    messaging = _fake_messaging()
    pkg = types.ModuleType("firebase_admin")
    pkg.messaging = messaging
    with patch.dict(sys.modules, {"firebase_admin": pkg, "firebase_admin.messaging": messaging}):
        yield messaging


def _v2_data(expires_at: datetime, **extra):
    return {
        "type": "new_ride_assignment",
        "offer_protocol": "v2",
        "expires_at": expires_at.isoformat(),
        "offer_expires_at": expires_at.isoformat(),
        **extra,
    }


def test_v2_push_sets_ttl_and_apns_expiration(fake_firebase):
    deadline = datetime.now(timezone.utc) + timedelta(seconds=12.2)
    msg = features._build_fcm_message("tok", "t", "b", _v2_data(deadline), "driver")
    ttl = msg.kwargs["android"].kwargs["ttl"]
    assert isinstance(ttl, timedelta)
    assert 12 <= ttl.total_seconds() <= 13
    headers = msg.kwargs["apns"].kwargs["headers"]
    assert headers["apns-expiration"] == str(int(deadline.timestamp()))
    assert headers["apns-priority"] == "10"


def test_v2_push_ttl_is_at_least_one_second(fake_firebase):
    deadline = datetime.now(timezone.utc) + timedelta(milliseconds=100)
    msg = features._build_fcm_message("tok", "t", "b", _v2_data(deadline), "driver")
    assert msg.kwargs["android"].kwargs["ttl"] == timedelta(seconds=1)


def test_legacy_push_is_unchanged(fake_firebase):
    deadline = datetime.now(timezone.utc) + timedelta(seconds=15)
    data = {"type": "new_ride_assignment", "offer_expires_at": deadline.isoformat()}
    msg = features._build_fcm_message("tok", "t", "b", data, "driver")
    assert msg.kwargs["android"].kwargs["ttl"] is None
    assert "apns-expiration" not in msg.kwargs["apns"].kwargs["headers"]


def test_expired_helper_only_applies_to_v2():
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert features._v2_offer_push_expired(_v2_data(past)) is True
    assert features._v2_offer_push_expired({"type": "new_ride_assignment", "expires_at": past.isoformat()}) is False
    assert features._v2_offer_push_expired(_v2_data(past + timedelta(minutes=1))) is False
    assert (
        features._v2_offer_push_expired({**_v2_data(past), "expires_at": "garbage", "offer_expires_at": None}) is False
    )


@pytest.mark.anyio
async def test_batch_skips_expired_v2_push_and_never_queues_it():
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    push = {"user_id": "u1", "title": "t", "body": "b", "data": _v2_data(past)}
    enqueue = AsyncMock()
    lookup = AsyncMock(return_value=[])
    with (
        patch("backend.utils.push_retry.enqueue_push", enqueue),
        patch.object(features.db_supabase, "get_rows_batched_in", lookup),
        patch.object(features, "_record_offer_push_skipped", MagicMock()) as skipped,
    ):
        await features.send_dispatch_offer_pushes_batch([push])
    lookup.assert_not_awaited()
    enqueue.assert_not_awaited()
    skipped.assert_called_once_with("expired")
