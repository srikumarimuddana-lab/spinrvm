"""backend/utils/notification_throttle.py — quiet hours + daily cap.

Pure logic + Redis, no Supabase involved, so no mock_supabase_client fixture
needed. Mirrors the mocking style of TestMcpDailyCap in test_ai_mcp.py
(patch.object on the module's imported redis_incr/redis_expire names).
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

import backend.utils.notification_throttle as throttle


class TestIsWithinQuietHours:
    def test_inside_non_wrapping_window(self):
        # 13:00-17:00, checking 14:30 → inside.
        now = datetime(2026, 1, 1, 14, 30)
        assert throttle.is_within_quiet_hours("13:00", "17:00", now=now) is True

    def test_outside_non_wrapping_window(self):
        now = datetime(2026, 1, 1, 18, 0)
        assert throttle.is_within_quiet_hours("13:00", "17:00", now=now) is False

    def test_inside_wrapping_window_late_night(self):
        # 22:00-07:00, checking 23:30 → inside (before midnight).
        now = datetime(2026, 1, 1, 23, 30)
        assert throttle.is_within_quiet_hours("22:00", "07:00", now=now) is True

    def test_inside_wrapping_window_early_morning(self):
        # 22:00-07:00, checking 05:00 → inside (after midnight).
        now = datetime(2026, 1, 1, 5, 0)
        assert throttle.is_within_quiet_hours("22:00", "07:00", now=now) is True

    def test_outside_wrapping_window(self):
        # 22:00-07:00, checking 12:00 → outside.
        now = datetime(2026, 1, 1, 12, 0)
        assert throttle.is_within_quiet_hours("22:00", "07:00", now=now) is False

    def test_start_boundary_is_inclusive(self):
        now = datetime(2026, 1, 1, 22, 0)
        assert throttle.is_within_quiet_hours("22:00", "07:00", now=now) is True

    def test_end_boundary_is_exclusive(self):
        now = datetime(2026, 1, 1, 7, 0)
        assert throttle.is_within_quiet_hours("22:00", "07:00", now=now) is False

    def test_malformed_start_fails_open(self):
        now = datetime(2026, 1, 1, 23, 0)
        assert throttle.is_within_quiet_hours("not-a-time", "07:00", now=now) is False

    def test_malformed_end_fails_open(self):
        now = datetime(2026, 1, 1, 23, 0)
        assert throttle.is_within_quiet_hours("22:00", "", now=now) is False

    def test_none_input_fails_open(self):
        now = datetime(2026, 1, 1, 23, 0)
        assert throttle.is_within_quiet_hours(None, "07:00", now=now) is False  # type: ignore[arg-type]

    def test_uses_real_clock_when_now_not_supplied(self):
        # Just confirm it doesn't raise when relying on the real tz-aware clock.
        result = throttle.is_within_quiet_hours("00:00", "23:59")
        assert isinstance(result, bool)


class TestIsOverDailyCap:
    @pytest.mark.anyio
    async def test_cap_zero_means_unlimited_no_redis_call(self):
        with patch.object(throttle, "redis_incr", AsyncMock()) as mock_incr:
            assert await throttle.is_over_daily_cap("u1", 0) is False
            mock_incr.assert_not_called()

    @pytest.mark.anyio
    async def test_blocks_after_limit(self):
        counts: dict[str, int] = {}

        async def fake_incr(key):
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        with (
            patch.object(throttle, "redis_incr", fake_incr),
            patch.object(throttle, "redis_expire", AsyncMock()),
        ):
            assert await throttle.is_over_daily_cap("u1", 2) is False
            assert await throttle.is_over_daily_cap("u1", 2) is False
            assert await throttle.is_over_daily_cap("u1", 2) is True
            # Independent per user.
            assert await throttle.is_over_daily_cap("u2", 2) is False

    @pytest.mark.anyio
    async def test_fails_open_on_redis_error(self):
        with patch.object(throttle, "redis_incr", AsyncMock(side_effect=RuntimeError("redis down"))):
            assert await throttle.is_over_daily_cap("u1", 2) is False

    @pytest.mark.anyio
    async def test_fails_open_on_expire_error(self):
        with (
            patch.object(throttle, "redis_incr", AsyncMock(return_value=1)),
            patch.object(throttle, "redis_expire", AsyncMock(side_effect=RuntimeError("redis down"))),
        ):
            assert await throttle.is_over_daily_cap("u1", 2) is False


class TestShouldThrottle:
    @pytest.mark.anyio
    async def test_quiet_hours_short_circuits_before_cap_check(self):
        with patch.object(throttle, "is_within_quiet_hours", return_value=True):
            with patch.object(throttle, "redis_incr", AsyncMock()) as mock_incr:
                result = await throttle.should_throttle("u1", "22:00", "07:00", 5)
        assert result is True
        mock_incr.assert_not_called()

    @pytest.mark.anyio
    async def test_falls_through_to_cap_check_outside_quiet_hours(self):
        with patch.object(throttle, "is_within_quiet_hours", return_value=False):
            with patch.object(throttle, "is_over_daily_cap", AsyncMock(return_value=True)) as mock_cap:
                result = await throttle.should_throttle("u1", "22:00", "07:00", 5)
        assert result is True
        mock_cap.assert_awaited_once_with("u1", 5)

    @pytest.mark.anyio
    async def test_neither_condition_means_not_throttled(self):
        with patch.object(throttle, "is_within_quiet_hours", return_value=False):
            with patch.object(throttle, "is_over_daily_cap", AsyncMock(return_value=False)):
                result = await throttle.should_throttle("u1", "22:00", "07:00", 5)
        assert result is False
