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
            # Offset paging is only sound with a stable order (PR #1848 review).
            assert kwargs.get("order") == "updated_at", "candidate scan must be deterministically ordered"
            # Honour limit/offset over the *currently matching* set, like
            # PostgREST would: flipped drivers leave the predicate.
            flipped = {f["id"] for f, _u in self.updates} if self.claim_returns_row else set()
            matching = [d for d in self.drivers if d["id"] not in flipped]
            offset = kwargs.get("offset") or 0
            limit = kwargs.get("limit") or len(matching)
            return matching[offset : offset + limit]
        if table == "rides":
            return self.active_rides
        return []

    async def update_one(self, table: str, filters: dict, update: dict):
        assert table == "drivers"
        assert filters.get("is_online") is True, "flip must be an atomic claim on is_online=true"
        assert "$lt" in filters.get("updated_at", {}), "claim must re-assert the staleness predicate"
        self.updates.append((filters, update))
        return {**update, "id": filters["id"]} if self.claim_returns_row else None


@pytest.fixture
def patched(monkeypatch):
    from utils import stale_intent_reconciler as rec

    fake_db = FakeReconcilerDB()
    monkeypatch.setattr(rec, "db", fake_db)
    monkeypatch.setattr(rec, "get_redis_stats", AsyncMock(return_value={"connected": True}))
    monkeypatch.setattr(rec, "present_driver_ids_checked", AsyncMock(return_value=(set(), True)))
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
    assert filters["id"] == "d1"
    assert filters["is_online"] is True
    assert filters["updated_at"] == {"$lt": (NOW - rec.timedelta(hours=4)).isoformat()}
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
    rec.present_driver_ids_checked = AsyncMock(return_value=({"d1"}, True))

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["flipped"] == 0
    assert stats["skipped_present"] == 1
    assert fake_db.updates == []


@pytest.mark.asyncio
async def test_presence_lookup_failure_skips_tick(patched):
    """No trustworthy presence → err on not flipping anyone."""
    rec, fake_db = patched
    fake_db.drivers = [{"id": "d1", "user_id": "u1", "updated_at": "old"}]
    rec.present_driver_ids_checked = AsyncMock(side_effect=RuntimeError("Redis down"))

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["flipped"] == 0
    assert fake_db.updates == []


@pytest.mark.asyncio
async def test_presence_unreachable_flag_aborts_tick(patched):
    """present_driver_ids_checked returning reachable=False (Redis configured
    but down mid-tick) must abort — an empty set in that state means
    'unknowable', not 'everyone offline'."""
    rec, fake_db = patched
    fake_db.drivers = [{"id": "d1", "user_id": "u1", "updated_at": "old"}]
    rec.present_driver_ids_checked = AsyncMock(return_value=(set(), False))

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["flipped"] == 0
    assert fake_db.updates == []


@pytest.mark.asyncio
async def test_reconnect_between_batch_check_and_claim_skips_flip(patched):
    """A WS pong refreshes Redis presence without touching drivers.updated_at,
    so the claim predicate can't see a reconnect — the per-driver presence
    re-check right before the claim must catch it."""
    rec, fake_db = patched
    fake_db.drivers = [{"id": "d1", "user_id": "u1", "updated_at": "old"}]
    # Batch check: absent. Per-driver re-check just before the claim: present.
    rec.present_driver_ids_checked = AsyncMock(side_effect=[(set(), True), ({"d1"}, True)])

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["flipped"] == 0
    assert stats["skipped_present"] == 1
    assert fake_db.updates == []
    rec.record_period_transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_read_failure_skips_tick(patched):
    """A settings outage must not silently apply the 4h default over an
    operator-raised threshold."""
    rec, fake_db = patched
    fake_db.drivers = [{"id": "d1", "user_id": "u1", "updated_at": "old"}]
    rec.get_app_settings = AsyncMock(side_effect=RuntimeError("settings DB down"))

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["flipped"] == 0
    assert fake_db.updates == []


@pytest.mark.asyncio
async def test_pages_past_skipped_candidates(patched, monkeypatch):
    """Skipped (present) drivers filling the first page must not shadow
    absent drivers behind them."""
    rec, fake_db = patched
    monkeypatch.setattr(rec, "CANDIDATE_LIMIT", 2)
    fake_db.drivers = [
        {"id": "d1", "user_id": "u1", "updated_at": "old"},  # present → skip
        {"id": "d2", "user_id": "u2", "updated_at": "old"},  # present → skip
        {"id": "d3", "user_id": "u3", "updated_at": "old"},  # absent → flip
    ]
    rec.present_driver_ids_checked = AsyncMock(side_effect=lambda ids: ({"d1", "d2"} & set(ids), True))

    stats = await rec.reconcile_stale_intent(NOW)

    assert stats["skipped_present"] == 2
    assert stats["flipped"] == 1
    assert fake_db.updates[0][0]["id"] == "d3"


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
    rec.present_driver_ids_checked.assert_not_awaited()
