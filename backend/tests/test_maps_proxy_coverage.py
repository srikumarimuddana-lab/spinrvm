"""Coverage for routes/maps_proxy.py (A1c, Sub-tier B).

Rider-facing Google Maps proxy (Places autocomplete/details, reverse-geocode
with a 24h Redis cache, venue-aware pickup points) sitting behind a daily-spend
circuit breaker (utils/maps_budget.py). Had 51.35% coverage and no dedicated
test file — `tests/test_maps_proxy.py` exists and covers the core Places(New)
billing + reverse-geocode cache happy paths and one budget-exceeded case for
autocomplete; this file fills the remaining branches: the "not configured" key
guard, upstream-error handling (HTTPStatusError vs generic exception) for all
three proxied endpoints, the budget-exceeded guard on details/reverse-geocode,
reverse-geocode's Redis-read-failure fallback and cache-write-failure warning,
the ZERO_RESULTS fallback formatting, autocomplete's distance-based re-sort,
and the pickup_points venue-matching branches (no venues, exception, no match,
nearest-of-several, malformed points filtered out, default radius).

Endpoint functions are called directly (bypassing FastAPI's Depends/route
machinery) with a plain `current_user` dict and a minimal real
`starlette.requests.Request` (required because these routes are wrapped by
`AsyncLimiter.limit(...)`, which does an `isinstance(request, Request)` check
before calling through — a MagicMock will not satisfy it).

Bug found, not fixed (test-only scope): `pickup_points` swallows *any*
exception from the venues lookup (`except Exception as e: logger.warning(...)`)
and returns an empty result rather than surfacing it — this violates this
repo's "do not silently swallow DB errors" convention (CLAUDE.md), which asks
for `logger.error` + a loud 503 on DB failures rather than a soft empty
fallback that looks identical to "no venue matched".

Test-only change — no application code modified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

pytestmark = pytest.mark.unit

_RIDER = {"id": "rider-1"}


def _fake_request() -> Request:
    """Minimal real Request that AsyncLimiter's isinstance check accepts."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/maps/test",
        "headers": [],
        "client": ("127.0.0.1", 0),
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
    }
    return Request(scope)


def _mock_httpx_response(payload: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _mock_async_client(get_result=None, post_result=None, get_side_effect=None, post_side_effect=None) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=get_result, side_effect=get_side_effect)
    client.post = AsyncMock(return_value=post_result, side_effect=post_side_effect)
    return client


# ── _maps_key / _ensure_budget ─────────────────────────────────────────────


class TestMapsKey:
    @pytest.mark.anyio
    async def test_raises_503_when_key_not_configured(self):
        from backend.routes.maps_proxy import _maps_key

        with patch("backend.routes.maps_proxy.get_app_settings", AsyncMock(return_value={})):
            with pytest.raises(HTTPException) as exc:
                await _maps_key()
            assert exc.value.status_code == 503
            assert "not configured" in exc.value.detail.lower()

    @pytest.mark.anyio
    async def test_raises_503_when_settings_row_is_none(self):
        from backend.routes.maps_proxy import _maps_key

        with patch("backend.routes.maps_proxy.get_app_settings", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await _maps_key()
            assert exc.value.status_code == 503

    @pytest.mark.anyio
    async def test_returns_key_when_configured(self):
        from backend.routes.maps_proxy import _maps_key

        settings_row = {"google_maps_api_key": "AIzaFakeKey"}
        with patch("backend.routes.maps_proxy.get_app_settings", AsyncMock(return_value=settings_row)):
            assert await _maps_key() == "AIzaFakeKey"


class TestEnsureBudget:
    @pytest.mark.anyio
    async def test_raises_503_when_budget_exceeded(self):
        from backend.routes.maps_proxy import _ensure_budget

        with patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(False, 6.0, 5.0))):
            with pytest.raises(HTTPException) as exc:
                await _ensure_budget()
            assert exc.value.status_code == 503
            assert "budget" in exc.value.detail.lower()

    @pytest.mark.anyio
    async def test_passes_when_within_budget(self):
        from backend.routes.maps_proxy import _ensure_budget

        with patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.1, 5.0))):
            await _ensure_budget()  # must not raise


# ── places_autocomplete: not-configured / budget / upstream errors / sort ──


class TestPlacesAutocompleteBranches:
    @pytest.mark.anyio
    async def test_budget_exceeded_returns_503_before_calling_google(self):
        from backend.routes.maps_proxy import places_autocomplete

        with (
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(False, 10.0, 5.0))),
            patch("backend.routes.maps_proxy.httpx.AsyncClient") as mock_client_cls,
        ):
            with pytest.raises(HTTPException) as exc:
                await places_autocomplete(
                    request=_fake_request(),
                    input="abc",
                    session_token=None,
                    location=None,
                    radius=50000,
                    current_user=_RIDER,
                )
            assert exc.value.status_code == 503
            mock_client_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_key_not_configured_returns_503(self):
        from backend.routes.maps_proxy import places_autocomplete

        with (
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy.get_app_settings", AsyncMock(return_value={})),
        ):
            with pytest.raises(HTTPException) as exc:
                await places_autocomplete(
                    request=_fake_request(),
                    input="abc",
                    session_token=None,
                    location=None,
                    radius=50000,
                    current_user=_RIDER,
                )
            assert exc.value.status_code == 503
            assert "key" in exc.value.detail.lower()

    @pytest.mark.anyio
    async def test_upstream_http_status_error_returns_502(self):
        import httpx

        from backend.routes.maps_proxy import places_autocomplete

        bad_resp = MagicMock()
        bad_resp.text = "quota exceeded"
        bad_resp.status_code = 429
        error = httpx.HTTPStatusError("boom", request=MagicMock(), response=bad_resp)
        client = _mock_async_client()
        client.post = AsyncMock(side_effect=error)

        with (
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
        ):
            with pytest.raises(HTTPException) as exc:
                await places_autocomplete(
                    request=_fake_request(),
                    input="abc",
                    session_token=None,
                    location=None,
                    radius=50000,
                    current_user=_RIDER,
                )
            assert exc.value.status_code == 502

    @pytest.mark.anyio
    async def test_upstream_generic_exception_returns_502(self):
        from backend.routes.maps_proxy import places_autocomplete

        client = _mock_async_client()
        client.post = AsyncMock(side_effect=RuntimeError("connection reset"))

        with (
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
        ):
            with pytest.raises(HTTPException) as exc:
                await places_autocomplete(
                    request=_fake_request(),
                    input="abc",
                    session_token=None,
                    location=None,
                    radius=50000,
                    current_user=_RIDER,
                )
            assert exc.value.status_code == 502
            assert "failed to call places api" in exc.value.detail.lower()

    @pytest.mark.anyio
    async def test_results_resorted_by_distance_when_location_given(self):
        """`Google's relevance sort can rank a distant match first; the route
        re-sorts by distance_meters when an origin was provided."""
        from backend.routes.maps_proxy import places_autocomplete

        payload = {
            "suggestions": [
                {
                    "placePrediction": {
                        "placeId": "far",
                        "text": {"text": "Far Walmart"},
                        "structuredFormat": {"mainText": {"text": "Far"}, "secondaryText": {"text": "SK"}},
                        "distanceMeters": 9000,
                    }
                },
                {
                    "placePrediction": {
                        "placeId": "near",
                        "text": {"text": "Near Walmart"},
                        "structuredFormat": {"mainText": {"text": "Near"}, "secondaryText": {"text": "SK"}},
                        "distanceMeters": 100,
                    }
                },
            ]
        }
        client = _mock_async_client(post_result=_mock_httpx_response(payload))

        with (
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
            patch("backend.routes.maps_proxy.record_call", AsyncMock()),
        ):
            result = await places_autocomplete(
                request=_fake_request(),
                input="walmart",
                session_token=None,
                location="52.1,-106.6",
                radius=50000,
                current_user=_RIDER,
            )

        assert [p["place_id"] for p in result["predictions"]] == ["near", "far"]

    @pytest.mark.anyio
    async def test_no_resort_when_location_not_given(self):
        """Without an origin the route returns Google's ordering unchanged."""
        from backend.routes.maps_proxy import places_autocomplete

        payload = {
            "suggestions": [
                {
                    "placePrediction": {
                        "placeId": "first",
                        "text": {"text": "First"},
                        "structuredFormat": {"mainText": {"text": "First"}, "secondaryText": {"text": "SK"}},
                    }
                },
                {
                    "placePrediction": {
                        "placeId": "second",
                        "text": {"text": "Second"},
                        "structuredFormat": {"mainText": {"text": "Second"}, "secondaryText": {"text": "SK"}},
                    }
                },
            ]
        }
        client = _mock_async_client(post_result=_mock_httpx_response(payload))

        with (
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
            patch("backend.routes.maps_proxy.record_call", AsyncMock()),
        ):
            result = await places_autocomplete(
                request=_fake_request(),
                input="abc",
                session_token=None,
                location=None,
                radius=50000,
                current_user=_RIDER,
            )

        assert [p["place_id"] for p in result["predictions"]] == ["first", "second"]

    @pytest.mark.anyio
    async def test_empty_predictions_not_resorted(self):
        """Guards the `if location and predictions:` branch when predictions is empty."""
        from backend.routes.maps_proxy import places_autocomplete

        client = _mock_async_client(post_result=_mock_httpx_response({"suggestions": []}))

        with (
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
            patch("backend.routes.maps_proxy.record_call", AsyncMock()),
        ):
            result = await places_autocomplete(
                request=_fake_request(),
                input="zzz",
                session_token=None,
                location="52.1,-106.6",
                radius=50000,
                current_user=_RIDER,
            )

        assert result == {"predictions": []}


# ── places_details: not-configured / budget / upstream errors / no token ──


class TestPlacesDetailsBranches:
    @pytest.mark.anyio
    async def test_budget_exceeded_returns_503(self):
        from backend.routes.maps_proxy import places_details

        with patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(False, 10.0, 5.0))):
            with pytest.raises(HTTPException) as exc:
                await places_details(request=_fake_request(), place_id="pid-1", session_token=None, current_user=_RIDER)
            assert exc.value.status_code == 503

    @pytest.mark.anyio
    async def test_key_not_configured_returns_503(self):
        from backend.routes.maps_proxy import places_details

        with (
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy.get_app_settings", AsyncMock(return_value={})),
        ):
            with pytest.raises(HTTPException) as exc:
                await places_details(request=_fake_request(), place_id="pid-1", session_token=None, current_user=_RIDER)
            assert exc.value.status_code == 503

    @pytest.mark.anyio
    async def test_upstream_http_status_error_returns_502(self):
        import httpx

        from backend.routes.maps_proxy import places_details

        bad_resp = MagicMock()
        bad_resp.text = "invalid place id"
        error = httpx.HTTPStatusError("boom", request=MagicMock(), response=bad_resp)
        client = _mock_async_client()
        client.get = AsyncMock(side_effect=error)

        with (
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
        ):
            with pytest.raises(HTTPException) as exc:
                await places_details(request=_fake_request(), place_id="pid-1", session_token=None, current_user=_RIDER)
            assert exc.value.status_code == 502

    @pytest.mark.anyio
    async def test_upstream_generic_exception_returns_502(self):
        from backend.routes.maps_proxy import places_details

        client = _mock_async_client()
        client.get = AsyncMock(side_effect=RuntimeError("dns failure"))

        with (
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
        ):
            with pytest.raises(HTTPException) as exc:
                await places_details(request=_fake_request(), place_id="pid-1", session_token=None, current_user=_RIDER)
            assert exc.value.status_code == 502

    @pytest.mark.anyio
    async def test_no_session_token_sends_empty_params(self):
        from backend.routes.maps_proxy import places_details

        payload = {"location": {"latitude": 1.0, "longitude": 2.0}, "formattedAddress": "1 St"}
        client = _mock_async_client(get_result=_mock_httpx_response(payload))

        with (
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
            patch("backend.routes.maps_proxy.record_call", AsyncMock()),
        ):
            result = await places_details(
                request=_fake_request(), place_id="pid-1", session_token=None, current_user=_RIDER
            )

        assert result == {"lat": 1.0, "lng": 2.0, "formatted_address": "1 St"}
        _, kwargs = client.get.await_args
        assert kwargs["params"] == {}


# ── reverse_geocode: cache-read failure / budget / not-configured / errors ─


class TestReverseGeocodeBranches:
    @pytest.mark.anyio
    async def test_redis_get_failure_falls_through_to_network(self):
        """`except Exception: cached = None` around redis_get — a Redis outage
        must not break reverse-geocode, just skip the cache."""
        from backend.routes.maps_proxy import reverse_geocode

        payload = {"status": "OK", "results": [{"formatted_address": "42 Main St"}]}
        client = _mock_async_client(get_result=_mock_httpx_response(payload))

        with (
            patch("backend.routes.maps_proxy.redis_get", AsyncMock(side_effect=RuntimeError("redis down"))),
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
            patch("backend.routes.maps_proxy.record_call", AsyncMock()),
            patch("backend.routes.maps_proxy.redis_set", AsyncMock()),
        ):
            result = await reverse_geocode(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)

        assert result == {"formatted_address": "42 Main St", "cached": False}

    @pytest.mark.anyio
    async def test_budget_exceeded_returns_503_on_cache_miss(self):
        from backend.routes.maps_proxy import reverse_geocode

        with (
            patch("backend.routes.maps_proxy.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(False, 10.0, 5.0))),
        ):
            with pytest.raises(HTTPException) as exc:
                await reverse_geocode(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)
            assert exc.value.status_code == 503

    @pytest.mark.anyio
    async def test_key_not_configured_returns_503(self):
        from backend.routes.maps_proxy import reverse_geocode

        with (
            patch("backend.routes.maps_proxy.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy.get_app_settings", AsyncMock(return_value={})),
        ):
            with pytest.raises(HTTPException) as exc:
                await reverse_geocode(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)
            assert exc.value.status_code == 503

    @pytest.mark.anyio
    async def test_network_exception_returns_502(self):
        from backend.routes.maps_proxy import reverse_geocode

        client = _mock_async_client()
        client.get = AsyncMock(side_effect=RuntimeError("timeout"))

        with (
            patch("backend.routes.maps_proxy.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
        ):
            with pytest.raises(HTTPException) as exc:
                await reverse_geocode(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)
            assert exc.value.status_code == 502
            assert "geocoding" in exc.value.detail.lower()

    @pytest.mark.anyio
    async def test_non_ok_status_returns_502(self):
        """`status` outside (OK, ZERO_RESULTS) — e.g. REQUEST_DENIED/OVER_QUERY_LIMIT."""
        from backend.routes.maps_proxy import reverse_geocode

        client = _mock_async_client(get_result=_mock_httpx_response({"status": "REQUEST_DENIED"}))

        with (
            patch("backend.routes.maps_proxy.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
        ):
            with pytest.raises(HTTPException) as exc:
                await reverse_geocode(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)
            assert exc.value.status_code == 502

    @pytest.mark.anyio
    async def test_zero_results_falls_back_to_rounded_coordinates(self):
        from backend.routes.maps_proxy import reverse_geocode

        client = _mock_async_client(get_result=_mock_httpx_response({"status": "ZERO_RESULTS", "results": []}))

        with (
            patch("backend.routes.maps_proxy.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
            patch("backend.routes.maps_proxy.record_call", AsyncMock()),
            patch("backend.routes.maps_proxy.redis_set", AsyncMock()),
        ):
            result = await reverse_geocode(request=_fake_request(), lat=52.12345, lng=-106.65432, current_user=_RIDER)

        # round(52.12345, 4) == 52.1234, not 52.1235 — float binary
        # representation of 52.12345 is slightly under the exact decimal
        # value, so Python's round() rounds down here.
        assert result == {"formatted_address": "52.1234, -106.6543", "cached": False}

    @pytest.mark.anyio
    async def test_cache_write_failure_is_swallowed_with_warning(self):
        """`redis_set` failing must not fail the request — the response was
        already computed; caching is best-effort."""
        from backend.routes.maps_proxy import reverse_geocode

        payload = {"status": "OK", "results": [{"formatted_address": "9 Elm St"}]}
        client = _mock_async_client(get_result=_mock_httpx_response(payload))

        with (
            patch("backend.routes.maps_proxy.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.maps_proxy.check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
            patch("backend.routes.maps_proxy._maps_key", AsyncMock(return_value="dummy")),
            patch("backend.routes.maps_proxy.httpx.AsyncClient", return_value=client),
            patch("backend.routes.maps_proxy.record_call", AsyncMock()),
            patch("backend.routes.maps_proxy.redis_set", AsyncMock(side_effect=RuntimeError("redis write down"))),
        ):
            result = await reverse_geocode(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)

        assert result == {"formatted_address": "9 Elm St", "cached": False}


# ── _haversine_m ────────────────────────────────────────────────────────


class TestHaversine:
    def test_same_point_is_zero_distance(self):
        from backend.routes.maps_proxy import _haversine_m

        assert _haversine_m(52.1, -106.6, 52.1, -106.6) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance_regina_to_saskatoon_is_roughly_correct(self):
        """Regina ↔ Saskatoon great-circle distance is ~230 km; sanity-bound
        the haversine implementation rather than pinning an exact value."""
        from backend.routes.maps_proxy import _haversine_m

        d_m = _haversine_m(50.4452, -104.6189, 52.1332, -106.6700)
        assert 180_000 < d_m < 280_000


# ── pickup_points: venue matching branches ─────────────────────────────


class TestPickupPoints:
    @pytest.mark.anyio
    async def test_venue_lookup_exception_returns_empty_result(self):
        from backend.routes.maps_proxy import pickup_points

        with patch("backend.routes.maps_proxy.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
            result = await pickup_points(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)

        assert result == {"venue": None, "pickup_points": []}

    @pytest.mark.anyio
    async def test_no_venues_returns_empty_result(self):
        from backend.routes.maps_proxy import pickup_points

        with patch("backend.routes.maps_proxy.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = await pickup_points(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)

        assert result == {"venue": None, "pickup_points": []}

    @pytest.mark.anyio
    async def test_venue_missing_center_coords_is_skipped(self):
        from backend.routes.maps_proxy import pickup_points

        venues = [{"id": "v1", "name": "No Center", "center_lat": None, "center_lng": None}]
        with patch("backend.routes.maps_proxy.db_supabase.get_rows", AsyncMock(return_value=venues)):
            result = await pickup_points(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)

        assert result == {"venue": None, "pickup_points": []}

    @pytest.mark.anyio
    async def test_pin_outside_every_venue_radius_returns_no_venue(self):
        from backend.routes.maps_proxy import pickup_points

        venues = [
            {"id": "v1", "name": "Far Mall", "center_lat": 52.5, "center_lng": -107.0, "radius_m": 100},
        ]
        with patch("backend.routes.maps_proxy.db_supabase.get_rows", AsyncMock(return_value=venues)):
            result = await pickup_points(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)

        assert result == {"venue": None, "pickup_points": []}

    @pytest.mark.anyio
    async def test_pin_inside_radius_returns_venue_and_points(self):
        from backend.routes.maps_proxy import pickup_points

        venues = [
            {
                "id": "v1",
                "name": "Midtown Mall",
                "center_lat": 52.1001,
                "center_lng": -106.6001,
                "radius_m": 500,
                "pickup_points": [
                    {"name": "Main Entrance", "lat": 52.1002, "lng": -106.6002},
                    {"name": "No Coords", "lat": None, "lng": None},
                ],
            }
        ]
        with patch("backend.routes.maps_proxy.db_supabase.get_rows", AsyncMock(return_value=venues)):
            result = await pickup_points(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)

        assert result["venue"] == {"id": "v1", "name": "Midtown Mall"}
        # Malformed point (missing lat/lng) must be filtered out.
        assert result["pickup_points"] == [{"name": "Main Entrance", "lat": 52.1002, "lng": -106.6002}]

    @pytest.mark.anyio
    async def test_nearest_of_several_overlapping_venues_wins(self):
        from backend.routes.maps_proxy import pickup_points

        venues = [
            {
                "id": "far",
                "name": "Farther",
                "center_lat": 52.1010,
                "center_lng": -106.6010,
                "radius_m": 2000,
                "pickup_points": [],
            },
            {
                "id": "near",
                "name": "Nearer",
                "center_lat": 52.1001,
                "center_lng": -106.6001,
                "radius_m": 2000,
                "pickup_points": [],
            },
        ]
        with patch("backend.routes.maps_proxy.db_supabase.get_rows", AsyncMock(return_value=venues)):
            result = await pickup_points(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)

        assert result["venue"]["id"] == "near"

    @pytest.mark.anyio
    async def test_missing_radius_defaults_to_150m(self):
        from backend.routes.maps_proxy import pickup_points

        # ~5m away from center, no radius_m key at all -> should still match
        # against the 150m default.
        venues = [
            {
                "id": "v1",
                "name": "Default Radius",
                "center_lat": 52.10005,
                "center_lng": -106.60005,
                "pickup_points": [],
            }
        ]
        with patch("backend.routes.maps_proxy.db_supabase.get_rows", AsyncMock(return_value=venues)):
            result = await pickup_points(request=_fake_request(), lat=52.1, lng=-106.6, current_user=_RIDER)

        assert result["venue"]["id"] == "v1"
