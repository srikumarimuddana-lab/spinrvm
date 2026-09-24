from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from backend.utils import driver_presence


class _Pipeline:
    def __init__(self, fields):
        self.fields = fields
        self.keys = []

    def hgetall(self, key):
        self.keys.append(key)
        return self

    async def execute(self):
        return [self.fields for _ in self.keys]


class _Redis:
    def __init__(self, fields):
        self.pipe = _Pipeline(fields)

    async def mget(self, *keys):
        return [b"1" for _ in keys]

    def pipeline(self, transaction=False):
        assert transaction is False
        return self.pipe


@pytest.mark.asyncio
async def test_scoped_presence_reader_uses_durable_scope_and_pipeline(monkeypatch):
    until = int((datetime.now(timezone.utc) + timedelta(seconds=30)).timestamp() * 1000)
    redis = _Redis({b"contact_valid_until_ms": str(until).encode(), b"location_valid_until_ms": str(until).encode()})
    monkeypatch.setattr(driver_presence, "_get_redis", AsyncMock(return_value=redis))
    rows = [{"id": "d1", "controller_session_id": "s1", "online_epoch": 9, "is_online": True}]
    get_rows = AsyncMock(return_value=rows)
    monkeypatch.setattr("backend.repositories._base.get_rows", get_rows)

    present, reachable = await driver_presence.scoped_present_driver_ids_checked(["d1"])

    assert present == {"d1"}
    assert reachable is True
    assert get_rows.await_args.kwargs["columns"] == "id,controller_session_id,online_epoch,is_online"
    assert len(redis.pipe.keys) == 1
    assert redis.pipe.keys[0] == driver_presence.scoped_presence_key("d1", "s1", 9)


@pytest.mark.asyncio
async def test_scoped_presence_reader_rejects_expired_or_gps_missing_scope(monkeypatch):
    until = int((datetime.now(timezone.utc) - timedelta(seconds=1)).timestamp() * 1000)
    redis = _Redis({b"contact_valid_until_ms": str(until).encode(), b"location_valid_until_ms": b"0"})
    monkeypatch.setattr(driver_presence, "_get_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(
        "backend.repositories._base.get_rows",
        AsyncMock(return_value=[{"id": "d1", "controller_session_id": "s1", "online_epoch": 9, "is_online": True}]),
    )

    present, reachable = await driver_presence.scoped_present_driver_ids_checked(["d1"])

    assert present == set()
    assert reachable is True
    assert "d1" not in present  # old legacy key cannot stand in for scoped GPS


@pytest.mark.asyncio
async def test_scoped_presence_reader_fails_closed_for_v2_when_redis_unconfigured(monkeypatch):
    monkeypatch.setattr(driver_presence, "_get_redis", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "backend.repositories._base.get_rows",
        AsyncMock(return_value=[{"id": "d1", "controller_session_id": "s1", "online_epoch": 9, "is_online": True}]),
    )
    legacy = AsyncMock(return_value=(set(), True))
    monkeypatch.setattr(driver_presence, "present_driver_ids_checked", legacy)

    present, reachable = await driver_presence.scoped_present_driver_ids_checked(["d1"])

    assert present == set()
    assert reachable is False


@pytest.mark.asyncio
async def test_v2_reader_does_not_accept_legacy_key_for_untransitioned_driver(monkeypatch):
    redis = _Redis({})
    monkeypatch.setattr(driver_presence, "_get_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(
        "backend.repositories._base.get_rows",
        AsyncMock(return_value=[{"id": "d1", "controller_session_id": None, "online_epoch": 0, "is_online": True}]),
    )
    legacy = AsyncMock(return_value=({"d1"}, True))
    monkeypatch.setattr(driver_presence, "present_driver_ids_checked", legacy)

    present, reachable = await driver_presence.scoped_present_driver_ids_checked(["d1"])

    assert present == set()
    assert reachable is True
    legacy.assert_not_awaited()
