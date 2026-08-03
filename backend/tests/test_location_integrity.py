"""Tests for backend/utils/location_integrity.py — server-side GPS spoofing
detection (A1c Sub-tier C, batch locintegrity-routegap-routedist).

No test file previously existed for this module. `check_location_integrity`
is on the driver location-update hot path and is safety-adjacent (feeds
dispatch/billing trust decisions), so every heuristic branch (mock flag,
accuracy sanity, impossible speed, teleportation) and every Redis
soft-failure path is covered here.

Per CLAUDE.md's PIPEDA logging rules, real lat/lng literals are fine as test
fixtures (this is test code, not a log sink) — no debug logging is added
that would leak them into CI output.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import location_integrity as li

# ── mock flag ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_flag_is_rejected_before_any_other_check():
    trusted, reason = await li.check_location_integrity("driver_1", 50.45, -104.62, mocked=True)
    assert trusted is False
    assert reason == "mock_location"


@pytest.mark.asyncio
async def test_mock_flag_false_does_not_short_circuit():
    with (
        patch.object(li, "redis_get", AsyncMock(return_value=None)),
        patch.object(li, "redis_set", AsyncMock()),
    ):
        trusted, reason = await li.check_location_integrity("driver_1", 50.45, -104.62, mocked=False)
    assert trusted is True
    assert reason is None


# ── accuracy sanity ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_accuracy_is_rejected():
    trusted, reason = await li.check_location_integrity("driver_1", 50.45, -104.62, accuracy=0)
    assert trusted is False
    assert reason == "zero_accuracy"


@pytest.mark.asyncio
async def test_accuracy_over_the_max_is_rejected():
    trusted, reason = await li.check_location_integrity("driver_1", 50.45, -104.62, accuracy=501)
    assert trusted is False
    assert reason == "low_accuracy"


@pytest.mark.asyncio
async def test_accuracy_within_bounds_passes_the_accuracy_check():
    with (
        patch.object(li, "redis_get", AsyncMock(return_value=None)),
        patch.object(li, "redis_set", AsyncMock()),
    ):
        trusted, reason = await li.check_location_integrity("driver_1", 50.45, -104.62, accuracy=10)
    assert trusted is True
    assert reason is None


# ── impossible speed ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_speed_over_the_max_is_rejected():
    # MAX_SPEED_KMH is 300 km/h -> ~83.33 m/s.
    trusted, reason = await li.check_location_integrity("driver_1", 50.45, -104.62, speed=100.0)
    assert trusted is False
    assert reason == "impossible_speed"


@pytest.mark.asyncio
async def test_speed_at_or_under_the_max_passes():
    with (
        patch.object(li, "redis_get", AsyncMock(return_value=None)),
        patch.object(li, "redis_set", AsyncMock()),
    ):
        trusted, reason = await li.check_location_integrity("driver_1", 50.45, -104.62, speed=20.0)
    assert trusted is True
    assert reason is None


# ── teleportation ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_teleport_detected_for_a_large_jump_in_a_short_window():
    # Previous point stored 5s ago, ~100km away (well over 10km/10s threshold).
    now = 1_800_000_000.0
    prev_raw = f"50.00,-104.00,{now - 5}"
    with (
        patch.object(li, "redis_get", AsyncMock(return_value=prev_raw)),
        patch.object(li, "redis_set", AsyncMock()),
        patch.object(li.time, "time", lambda: now),
    ):
        trusted, reason = await li.check_location_integrity("driver_1", 51.00, -104.00, accuracy=10)
    assert trusted is False
    assert reason == "teleport"


@pytest.mark.asyncio
async def test_no_teleport_for_a_small_jump_in_the_same_window():
    now = 1_800_000_000.0
    prev_raw = f"50.4500,-104.6200,{now - 5}"
    set_mock = AsyncMock()
    with (
        patch.object(li, "redis_get", AsyncMock(return_value=prev_raw)),
        patch.object(li, "redis_set", set_mock),
        patch.object(li.time, "time", lambda: now),
    ):
        trusted, reason = await li.check_location_integrity("driver_1", 50.4501, -104.6201, accuracy=10)
    assert trusted is True
    assert reason is None
    # The current point is persisted as the new "last known" point.
    set_mock.assert_awaited_once()
    key, value = set_mock.await_args[0]
    assert key == "loc:last:driver_1"
    assert value.startswith("50.4501,-104.6201,")


@pytest.mark.asyncio
async def test_no_teleport_check_once_the_window_has_elapsed():
    # Same large jump, but the previous point is stale (>= TELEPORT_MIN_SECONDS
    # old) -- outside the window, so the jump is not flagged as teleportation.
    now = 1_800_000_000.0
    prev_raw = f"50.00,-104.00,{now - li.TELEPORT_MIN_SECONDS}"
    with (
        patch.object(li, "redis_get", AsyncMock(return_value=prev_raw)),
        patch.object(li, "redis_set", AsyncMock()),
        patch.object(li.time, "time", lambda: now),
    ):
        trusted, reason = await li.check_location_integrity("driver_1", 51.00, -104.00, accuracy=10)
    assert trusted is True
    assert reason is None


@pytest.mark.asyncio
async def test_malformed_cached_point_is_ignored_without_raising():
    with (
        patch.object(li, "redis_get", AsyncMock(return_value="not,enough")),
        patch.object(li, "redis_set", AsyncMock()),
    ):
        trusted, reason = await li.check_location_integrity("driver_1", 50.45, -104.62, accuracy=10)
    assert trusted is True
    assert reason is None


@pytest.mark.asyncio
async def test_empty_cached_point_is_treated_as_no_history():
    with (
        patch.object(li, "redis_get", AsyncMock(return_value="")),
        patch.object(li, "redis_set", AsyncMock()),
    ):
        trusted, reason = await li.check_location_integrity("driver_1", 50.45, -104.62, accuracy=10)
    assert trusted is True
    assert reason is None


# ── Redis soft failures never break the check ─────────────────────────────


@pytest.mark.asyncio
async def test_survives_a_redis_get_failure_and_still_stores_the_new_point():
    set_mock = AsyncMock()
    with (
        patch.object(li, "redis_get", AsyncMock(side_effect=RuntimeError("redis down"))),
        patch.object(li, "redis_set", set_mock),
    ):
        trusted, reason = await li.check_location_integrity("driver_1", 50.45, -104.62, accuracy=10)
    assert trusted is True
    assert reason is None
    set_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_survives_a_redis_set_failure():
    with (
        patch.object(li, "redis_get", AsyncMock(return_value=None)),
        patch.object(li, "redis_set", AsyncMock(side_effect=RuntimeError("redis down"))),
    ):
        # Must not raise even though persisting the new point fails.
        trusted, reason = await li.check_location_integrity("driver_1", 50.45, -104.62, accuracy=10)
    assert trusted is True
    assert reason is None


# ── haversine helper ────────────────────────────────────────────────────────


def test_haversine_km_zero_distance_for_identical_points():
    assert li._haversine_km(50.45, -104.62, 50.45, -104.62) == 0.0


def test_haversine_km_matches_a_known_approximate_distance():
    # Regina (50.45, -104.62) to Saskatoon (52.13, -106.67) is ~235km great-circle.
    km = li._haversine_km(50.45, -104.62, 52.13, -106.67)
    assert 225 <= km <= 245
