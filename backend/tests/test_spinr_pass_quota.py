"""Unit tests for the Spinr Pass daily ride-quota helpers.

Covers the per-calendar-day allowance model (America/Regina reset boundary),
the remaining/exhausted math surfaced to every enforcement gate, the batched
dispatch exhaustion filter, and the force-offline-on-completion side effect.

These exercise ``backend/utils/spinr_pass.py`` directly. The DB-touching helpers
are isolated by patching ``spinr_pass._db`` with an in-memory fake, so no live
Supabase is needed.
"""

import os
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

# Project paths so ``utils.spinr_pass`` resolves when run standalone.
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# In a bare unit env ``schemas`` pulls pydantic; only stub it when the real
# module can't import, so we never shadow the real schemas in CI.
try:  # pragma: no cover - import-path guard
    import schemas  # noqa: F401
except Exception:  # pragma: no cover
    _schemas = types.ModuleType("schemas")

    class _RideStatus:
        COMPLETED = "completed"

    _schemas.RideStatus = _RideStatus
    sys.modules["schemas"] = _schemas

from utils import spinr_pass  # noqa: E402

# America/Regina is UTC-6 year-round (no DST).
REGINA_OFFSET = timedelta(hours=-6)


# ── Pure window math ────────────────────────────────────────────────────────


class TestQuotaDayBounds:
    def test_regina_midnight_boundary(self):
        # 2026-06-25T03:00Z == 2026-06-24 21:00 Regina → day is the 24th local.
        now = datetime(2026, 6, 25, 3, 0, tzinfo=timezone.utc)
        start, end = spinr_pass.quota_day_bounds_utc(now)
        assert start == datetime(2026, 6, 24, 6, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
        assert end - start == timedelta(days=1)

    def test_now_inside_window(self):
        now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
        start, end = spinr_pass.quota_day_bounds_utc(now)
        assert start <= now < end

    def test_naive_now_treated_as_utc(self):
        naive = datetime(2026, 6, 25, 12, 0)
        aware = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
        assert spinr_pass.quota_day_bounds_utc(naive) == spinr_pass.quota_day_bounds_utc(aware)


class TestComputeQuota:
    def _now(self):
        # 03:00Z → 3h to the 06:00Z Regina reset.
        return datetime(2026, 6, 25, 3, 0, tzinfo=timezone.utc)

    def test_exhausted(self):
        q = spinr_pass.compute_quota(4, 4, self._now())
        assert q["exhausted"] is True
        assert q["rides_remaining"] == 0
        assert q["can_accept_rides"] is False
        assert q["hours_until_reset"] == 3.0

    def test_over_cap_clamps_to_zero(self):
        q = spinr_pass.compute_quota(4, 9, self._now())
        assert q["exhausted"] is True
        assert q["rides_remaining"] == 0

    def test_partial(self):
        q = spinr_pass.compute_quota(4, 1, self._now())
        assert q["exhausted"] is False
        assert q["rides_remaining"] == 3
        assert q["can_accept_rides"] is True

    def test_unlimited(self):
        q = spinr_pass.compute_quota(-1, 99, self._now())
        assert q["unlimited"] is True
        assert q["exhausted"] is False
        assert q["rides_remaining"] == "unlimited"
        assert q["can_accept_rides"] is True

    def test_malformed_rides_per_day_is_unlimited(self):
        q = spinr_pass.compute_quota(None, 5, self._now())
        assert q["unlimited"] is True
        assert q["exhausted"] is False


class TestHoursUntil:
    def test_none(self):
        assert spinr_pass.hours_until(None) is None

    def test_past_is_zero(self):
        now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
        assert spinr_pass.hours_until("2020-01-01T00:00:00Z", now) == 0.0

    def test_future(self):
        now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
        target = (now + timedelta(hours=30)).isoformat()
        assert spinr_pass.hours_until(target, now) == 30.0


# ── DB-backed helpers (fake _db) ────────────────────────────────────────────


class _FakeDB:
    """Minimal async stand-in for the db_supabase module surface used here."""

    def __init__(self, *, count=0, rides_rows=None):
        self._count = count
        self._rides_rows = rides_rows or []
        self.updated = []
        self.inserted = []
        self.count_documents = AsyncMock(return_value=count)
        self.get_rows = AsyncMock(side_effect=self._get_rows)
        self.update_one = AsyncMock(side_effect=self._update_one)
        self.insert_one = AsyncMock(side_effect=self._insert_one)

    async def _get_rows(self, table, filt=None, columns=None, limit=None, **kw):
        if table == "rides":
            return self._rides_rows
        if table == "driver_subscriptions":
            return []
        return []

    async def _update_one(self, table, filt, updates):
        self.updated.append((table, filt, updates))
        return {**filt, **updates}

    async def _insert_one(self, table, row):
        self.inserted.append((table, row))
        return row


@pytest.fixture
def patch_db(monkeypatch):
    def _install(db):
        monkeypatch.setattr(spinr_pass, "_db", lambda: db)
        return db

    return _install


@pytest.mark.anyio
class TestExhaustedDriverIds:
    async def test_finite_exhausted_only(self, patch_db):
        # d1 cap 2 used 2 (exhausted), d2 cap 3 used 1 (ok), d3 unlimited (skip).
        rides = [{"driver_id": "d1"}, {"driver_id": "d1"}, {"driver_id": "d2"}]
        patch_db(_FakeDB(rides_rows=rides))
        subs = [
            {"driver_id": "d1", "rides_per_day": 2},
            {"driver_id": "d2", "rides_per_day": 3},
            {"driver_id": "d3", "rides_per_day": -1},
        ]
        result = await spinr_pass.exhausted_driver_ids(subs)
        assert result == {"d1"}

    async def test_no_finite_subs_skips_query(self, patch_db):
        db = patch_db(_FakeDB())
        result = await spinr_pass.exhausted_driver_ids([{"driver_id": "d3", "rides_per_day": -1}])
        assert result == set()
        db.get_rows.assert_not_called()


@pytest.mark.anyio
class TestQuotaStatusAndExhaustion:
    async def test_quota_status_none_without_sub(self, patch_db):
        db = _FakeDB(count=0)
        db.get_rows = AsyncMock(return_value=[])  # no active sub
        patch_db(db)
        assert await spinr_pass.quota_status("d1") is None

    async def test_is_quota_exhausted_true(self, patch_db):
        db = _FakeDB(count=4)
        sub = {"id": "s1", "rides_per_day": 4}
        patch_db(db)
        assert await spinr_pass.is_quota_exhausted("d1", sub=sub) is True

    async def test_is_quota_exhausted_false_when_remaining(self, patch_db):
        db = _FakeDB(count=1)
        sub = {"id": "s1", "rides_per_day": 4}
        patch_db(db)
        assert await spinr_pass.is_quota_exhausted("d1", sub=sub) is False

    async def test_is_quota_exhausted_fails_open(self, patch_db, monkeypatch):
        async def _boom(*a, **k):
            raise RuntimeError("db down")

        db = _FakeDB()
        db.count_documents = AsyncMock(side_effect=_boom)
        patch_db(db)
        # Error path must not block the driver here — returns False.
        assert await spinr_pass.is_quota_exhausted("d1", sub={"id": "s1", "rides_per_day": 4}) is False


@pytest.mark.anyio
class TestForceOfflineIfExhausted:
    async def test_flips_offline_when_exhausted(self, patch_db):
        db = _FakeDB(count=4)
        patch_db(db)
        sub = {"id": "s1", "rides_per_day": 4}
        status = await spinr_pass.force_offline_if_exhausted({"id": "d1", "user_id": "u1"}, sub=sub)
        assert status is not None
        assert status["exhausted"] is True
        # The driver row was flipped offline.
        drivers_updates = [u for u in db.updated if u[0] == "drivers"]
        assert drivers_updates, "expected a drivers update"
        _, _, updates = drivers_updates[0]
        assert updates["is_online"] is False
        assert updates["is_available"] is False

    async def test_noop_when_not_exhausted(self, patch_db):
        db = _FakeDB(count=1)
        patch_db(db)
        sub = {"id": "s1", "rides_per_day": 4}
        status = await spinr_pass.force_offline_if_exhausted({"id": "d1", "user_id": "u1"}, sub=sub)
        assert status is None
        assert [u for u in db.updated if u[0] == "drivers"] == []
