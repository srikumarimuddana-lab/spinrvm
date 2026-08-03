"""Coverage tests for backend/utils/quest_tracker.py.

A1c Sub-tier C — test-only change, no application code modified. Goal is to
push branch/line coverage on this module from ~70% toward 100% by exercising
the paths the existing `TestQuestTrackerOnRideComplete` class in
`test_quests.py` doesn't reach: the DB-exception guard, expired-quest
handling, the earnings_target branch, the per-progress-row exception guard,
and every branch of `_ride_local_completion_hour` (missing timestamp,
unparseable timestamp, naive datetime, area-timezone lookup success/
failure/invalid-tz fallback).

Mocking style follows `backend/tests/test_quests.py::TestQuestTrackerOnRideComplete`
(patch `utils.quest_tracker.db`, `@pytest.mark.anyio` for async tests), but
uses a lighter-weight ad-hoc mock (not the routes-oriented `make_mock_db()`
helper) since this module talks to `quest_progress`, `quests`, and
`service_areas` directly via `db.get_rows` / `db.find_one` / `db.update_one`.

NOTE ("found not fixed"): `_ride_local_completion_hour` treats a naive
`datetime` object (not a string) passed as `ride_completed_at` /
`completed_at` / `updated_at` as already UTC (quest_tracker.py:46-47,
mirroring the same "assume UTC" convention as
`utils/datetime_utils.parse_iso_utc`). That's intentional and matches the
documented convention, not a bug. No behavioral bug was found while writing
these tests; see individual test docstrings for the exact branch each one
pins.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
_PAST = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

BASE_QUEST = {
    "id": "quest_1",
    "title": "Complete 10 Rides",
    "type": "ride_count",
    "target_value": 10.0,
    "reward_amount": 25.0,
    "start_date": _PAST,
    "end_date": _FUTURE,
    "is_active": True,
}

BASE_PROGRESS = {
    "id": "progress_1",
    "quest_id": "quest_1",
    "driver_id": "driver_123",
    "current_value": 5,
    "status": "active",
}


def make_db(*, quests=None, areas=None, get_rows_result=None, get_rows_side_effect=None):
    """Lightweight db double for utils.quest_tracker.

    - `quests`: dict of quest_id -> quest doc, used to answer
      `db.find_one("quests", {"id": ...})`.
    - `areas`: dict of area_id -> area doc, used to answer
      `db.find_one("service_areas", {"id": ...})`.
    - `get_rows_result` / `get_rows_side_effect`: wired straight to
      `db.get_rows` (quest_progress fetch).
    """
    quests = quests or {}
    areas = areas or {}
    mock = MagicMock()

    async def _find_one(table, filters=None, **kwargs):
        if table == "quests":
            return quests.get((filters or {}).get("id"))
        if table == "service_areas":
            return areas.get((filters or {}).get("id"))
        return None

    mock.find_one = AsyncMock(side_effect=_find_one)
    mock.update_one = AsyncMock(return_value={"id": "progress_1"})
    if get_rows_side_effect is not None:
        mock.get_rows = AsyncMock(side_effect=get_rows_side_effect)
    else:
        mock.get_rows = AsyncMock(return_value=get_rows_result if get_rows_result is not None else [])
    return mock


class TestGetRowsFailureGuard:
    """Lines 76-77: a failing quest_progress fetch must not raise — it logs
    and returns, leaving the ride-completion caller unaffected."""

    @pytest.mark.anyio
    async def test_get_rows_exception_is_swallowed(self):
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        mock_db = make_db(get_rows_side_effect=RuntimeError("db down"))

        with patch("utils.quest_tracker.db", mock_db):
            # Must not raise.
            await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

        mock_db.update_one.assert_not_awaited()


class TestQuestLookupSkip:
    """Line 86: a progress row whose quest is missing or inactive is skipped
    (no crash, no update) rather than treated as an error."""

    @pytest.mark.anyio
    async def test_missing_quest_is_skipped(self):
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        mock_db = make_db(quests={}, get_rows_result=[dict(BASE_PROGRESS)])

        with patch("utils.quest_tracker.db", mock_db):
            await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

        mock_db.update_one.assert_not_awaited()

    @pytest.mark.anyio
    async def test_inactive_quest_is_skipped(self):
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        inactive_quest = {**BASE_QUEST, "is_active": False}
        mock_db = make_db(
            quests={"quest_1": inactive_quest},
            get_rows_result=[dict(BASE_PROGRESS)],
        )

        with patch("utils.quest_tracker.db", mock_db):
            await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

        mock_db.update_one.assert_not_awaited()


class TestExpiredQuest:
    """Lines 91-96: an active progress row against an already-expired quest
    is flipped to status=expired instead of having its value advanced."""

    @pytest.mark.anyio
    async def test_expired_quest_marks_progress_expired(self):
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        expired_quest = {**BASE_QUEST, "end_date": _PAST}
        mock_db = make_db(
            quests={"quest_1": expired_quest},
            get_rows_result=[dict(BASE_PROGRESS)],
        )

        with patch("utils.quest_tracker.db", mock_db):
            await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

        mock_db.update_one.assert_awaited_once()
        table, filters, update = mock_db.update_one.await_args.args[:3]
        assert table == "quest_progress"
        assert filters == {"id": "progress_1"}
        assert update["$set"]["status"] == "expired"
        assert "updated_at" in update["$set"]
        # Expired branch must not also try to bump current_value.
        assert "current_value" not in update["$set"]


class TestEarningsTargetQuest:
    """Lines 104-105: earnings_target quests advance by the ride's
    driver_earnings, not by a flat +1 like ride_count."""

    @pytest.mark.anyio
    async def test_earnings_target_increments_by_ride_earnings(self):
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        earnings_quest = {**BASE_QUEST, "id": "quest_e", "type": "earnings_target", "target_value": 100.0}
        progress = {**BASE_PROGRESS, "quest_id": "quest_e", "current_value": 40}
        mock_db = make_db(
            quests={"quest_e": earnings_quest},
            get_rows_result=[progress],
        )

        with patch("utils.quest_tracker.db", mock_db):
            await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1", "driver_earnings": 15.5})

        mock_db.update_one.assert_awaited_once()
        update = mock_db.update_one.await_args.args[2]
        assert update["$set"]["current_value"] == 55.5
        assert update["$set"].get("status") != "completed"

    @pytest.mark.anyio
    async def test_earnings_target_missing_earnings_treated_as_zero(self):
        """ride.get("driver_earnings", 0) or 0 — a missing/None/0 earnings
        field must not advance the quest nor raise."""
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        earnings_quest = {**BASE_QUEST, "id": "quest_e", "type": "earnings_target", "target_value": 100.0}
        progress = {**BASE_PROGRESS, "quest_id": "quest_e", "current_value": 40}
        mock_db = make_db(
            quests={"quest_e": earnings_quest},
            get_rows_result=[progress],
        )

        with patch("utils.quest_tracker.db", mock_db):
            await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

        update = mock_db.update_one.await_args.args[2]
        assert update["$set"]["current_value"] == 40


class TestPerProgressRowExceptionGuard:
    """Lines 131-132: an exception while processing one progress row must be
    caught and logged so a single malformed/erroring row can't abort quest
    progress updates for the driver's other active quests."""

    @pytest.mark.anyio
    async def test_one_bad_row_does_not_block_the_next(self):
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        good_quest = {**BASE_QUEST, "id": "quest_good"}
        bad_progress = {**BASE_PROGRESS, "id": "progress_bad", "quest_id": "quest_bad"}
        good_progress = {**BASE_PROGRESS, "id": "progress_good", "quest_id": "quest_good"}

        mock_db = make_db(get_rows_result=[bad_progress, good_progress])

        async def _find_one(table, filters=None, **kwargs):
            if table == "quests":
                qid = (filters or {}).get("id")
                if qid == "quest_bad":
                    raise RuntimeError("quests table unavailable")
                return good_quest if qid == "quest_good" else None
            return None

        mock_db.find_one = AsyncMock(side_effect=_find_one)

        with patch("utils.quest_tracker.db", mock_db):
            # Must not raise despite the first row's quest lookup blowing up.
            await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

        # Only the second (good) row reaches the update call.
        mock_db.update_one.assert_awaited_once()
        filters = mock_db.update_one.await_args.args[1]
        assert filters == {"id": "progress_good"}


class TestRideLocalCompletionHour:
    """Direct coverage of utils.quest_tracker._ride_local_completion_hour —
    every early-return and fallback branch, exercised through the
    peak_rides code path in update_quest_progress_on_ride_complete since
    that's the only caller."""

    @staticmethod
    def _peak(ride, area=None):
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        peak_quest = {**BASE_QUEST, "id": "quest_peak", "type": "peak_rides", "target_value": 5.0}
        progress = {**BASE_PROGRESS, "id": "progress_peak", "quest_id": "quest_peak", "current_value": 2}
        mock_db = make_db(
            quests={"quest_peak": peak_quest},
            areas=area or {},
            get_rows_result=[progress],
        )
        return update_quest_progress_on_ride_complete, mock_db

    @pytest.mark.anyio
    async def test_no_completion_timestamp_present_does_not_advance(self):
        """Line 41: none of ride_completed_at/completed_at/updated_at set ->
        _ride_local_completion_hour returns None -> quest value untouched."""
        fn, mock_db = self._peak({"id": "ride_1"})

        with patch("utils.quest_tracker.db", mock_db):
            await fn("driver_123", {"id": "ride_1"})

        update = mock_db.update_one.await_args.args[2]
        assert update["$set"]["current_value"] == 2

    @pytest.mark.anyio
    async def test_unparseable_timestamp_string_does_not_advance(self):
        """Line 45: parse_iso_utc returns None for a garbage string ->
        early-return None, same as no timestamp at all."""
        fn, mock_db = self._peak({"id": "ride_1", "ride_completed_at": "not-a-real-timestamp"})

        with patch("utils.quest_tracker.db", mock_db):
            await fn("driver_123", {"id": "ride_1", "ride_completed_at": "not-a-real-timestamp"})

        update = mock_db.update_one.await_args.args[2]
        assert update["$set"]["current_value"] == 2

    @pytest.mark.anyio
    async def test_naive_datetime_object_is_assumed_utc(self):
        """Lines 46-47: a non-string datetime with tzinfo=None (e.g. a raw
        Supabase timestamp column deserialized without a tz) is tagged UTC
        rather than raising, then converted to local time as usual.
        2026-06-21T23:30:00 UTC -> 17:30 America/Regina (evening peak)."""
        naive_dt = datetime(2026, 6, 21, 23, 30, 0)  # no tzinfo
        assert naive_dt.tzinfo is None
        ride = {"id": "ride_1", "ride_completed_at": naive_dt, "service_area_id": None}
        fn, mock_db = self._peak(ride)

        with patch("utils.quest_tracker.db", mock_db):
            await fn("driver_123", ride)

        update = mock_db.update_one.await_args.args[2]
        assert update["$set"]["current_value"] == 3  # advanced -> local peak hour

    @pytest.mark.anyio
    async def test_service_area_timezone_is_used_when_resolvable(self):
        """Lines 52-55: an area with a resolvable timezone overrides the
        America/Regina default. 2026-06-21T23:30:00+00:00 is 16:30 in
        America/Vancouver (UTC-7 in June, DST) -- off-peak there -- versus
        17:30 (peak) in the default America/Regina, so this pins that the
        area's timezone genuinely changes the outcome."""
        ride = {
            "id": "ride_1",
            "ride_completed_at": "2026-06-21T23:30:00+00:00",
            "service_area_id": "area_van",
        }
        fn, mock_db = self._peak(ride, area={"area_van": {"id": "area_van", "timezone": "America/Vancouver"}})

        with patch("utils.quest_tracker.db", mock_db):
            await fn("driver_123", ride)

        update = mock_db.update_one.await_args.args[2]
        assert update["$set"]["current_value"] == 2  # 16:30 local -> not in 17-20 window

    @pytest.mark.anyio
    async def test_area_lookup_exception_falls_back_to_default_timezone(self):
        """Lines 56-57: db.find_one("service_areas", ...) raising must be
        caught and logged, falling back to America/Regina rather than
        propagating and aborting the whole quest update."""
        from utils.quest_tracker import update_quest_progress_on_ride_complete

        ride = {
            "id": "ride_1",
            "ride_completed_at": "2026-06-21T23:30:00+00:00",
            "service_area_id": "area_broken",
        }
        peak_quest = {**BASE_QUEST, "id": "quest_peak", "type": "peak_rides", "target_value": 5.0}
        progress = {**BASE_PROGRESS, "id": "progress_peak", "quest_id": "quest_peak", "current_value": 2}
        mock_db = make_db(quests={"quest_peak": peak_quest}, get_rows_result=[progress])

        async def _find_one(table, filters=None, **kwargs):
            if table == "quests":
                return peak_quest
            if table == "service_areas":
                raise RuntimeError("service_areas lookup failed")
            return None

        mock_db.find_one = AsyncMock(side_effect=_find_one)

        with patch("utils.quest_tracker.db", mock_db):
            # Must not raise despite the area lookup blowing up.
            await update_quest_progress_on_ride_complete("driver_123", ride)

        update = mock_db.update_one.await_args.args[2]
        # Falls back to America/Regina -> 17:30 local -> evening peak -> advances.
        assert update["$set"]["current_value"] == 3

    @pytest.mark.anyio
    async def test_invalid_area_timezone_falls_back_to_default(self):
        """Lines 60-61: a resolved but bogus timezone string raises inside
        ZoneInfo(...)/astimezone -- caught and retried against the default
        America/Regina timezone instead of propagating."""
        ride = {
            "id": "ride_1",
            "ride_completed_at": "2026-06-21T23:30:00+00:00",
            "service_area_id": "area_bogus",
        }
        fn, mock_db = self._peak(ride, area={"area_bogus": {"id": "area_bogus", "timezone": "Not/ARealZone"}})

        with patch("utils.quest_tracker.db", mock_db):
            await fn("driver_123", ride)

        update = mock_db.update_one.await_args.args[2]
        # Falls back to America/Regina -> 17:30 local -> evening peak -> advances.
        assert update["$set"]["current_value"] == 3
