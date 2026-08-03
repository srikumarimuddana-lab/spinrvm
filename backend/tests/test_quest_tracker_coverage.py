"""Coverage-closing tests for backend/utils/quest_tracker.py.

Companion to TestQuestTrackerOnRideComplete in test_quests.py (which covers
the ride_count-increments-and-completes happy path and the peak_rides local-
timezone math). This file closes the remaining branches: db errors on the
progress fetch, an inactive/missing quest, an expired quest, the
earnings_target quest type, the service_areas timezone lookup (both success
and its swallowed-exception fallback), an invalid area timezone falling back
to America/Regina, a naive (no-tzinfo) completed_at, no usable completion
timestamp at all, and the per-progress exception guard that lets one bad
quest row not abort the rest of the batch.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from utils.quest_tracker import _ride_local_completion_hour, update_quest_progress_on_ride_complete

_FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
_PAST = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
_NOW = datetime.now(timezone.utc).isoformat()

RIDE_COUNT_QUEST = {
    "id": "quest_1",
    "title": "Complete 10 Rides",
    "type": "ride_count",
    "target_value": 10.0,
    "is_active": True,
    "end_date": _FUTURE,
}


def _mock_db(**overrides):
    """A minimal stand-in for db_supabase exposing exactly the flat functions
    quest_tracker.py calls: get_rows, find_one, update_one. Unlike test_quests'
    make_mock_db (a bare MagicMock with no service_areas sub-mock — awaiting
    an unconfigured attribute there raises), find_one here is a single
    AsyncMock so a peak_rides test can freely pass a service_area_id."""
    mock = MagicMock()
    mock.get_rows = overrides.get("get_rows", AsyncMock(return_value=[]))
    mock.find_one = overrides.get("find_one", AsyncMock(return_value=None))
    mock.update_one = overrides.get("update_one", AsyncMock(return_value=None))
    return mock


def _progress(**overrides):
    row = {
        "id": "progress_1",
        "quest_id": "quest_1",
        "driver_id": "driver_123",
        "current_value": 5,
        "status": "active",
    }
    row.update(overrides)
    return row


# ── progress fetch failure ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_progress_fetch_error_is_logged_and_swallowed(monkeypatch, caplog):
    import logging

    mock_db = _mock_db(get_rows=AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)

    with caplog.at_level(logging.ERROR):
        await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

    assert "Failed to fetch quest progress" in caplog.text
    mock_db.update_one.assert_not_awaited()


# ── quest not found / inactive ───────────────────────────────────────────


@pytest.mark.anyio
async def test_quest_not_found_is_skipped(monkeypatch):
    mock_db = _mock_db(
        get_rows=AsyncMock(return_value=[_progress()]),
        find_one=AsyncMock(return_value=None),
    )
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)

    await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

    mock_db.update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_inactive_quest_is_skipped(monkeypatch):
    inactive_quest = {**RIDE_COUNT_QUEST, "is_active": False}
    mock_db = _mock_db(
        get_rows=AsyncMock(return_value=[_progress()]),
        find_one=AsyncMock(return_value=inactive_quest),
    )
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)

    await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

    mock_db.update_one.assert_not_awaited()


# ── expired quest ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_expired_quest_marks_progress_expired(monkeypatch):
    expired_quest = {**RIDE_COUNT_QUEST, "end_date": _PAST}
    captured = {}

    async def _update_one(table, filters, update, **kwargs):
        captured["table"] = table
        captured["filters"] = filters
        captured["update"] = update

    mock_db = _mock_db(
        get_rows=AsyncMock(return_value=[_progress()]),
        find_one=AsyncMock(return_value=expired_quest),
        update_one=_update_one,
    )
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)

    await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

    assert captured["table"] == "quest_progress"
    assert captured["filters"] == {"id": "progress_1"}
    assert captured["update"]["$set"]["status"] == "expired"


# ── earnings_target quest type ────────────────────────────────────────────


@pytest.mark.anyio
async def test_earnings_target_quest_adds_ride_earnings(monkeypatch):
    earnings_quest = {**RIDE_COUNT_QUEST, "id": "quest_e", "type": "earnings_target", "target_value": 100.0}
    progress = _progress(quest_id="quest_e", current_value=40)
    captured = {}

    async def _update_one(table, filters, update, **kwargs):
        captured["update"] = update

    mock_db = _mock_db(
        get_rows=AsyncMock(return_value=[progress]),
        find_one=AsyncMock(return_value=earnings_quest),
        update_one=_update_one,
    )
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)

    await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1", "driver_earnings": 15})

    assert captured["update"]["$set"]["current_value"] == 55
    assert "status" not in captured["update"]["$set"]  # short of the 100 target


@pytest.mark.anyio
async def test_earnings_target_missing_earnings_defaults_to_zero(monkeypatch):
    earnings_quest = {**RIDE_COUNT_QUEST, "id": "quest_e", "type": "earnings_target", "target_value": 100.0}
    progress = _progress(quest_id="quest_e", current_value=40)
    captured = {}

    async def _update_one(table, filters, update, **kwargs):
        captured["update"] = update

    mock_db = _mock_db(
        get_rows=AsyncMock(return_value=[progress]),
        find_one=AsyncMock(return_value=earnings_quest),
        update_one=_update_one,
    )
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)

    await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})  # no driver_earnings key

    assert captured["update"]["$set"]["current_value"] == 40


# ── per-progress exception guard ─────────────────────────────────────────


@pytest.mark.anyio
async def test_one_bad_progress_row_does_not_abort_the_batch(monkeypatch, caplog):
    """A quest row missing the required "type"/"target_value" keys raises a
    KeyError inside the loop body; the per-progress try/except must log it
    and continue to the next row rather than losing the whole batch."""
    import logging

    broken_quest = {"id": "quest_broken", "is_active": True, "end_date": _FUTURE}  # no "type"/"target_value"
    good_progress = _progress(id="progress_2", quest_id="quest_1", current_value=9)
    bad_progress = _progress(id="progress_1", quest_id="quest_broken")

    calls = {"n": 0}

    async def _find_one(table, filters):
        calls["n"] += 1
        qid = filters.get("id")
        if qid == "quest_broken":
            return broken_quest
        return RIDE_COUNT_QUEST

    captured = {}

    async def _update_one(table, filters, update, **kwargs):
        captured["update"] = update

    mock_db = _mock_db(
        get_rows=AsyncMock(return_value=[bad_progress, good_progress]),
        find_one=_find_one,
        update_one=_update_one,
    )
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)

    with caplog.at_level(logging.ERROR):
        await update_quest_progress_on_ride_complete("driver_123", {"id": "ride_1"})

    assert "Failed to update quest quest_broken" in caplog.text
    # The second (good) row still got processed despite the first raising.
    assert captured["update"]["$set"]["current_value"] == 10
    assert captured["update"]["$set"]["status"] == "completed"


# ── _ride_local_completion_hour branches ─────────────────────────────────


@pytest.mark.anyio
async def test_no_completion_timestamp_returns_none(monkeypatch):
    mock_db = _mock_db()
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)
    assert await _ride_local_completion_hour({"id": "ride_1"}) is None


@pytest.mark.anyio
async def test_unparseable_completion_timestamp_returns_none(monkeypatch):
    mock_db = _mock_db()
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)
    result = await _ride_local_completion_hour({"ride_completed_at": "not-a-timestamp"})
    assert result is None


@pytest.mark.anyio
async def test_naive_completion_timestamp_is_assumed_utc(monkeypatch):
    """A datetime object with no tzinfo (as opposed to an ISO string) must be
    treated as UTC rather than raising when astimezone() is called."""
    mock_db = _mock_db()
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)
    naive_dt = datetime(2026, 6, 21, 23, 30, 0)  # no tzinfo
    hour = await _ride_local_completion_hour({"ride_completed_at": naive_dt, "service_area_id": None})
    assert hour == 17  # 23:30 UTC -> 17:30 America/Regina (UTC-6, no DST)


@pytest.mark.anyio
async def test_service_area_timezone_lookup_success(monkeypatch):
    """A service area with its own timezone overrides the America/Regina
    default — Winnipeg (America/Winnipeg, UTC-5) is one hour ahead of Regina."""
    mock_db = _mock_db(find_one=AsyncMock(return_value={"id": "area_1", "timezone": "America/Winnipeg"}))
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)
    hour = await _ride_local_completion_hour(
        {"ride_completed_at": "2026-06-21T23:30:00+00:00", "service_area_id": "area_1"}
    )
    assert hour == 18  # 23:30 UTC -> 18:30 America/Winnipeg
    mock_db.find_one.assert_awaited_once_with("service_areas", {"id": "area_1"})


@pytest.mark.anyio
async def test_service_area_lookup_exception_falls_back_to_default_tz(monkeypatch, caplog):
    import logging

    mock_db = _mock_db(find_one=AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)
    with caplog.at_level(logging.WARNING):
        hour = await _ride_local_completion_hour(
            {"ride_completed_at": "2026-06-21T23:30:00+00:00", "service_area_id": "area_1"}
        )
    assert hour == 17  # falls back to America/Regina
    assert "could not resolve timezone" in caplog.text


@pytest.mark.anyio
async def test_service_area_with_no_timezone_field_uses_default(monkeypatch):
    """Area row found but has no "timezone" key -> stays on America/Regina."""
    mock_db = _mock_db(find_one=AsyncMock(return_value={"id": "area_1"}))
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)
    hour = await _ride_local_completion_hour(
        {"ride_completed_at": "2026-06-21T23:30:00+00:00", "service_area_id": "area_1"}
    )
    assert hour == 17


@pytest.mark.anyio
async def test_invalid_area_timezone_falls_back_to_default(monkeypatch):
    """A garbage timezone string from the DB must not blow up the whole ride-
    completion flow — ZoneInfo() raising is caught and Regina is used."""
    mock_db = _mock_db(find_one=AsyncMock(return_value={"id": "area_1", "timezone": "Not/A_Real_Zone"}))
    monkeypatch.setattr("utils.quest_tracker.db", mock_db)
    hour = await _ride_local_completion_hour(
        {"ride_completed_at": "2026-06-21T23:30:00+00:00", "service_area_id": "area_1"}
    )
    assert hour == 17
