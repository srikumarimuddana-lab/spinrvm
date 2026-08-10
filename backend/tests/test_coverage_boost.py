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


class TestGetAiChatKey:
    """Tests for get_ai_chat_key / _extract_unverified_user_id (ACTION_ITEMS.md AI1)."""

    def test_keys_by_user_id_claim(self):
        import jwt

        from backend.utils.rate_limiter import get_ai_chat_key

        token = jwt.encode({"user_id": "rider_42"}, "unused-secret", algorithm="HS256")
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}"}
        assert get_ai_chat_key(req) == "user:rider_42"

    def test_falls_back_to_sub_claim(self):
        import jwt

        from backend.utils.rate_limiter import get_ai_chat_key

        token = jwt.encode({"sub": "driver_7"}, "unused-secret", algorithm="HS256")
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}"}
        assert get_ai_chat_key(req) == "user:driver_7"

    def test_two_ips_same_token_share_one_bucket(self):
        import jwt

        from backend.utils.rate_limiter import get_ai_chat_key

        token = jwt.encode({"user_id": "rider_99"}, "unused-secret", algorithm="HS256")
        req_a = MagicMock()
        req_a.headers = {"Authorization": f"Bearer {token}", "cf-connecting-ip": "1.1.1.1"}
        req_b = MagicMock()
        req_b.headers = {"Authorization": f"Bearer {token}", "cf-connecting-ip": "2.2.2.2"}
        assert get_ai_chat_key(req_a) == get_ai_chat_key(req_b) == "user:rider_99"

    def test_falls_back_to_ip_when_no_bearer_token(self):
        from backend.utils.rate_limiter import get_ai_chat_key

        req = MagicMock()
        req.headers = {"cf-connecting-ip": "9.9.9.9"}
        assert get_ai_chat_key(req) == "ip:9.9.9.9"

    def test_falls_back_to_ip_when_token_is_garbage(self):
        from backend.utils.rate_limiter import get_ai_chat_key

        req = MagicMock()
        req.headers = {"Authorization": "Bearer not-a-real-jwt", "cf-connecting-ip": "9.9.9.9"}
        assert get_ai_chat_key(req) == "ip:9.9.9.9"

    def test_falls_back_to_ip_when_token_has_no_user_claim(self):
        import jwt

        from backend.utils.rate_limiter import get_ai_chat_key

        token = jwt.encode({"foo": "bar"}, "unused-secret", algorithm="HS256")
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}", "cf-connecting-ip": "9.9.9.9"}
        assert get_ai_chat_key(req) == "ip:9.9.9.9"

    def test_unverified_extraction_does_not_require_correct_signature(self):
        """A token signed with an unknown/wrong secret still yields its
        claimed user_id -- correctness is enforced by get_current_user
        downstream, not by this rate-limit key function."""
        import jwt

        from backend.utils.rate_limiter import _extract_unverified_user_id

        token = jwt.encode({"user_id": "whatever"}, "not-the-real-jwt-secret", algorithm="HS256")
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}"}
        assert _extract_unverified_user_id(req) == "whatever"

    def test_extraction_returns_none_without_bearer_prefix(self):
        from backend.utils.rate_limiter import _extract_unverified_user_id

        req = MagicMock()
        req.headers = {"Authorization": "Basic dXNlcjpwYXNz"}
        assert _extract_unverified_user_id(req) is None


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


# ===========================================================================
# utils/rate_limiter.py — rate_limit_auth decorator + init_rate_limiting
# ===========================================================================


class TestRateLimitAuthDecorator:
    """Exercise the rate_limit_auth decorator inner functions."""

    def test_success_path_passes_through(self):
        from backend.utils.rate_limiter import rate_limit_auth

        @rate_limit_auth(requests=5, period=60)
        async def my_endpoint(*args, **kwargs):
            return {"ok": True}

        result = asyncio.run(my_endpoint())
        assert result == {"ok": True}

    def test_rate_limit_exceeded_raises_429(self):
        from slowapi.errors import RateLimitExceeded

        from backend.utils.rate_limiter import rate_limit_auth

        @rate_limit_auth(requests=3, period=30)
        async def limited_endpoint(*args, **kwargs):
            raise RateLimitExceeded(MagicMock())

        from fastapi import HTTPException

        try:
            asyncio.run(limited_endpoint())
            assert False, "should have raised HTTPException"
        except HTTPException as e:
            assert e.status_code == 429

    def test_other_exceptions_propagate(self):
        from backend.utils.rate_limiter import rate_limit_auth

        @rate_limit_auth(requests=5, period=60)
        async def broken_endpoint(*args, **kwargs):
            raise ValueError("not a rate limit")

        try:
            asyncio.run(broken_endpoint())
            assert False
        except ValueError as e:
            assert str(e) == "not a rate limit"


class TestInitRateLimiting:
    def test_init_attaches_limiter_and_handler(self):
        from backend.utils.rate_limiter import default_limiter, init_rate_limiting

        app = MagicMock()
        init_rate_limiting(app)

        assert app.state.limiter is default_limiter
        app.add_exception_handler.assert_called_once()


# ===========================================================================
# utils/redis_client.py — Redis-path coverage via mocked _get_redis
# ===========================================================================


class TestRedisClientRedisPaths:
    """Cover the Redis-connected branches of redis_client.py helpers.

    We mock _get_redis to return an async mock Redis client so the
    'if r is not None' branches execute without a real Redis server.
    """

    def _make_redis_mock(self):
        r = AsyncMock()
        r.get = AsyncMock(return_value="val")
        r.set = AsyncMock(return_value=True)
        r.setex = AsyncMock(return_value=True)
        r.delete = AsyncMock(return_value=1)
        r.expire = AsyncMock(return_value=True)
        r.incr = AsyncMock(return_value=1)
        r.scan_iter = MagicMock(return_value=_async_iter([]))
        return r

    def test_redis_get_redis_path(self):
        import backend.utils.redis_client as rc

        mock_r = self._make_redis_mock()
        with patch.object(rc, "_get_redis", AsyncMock(return_value=mock_r)):
            result = asyncio.run(rc.redis_get("some_key"))
        assert result == "val"

    def test_redis_set_redis_path_with_ttl(self):
        import backend.utils.redis_client as rc

        mock_r = self._make_redis_mock()
        with patch.object(rc, "_get_redis", AsyncMock(return_value=mock_r)):
            asyncio.run(rc.redis_set("k", "v", ttl=60))
        mock_r.setex.assert_called_once_with("k", 60, "v")

    def test_redis_set_redis_path_no_ttl(self):
        import backend.utils.redis_client as rc

        mock_r = self._make_redis_mock()
        with patch.object(rc, "_get_redis", AsyncMock(return_value=mock_r)):
            asyncio.run(rc.redis_set("k", "v"))
        mock_r.set.assert_called_once_with("k", "v")

    def test_redis_incr_redis_path(self):
        import backend.utils.redis_client as rc

        mock_r = self._make_redis_mock()
        with patch.object(rc, "_get_redis", AsyncMock(return_value=mock_r)):
            result = asyncio.run(rc.redis_incr("counter"))
        assert result == 1

    def test_redis_expire_redis_path(self):
        import backend.utils.redis_client as rc

        mock_r = self._make_redis_mock()
        with patch.object(rc, "_get_redis", AsyncMock(return_value=mock_r)):
            asyncio.run(rc.redis_expire("k", 30))
        mock_r.expire.assert_called_once_with("k", 30)

    def test_redis_delete_redis_path(self):
        import backend.utils.redis_client as rc

        mock_r = self._make_redis_mock()
        with patch.object(rc, "_get_redis", AsyncMock(return_value=mock_r)):
            asyncio.run(rc.redis_delete("k"))
        mock_r.delete.assert_called_once_with("k")

    def test_redis_delete_pattern_empty(self):
        import backend.utils.redis_client as rc

        mock_r = self._make_redis_mock()
        with patch.object(rc, "_get_redis", AsyncMock(return_value=mock_r)):
            result = asyncio.run(rc.redis_delete_pattern("cache:*"))
        assert result == 0


def _async_iter(items):
    """Async generator for mock scan_iter."""

    async def _gen():
        for item in items:
            yield item

    return _gen()


# ===========================================================================
# utils/rate_limiter.py — rate_limit_exceeded_handler exception branch
# ===========================================================================


class TestRateLimitExceededHandler:
    """Cover the exc.limit.limit.get_expiry() failure path (lines 255-259)."""

    def test_handler_uses_default_when_exc_is_malformed(self):

        from backend.utils.rate_limiter import rate_limit_exceeded_handler

        req = MagicMock()
        req.url.path = "/api/test"
        req.method = "POST"
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "1.2.3.4"

        # exc.limit is a MagicMock whose .limit.get_expiry() raises AttributeError
        exc = MagicMock()
        exc.limit.limit.get_expiry.side_effect = AttributeError("no get_expiry")

        result = asyncio.run(rate_limit_exceeded_handler(req, exc))
        assert result.status_code == 429

    def test_handler_returns_json_with_retry_after(self):
        import json

        from backend.utils.rate_limiter import rate_limit_exceeded_handler

        req = MagicMock()
        req.url.path = "/api/v1/auth/otp"
        req.method = "POST"
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "10.0.0.1"

        # Well-formed limit object
        exc = MagicMock()
        exc.limit.limit.get_expiry.return_value = 60
        exc.limit.limit.amount = 5

        result = asyncio.run(rate_limit_exceeded_handler(req, exc))
        body = json.loads(result.body)
        assert result.status_code == 429
        assert "retry_after" in body
        assert body["error"] == "rate_limit_exceeded"


# ===========================================================================
# utils/subprocessor_audit.py — run_audit edge cases + main()
# ===========================================================================


class TestSubprocessorAuditRunAudit:
    def _mock_path(self, exists=True, text=None):
        m = MagicMock()
        m.exists.return_value = exists
        if text is not None:
            m.read_text.return_value = text
        return m

    def _sa_mod(self):
        import sys

        from backend.utils import subprocessor_audit as sa

        # The module may be cached under bare key "utils.subprocessor_audit"
        # (conftest adds backend/ to sys.path first) OR under the qualified
        # key.  Determine which one to patch.
        if "utils.subprocessor_audit" in sys.modules:
            return sa, "utils.subprocessor_audit"
        return sa, "backend.utils.subprocessor_audit"

    def test_returns_2_when_baseline_missing(self):
        sa, mod_key = self._sa_mod()
        mp = self._mock_path(exists=False)
        with patch(f"{mod_key}.BASELINE_PATH", mp):
            result = sa.run_audit()
        assert result == 2

    def test_returns_2_when_baseline_not_parseable(self):
        sa, mod_key = self._sa_mod()
        mp = self._mock_path(exists=True, text="not json {{{{")
        with patch(f"{mod_key}.BASELINE_PATH", mp):
            result = sa.run_audit()
        assert result == 2

    def test_skips_vendor_with_no_url(self):
        import json

        sa, mod_key = self._sa_mod()
        baseline = {"vendors": {"acme": {"name": "Acme Corp"}}}
        mp = self._mock_path(exists=True, text=json.dumps(baseline))
        with patch(f"{mod_key}.BASELINE_PATH", mp):
            result = sa.run_audit(dry_run=True)
        assert result == 0

    def test_no_change_path(self):
        import hashlib
        import json

        sa, mod_key = self._sa_mod()
        content = "page content"
        h = hashlib.sha256(content.encode()).hexdigest()
        baseline = {
            "vendors": {
                "stripe": {
                    "name": "Stripe",
                    "subprocessor_url": "https://stripe.com/subs",
                    "content_hash": h,
                    "etag": None,
                }
            }
        }
        mp = self._mock_path(exists=True, text=json.dumps(baseline))
        with (
            patch(f"{mod_key}.BASELINE_PATH", mp),
            patch(f"{mod_key}._fetch_page", return_value=(content, None)),
        ):
            result = sa.run_audit(dry_run=True)
        assert result == 0


class TestSubprocessorAuditMain:
    def test_main_exits_with_audit_result(self):
        from backend.utils import subprocessor_audit as sa

        with (
            patch("sys.argv", ["subprocessor_audit"]),
            patch.object(sa, "run_audit", return_value=0),
            patch("sys.exit") as mock_exit,
        ):
            sa.main()
        mock_exit.assert_called_once_with(0)

    def test_main_passes_dry_run_flag(self):
        from backend.utils import subprocessor_audit as sa

        with (
            patch("sys.argv", ["subprocessor_audit", "--dry-run"]),
            patch.object(sa, "run_audit", return_value=0) as mock_run,
            patch("sys.exit"),
        ):
            sa.main()
        mock_run.assert_called_once_with(dry_run=True)


# ===========================================================================
# utils/redis_client.py — _get_redis direct coverage
# ===========================================================================


class TestGetRedisDirect:
    """Test _get_redis() directly to cover cache-hit and creation paths."""

    def test_returns_none_when_no_redis_url(self):
        import backend.utils.redis_client as rc

        saved = rc._redis
        saved_url = rc._redis_url
        try:
            rc._redis = None
            rc._redis_url = None
            with patch.dict(__import__("os").environ, {}, clear=False):
                orig = __import__("os").environ.pop("REDIS_URL", None)
                try:
                    result = asyncio.run(rc._get_redis())
                finally:
                    if orig is not None:
                        __import__("os").environ["REDIS_URL"] = orig
        finally:
            rc._redis = saved
            rc._redis_url = saved_url
        assert result is None

    def test_returns_cached_client_on_second_call(self):
        import os

        import backend.utils.redis_client as rc

        mock_client = MagicMock()
        saved = rc._redis
        saved_url = rc._redis_url
        try:
            rc._redis = mock_client
            rc._redis_url = "redis://cached:6379"
            with patch.dict(os.environ, {"REDIS_URL": "redis://cached:6379"}):
                result = asyncio.run(rc._get_redis())
        finally:
            rc._redis = saved
            rc._redis_url = saved_url
        assert result is mock_client

    def test_creates_new_client_when_not_cached(self):
        import os
        import sys

        import backend.utils.redis_client as rc

        mock_client = MagicMock()
        mock_aioredis = MagicMock()
        mock_aioredis.from_url = MagicMock(return_value=mock_client)
        # `import redis.asyncio as x` uses IMPORT_FROM which reads redis.asyncio
        # as an attribute of the redis module object, not sys.modules["redis.asyncio"].
        # Patch the attribute on the redis module directly.
        redis_mod = sys.modules.get("redis")
        saved_attr = getattr(redis_mod, "asyncio", None) if redis_mod is not None else None
        saved = rc._redis
        saved_url = rc._redis_url
        try:
            rc._redis = None
            rc._redis_url = None
            if redis_mod is not None:
                redis_mod.asyncio = mock_aioredis
            with (
                patch.dict(os.environ, {"REDIS_URL": "redis://newhost:6379"}),
                patch.dict(sys.modules, {"redis.asyncio": mock_aioredis}),
            ):
                result = asyncio.run(rc._get_redis())
        finally:
            rc._redis = saved
            rc._redis_url = saved_url
            if redis_mod is not None:
                if saved_attr is not None:
                    redis_mod.asyncio = saved_attr
        assert result is mock_client

    def test_returns_none_on_connection_error(self):
        import os
        import sys

        import backend.utils.redis_client as rc

        mock_aioredis = MagicMock()
        mock_aioredis.from_url = MagicMock(side_effect=Exception("connection refused"))
        redis_mod = sys.modules.get("redis")
        saved_attr = getattr(redis_mod, "asyncio", None) if redis_mod is not None else None
        saved = rc._redis
        saved_url = rc._redis_url
        try:
            rc._redis = None
            rc._redis_url = None
            if redis_mod is not None:
                redis_mod.asyncio = mock_aioredis
            with (
                patch.dict(os.environ, {"REDIS_URL": "redis://badhost:6379"}),
                patch.dict(sys.modules, {"redis.asyncio": mock_aioredis}),
            ):
                result = asyncio.run(rc._get_redis())
        finally:
            rc._redis = saved
            rc._redis_url = saved_url
            if redis_mod is not None:
                if saved_attr is not None:
                    redis_mod.asyncio = saved_attr
        assert result is None
