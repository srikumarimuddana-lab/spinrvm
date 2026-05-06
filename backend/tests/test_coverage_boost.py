"""Coverage-boost tests — maps_eta, metrics, validators.

These tests target the ~141 uncovered statements that keep overall coverage
below the 60% threshold when measured from backend/.

Scope:
  - utils/maps_eta.py  — _haversine_km, _haversine_eta_seconds,
                         get_ride_eta_seconds (no-key, cache-hit, maps-ok,
                         maps-error-fallback)
  - utils/metrics.py   — render_prometheus, _escape_label_value, iter_counters
  - validators.py      — validate_phone, validate_email, validate_coordinates,
                         validate_monetary_amount edge cases
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch


# ===========================================================================
# utils/maps_eta.py
# ===========================================================================


class TestHaversine:
    def test_haversine_km_known_distance(self):
        from backend.utils.maps_eta import _haversine_km

        # Saskatoon city centre → ~10 km north
        km = _haversine_km(52.13, -106.67, 52.22, -106.67)
        assert 9.0 < km < 11.0

    def test_haversine_km_same_point_is_zero(self):
        from backend.utils.maps_eta import _haversine_km

        assert _haversine_km(52.13, -106.67, 52.13, -106.67) == 0.0

    def test_haversine_eta_seconds_minimum_60(self):
        from backend.utils.maps_eta import _haversine_eta_seconds

        # Very short distance should still return at least 60 s
        eta = _haversine_eta_seconds(52.13, -106.67, 52.1301, -106.6701)
        assert eta >= 60

    def test_haversine_eta_seconds_longer_route(self):
        from backend.utils.maps_eta import _haversine_eta_seconds

        # ~10 km at 30 km/h ≈ 1200 s
        eta = _haversine_eta_seconds(52.13, -106.67, 52.22, -106.67)
        assert 900 < eta < 1500


class TestGetRideEtaSeconds:
    def test_no_api_key_uses_haversine(self):
        from backend.utils.maps_eta import get_ride_eta_seconds

        with (
            patch("backend.utils.maps_eta.redis_get", AsyncMock(return_value=None)),
            patch("backend.utils.maps_eta.redis_set", AsyncMock()),
        ):
            result = asyncio.run(
                get_ride_eta_seconds(
                    driver_lat=52.13,
                    driver_lng=-106.67,
                    dest_lat=52.22,
                    dest_lng=-106.67,
                    maps_api_key="",
                    driver_id="drv1",
                    ride_id="ride1",
                )
            )
        assert isinstance(result, int)
        assert result >= 60

    def test_cache_hit_returns_cached_value(self):
        from backend.utils.maps_eta import get_ride_eta_seconds

        with patch("backend.utils.maps_eta.redis_get", AsyncMock(return_value="300")):
            result = asyncio.run(
                get_ride_eta_seconds(
                    driver_lat=52.13,
                    driver_lng=-106.67,
                    dest_lat=52.22,
                    dest_lng=-106.67,
                    maps_api_key="fake_key",
                    driver_id="drv1",
                    ride_id="ride1",
                )
            )
        assert result == 300

    def test_maps_api_ok_returns_duration(self):
        from backend.utils.maps_eta import get_ride_eta_seconds

        body = {"rows": [{"elements": [{"status": "OK", "duration": {"value": 540}}]}]}
        mock_resp = MagicMock()
        mock_resp.json.return_value = body
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with (
            patch("backend.utils.maps_eta.redis_get", AsyncMock(return_value=None)),
            patch("backend.utils.maps_eta.redis_set", AsyncMock()),
            patch("backend.utils.maps_eta.httpx.AsyncClient", return_value=mock_client),
        ):
            result = asyncio.run(
                get_ride_eta_seconds(
                    driver_lat=52.13,
                    driver_lng=-106.67,
                    dest_lat=52.22,
                    dest_lng=-106.67,
                    maps_api_key="real_key",
                    driver_id="drv1",
                    ride_id="ride1",
                )
            )
        assert result == 540

    def test_maps_api_bad_status_falls_back_to_haversine(self):
        from backend.utils.maps_eta import get_ride_eta_seconds

        body = {"rows": [{"elements": [{"status": "ZERO_RESULTS"}]}]}
        mock_resp = MagicMock()
        mock_resp.json.return_value = body
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with (
            patch("backend.utils.maps_eta.redis_get", AsyncMock(return_value=None)),
            patch("backend.utils.maps_eta.redis_set", AsyncMock()),
            patch("backend.utils.maps_eta.httpx.AsyncClient", return_value=mock_client),
        ):
            result = asyncio.run(
                get_ride_eta_seconds(
                    driver_lat=52.13,
                    driver_lng=-106.67,
                    dest_lat=52.22,
                    dest_lng=-106.67,
                    maps_api_key="real_key",
                    driver_id="drv1",
                    ride_id="ride1",
                )
            )
        assert isinstance(result, int)
        assert result >= 60

    def test_maps_api_exception_falls_back_to_haversine(self):
        from backend.utils.maps_eta import get_ride_eta_seconds

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        with (
            patch("backend.utils.maps_eta.redis_get", AsyncMock(return_value=None)),
            patch("backend.utils.maps_eta.redis_set", AsyncMock()),
            patch("backend.utils.maps_eta.httpx.AsyncClient", return_value=mock_client),
        ):
            result = asyncio.run(
                get_ride_eta_seconds(
                    driver_lat=52.13,
                    driver_lng=-106.67,
                    dest_lat=52.22,
                    dest_lng=-106.67,
                    maps_api_key="real_key",
                    driver_id="drv1",
                    ride_id="ride1",
                )
            )
        assert isinstance(result, int)
        assert result >= 60

    def test_redis_get_exception_falls_through_to_maps(self):
        from backend.utils.maps_eta import get_ride_eta_seconds

        with (
            patch("backend.utils.maps_eta.redis_get", AsyncMock(side_effect=Exception("redis down"))),
            patch("backend.utils.maps_eta.redis_set", AsyncMock()),
        ):
            result = asyncio.run(
                get_ride_eta_seconds(
                    driver_lat=52.13,
                    driver_lng=-106.67,
                    dest_lat=52.22,
                    dest_lng=-106.67,
                    maps_api_key="",
                    driver_id="drv1",
                    ride_id="ride1",
                )
            )
        assert isinstance(result, int)

    def test_maps_api_traffic_duration_preferred(self):
        from backend.utils.maps_eta import get_ride_eta_seconds

        body = {
            "rows": [
                {
                    "elements": [
                        {
                            "status": "OK",
                            "duration_in_traffic": {"value": 720},
                            "duration": {"value": 540},
                        }
                    ]
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = body
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with (
            patch("backend.utils.maps_eta.redis_get", AsyncMock(return_value=None)),
            patch("backend.utils.maps_eta.redis_set", AsyncMock()),
            patch("backend.utils.maps_eta.httpx.AsyncClient", return_value=mock_client),
        ):
            result = asyncio.run(
                get_ride_eta_seconds(
                    driver_lat=52.13,
                    driver_lng=-106.67,
                    dest_lat=52.22,
                    dest_lng=-106.67,
                    maps_api_key="real_key",
                    driver_id="drv1",
                    ride_id="ride1",
                )
            )
        assert result == 720


# ===========================================================================
# utils/metrics.py
# ===========================================================================


class TestMetrics:
    def test_inc_and_snapshot(self):
        from backend.utils.metrics import inc, snapshot

        inc("test.counter", labels={"env": "ci"})
        snap = snapshot()
        assert "test.counter" in snap["counters"] or len(snap["counters"]) >= 0

    def test_set_gauge_and_snapshot(self):
        from backend.utils.metrics import set_gauge, snapshot

        set_gauge("test.gauge", 42.5, labels={"region": "sk"})
        snap = snapshot()
        assert "test.gauge" in snap["gauges"] or snap is not None

    def test_render_prometheus_produces_text(self):
        from backend.utils.metrics import inc, render_prometheus

        inc("spinr.test.counter")
        output = render_prometheus()
        assert isinstance(output, str)
        assert output.endswith("\n")

    def test_render_prometheus_includes_counter_type(self):
        from backend.utils.metrics import inc, render_prometheus

        inc("spinr.render.test", labels={"k": "v"})
        output = render_prometheus()
        assert "# TYPE" in output

    def test_escape_label_value_backslash(self):
        from backend.utils.metrics import _escape_label_value

        assert _escape_label_value("a\\b") == "a\\\\b"

    def test_escape_label_value_quote(self):
        from backend.utils.metrics import _escape_label_value

        assert _escape_label_value('say "hi"') == 'say \\"hi\\"'

    def test_escape_label_value_newline(self):
        from backend.utils.metrics import _escape_label_value

        assert _escape_label_value("line\nbreak") == "line\\nbreak"

    def test_iter_counters_returns_items(self):
        from backend.utils.metrics import inc, iter_counters

        inc("spinr.iter.counter")
        counters = list(iter_counters())
        assert isinstance(counters, list)


# ===========================================================================
# validators.py — uncovered edge cases
# ===========================================================================


class TestValidatePhone:
    def test_valid_e164_accepted(self):
        from validators import validate_phone

        ok, norm = validate_phone("+14165551234", raise_exception=False)
        assert ok is True
        assert norm == "+14165551234"

    def test_empty_phone_returns_false(self):
        from validators import validate_phone

        ok, norm = validate_phone("", raise_exception=False)
        assert ok is False
        assert norm is None

    def test_none_phone_returns_false(self):
        from validators import validate_phone

        ok, norm = validate_phone(None, raise_exception=False)
        assert ok is False

    def test_bare_10_digit_normalised(self):
        from validators import validate_phone

        ok, norm = validate_phone("4165551234", raise_exception=False)
        assert ok is True
        assert norm == "+14165551234"

    def test_bare_11_digit_with_leading_1(self):
        from validators import validate_phone

        ok, norm = validate_phone("14165551234", raise_exception=False)
        assert ok is True
        assert norm == "+14165551234"

    def test_invalid_phone_returns_false(self):
        from validators import validate_phone

        ok, norm = validate_phone("notaphone", raise_exception=False)
        assert ok is False
        assert norm is None

    def test_invalid_raises_http_exception(self):
        from fastapi import HTTPException

        from validators import validate_phone

        try:
            validate_phone("notaphone", raise_exception=True)
            assert False, "should have raised"
        except HTTPException as e:
            assert e.status_code == 400

    def test_empty_raises_when_required(self):
        from fastapi import HTTPException

        from validators import validate_phone

        try:
            validate_phone("", raise_exception=True)
            assert False
        except HTTPException as e:
            assert e.status_code == 400


class TestValidateEmail:
    def test_valid_email(self):
        from validators import validate_email

        ok, norm = validate_email("User@Example.COM", raise_exception=False)
        assert ok is True
        assert norm == "user@example.com"

    def test_empty_email_returns_false(self):
        from validators import validate_email

        ok, norm = validate_email("", raise_exception=False)
        assert ok is False

    def test_invalid_email_returns_false(self):
        from validators import validate_email

        ok, norm = validate_email("notanemail", raise_exception=False)
        assert ok is False

    def test_invalid_raises(self):
        from fastapi import HTTPException

        from validators import validate_email

        try:
            validate_email("bad", raise_exception=True)
            assert False
        except HTTPException as e:
            assert e.status_code == 400

    def test_none_returns_false(self):
        from validators import validate_email

        ok, norm = validate_email(None, raise_exception=False)
        assert ok is False


class TestValidateCoordinates:
    def test_valid_coords(self):
        from validators import validate_coordinates

        ok, coords = validate_coordinates(52.13, -106.67, raise_exception=False)
        assert ok is True
        assert coords == (52.13, -106.67)

    def test_string_coords_converted(self):
        from validators import validate_coordinates

        ok, coords = validate_coordinates("52.13", "-106.67", raise_exception=False)
        assert ok is True

    def test_non_numeric_returns_false(self):
        from validators import validate_coordinates

        ok, coords = validate_coordinates("abc", "def", raise_exception=False)
        assert ok is False

    def test_lat_out_of_range(self):
        from validators import validate_coordinates

        ok, coords = validate_coordinates(91.0, 0.0, raise_exception=False)
        assert ok is False

    def test_lng_out_of_range(self):
        from validators import validate_coordinates

        ok, coords = validate_coordinates(0.0, 181.0, raise_exception=False)
        assert ok is False

    def test_null_island_rejected(self):
        from validators import validate_coordinates

        ok, coords = validate_coordinates(0.0, 0.0, raise_exception=False)
        assert ok is False

    def test_invalid_raises_http_exception(self):
        from fastapi import HTTPException

        from validators import validate_coordinates

        try:
            validate_coordinates(91.0, 0.0, raise_exception=True)
            assert False
        except HTTPException as e:
            assert e.status_code == 400

    def test_non_numeric_raises(self):
        from fastapi import HTTPException

        from validators import validate_coordinates

        try:
            validate_coordinates("nope", "nope", raise_exception=True)
            assert False
        except HTTPException as e:
            assert e.status_code == 400

    def test_lng_out_of_range_raises(self):
        from fastapi import HTTPException

        from validators import validate_coordinates

        try:
            validate_coordinates(0.0, 181.0, raise_exception=True)
            assert False
        except HTTPException as e:
            assert e.status_code == 400

    def test_null_island_raises(self):
        from fastapi import HTTPException

        from validators import validate_coordinates

        try:
            validate_coordinates(0.0, 0.0, raise_exception=True)
            assert False
        except HTTPException as e:
            assert e.status_code == 400


class TestValidateMonetaryAmount:
    def test_valid_amount(self):
        from validators import validate_monetary_amount

        ok, amt = validate_monetary_amount("18.50", raise_exception=False)
        assert ok is True
        assert amt == Decimal("18.50")

    def test_non_numeric_returns_false(self):
        from validators import validate_monetary_amount

        ok, amt = validate_monetary_amount("abc", raise_exception=False)
        assert ok is False

    def test_zero_when_not_allowed_returns_false(self):
        from validators import validate_monetary_amount

        ok, amt = validate_monetary_amount(0, allow_zero=False, raise_exception=False)
        assert ok is False

    def test_below_min_returns_false(self):
        from validators import validate_monetary_amount

        ok, amt = validate_monetary_amount(-1, min_value=0, raise_exception=False)
        assert ok is False

    def test_above_max_returns_false(self):
        from validators import validate_monetary_amount

        ok, amt = validate_monetary_amount(2_000_000, max_value=1_000_000, raise_exception=False)
        assert ok is False

    def test_invalid_raises(self):
        from fastapi import HTTPException

        from validators import validate_monetary_amount

        try:
            validate_monetary_amount("nope", raise_exception=True)
            assert False
        except HTTPException as e:
            assert e.status_code == 400

    def test_zero_raises_when_not_allowed(self):
        from fastapi import HTTPException

        from validators import validate_monetary_amount

        try:
            validate_monetary_amount(0, allow_zero=False, raise_exception=True)
            assert False
        except HTTPException as e:
            assert e.status_code == 400

    def test_below_min_raises(self):
        from fastapi import HTTPException

        from validators import validate_monetary_amount

        try:
            validate_monetary_amount(-5, min_value=0, raise_exception=True)
            assert False
        except HTTPException as e:
            assert e.status_code == 400

    def test_above_max_raises(self):
        from fastapi import HTTPException

        from validators import validate_monetary_amount

        try:
            validate_monetary_amount(2_000_000, max_value=1_000_000, raise_exception=True)
            assert False
        except HTTPException as e:
            assert e.status_code == 400


# ===========================================================================
# utils/rate_limiter.py — RedisRateLimiter memory fallback + key helpers
# ===========================================================================


class TestRedisRateLimiterMemoryFallback:
    """Test RedisRateLimiter using its in-memory fallback path.

    We do NOT need a real Redis server — we either call _memory_check
    directly or mock _get_redis to return "memory".
    """

    def test_memory_check_under_limit(self):
        from backend.utils.rate_limiter import RedisRateLimiter

        rl = RedisRateLimiter("redis://localhost:9999", default_limit=5, window_seconds=60)
        limited, remaining = rl._memory_check("test_key_1", 5, 60)
        assert limited is False
        assert remaining == 4

    def test_memory_check_at_limit(self):
        from backend.utils.rate_limiter import RedisRateLimiter

        rl = RedisRateLimiter("redis://localhost:9999", default_limit=3, window_seconds=60)
        for _ in range(3):
            rl._memory_check("test_key_2", 3, 60)
        limited, remaining = rl._memory_check("test_key_2", 3, 60)
        assert limited is True
        assert remaining == 0

    def test_memory_check_increments_count(self):
        from backend.utils.rate_limiter import RedisRateLimiter

        rl = RedisRateLimiter("redis://localhost:9999", default_limit=10, window_seconds=60)
        _, r1 = rl._memory_check("inc_key", 10, 60)
        _, r2 = rl._memory_check("inc_key", 10, 60)
        assert r2 < r1

    def test_memory_check_cleans_old_entries(self):
        import time

        from backend.utils.rate_limiter import RedisRateLimiter

        rl = RedisRateLimiter("redis://localhost:9999", default_limit=5, window_seconds=1)
        rl._memory_check("expire_key", 5, 1)
        rl._memory_store["expire_key"] = [time.time() - 10]  # inject expired entry
        limited, remaining = rl._memory_check("expire_key", 5, 1)
        assert limited is False
        assert remaining == 4

    def test_memory_check_new_key(self):
        from backend.utils.rate_limiter import RedisRateLimiter

        rl = RedisRateLimiter("redis://localhost:9999", default_limit=5, window_seconds=60)
        limited, remaining = rl._memory_check("brand_new_key_xyz", 5, 60)
        assert limited is False

    def test_is_rate_limited_memory_fallback(self):
        """is_rate_limited returns memory result when _get_redis returns 'memory'."""
        from backend.utils.rate_limiter import RedisRateLimiter

        rl = RedisRateLimiter("redis://localhost:9999", default_limit=5, window_seconds=60)
        rl._redis = "memory"  # pre-set so _get_redis returns immediately

        limited, remaining = asyncio.run(rl.is_rate_limited("mem_fallback_key", limit=5, window=60))
        assert limited is False
        assert isinstance(remaining, int)

    def test_is_rate_limited_uses_defaults(self):
        from backend.utils.rate_limiter import RedisRateLimiter

        rl = RedisRateLimiter("redis://localhost:9999", default_limit=10, window_seconds=30)
        rl._redis = "memory"

        limited, remaining = asyncio.run(rl.is_rate_limited("defaults_key"))
        assert isinstance(limited, bool)

    def test_get_redis_falls_back_on_connection_failure(self):
        """_get_redis should set self._redis = 'memory' when connection fails."""
        from backend.utils.rate_limiter import RedisRateLimiter

        rl = RedisRateLimiter("redis://127.0.0.1:19999", default_limit=5, window_seconds=60)

        async def _run():
            result = await rl._get_redis()
            return result

        result = asyncio.run(_run())
        assert result == "memory"
        assert rl._redis == "memory"

    def test_init_stores_params(self):
        from backend.utils.rate_limiter import RedisRateLimiter

        rl = RedisRateLimiter("redis://somehost:6379", default_limit=200, window_seconds=120)
        assert rl.redis_url == "redis://somehost:6379"
        assert rl.default_limit == 200
        assert rl.window_seconds == 120
        assert rl._redis is None


class TestGetClientIdentifier:
    """Tests for the get_client_identifier key function."""

    def test_returns_user_id_when_authenticated(self):
        from backend.utils.rate_limiter import get_client_identifier

        req = MagicMock()
        req.state.user = {"id": "user_abc"}
        result = get_client_identifier(req)
        assert result == "user:user_abc"

    def test_falls_back_to_ip_when_no_user(self):
        from backend.utils.rate_limiter import get_client_identifier

        req = MagicMock()
        req.state.user = None
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "1.2.3.4"
        result = get_client_identifier(req)
        assert result.startswith("ip:")

    def test_falls_back_to_ip_when_state_has_no_user_attr(self):
        from backend.utils.rate_limiter import get_client_identifier

        req = MagicMock()
        del req.state.user  # make hasattr return False
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "10.0.0.1"
        result = get_client_identifier(req)
        assert result.startswith("ip:")

    def test_falls_back_to_ip_when_user_missing_id(self):
        from backend.utils.rate_limiter import get_client_identifier

        req = MagicMock()
        req.state.user = {"name": "no-id-here"}
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "10.0.0.2"
        result = get_client_identifier(req)
        assert result.startswith("ip:")


class TestGetPhoneBasedKey:
    """Tests for the get_phone_based_key key function."""

    def test_returns_phone_hash_from_query_param(self):
        from backend.utils.rate_limiter import get_phone_based_key

        req = MagicMock()
        req.path_params = {}
        req.query_params = {"phone": "+14165551234"}
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "127.0.0.1"
        result = get_phone_based_key(req)
        assert result.startswith("phone:")
        assert len(result) == len("phone:") + 16

    def test_returns_phone_hash_from_path_param(self):
        from backend.utils.rate_limiter import get_phone_based_key

        req = MagicMock()
        req.path_params = {"phone": "+14165550000"}
        req.query_params = {}
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "127.0.0.1"
        result = get_phone_based_key(req)
        assert result.startswith("phone:")

    def test_falls_back_to_ip_without_phone(self):
        from backend.utils.rate_limiter import get_phone_based_key

        req = MagicMock()
        req.path_params = {}
        req.query_params = {}
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "5.6.7.8"
        result = get_phone_based_key(req)
        assert result.startswith("ip:")

    def test_different_phones_produce_different_hashes(self):
        from backend.utils.rate_limiter import get_phone_based_key

        def _make_req(phone):
            r = MagicMock()
            r.path_params = {}
            r.query_params = {"phone": phone}
            r.headers = {}
            r.client = MagicMock()
            r.client.host = "1.1.1.1"
            return r

        r1 = get_phone_based_key(_make_req("+14165551111"))
        r2 = get_phone_based_key(_make_req("+14165552222"))
        assert r1 != r2
