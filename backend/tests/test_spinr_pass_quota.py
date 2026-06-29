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
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

# Project paths so ``utils.spinr_pass`` resolves when run standalone.
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# RideStatus lives in ``models.ride_status`` (a plain str-Enum, no pydantic),
# NOT in ``schemas``. spinr_pass once imported it from ``schemas``, which raised
# ``ImportError`` in production and made /subscription/current crash — the app
# then showed "No plans available" to drivers who *were* subscribed. This file
# previously stubbed ``schemas.RideStatus`` to mask that, so the bug shipped
# green. We import the real enum here instead, so the import path is exercised.
from models.ride_status import RideStatus  # noqa: E402
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

    def test_edmonton_winter_resets_one_hour_after_regina(self):
        # Jan: Edmonton is MST (UTC-7), Regina is UTC-6. Edmonton local midnight
        # is 07:00Z; Regina local midnight is 06:00Z — an hour apart.
        now = datetime(2026, 1, 15, 6, 30, tzinfo=timezone.utc)
        _, regina_end = spinr_pass.quota_day_bounds_utc(now)
        _, edm_end = spinr_pass.quota_day_bounds_utc(now, tz="America/Edmonton")
        assert regina_end == datetime(2026, 1, 16, 6, 0, tzinfo=timezone.utc)
        assert edm_end == datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)

    def test_edmonton_summer_equals_regina(self):
        # Jul: Edmonton is MDT (UTC-6) == Regina, so boundaries coincide.
        now = datetime(2026, 7, 15, 7, 0, tzinfo=timezone.utc)
        assert spinr_pass.quota_day_bounds_utc(now, tz="America/Edmonton") == spinr_pass.quota_day_bounds_utc(now)

    def test_unknown_tz_falls_back_to_regina(self):
        now = datetime(2026, 1, 15, 6, 30, tzinfo=timezone.utc)
        assert spinr_pass.quota_day_bounds_utc(now, tz="Mars/Phobos") == spinr_pass.quota_day_bounds_utc(now)

    def test_tzinfo_arg_accepted(self):
        now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
        # A fixed-offset tzinfo is honored directly (no zoneinfo lookup).
        edt = timezone(timedelta(hours=-4))
        start, _ = spinr_pass.quota_day_bounds_utc(now, tz=edt)
        assert start == datetime(2026, 6, 25, 4, 0, tzinfo=timezone.utc)


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

    def test_negative_other_than_minus_one_is_unlimited(self):
        # A misconfigured -2/-5 must read as "no limit", never 0-remaining.
        q = spinr_pass.compute_quota(-2, 50, self._now())
        assert q["unlimited"] is True
        assert q["exhausted"] is False
        assert q["rides_remaining"] == "unlimited"

    def test_zero_cap_is_exhausted(self):
        # 0 is a real cap (no rides), distinct from unlimited.
        q = spinr_pass.compute_quota(0, 0, self._now())
        assert q["unlimited"] is False
        assert q["exhausted"] is True
        assert q["rides_remaining"] == 0


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

    def __init__(self, *, count=0, rides_rows=None, area=None):
        self._count = count
        self._rides_rows = rides_rows or []
        self._area = area
        self.updated = []
        self.inserted = []
        self.count_filters = []
        self.rides_filters = []
        self.count_documents = AsyncMock(side_effect=self._count_documents)
        self.get_rows = AsyncMock(side_effect=self._get_rows)
        self.find_one = AsyncMock(side_effect=self._find_one)
        self.update_one = AsyncMock(side_effect=self._update_one)
        self.insert_one = AsyncMock(side_effect=self._insert_one)

    async def _count_documents(self, table, filt=None, **kw):
        self.count_filters.append((table, filt))
        return self._count

    async def _find_one(self, table, filt=None):
        if table == "service_areas":
            return self._area
        return None

    async def _get_rows(self, table, filt=None, columns=None, limit=None, **kw):
        if table == "rides":
            self.rides_filters.append(filt)
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
        # Rows carry ride_completed_at (the DB query filters on it, so returned
        # rows always have it) — the helper buckets each ride into its driver's
        # window. now() is current, so "just now" completions land in-window.
        recent = datetime.now(timezone.utc).isoformat()
        rides = [
            {"driver_id": "d1", "ride_completed_at": recent},
            {"driver_id": "d1", "ride_completed_at": recent},
            {"driver_id": "d2", "ride_completed_at": recent},
        ]
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

    async def test_skips_expired_and_negative_caps(self, patch_db):
        now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
        past = (now - timedelta(hours=1)).isoformat()
        future = (now + timedelta(days=2)).isoformat()
        # d1 finite+valid+over cap, d2 finite but expired (skip), d3 cap -2 (unlimited).
        recent = (now - timedelta(minutes=5)).isoformat()
        rides = [
            {"driver_id": "d1", "ride_completed_at": recent},
            {"driver_id": "d1", "ride_completed_at": recent},
            {"driver_id": "d2", "ride_completed_at": recent},
            {"driver_id": "d2", "ride_completed_at": recent},
        ]
        patch_db(_FakeDB(rides_rows=rides))
        subs = [
            {"driver_id": "d1", "rides_per_day": 2, "expires_at": future},
            {"driver_id": "d2", "rides_per_day": 2, "expires_at": past},
            {"driver_id": "d3", "rides_per_day": -2},
        ]
        result = await spinr_pass.exhausted_driver_ids(subs, now=now)
        assert result == {"d1"}


@pytest.mark.anyio
class TestQuotaStatusAndExhaustion:
    async def test_quota_status_none_without_sub(self, patch_db):
        db = _FakeDB(count=0)
        db.get_rows = AsyncMock(return_value=[])  # no active sub
        patch_db(db)
        assert await spinr_pass.quota_status("d1") is None

    async def test_quota_status_ignores_expired_active_sub(self, patch_db):
        now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
        past = (now - timedelta(hours=1)).isoformat()
        db = _FakeDB(count=4)
        sub = {"id": "s1", "rides_per_day": 4, "expires_at": past}
        patch_db(db)
        # Pass lapsed → quota is moot (expiry gates own it), not "rides used up".
        assert await spinr_pass.quota_status("d1", sub=sub, now=now) is None

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
        # Durable offline intent stamped so intent_online() reads offline.
        assert updates["went_offline_at"] == updates["updated_at"]

    async def test_noop_when_not_exhausted(self, patch_db):
        db = _FakeDB(count=1)
        patch_db(db)
        sub = {"id": "s1", "rides_per_day": 4}
        status = await spinr_pass.force_offline_if_exhausted({"id": "d1", "user_id": "u1"}, sub=sub)
        assert status is None
        assert [u for u in db.updated if u[0] == "drivers"] == []


@pytest.mark.anyio
class TestAssertQuotaAvailable:
    async def test_raises_403_when_exhausted(self, patch_db):
        db = _FakeDB(count=4)
        patch_db(db)
        with pytest.raises(Exception) as exc_info:
            await spinr_pass.assert_quota_available("d1", sub={"id": "s1", "rides_per_day": 4})
        assert getattr(exc_info.value, "status_code", None) == 403

    async def test_noop_returns_status_when_remaining(self, patch_db):
        db = _FakeDB(count=1)
        patch_db(db)
        status = await spinr_pass.assert_quota_available("d1", sub={"id": "s1", "rides_per_day": 4})
        assert status is not None
        assert status["exhausted"] is False

    async def test_unlimited_is_noop(self, patch_db):
        db = _FakeDB(count=99)
        patch_db(db)
        status = await spinr_pass.assert_quota_available("d1", sub={"id": "s1", "rides_per_day": -1})
        assert status is not None
        assert status["unlimited"] is True

    async def test_fails_open_on_lookup_error(self, patch_db):
        async def _boom(*a, **k):
            raise RuntimeError("db down")

        db = _FakeDB()
        db.count_documents = AsyncMock(side_effect=_boom)
        patch_db(db)
        # Lookup failure must not block — returns None instead of raising.
        assert await spinr_pass.assert_quota_available("d1", sub={"id": "s1", "rides_per_day": 4}) is None


@pytest.mark.anyio
class TestAreaTimezone:
    async def test_resolves_area_timezone(self, patch_db):
        patch_db(_FakeDB(area={"id": "a1", "timezone": "America/Edmonton"}))
        assert await spinr_pass.area_timezone("a1") == "America/Edmonton"

    async def test_none_area_id_skips_lookup(self, patch_db):
        db = patch_db(_FakeDB())
        assert await spinr_pass.area_timezone(None) is None
        db.find_one.assert_not_called()

    async def test_missing_timezone_column_is_none(self, patch_db):
        patch_db(_FakeDB(area={"id": "a1"}))
        assert await spinr_pass.area_timezone("a1") is None

    async def test_lookup_error_propagates(self, patch_db):
        # A DB error must NOT silently fall back to Regina — it propagates so the
        # caller's fail-open/closed handling decides (enforcing on the wrong tz
        # could mis-gate a driver near their local midnight).
        db = _FakeDB()
        db.find_one = AsyncMock(side_effect=RuntimeError("down"))
        patch_db(db)
        with pytest.raises(RuntimeError):
            await spinr_pass.area_timezone("a1")

    async def test_quota_status_fails_open_on_tz_error(self, patch_db):
        # quota_status propagates the tz error; assert_quota_available swallows it.
        db = _FakeDB(count=4, area=None)
        db.find_one = AsyncMock(side_effect=RuntimeError("down"))
        patch_db(db)
        result = await spinr_pass.assert_quota_available("d1", sub={"id": "s1", "rides_per_day": 4}, area_id="a1")
        assert result is None  # fail open, no raise


@pytest.mark.anyio
class TestTimezonePassthrough:
    async def test_completed_today_uses_tz_day_start(self, patch_db):
        db = _FakeDB(count=0)
        patch_db(db)
        # Winter instant: Edmonton (MST) day starts at 07:00Z, Regina at 06:00Z.
        now = datetime(2026, 1, 15, 6, 30, tzinfo=timezone.utc)
        await spinr_pass.completed_today("d1", now=now, tz="America/Edmonton")
        _, filt = db.count_filters[-1]
        assert filt["ride_completed_at"]["$gte"] == datetime(2026, 1, 14, 7, 0, tzinfo=timezone.utc).isoformat()
        # Regression: the status filter must be the plain "completed" string.
        # A broken `from schemas import RideStatus` used to crash this call,
        # which surfaced to drivers as "No plans available" despite an active pass.
        assert filt["status"] == "completed"
        assert filt["status"] == RideStatus.COMPLETED.value

    async def test_quota_status_resolves_area_tz(self, patch_db):
        # Edmonton area + 4-ride cap, 4 used → exhausted, reset at Edmonton
        # midnight. Winter now=06:30Z is 23:30 MST, so ~30m to the 07:00Z reset.
        db = _FakeDB(count=4, area={"id": "a1", "timezone": "America/Edmonton"})
        patch_db(db)
        now = datetime(2026, 1, 15, 6, 30, tzinfo=timezone.utc)
        status = await spinr_pass.quota_status("d1", sub={"id": "s1", "rides_per_day": 4}, now=now, area_id="a1")
        assert status["exhausted"] is True
        assert status["quota_resets_at"] == datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc).isoformat()
        assert status["hours_until_reset"] == 0.5


# ── 1-day pass: 24h window, expiry by hours (not a midnight reset) ───────────


class TestPassIsHourly:
    def _sub(self, hours):
        start = datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)
        return {
            "started_at": start.isoformat(),
            "expires_at": (start + timedelta(hours=hours)).isoformat(),
        }

    def test_24h_pass_is_hourly(self):
        assert spinr_pass._pass_is_hourly(self._sub(24)) is True

    def test_7day_pass_is_not_hourly(self):
        assert spinr_pass._pass_is_hourly(self._sub(24 * 7)) is False

    def test_missing_timestamps_not_hourly(self):
        assert spinr_pass._pass_is_hourly({"rides_per_day": 4}) is False
        assert spinr_pass._pass_is_hourly({"started_at": self._sub(24)["started_at"]}) is False

    def test_zero_lifetime_not_hourly(self):
        # started == expires (degenerate row) must not read as an hourly window.
        start = datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc).isoformat()
        assert spinr_pass._pass_is_hourly({"started_at": start, "expires_at": start}) is False


class TestQuotaWindowForSub:
    def test_oneday_pass_window_is_the_pass_lifetime(self):
        start = datetime(2026, 6, 25, 18, 0, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        sub = {"started_at": start.isoformat(), "expires_at": end.isoformat()}
        assert spinr_pass.quota_window_for_sub(sub) == (start, end)

    def test_multiday_pass_window_is_calendar_day(self):
        now = datetime(2026, 6, 25, 23, 0, tzinfo=timezone.utc)  # 17:00 Regina
        start = now - timedelta(days=3)
        end = start + timedelta(days=30)
        sub = {"started_at": start.isoformat(), "expires_at": end.isoformat()}
        # Calendar day for a Regina 17:00 instant: [06:00Z that day, 06:00Z next).
        assert spinr_pass.quota_window_for_sub(sub, now=now) == (
            datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 26, 6, 0, tzinfo=timezone.utc),
        )


class TestComputeQuotaWindowOverride:
    def test_window_drives_the_countdown(self):
        now = datetime(2026, 6, 25, 20, 0, tzinfo=timezone.utc)
        window = (now - timedelta(hours=2), now + timedelta(hours=4))
        q = spinr_pass.compute_quota(4, 1, now, window=window, hourly=True)
        assert q["hourly"] is True
        assert q["quota_resets_at"] == window[1].isoformat()
        assert q["hours_until_reset"] == 4.0


@pytest.mark.anyio
class TestOneDayPassEnforcement:
    def _evening_sub(self, now, *, cap=4, hours=24):
        # Bought 2h ago in the evening; a `hours`-long pass.
        started = now - timedelta(hours=2)
        return started, {
            "id": "s1",
            "driver_id": "d1",
            "rides_per_day": cap,
            "started_at": started.isoformat(),
            "expires_at": (started + timedelta(hours=hours)).isoformat(),
        }

    async def test_quota_status_counts_over_24h_not_midnight(self, patch_db):
        # Evening 1-day pass: rides counted from started_at, countdown is the
        # pass end — NOT the upcoming midnight. 4 used of 4 → exhausted.
        now = datetime(2026, 6, 25, 20, 0, tzinfo=timezone.utc)
        started, sub = self._evening_sub(now)
        db = _FakeDB(count=4)
        patch_db(db)
        status = await spinr_pass.quota_status("d1", sub=sub, now=now)
        assert status["hourly"] is True
        assert status["exhausted"] is True
        assert status["quota_resets_at"] == sub["expires_at"]
        # completed_today queried from the pass window opening, not local midnight.
        _, filt = db.count_filters[-1]
        assert filt["ride_completed_at"]["$gte"] == started.isoformat()

    async def test_multiday_evening_pass_resets_at_midnight(self, patch_db):
        # 30-day pass bought in the evening still resets that day's allowance at
        # the upcoming local midnight (06:00Z), not 30 days out.
        now = datetime(2026, 6, 25, 23, 0, tzinfo=timezone.utc)  # 17:00 Regina
        _started, sub = self._evening_sub(now, hours=24 * 30)
        db = _FakeDB(count=2)
        patch_db(db)
        status = await spinr_pass.quota_status("d1", sub=sub, now=now)
        assert status["hourly"] is False
        assert status["quota_resets_at"] == datetime(2026, 6, 26, 6, 0, tzinfo=timezone.utc).isoformat()
        _, filt = db.count_filters[-1]
        assert filt["ride_completed_at"]["$gte"] == datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc).isoformat()

    async def test_exhausted_filter_ignores_rides_before_pass_window(self, patch_db):
        # Two completions predate the 24h pass window (e.g. a prior pass earlier
        # the same day); only the two inside the window count → not exhausted.
        now = datetime(2026, 6, 25, 20, 0, tzinfo=timezone.utc)
        started, sub = self._evening_sub(now)
        before = (started - timedelta(hours=1)).isoformat()
        inside = (now - timedelta(minutes=10)).isoformat()
        rides = [
            {"driver_id": "d1", "ride_completed_at": before},
            {"driver_id": "d1", "ride_completed_at": before},
            {"driver_id": "d1", "ride_completed_at": inside},
            {"driver_id": "d1", "ride_completed_at": inside},
        ]
        patch_db(_FakeDB(rides_rows=rides))
        assert await spinr_pass.exhausted_driver_ids([sub], now=now) == set()

    async def test_exhausted_filter_counts_rides_inside_pass_window(self, patch_db):
        now = datetime(2026, 6, 25, 20, 0, tzinfo=timezone.utc)
        _started, sub = self._evening_sub(now)
        inside = (now - timedelta(minutes=10)).isoformat()
        rides = [{"driver_id": "d1", "ride_completed_at": inside} for _ in range(4)]
        patch_db(_FakeDB(rides_rows=rides))
        assert await spinr_pass.exhausted_driver_ids([sub], now=now) == {"d1"}

    async def test_assert_quota_message_wording_for_oneday_pass(self, patch_db):
        now = datetime(2026, 6, 25, 20, 0, tzinfo=timezone.utc)
        _started, sub = self._evening_sub(now)
        db = _FakeDB(count=4)
        patch_db(db)
        with pytest.raises(Exception) as exc_info:
            await spinr_pass.assert_quota_available("d1", sub=sub, now=now)
        err = exc_info.value
        assert getattr(err, "status_code", None) == 403
        assert getattr(err, "action_hint", None) == "1-day pass limit reached"
        assert "24 hours" in getattr(err, "message", "")
