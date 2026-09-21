"""`get_last_known_app_settings()` — the stale-tolerant settings accessor.

Added for the G5 booking kill switch (2026-09-20 review, finding E2), which
must still make a correct decision when `get_app_settings()` *raises*. Its
whole value rests on one property of the loader that is easy to break by
accident: **a failed read must never overwrite the cached value.**
`get_app_settings` writes `_settings_cache` only on the success path, after the
`await`, so an exception propagates with the previous value intact. If someone
later "tidies" that into a `finally:` or seeds the cache before the read, the
kill switch silently reverts to its old fail-open behaviour with no test
failing anywhere near it. These tests pin the property directly.

Also pins the one deliberate difference from the pre-existing
`get_cached_app_settings()`: that one returns None once the 60 s TTL is up,
this one does not. The TTL decides *when to refresh*; it is not a claim that a
minute-old value is worthless — and when the refresh itself is failing, a stale
flag beats a hardcoded default.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from backend import settings_loader

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _isolate_cache():
    """`_settings_cache` is a module global shared with every other test in the
    session. Save and restore it so these tests neither read someone else's
    state nor leak their own."""
    original = settings_loader._settings_cache
    settings_loader._settings_cache = None
    try:
        yield
    finally:
        settings_loader._settings_cache = original


class TestNeverLoaded:
    def test_returns_none_before_any_successful_load(self):
        """Cold start. The caller must be able to tell 'no opinion' apart from
        a real value, which is why this returns None rather than {} — the
        booking guard branches on exactly this to pick its cold-start posture.
        """
        assert settings_loader.get_last_known_app_settings() is None


class TestIgnoresTTL:
    def test_returns_a_value_older_than_the_ttl(self):
        stale_at = time.monotonic() - (settings_loader._SETTINGS_TTL * 10)
        settings_loader._settings_cache = (stale_at, {"new_ride_requests_enabled": False})

        assert settings_loader.get_last_known_app_settings() == {"new_ride_requests_enabled": False}

    def test_differs_from_get_cached_app_settings_past_the_ttl(self):
        """The two accessors must not be collapsed into one. Past the TTL the
        sync-render accessor goes quiet and this one keeps answering."""
        stale_at = time.monotonic() - (settings_loader._SETTINGS_TTL * 10)
        settings_loader._settings_cache = (stale_at, {"new_ride_requests_enabled": False})

        assert settings_loader.get_cached_app_settings() is None
        assert settings_loader.get_last_known_app_settings() is not None


class TestFailedReadDoesNotClobber:
    async def test_last_known_survives_a_raising_read(self):
        """The property the kill switch depends on.

        An operator pauses bookings (a successful read caches False), then the
        DB degrades. The next read raises — and the cached False must still be
        there afterwards, or the switch silently un-pauses.
        """
        with patch.object(
            settings_loader.db_supabase,
            "get_rows",
            AsyncMock(return_value=[{"id": "app_settings", "new_ride_requests_enabled": False}]),
        ):
            loaded = await settings_loader.get_app_settings()
        assert loaded["new_ride_requests_enabled"] is False

        # Expire the cache so the next call actually attempts a read, then make
        # that read fail.
        cached_at, cached_val = settings_loader._settings_cache
        settings_loader._settings_cache = (cached_at - (settings_loader._SETTINGS_TTL + 1), cached_val)

        with patch.object(
            settings_loader.db_supabase,
            "get_rows",
            AsyncMock(side_effect=RuntimeError("settings db down")),
        ):
            with pytest.raises(RuntimeError):
                await settings_loader.get_app_settings()

        last_known = settings_loader.get_last_known_app_settings()
        assert last_known is not None
        assert last_known["new_ride_requests_enabled"] is False

    async def test_a_successful_read_does_advance_it(self):
        """The other direction: once the read recovers and says True, the stale
        False must not linger."""
        with patch.object(
            settings_loader.db_supabase,
            "get_rows",
            AsyncMock(return_value=[{"id": "app_settings", "new_ride_requests_enabled": False}]),
        ):
            await settings_loader.get_app_settings()
        assert settings_loader.get_last_known_app_settings()["new_ride_requests_enabled"] is False

        cached_at, cached_val = settings_loader._settings_cache
        settings_loader._settings_cache = (cached_at - (settings_loader._SETTINGS_TTL + 1), cached_val)

        with patch.object(
            settings_loader.db_supabase,
            "get_rows",
            AsyncMock(return_value=[{"id": "app_settings", "new_ride_requests_enabled": True}]),
        ):
            await settings_loader.get_app_settings()

        assert settings_loader.get_last_known_app_settings()["new_ride_requests_enabled"] is True
