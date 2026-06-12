"""Tests for utils/stale_intent_reconciler.py.

The reconciler must only flip intent offline for drivers that are stale on
the durable DB signal AND absent from Redis presence AND not on an active
ride — and must refuse to act at all when Redis is in fallback mode (the
retired presence_sweeper's failure class).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


class FakeReconcilerDB:
    def __init__(self, drivers: list[dict] | None = None, active_rides: list[dict] | None = None):
        self.drivers = drivers or []
        self.active_rides = active_rides or []
        self.updates: list[tuple[dict, dict]] = []
        self.claim_returns_row = True

    async def get_rows(self, table: str, filters: dict | None = None, **kwargs):
        if table == "drivers":
            return self.drivers
        if table == "rides":
            return self.active_rides
        return []

    async def update_one(self, table: str, filters: dict, update: dict):
        assert table == "drivers"
        assert filters.get("is_online") is True, "flip must be an atomic claim on is_online=true"
        self.updates.append((filters, update))
        return {**filters, **update} if self.claim_returns_row else None


@pytest.fixture
def patched(monkeypatch):
    from utils import stale_intent_reconciler as rec

    fake_db = FakeReconcilerDB()
    monkeypatch.setattr(rec, "db", fake_db)
    monkeypatch.setattr(rec, "get_redis_stats", AsyncMock(return_value={"connected": True}))
    monkeypatch.setattr(rec, "present_driver_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(rec, "record_period_transition", AsyncMock())
    monkeypatch.setattr(rec, "send_push_notification", AsyncMock(return_value=True))
    monkeypatch.setattr(rec, "get_app_settings", AsyncMock(return_value={}))
    return rec, fake_db


@pytest.mark.asyncio
async def test_flips_stale_absent_driver(patched):
    rec, fake_db = patched
    fake_db.drivers = [{"id": "d1", "user_id": "u1", "updated_at": "2026-06-12T06:00:00+00:00"}]

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["flipped"] == 1
    filters, update = fake_db.updates[0]
    assert filters == {"id": "d1", "is_online": True}
    assert update["is_online"] is False
    assert update["is_available"] is False
    assert update["went_offline_at"] == NOW.isoformat()
    rec.record_period_transition.assert_awaited_once_with("d1", 0)
    rec.send_push_notification.assert_awaited_once()
    assert rec.send_push_notification.await_args.kwargs["target_app"] == "driver"


@pytest.mark.asyncio
async def test_redis_fallback_skips_tick_entirely(patched):
    """In-process Redis fallback → presence is per-replica → never flip."""
    rec, fake_db = patched
    fake_db.drivers = [{"id": "d1", "user_id": "u1", "updated_at": "old"}]
    rec.get_redis_stats = AsyncMock(return_value={"connected": False})

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats == {"candidates": 0, "skipped_present": 0, "skipped_active_ride": 0, "flipped": 0}
    assert fake_db.updates == []


@pytest.mark.asyncio
async def test_present_driver_is_never_flipped(patched):
    """Stale DB row but live presence key (e.g. WS alive, location permission
    revoked) → reachable → leave intent alone."""
    rec, fake_db = patched
    fake_db.drivers = [{"id": "d1", "user_id": "u1", "updated_at": "old"}]
    rec.present_driver_ids = AsyncMock(return_value={"d1"})

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["flipped"] == 0
    assert stats["skipped_present"] == 1
    assert fake_db.updates == []


@pytest.mark.asyncio
async def test_presence_lookup_failure_skips_tick(patched):
    """No trustworthy presence → err on not flipping anyone."""
    rec, fake_db = patched
    fake_db.drivers = [{"id": "d1", "user_id": "u1", "updated_at": "old"}]
    rec.present_driver_ids = AsyncMock(side_effect=RuntimeError("Redis down"))

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["flipped"] == 0
    assert fake_db.updates == []


@pytest.mark.asyncio
async def test_driver_on_active_ride_is_skipped(patched):
    """A Period 0 write with an open ride would corrupt the insurance log."""
    rec, fake_db = patched
    fake_db.drivers = [{"id": "d1", "user_id": "u1", "updated_at": "old"}]
    fake_db.active_rides = [{"id": "r1", "driver_id": "d1"}]

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["flipped"] == 0
    assert stats["skipped_active_ride"] == 1
    assert fake_db.updates == []
    rec.record_period_transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_lost_claim_means_no_side_effects(patched):
    """Zero rows from the claim (other replica / driver toggled) → no period
    transition, no push."""
    rec, fake_db = patched
    fake_db.drivers = [{"id": "d1", "user_id": "u1", "updated_at": "old"}]
    fake_db.claim_returns_row = False

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["flipped"] == 0
    rec.record_period_transition.assert_not_awaited()
    rec.send_push_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_candidates_is_a_cheap_noop(patched):
    rec, fake_db = patched

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["candidates"] == 0
    assert fake_db.updates == []
    rec.present_driver_ids.assert_not_awaited()
