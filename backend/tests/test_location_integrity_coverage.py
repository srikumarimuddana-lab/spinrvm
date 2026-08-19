"""Coverage for utils/location_integrity.py (GPS spoofing / teleport heuristic
checks applied to every driver location update).

Existing coverage exercised this module only indirectly (as a monkeypatched
dependency of `routes/drivers/location.py`'s batch endpoint), leaving the
teleport-detection block, the Redis error-swallowing branches, the
accuracy/speed thresholds, and the dual-import fallback (CLAUDE.md's
"Dual import pattern") unexercised. This file adds direct, isolated coverage
of `check_location_integrity` for every branch:

- `mocked is True` short-circuit (and its PIPEDA-relevant log line)
- accuracy == 0 / accuracy > MAX_ACCURACY_METERS
- speed > MAX_SPEED_KMH (converted to m/s)
- the Redis-backed teleport check: hit/no-hit, malformed cache value,
  elapsed-too-large skip, distance-within-threshold skip
- `redis_get` raising (swallowed, falls through to storing the new point)
- `redis_set` raising (swallowed, point still reported trusted)
- the module-level `try/except ImportError` fallback import branch

Test-only change — no application code modified.

Fixed (2026-08-03, application code change, explicitly approved by the
user via AskUserQuestion before applying — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 10):
`check_location_integrity`'s mock-flag check previously used `if mocked
is True:` — a strict identity check that let a client sending `1` or
`"true"` instead of a literal JSON boolean bypass GPS-spoofing detection
entirely. Now uses a plain truthy check (`if mocked:`), so any of
`1`/`"true"`/`True` are all caught. See
`test_mocked_flag_int_one_is_caught`/`test_mocked_flag_string_true_is_caught`
below.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from backend.utils import location_integrity as li  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────
# Mock-flag branch (lines 53-55)
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_mocked_true_is_rejected_and_logs_no_raw_coordinates(caplog):
    """mocked=True short-circuits everything else and returns untrusted.

    PIPEDA check: the warning log must not contain the raw lat/lng values
    passed in (CLAUDE.md: raw GPS coordinates must never appear in logs).
    """
    caplog.set_level("WARNING", logger="backend.utils.location_integrity")
    trusted, reason = await li.check_location_integrity("driver-1", 50.4452, -104.6189, mocked=True)
    assert (trusted, reason) == (False, "mock_location")
    warning_messages = " ".join(r.getMessage() for r in caplog.records)
    assert "50.4452" not in warning_messages
    assert "-104.6189" not in warning_messages
    assert "driver-1" in warning_messages


@pytest.mark.anyio
async def test_mocked_false_does_not_short_circuit(monkeypatch):
    monkeypatch.setattr(li, "redis_get", AsyncMock(return_value=None))
    monkeypatch.setattr(li, "redis_set", AsyncMock())
    trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0, mocked=False)
    assert (trusted, reason) == (True, None)


@pytest.mark.anyio
async def test_mocked_none_does_not_short_circuit(monkeypatch):
    monkeypatch.setattr(li, "redis_get", AsyncMock(return_value=None))
    monkeypatch.setattr(li, "redis_set", AsyncMock())
    trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0, mocked=None)
    assert (trusted, reason) == (True, None)


@pytest.mark.anyio
async def test_mocked_flag_int_one_is_caught(monkeypatch):
    """Fixed: `mocked=1` (int) is now caught by the truthy check and the
    spoofed point is correctly rejected."""
    monkeypatch.setattr(li, "redis_get", AsyncMock(return_value=None))
    monkeypatch.setattr(li, "redis_set", AsyncMock())
    trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0, mocked=1)
    assert (trusted, reason) == (False, "mock_location")


@pytest.mark.anyio
async def test_mocked_flag_string_true_is_caught(monkeypatch):
    """Fixed: a string `"true"` is now caught by the truthy check too."""
    monkeypatch.setattr(li, "redis_get", AsyncMock(return_value=None))
    monkeypatch.setattr(li, "redis_set", AsyncMock())
    trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0, mocked="true")
    assert (trusted, reason) == (False, "mock_location")


# ─────────────────────────────────────────────────────────────────────────
# Accuracy branch (lines 57-61)
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_zero_accuracy_is_rejected():
    trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0, accuracy=0)
    assert (trusted, reason) == (False, "zero_accuracy")


@pytest.mark.anyio
async def test_accuracy_above_max_is_rejected():
    trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0, accuracy=li.MAX_ACCURACY_METERS + 1)
    assert (trusted, reason) == (False, "low_accuracy")


@pytest.mark.anyio
async def test_accuracy_within_range_passes(monkeypatch):
    monkeypatch.setattr(li, "redis_get", AsyncMock(return_value=None))
    monkeypatch.setattr(li, "redis_set", AsyncMock())
    trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0, accuracy=25)
    assert (trusted, reason) == (True, None)


@pytest.mark.anyio
async def test_accuracy_none_skips_check(monkeypatch):
    monkeypatch.setattr(li, "redis_get", AsyncMock(return_value=None))
    monkeypatch.setattr(li, "redis_set", AsyncMock())
    trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0, accuracy=None)
    assert (trusted, reason) == (True, None)


# ─────────────────────────────────────────────────────────────────────────
# Speed branch (lines 63-64)
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_impossible_speed_is_rejected():
    # MAX_SPEED_KMH / 3.6 m/s is the threshold; comfortably exceed it.
    trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0, speed=(li.MAX_SPEED_KMH / 3.6) + 10)
    assert (trusted, reason) == (False, "impossible_speed")


@pytest.mark.anyio
async def test_speed_at_threshold_is_not_rejected(monkeypatch):
    """Strictly-greater-than comparison: speed exactly at the threshold
    passes (only speeds > threshold are rejected)."""
    monkeypatch.setattr(li, "redis_get", AsyncMock(return_value=None))
    monkeypatch.setattr(li, "redis_set", AsyncMock())
    trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0, speed=li.MAX_SPEED_KMH / 3.6)
    assert (trusted, reason) == (True, None)


@pytest.mark.anyio
async def test_speed_none_skips_check(monkeypatch):
    monkeypatch.setattr(li, "redis_get", AsyncMock(return_value=None))
    monkeypatch.setattr(li, "redis_set", AsyncMock())
    trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0, speed=None)
    assert (trusted, reason) == (True, None)


@pytest.mark.anyio
async def test_accuracy_checked_before_speed():
    """Both bad accuracy and bad speed supplied together -- accuracy is
    checked first, so that's the reason returned."""
    trusted, reason = await li.check_location_integrity(
        "driver-1",
        50.0,
        -104.0,
        accuracy=0,
        speed=(li.MAX_SPEED_KMH / 3.6) + 50,
    )
    assert (trusted, reason) == (False, "zero_accuracy")


# ─────────────────────────────────────────────────────────────────────────
# Teleportation branch (lines 66-92) -- the previously-uncovered core
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_teleport_detected_returns_false_and_does_not_overwrite_cache():
    """Regina -> Saskatoon (~230 km) inside 5s elapsed must trip the
    teleport check (lines 78-84) and must NOT proceed to overwrite the
    cached point (redis_set is never reached -- early return at line 84)."""
    now = 1_700_000_000.0
    prev_lat, prev_lng = 50.4452, -104.6189  # Regina
    new_lat, new_lng = 52.1332, -106.6700  # Saskatoon
    prev_raw = f"{prev_lat},{prev_lng},{now - 5}"

    mock_get = AsyncMock(return_value=prev_raw)
    mock_set = AsyncMock()
    with (
        patch.object(li, "redis_get", mock_get),
        patch.object(li, "redis_set", mock_set),
        patch.object(li.time, "time", return_value=now),
    ):
        trusted, reason = await li.check_location_integrity("driver-1", new_lat, new_lng)

    assert (trusted, reason) == (False, "teleport")
    mock_set.assert_not_called()


@pytest.mark.anyio
async def test_teleport_warning_log_omits_raw_coordinates(caplog):
    """PIPEDA check on the teleport warning log (lines 78-83): dist and
    elapsed are logged, but raw lat/lng of either point must not be."""
    caplog.set_level("WARNING", logger="backend.utils.location_integrity")
    now = 1_700_000_000.0
    prev_lat, prev_lng = 50.4452, -104.6189
    new_lat, new_lng = 52.1332, -106.6700
    prev_raw = f"{prev_lat},{prev_lng},{now - 5}"

    with (
        patch.object(li, "redis_get", AsyncMock(return_value=prev_raw)),
        patch.object(li, "redis_set", AsyncMock()),
        patch.object(li.time, "time", return_value=now),
    ):
        await li.check_location_integrity("driver-1", new_lat, new_lng)

    warning_messages = " ".join(r.getMessage() for r in caplog.records)
    assert "teleport detected" in warning_messages
    for coord in (str(prev_lat), str(prev_lng), str(new_lat), str(new_lng)):
        assert coord not in warning_messages


@pytest.mark.anyio
async def test_teleport_elapsed_too_large_is_not_flagged():
    """>= TELEPORT_MIN_SECONDS elapsed disables the teleport check even
    though the distance is large -- covers the `elapsed < ...` False arm."""
    now = 1_700_000_000.0
    prev_raw = f"50.4452,-104.6189,{now - li.TELEPORT_MIN_SECONDS - 1}"
    mock_set = AsyncMock()
    with (
        patch.object(li, "redis_get", AsyncMock(return_value=prev_raw)),
        patch.object(li, "redis_set", mock_set),
        patch.object(li.time, "time", return_value=now),
    ):
        trusted, reason = await li.check_location_integrity("driver-1", 52.1332, -106.6700)
    assert (trusted, reason) == (True, None)
    mock_set.assert_awaited_once()


@pytest.mark.anyio
async def test_teleport_distance_within_threshold_is_not_flagged():
    """Small movement (well under TELEPORT_THRESHOLD_KM) within the elapsed
    window must not be flagged -- covers the `dist > threshold` False arm."""
    now = 1_700_000_000.0
    prev_raw = f"50.4452,-104.6189,{now - 5}"
    with (
        patch.object(li, "redis_get", AsyncMock(return_value=prev_raw)),
        patch.object(li, "redis_set", AsyncMock()),
        patch.object(li.time, "time", return_value=now),
    ):
        trusted, reason = await li.check_location_integrity("driver-1", 50.4460, -104.6195)
    assert (trusted, reason) == (True, None)


@pytest.mark.anyio
async def test_teleport_elapsed_exactly_zero_is_not_flagged():
    """`elapsed` must be strictly > 0 (line 75's `0 < elapsed` guard) --
    a duplicate/same-timestamp update does not trip teleport detection even
    with a huge apparent distance (e.g. a clock/cache anomaly)."""
    now = 1_700_000_000.0
    prev_raw = f"50.4452,-104.6189,{now}"
    with (
        patch.object(li, "redis_get", AsyncMock(return_value=prev_raw)),
        patch.object(li, "redis_set", AsyncMock()),
        patch.object(li.time, "time", return_value=now),
    ):
        trusted, reason = await li.check_location_integrity("driver-1", 52.1332, -106.6700)
    assert (trusted, reason) == (True, None)


@pytest.mark.anyio
async def test_malformed_cache_value_wrong_part_count_is_ignored():
    """A cached value that doesn't split into exactly 3 comma parts skips
    the whole teleport block (line 72's `len(parts) == 3` guard) instead of
    raising."""
    with (
        patch.object(li, "redis_get", AsyncMock(return_value="50.4452,-104.6189")),
        patch.object(li, "redis_set", AsyncMock()) as mock_set,
    ):
        trusted, reason = await li.check_location_integrity("driver-1", 52.1332, -106.6700)
    assert (trusted, reason) == (True, None)
    mock_set.assert_awaited_once()


@pytest.mark.anyio
async def test_no_cached_previous_point_skips_teleport_check():
    """redis_get returning None/empty (no prior point cached) must skip
    straight to storing the new point and reporting trusted."""
    mock_set = AsyncMock()
    with patch.object(li, "redis_get", AsyncMock(return_value=None)), patch.object(li, "redis_set", mock_set):
        trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0)
    assert (trusted, reason) == (True, None)
    mock_set.assert_awaited_once()
    stored_key, stored_value = mock_set.call_args.args[0], mock_set.call_args.args[1]
    assert stored_key == "loc:last:driver-1"
    assert stored_value.startswith("50.0,-104.0,")


# ─────────────────────────────────────────────────────────────────────────
# Redis error-swallowing branches (lines 85-86, 91-92)
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_redis_get_exception_is_swallowed_and_still_stores():
    """A raising redis_get must not propagate -- caught, logged at debug
    level, and the function still proceeds to store + report trusted.

    Uses a direct `logger.debug` mock rather than `caplog` -- this repo's
    full test-suite logging setup (loguru interception via the FastAPI
    app's lifespan, triggered by other fixtures in the session) does not
    reliably deliver DEBUG-level stdlib `logging` records to `caplog`'s
    handler, even with `caplog.set_level("DEBUG", logger=...)` set; no
    other test file in this repo asserts on DEBUG-level caplog records for
    the same reason. Patching the logger call directly sidesteps that.
    """
    mock_set = AsyncMock()
    mock_debug = MagicMock()
    with (
        patch.object(li, "redis_get", AsyncMock(side_effect=RuntimeError("redis down"))),
        patch.object(li, "redis_set", mock_set),
        patch.object(li.logger, "debug", mock_debug),
    ):
        trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0)
    assert (trusted, reason) == (True, None)
    mock_set.assert_awaited_once()
    assert any("redis_get failed" in str(c.args[0]) for c in mock_debug.call_args_list)


@pytest.mark.anyio
async def test_redis_set_exception_is_swallowed():
    """A raising redis_set must not propagate or flip the trust result --
    caught and logged at debug level only. See the caplog note on the
    sibling `test_redis_get_exception_is_swallowed_and_still_stores` above
    for why this asserts via a direct `logger.debug` mock, not `caplog`."""
    mock_debug = MagicMock()
    with (
        patch.object(li, "redis_get", AsyncMock(return_value=None)),
        patch.object(li, "redis_set", AsyncMock(side_effect=RuntimeError("redis down"))),
        patch.object(li.logger, "debug", mock_debug),
    ):
        trusted, reason = await li.check_location_integrity("driver-1", 50.0, -104.0)
    assert (trusted, reason) == (True, None)
    assert any("redis_set failed" in str(c.args[0]) for c in mock_debug.call_args_list)


# ─────────────────────────────────────────────────────────────────────────
# Haversine helper sanity (used by the teleport tests above)
# ─────────────────────────────────────────────────────────────────────────


def test_haversine_km_known_distance_regina_saskatoon():
    """Sanity-checks the distance assumption the teleport tests rely on --
    Regina to Saskatoon is ~230km, comfortably over TELEPORT_THRESHOLD_KM."""
    dist = li._haversine_km(50.4452, -104.6189, 52.1332, -106.6700)
    assert 220 < dist < 245


def test_haversine_km_same_point_is_zero():
    assert li._haversine_km(50.0, -104.0, 50.0, -104.0) == pytest.approx(0.0, abs=1e-9)


# ─────────────────────────────────────────────────────────────────────────
# evaluate_gps_plausibility -- pure (no I/O) sibling used by the v2
# location-batch path (ranked blocker #7, 2026-08-19) so a batch of up to
# 500 points can be checked without one Redis round trip per point. Same
# thresholds/reasons as check_location_integrity above, just with the
# "previous point" passed in explicitly instead of read from Redis.
# ─────────────────────────────────────────────────────────────────────────


def test_evaluate_plausibility_mock_flag_short_circuits():
    trusted, reason = li.evaluate_gps_plausibility(50.0, -104.0, mocked=True)
    assert (trusted, reason) == (False, "mock_location")


def test_evaluate_plausibility_zero_accuracy_is_rejected():
    trusted, reason = li.evaluate_gps_plausibility(50.0, -104.0, accuracy=0)
    assert (trusted, reason) == (False, "zero_accuracy")


def test_evaluate_plausibility_accuracy_above_max_is_rejected():
    trusted, reason = li.evaluate_gps_plausibility(50.0, -104.0, accuracy=li.MAX_ACCURACY_METERS + 1)
    assert (trusted, reason) == (False, "low_accuracy")


def test_evaluate_plausibility_impossible_speed_is_rejected():
    trusted, reason = li.evaluate_gps_plausibility(50.0, -104.0, speed=(li.MAX_SPEED_KMH / 3.6) + 10)
    assert (trusted, reason) == (False, "impossible_speed")


def test_evaluate_plausibility_no_prev_point_skips_teleport_check():
    """No previous point supplied (e.g. first-ever point for a driver with
    no last-known DB position) -- nothing to compare against, so trusted."""
    trusted, reason = li.evaluate_gps_plausibility(52.1332, -106.6700)
    assert (trusted, reason) == (True, None)


def test_evaluate_plausibility_teleport_detected_with_explicit_prev():
    trusted, reason = li.evaluate_gps_plausibility(
        52.1332,
        -106.6700,
        prev_lat=50.4452,
        prev_lng=-104.6189,
        elapsed_seconds=5.0,
    )
    assert (trusted, reason) == (False, "teleport")


def test_evaluate_plausibility_small_movement_is_not_a_teleport():
    trusted, reason = li.evaluate_gps_plausibility(
        50.4460,
        -104.6195,
        prev_lat=50.4452,
        prev_lng=-104.6189,
        elapsed_seconds=5.0,
    )
    assert (trusted, reason) == (True, None)


def test_evaluate_plausibility_elapsed_outside_window_skips_teleport_check():
    """>= TELEPORT_MIN_SECONDS elapsed disables the check even for a huge
    jump -- mirrors check_location_integrity's Redis-backed teleport window."""
    trusted, reason = li.evaluate_gps_plausibility(
        52.1332,
        -106.6700,
        prev_lat=50.4452,
        prev_lng=-104.6189,
        elapsed_seconds=li.TELEPORT_MIN_SECONDS,
    )
    assert (trusted, reason) == (True, None)


def test_evaluate_plausibility_zero_elapsed_skips_teleport_check():
    """elapsed_seconds must be strictly > 0, same as check_location_integrity's
    `0 < elapsed` guard -- a duplicate/same-timestamp point isn't flagged."""
    trusted, reason = li.evaluate_gps_plausibility(
        52.1332,
        -106.6700,
        prev_lat=50.4452,
        prev_lng=-104.6189,
        elapsed_seconds=0.0,
    )
    assert (trusted, reason) == (True, None)


def test_evaluate_plausibility_never_touches_redis():
    """Pure function: no Redis calls, regardless of outcome -- this is the
    whole point of using it (vs check_location_integrity) inside a batch
    loop that must not do per-point I/O."""
    with (
        patch.object(li, "redis_get", AsyncMock(side_effect=AssertionError("must not call redis_get"))),
        patch.object(li, "redis_set", AsyncMock(side_effect=AssertionError("must not call redis_set"))),
    ):
        trusted, reason = li.evaluate_gps_plausibility(
            52.1332,
            -106.6700,
            prev_lat=50.4452,
            prev_lng=-104.6189,
            elapsed_seconds=5.0,
        )
    assert (trusted, reason) == (False, "teleport")


# ─────────────────────────────────────────────────────────────────────────
# Dual-import fallback branch (lines 23-24)
# ─────────────────────────────────────────────────────────────────────────


def test_relative_import_failure_falls_back_to_bare_utils_import():
    """Forces the package-relative `from .redis_client import ...` (line 22)
    to raise ImportError so the module falls back to the bare
    `from utils.redis_client import ...` (line 24) -- the "top-level import
    mode" half of CLAUDE.md's mandated dual-import pattern. Uses the same
    `sys.modules[name] = None` trick as
    test_presence_sweeper_coverage.py's fallback-import test."""
    mod_name = li.__name__  # "backend.utils.location_integrity"
    relative_target = "backend.utils.redis_client"

    try:
        with patch.dict(sys.modules, {relative_target: None}):
            reloaded = importlib.reload(sys.modules[mod_name])
            # Fallback branch bound the same names as the try branch -- the
            # module must still expose working redis_get/redis_set symbols.
            assert callable(reloaded.redis_get)
            assert callable(reloaded.redis_set)
    finally:
        # patch.dict has restored `backend.utils.redis_client` in
        # sys.modules by now; reload once more outside the patch so the
        # module (and this test file's `li` alias) goes back to using the
        # normal relative-import path and later tests aren't affected.
        importlib.reload(li)
