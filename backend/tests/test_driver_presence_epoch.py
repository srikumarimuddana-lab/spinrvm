"""Unit tests for v2 presence isolation and concurrent-evidence merging."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from backend.repositories import driver_presence_repo
from backend.utils import driver_presence


class FakeRedis:
    def __init__(self):
        self.hashes = {}

    async def eval(self, script, numkeys, key, contact_ms, contact_deadline_ms, location_deadline_ms):
        fields = self.hashes.setdefault(key, {})
        old_contact_ms = int(fields.get("contact_received_ms", 0))
        old_contact_deadline_ms = int(fields.get("contact_valid_until_ms", 0))
        old_location_deadline_ms = int(fields.get("location_valid_until_ms", 0))
        if int(contact_ms) >= old_contact_ms:
            fields["contact_received_ms"] = str(contact_ms)
            fields["contact_valid_until_ms"] = str(contact_deadline_ms)
        if int(location_deadline_ms) > old_location_deadline_ms:
            fields["location_valid_until_ms"] = str(location_deadline_ms)
        fields["expires_at_ms"] = str(max(int(contact_deadline_ms), old_contact_deadline_ms))
        return [
            part
            for pair in fields.items()
            for part in (pair[0].encode(), pair[1].encode())
            if pair[0] != "expires_at_ms"
        ]

    async def hgetall(self, key):
        return {
            name.encode(): value.encode() for name, value in self.hashes.get(key, {}).items() if name != "expires_at_ms"
        }

    async def delete(self, key):
        self.hashes.pop(key, None)


@pytest.mark.asyncio
async def test_scoped_presence_isolated_by_session_and_decimal_epoch(monkeypatch):
    now = datetime.now(timezone.utc)
    fake_redis = FakeRedis()
    monkeypatch.setattr(driver_presence, "_get_redis", AsyncMock(return_value=fake_redis))
    monkeypatch.setattr(
        driver_presence_repo,
        "renew_driver_presence",
        AsyncMock(
            return_value={
                "status": "renewed",
                "code": "renewed",
                "online_epoch": "9007199254741000",
                "contact_received_at": now.isoformat(),
                "contact_valid_until": (now + timedelta(seconds=90)).isoformat(),
                "location_valid_until": (now + timedelta(seconds=1)).isoformat(),
            }
        ),
    )
    key_a = driver_presence.scoped_presence_key("driver-1", "session/A", 9007199254741000)
    key_b = driver_presence.scoped_presence_key("driver-1", "session/B", 9007199254741000)
    assert key_a != key_b
    assert key_a.endswith(":epoch:9007199254741000")
    result = await driver_presence.renew_driver_presence("driver-1", "session/A", 9007199254741000)
    assert result["status"] == "renewed"
    assert result["online_epoch"] == "9007199254741000"
    assert await driver_presence.get_scoped_driver_presence("driver-1", "session/A", 9007199254741000)
    assert await driver_presence.get_scoped_driver_presence("driver-1", "session/B", 9007199254741000) is None
    assert driver_presence._key("driver-1") not in driver_presence._local


@pytest.mark.asyncio
async def test_contact_only_renewal_preserves_gps_deadline_and_old_key_isolated(monkeypatch):
    now = datetime.now(timezone.utc)
    fake_redis = FakeRedis()
    monkeypatch.setattr(driver_presence, "_get_redis", AsyncMock(return_value=fake_redis))
    results = [
        {
            "status": "renewed",
            "code": "renewed",
            "contact_received_at": now.isoformat(),
            "contact_valid_until": (now + timedelta(seconds=90)).isoformat(),
            "location_valid_until": (now + timedelta(seconds=30)).isoformat(),
        },
        {
            "status": "renewed",
            "code": "renewed",
            "contact_received_at": (now + timedelta(seconds=1)).isoformat(),
            "contact_valid_until": (now + timedelta(seconds=91)).isoformat(),
            "location_valid_until": None,
        },
    ]
    monkeypatch.setattr(driver_presence_repo, "renew_driver_presence", AsyncMock(side_effect=results))
    key = driver_presence.scoped_presence_key("driver-2", "sess-1", 17)
    assert key.endswith(":epoch:17")
    first = await driver_presence.renew_driver_presence("driver-2", "sess-1", 17, location_captured_at=now)
    second = await driver_presence.renew_driver_presence("driver-2", "sess-1", 17)
    evidence = await driver_presence.get_scoped_driver_presence("driver-2", "sess-1", 17)
    assert second["contact_valid_until"] > first["contact_valid_until"]
    assert evidence["location_valid_until"] == first["location_valid_until"]
    await driver_presence.clear_scoped_driver_presence("driver-2", "sess-1", 17)
    assert await driver_presence.get_scoped_driver_presence("driver-2", "sess-1", 17) is None


@pytest.mark.asyncio
async def test_scoped_renewal_maps_fence_conflicts_without_writing_redis(monkeypatch):
    monkeypatch.setattr(
        driver_presence_repo,
        "renew_driver_presence",
        AsyncMock(return_value={"status": "stale_epoch", "code": "CONTROLLER_SESSION_MISMATCH", "online_epoch": "18"}),
    )
    result = await driver_presence.renew_driver_presence("driver-3", "old-session", 17)
    # F2-8a: a controller mismatch when the caller IS the current session
    # means the stored controller is stale, not the caller -- mapped to
    # ONLINE_EPOCH_STALE (with the original code preserved as reason_code) so
    # the newest login is no longer incorrectly told SESSION_SUPERSEDED.
    assert result == {
        "status": "stale_epoch",
        "code": "ONLINE_EPOCH_STALE",
        "online_epoch": "18",
        "reason_code": "CONTROLLER_SESSION_MISMATCH",
    }


@pytest.mark.asyncio
async def test_unconfigured_redis_never_uses_per_process_presence_for_v2(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(driver_presence, "_get_redis", AsyncMock(return_value=None))
    monkeypatch.setattr(
        driver_presence_repo,
        "renew_driver_presence",
        AsyncMock(
            return_value={
                "status": "renewed",
                "code": "renewed",
                "contact_received_at": now.isoformat(),
                "contact_valid_until": (now + timedelta(seconds=90)).isoformat(),
                "location_valid_until": None,
            }
        ),
    )
    result = await driver_presence.renew_driver_presence("driver-4", "sess-1", 2)
    assert result["status"] == "unavailable"
    assert result["code"] == "PRESENCE_UNAVAILABLE"
