"""Configurable on-demand search window (settings.ride_search_timeout_seconds).

Plan: .claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md, Phase 2.
Change log: docs/change-log/2026-09-25-configurable-ride-search-timeout.md.

Pins, for the three places that must agree on the window:
  * routes/rides/matching.py ride_search_timeout (in-process timer),
  * routes/rides/matching.py _dispatch_retry (no-driver retry cap),
  * utils/stuck_ride_sweeper.py _sweep (durable backstop),
that a configured 180 s is honoured, a missing/garbage/unreadable setting
falls back to today's 300 s, the clamp is 90..300, and scheduled rides keep
their fixed 300 s grace whatever the setting says.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.unit]

RIDE_ID = "ride-search-window-1"
RIDER_ID = "rider-search-window-1"


def _settings(value):
    return AsyncMock(return_value={"ride_search_timeout_seconds": value})


def _searching_ride(**extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "status": "searching",
        "driver_id": None,
        "payment_method": "card",
        "ride_requested_at": datetime.now(timezone.utc).isoformat(),
    }
    row.update(extra)
    return row


# ── The setting reader: matching and sweeper must agree ─────────────────────


@pytest.mark.parametrize(
    "stored, expected",
    [
        (180, 180),
        (300, 300),
        (90, 90),
        ("180", 180),
        (180.0, 180),
        (30, 90),  # below the CHECK range → clamped up
        (301, 300),  # above → clamped down (UNIQUE(ride_id, driver_id) vs the 300 s offer-skip key)
        (600, 300),
        (None, 300),  # present but null → default
        ("abc", 300),  # garbage → default
        (True, 300),  # a bool is not a timeout
        ([180], 300),
    ],
)
async def test_matching_and_sweeper_read_the_same_window(stored, expected):
    from backend.routes.rides import matching as m
    from backend.utils import stuck_ride_sweeper as sw

    with patch("backend.routes.rides._deps.get_app_settings", _settings(stored)):
        assert await m._ride_search_timeout_seconds() == expected
    with patch.object(sw, "get_app_settings", _settings(stored)):
        assert await sw._search_timeout_seconds() == expected


async def test_missing_setting_defaults_to_300_without_error_log(caplog):
    from backend.routes.rides import matching as m
    from backend.utils import stuck_ride_sweeper as sw

    fake_logger = MagicMock()
    with (
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={})),
        patch.object(m, "logger", fake_logger),
    ):
        assert await m._ride_search_timeout_seconds() == 300
    fake_logger.error.assert_not_called()
    fake_logger.opt.assert_not_called()

    with caplog.at_level(logging.ERROR), patch.object(sw, "get_app_settings", AsyncMock(return_value={})):
        assert await sw._search_timeout_seconds() == 300
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_settings_read_error_falls_back_to_300_and_logs_error(caplog):
    from backend.routes.rides import matching as m
    from backend.utils import stuck_ride_sweeper as sw

    fake_logger = MagicMock()
    with (
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(side_effect=RuntimeError("settings db down"))),
        patch.object(m, "logger", fake_logger),
    ):
        assert await m._ride_search_timeout_seconds() == 300
    # loguru: traceback via opt(exception=True), never exc_info=.
    fake_logger.opt.assert_called_once_with(exception=True)
    fake_logger.opt.return_value.error.assert_called_once()

    with (
        caplog.at_level(logging.ERROR),
        patch.object(sw, "get_app_settings", AsyncMock(side_effect=RuntimeError("settings db down"))),
    ):
        assert await sw._search_timeout_seconds() == 300
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors and errors[0].exc_info is not None


async def test_garbage_setting_logs_error():
    from backend.routes.rides import matching as m

    fake_logger = MagicMock()
    with (
        patch("backend.routes.rides._deps.get_app_settings", _settings("abc")),
        patch.object(m, "logger", fake_logger),
    ):
        assert await m._ride_search_timeout_seconds() == 300
    fake_logger.error.assert_called_once()


# ── In-process timer (ride_search_timeout) ──────────────────────────────────


def _cancel_patches(ride, sleep_mock, claim_calls):
    async def _capture_claim(table, filters, patch_, retry_policy="read"):
        claim_calls.append((table, filters, patch_))
        return {**ride, **patch_}

    return (
        patch("backend.routes.rides._deps.asyncio.sleep", sleep_mock),
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock(side_effect=_capture_claim)),
        patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
        patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
    )


async def test_configured_180s_cancels_on_demand_ride_at_180s():
    from backend.routes import rides as rides_mod

    sleep = AsyncMock()
    claims: list = []
    p = _cancel_patches(_searching_ride(), sleep, claims)
    with p[0], p[1], p[2], p[3], p[4], p[5], patch("backend.routes.rides._deps.get_app_settings", _settings(180)):
        await rides_mod.ride_search_timeout(RIDE_ID, timeout_seconds=None)

    assert sleep.await_args_list[0].args[0] == 180
    assert claims, "the no-drivers cancel claim was not issued"
    assert claims[0][1] == {"id": RIDE_ID, "status": "searching"}
    assert claims[0][2]["status"] == "cancelled"


@pytest.mark.parametrize("settings_mock", [AsyncMock(side_effect=RuntimeError("down")), _settings("garbage")])
async def test_unreadable_setting_keeps_300s_timer(settings_mock):
    from backend.routes import rides as rides_mod

    sleep = AsyncMock()
    claims: list = []
    p = _cancel_patches(_searching_ride(), sleep, claims)
    with p[0], p[1], p[2], p[3], p[4], p[5], patch("backend.routes.rides._deps.get_app_settings", settings_mock):
        await rides_mod.ride_search_timeout(RIDE_ID, timeout_seconds=None)

    assert sleep.await_args_list[0].args[0] == 300
    assert claims, "a settings failure must not stop the cancel"


async def test_default_call_ignores_setting_scheduled_path():
    """utils/scheduled_rides.py calls ride_search_timeout(ride_id) with no
    window. That must stay a fixed 300 s and never read the setting."""
    from backend.routes import rides as rides_mod

    sleep = AsyncMock()
    claims: list = []
    settings = _settings(180)
    p = _cancel_patches(_searching_ride(), sleep, claims)
    with p[0], p[1], p[2], p[3], p[4], p[5], patch("backend.routes.rides._deps.get_app_settings", settings):
        await rides_mod.ride_search_timeout(RIDE_ID)

    assert sleep.await_args_list[0].args[0] == 300
    settings.assert_not_awaited()


async def test_scheduled_ride_keeps_300s_grace_even_when_window_is_configured():
    """If a scheduled ride ever reaches the timer with timeout_seconds=None,
    its deadline is still pickup + 300 s, not pickup + the setting."""
    from backend.routes.rides import matching as m

    past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    scheduled = _searching_ride(is_scheduled=True, scheduled_time=past, ride_requested_at=past)
    sleep = AsyncMock()
    with (
        patch("backend.routes.rides._deps.asyncio.sleep", sleep),
        patch(
            "backend.routes.rides._deps.db_supabase.get_ride",
            AsyncMock(side_effect=[scheduled, {**scheduled, "status": "driver_accepted"}]),
        ),
        patch("backend.routes.rides._deps.db_supabase.update_one", AsyncMock()) as claim,
        patch("backend.routes.rides._deps.get_app_settings", _settings(180)),
    ):
        await m.ride_search_timeout(RIDE_ID, timeout_seconds=None)

    assert sleep.await_count == 2
    # deadline = pickup (60 s ago) + 300 s → ~240 s left. With the 180 s
    # setting as grace it would be ~120 s.
    assert 200 < sleep.await_args_list[1].args[0] <= 240
    claim.assert_not_awaited()


# ── No-driver retry cap (_dispatch_retry) ───────────────────────────────────


def test_retry_cap_is_derived_from_window():
    from backend.routes.rides import matching as m

    assert m._max_dispatch_attempts(300) == m._MAX_DISPATCH_ATTEMPTS == 30
    assert m._max_dispatch_attempts(180) == 18
    assert m._max_dispatch_attempts(95) == 10
    assert m._max_dispatch_attempts(90) == 9


@pytest.mark.parametrize(
    "settings_mock, last_allowed",
    [
        (_settings(180), 18),
        (_settings(300), 30),
        (AsyncMock(return_value={}), 30),
        (AsyncMock(side_effect=RuntimeError("down")), 30),
    ],
)
async def test_dispatch_retry_cap_follows_setting(settings_mock, last_allowed):
    from backend.routes.rides import matching as m

    for attempt, should_dispatch in ((last_allowed, True), (last_allowed + 1, False)):
        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_searching_ride())),
            patch("backend.routes.rides._deps.get_app_settings", settings_mock),
            patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()) as match,
        ):
            await m._dispatch_retry(RIDE_ID, delay=0, attempt=attempt)
        assert match.await_count == int(should_dispatch), f"attempt {attempt}"


async def test_scheduled_ride_retry_is_not_capped_by_setting():
    from backend.routes.rides import matching as m

    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    scheduled = _searching_ride(is_scheduled=True, scheduled_time=future)
    settings = _settings(90)
    with (
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=scheduled)),
        patch("backend.routes.rides._deps.get_app_settings", settings),
        patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()) as match,
    ):
        await m._dispatch_retry(RIDE_ID, delay=0, attempt=40)
    match.assert_awaited_once()
    settings.assert_not_awaited()


# ── Durable backstop (stuck_ride_sweeper) ───────────────────────────────────

_CLAUSE = re.compile(
    r"^and\(scheduled_time\.is\.null,ride_requested_at\.lt\.(?P<od>[^)]+)\),"
    r"and\(scheduled_time\.lt\.(?P<s1>[^,]+),ride_requested_at\.lt\.(?P<s2>[^)]+)\)$"
)


async def _sweep_clause(monkeypatch, settings_mock):
    from backend.utils import stuck_ride_sweeper as sw

    client = MagicMock(name="supabase_client")
    monkeypatch.setattr(sw, "supabase", client)
    monkeypatch.setattr(sw, "get_app_settings", settings_mock)

    async def _run_sync(fn, **_kw):
        return fn()

    monkeypatch.setattr(sw.db_supabase, "run_sync", _run_sync)
    monkeypatch.setattr(sw.db_supabase, "_rows_from_res", lambda _res: [])

    before = datetime.now(timezone.utc)
    await sw._sweep()
    after = datetime.now(timezone.utc)

    status_filter = client.table.return_value.update.return_value.eq
    status_filter.assert_called_once_with("status", "searching")
    clause = status_filter.return_value.or_.call_args.args[0]
    match = _CLAUSE.match(clause)
    assert match, clause
    parsed = {k: datetime.fromisoformat(v) for k, v in match.groupdict().items()}
    return before, after, parsed


async def test_sweeper_on_demand_cutoff_honours_180s_and_scheduled_stays_5min(monkeypatch):
    before, after, c = await _sweep_clause(monkeypatch, _settings(180))

    assert before - timedelta(seconds=180) <= c["od"] <= after - timedelta(seconds=180)
    assert c["s1"] == c["s2"]
    assert before - timedelta(minutes=5) <= c["s1"] <= after - timedelta(minutes=5)


@pytest.mark.parametrize(
    "settings_mock", [AsyncMock(return_value={}), AsyncMock(side_effect=RuntimeError("down")), _settings("abc")]
)
async def test_sweeper_falls_back_to_300s(monkeypatch, settings_mock):
    before, after, c = await _sweep_clause(monkeypatch, settings_mock)

    assert before - timedelta(seconds=300) <= c["od"] <= after - timedelta(seconds=300)
    # At the default both cutoffs are the same instant: identical to the old filter.
    assert c["od"] == c["s1"] == c["s2"]


# ── Booking spawn site ──────────────────────────────────────────────────────


async def test_booking_spawns_configured_window_for_on_demand_ride():
    from backend.tests.test_create_ride_post_insert_branches import _run_happy_path

    _result, _sb, _mgr, mock_timeout = await _run_happy_path()

    mock_timeout.assert_called_once()
    assert mock_timeout.call_args.kwargs == {"timeout_seconds": None}


async def test_booking_keeps_default_window_for_scheduled_ride_dispatched_now():
    from backend.tests.test_create_ride_post_insert_branches import _inserted_ride, _run_happy_path

    def _scheduled(mock_supabase, _mgr):
        mock_supabase.get_ride = AsyncMock(
            return_value={**_inserted_ride(), "status": "searching", "is_scheduled": True}
        )

    _result, _sb, _mgr, mock_timeout = await _run_happy_path(mock_overrides=_scheduled)

    mock_timeout.assert_called_once()
    assert mock_timeout.call_args.kwargs == {}
